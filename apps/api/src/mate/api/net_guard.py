"""SSRF guard for server-side fetches of user-supplied URLs.

MATE fetches URLs that a logged-in user controls in two places: the
"import from URL" event-log import (``services.log_aggregates._download``) and a
self-hosted AI provider's ``base_url`` (``ai_guidance`` / ``routes.ai``). Without
a guard, a user could point either at an internal address the API container can
reach — other containers, the host, the university network, or a cloud-metadata
endpoint — and use the server as a proxy (SSRF), reading back internal responses.

``assert_public_url`` enforces, before any connection:

- scheme is ``http`` or ``https`` (no ``file://``, ``gopher://``, ``ftp://`` …);
- the host resolves only to **public** addresses — every A/AAAA record is
  rejected if it is loopback, private, link-local (incl. ``169.254.0.0/16`` /
  ``fd00::/8`` / cloud metadata), unspecified, reserved or multicast.

Because DNS is resolved here and the set of IPs is returned, the caller connects
with redirects disabled and re-checks every hop, which closes the
"external URL 302-redirects to an internal one" bypass. A residual DNS-rebinding
race (resolve public here, connect to a re-pointed record) is not fully closed
without pinning the socket to a vetted IP; that is noted as a follow-up. This
guard blocks every static internal target, which is the exploitable case.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from fastapi import HTTPException

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _reject(detail: str) -> HTTPException:
    # 400, not 403: don't confirm reachability of internal hosts to the caller.
    return HTTPException(status_code=400, detail=detail)


async def assert_public_url(url: str) -> list[str]:
    """Validate ``url`` for a server-side fetch; return its resolved public IPs.

    Raises ``HTTPException(400)`` on a non-http(s) scheme, an unresolvable host,
    or any address that is not public. The returned IPs let the caller connect
    to a vetted target and re-validate after each redirect hop.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise _reject("Only http and https URLs can be imported.")
    host = parts.hostname
    if not host:
        raise _reject("The URL has no host.")
    # A bare IP literal in the URL: check it directly (no DNS).
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False

    if literal:
        if not _is_public(host):
            raise _reject("That address is not allowed.")
        return [host]

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise _reject("The URL's host could not be resolved.") from exc

    ips = {info[4][0] for info in infos}
    if not ips:
        raise _reject("The URL's host could not be resolved.")
    for ip in ips:
        if not _is_public(ip):
            raise _reject("That host resolves to a non-public address and is not allowed.")
    return sorted(ips)
