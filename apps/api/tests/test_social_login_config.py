"""Google / Microsoft / GitHub sign-in: the pieces that must keep agreeing.

A provider alias is written in five places - the Keycloak provisioning script, the web
allowlist, the web brand icons, the theme's logo mapping, and the callback URL
registered at the provider itself (`.../broker/<alias>/endpoint`) - and none of them can
be renamed on one side only. The script also carries two decisions that fail quietly
when edited away: a provider's email never links an account (the first-login flow), and
Microsoft's email is never trusted. Cheap static guards for all of it; the flows
themselves are checked against a real Keycloak (see docs/DEPLOY.md).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "infra" / "keycloak" / "configure-social-idps.sh"
THEME_LOGIN = REPO / "infra" / "keycloak" / "themes" / "mate" / "login"
WEB = REPO / "apps" / "web"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _script_aliases() -> set[str]:
    """The keys of the script's per-provider table (`"google": {"name": ...}`)."""
    return set(re.findall(r'^\s+"(\w+)":\s+\{"name"', _script(), re.M))


def _web_aliases() -> set[str]:
    """The keys of `SOCIAL_IDPS` in lib/social-idps.ts."""
    text = (WEB / "lib" / "social-idps.ts").read_text(encoding="utf-8")
    block = text.split("export const SOCIAL_IDPS = {", 1)[1].split("} as const;", 1)[0]
    return set(re.findall(r"^\s+(\w+):\s+\{\s*label:", block, re.M))


def _icon_aliases() -> set[str]:
    """The keys of the `ICONS` record in components/brand-icons.tsx."""
    text = (WEB / "components" / "brand-icons.tsx").read_text(encoding="utf-8")
    block = text.split("const ICONS:", 1)[1].split("};", 1)[0]
    return set(re.findall(r"^\s+(\w+):\s+\w+Icon,", block, re.M))


def _theme_aliases() -> set[str]:
    props = (THEME_LOGIN / "theme.properties").read_text(encoding="utf-8")
    return set(re.findall(r"^kcLogoIdP-([\w-]+)=", props, re.M))


def test_the_script_is_valid_bash() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_place_that_names_a_provider_agrees() -> None:
    expected = {"google", "microsoft", "github"}
    assert _script_aliases() == expected, "provider table in configure-social-idps.sh"
    assert _web_aliases() == expected, "SOCIAL_IDPS in apps/web/lib/social-idps.ts"
    assert _icon_aliases() == expected, "ICONS in apps/web/components/brand-icons.tsx"
    assert _theme_aliases() == expected, "kcLogoIdP-* in the mate theme's theme.properties"
    # the loop that reads <PROVIDER>_CLIENT_ID / _SECRET walks the same three
    assert "for p in google microsoft github; do" in _script()


def test_a_provider_email_never_links_an_account() -> None:
    """The first-login flow of the providers: no "Handle Existing Account", "Create User If
    Unique" required (only a required one shows the 'already exists' page)."""
    script = _script()
    assert 'set_requirement "$FLOW" "$handle_id" DISABLED' in script
    assert 'set_requirement "$FLOW" "$create_id" REQUIRED' in script
    # ... and every provider the script creates is put on that flow
    assert '"firstBrokerLoginFlowAlias": env["FLOW"]' in script


def test_microsoft_email_is_never_trusted() -> None:
    """A tenant admin can put any address in `mail`; Google and GitHub vouch for theirs."""
    script = _script()
    trust = dict(re.findall(r'"(\w+)":\s+\{"name": "\w+",\s+"trust_email": (True|False)', script))
    assert trust == {"google": "True", "microsoft": "False", "github": "True"}


def test_a_later_sign_in_never_rewrites_the_account() -> None:
    """IMPORT copies name/email once; FORCE would swap them on every sign-in."""
    script = _script()
    assert '"syncMode": "IMPORT"' in script
    assert '"syncMode": "FORCE"' not in script


def test_microsoft_needs_an_explicit_tenant() -> None:
    """The tenant must match the Entra app registration and is a trust decision: no default."""
    script = _script()
    assert 'MICROSOFT_TENANT="${MICROSOFT_TENANT:-}"' in script
    assert '[ -z "$MICROSOFT_TENANT" ]' in script


def test_the_script_leaves_the_browser_flow_alone() -> None:
    """A default provider on the Identity Provider Redirector would send "Login with email"
    and every direct Keycloak sign-in to that provider, hint or not (see /login/start).
    The script may read it to warn, never write it."""
    script = _script()
    writes = re.findall(
        r'kc(?:_pipe)? (?:update|create|delete) "authentication/(?:config|executions/[^"]*/config)',
        script,
    )
    assert not writes, f"configure-social-idps.sh writes redirector config: {writes}"
    assert "-s config.defaultProvider" not in script


def test_the_profile_redirect_is_registered_where_the_web_sends_users() -> None:
    """Profile -> Sign-in methods returns from Keycloak to <public base>/profile, which the
    web client must list as a valid redirect URI (Keycloak refuses any other)."""
    assert 'PROFILE_URL="${WEB_URL%/}/profile"' in _script()
    route = (
        WEB / "app" / "api" / "account" / "sign-in-methods" / "[id]" / "connect" / "route.ts"
    ).read_text(encoding="utf-8")
    assert "`${base}/profile`" in route


def test_the_web_service_is_given_the_provider_list() -> None:
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "KEYCLOAK_SOCIAL_IDPS: ${KEYCLOAK_SOCIAL_IDPS:-}" in compose
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^KEYCLOAK_SOCIAL_IDPS=$", env_example, re.M), (
        ".env.example must document it (empty)"
    )


def test_the_login_start_route_only_hints_listed_providers() -> None:
    """`?idp=` ends up in `kc_idp_hint`: it must pass the allowlist AND the deployment's list."""
    route = (WEB / "app" / "(auth)" / "login" / "start" / "route.ts").read_text(encoding="utf-8")
    assert "isSocialIdp(idp)" in route
    assert "SOCIAL_IDPS_ENABLED.includes(idp)" in route
