"""The Keycloak login theme (`infra/keycloak/themes/mate`) must stay wired up.

The theme is named in four places - the realm JSON, the compose mount, the
realm-hardening one-shot and the directory itself - and a realm whose theme does
not exist silently falls back to Keycloak's stock look (one log warning, no
error). Inside the theme, two more mistakes fail just as quietly:

* a class named in `theme.properties` / `template.ftl` that `mate.css` never
  defines leaves that element unstyled;
* a lone apostrophe in a message is swallowed by `MessageFormat` ("we'll" shows
  as "well").

Cheap static guards for all of it; the pages themselves are checked in a browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KEYCLOAK = REPO / "infra" / "keycloak"
REALM_JSON = KEYCLOAK / "realm-export" / "flows-funds-realm.json"
THEMES = KEYCLOAK / "themes"


def _theme_name() -> str:
    return json.loads(REALM_JSON.read_text(encoding="utf-8"))["loginTheme"]


def _login_dir() -> Path:
    return THEMES / _theme_name() / "login"


def _property_lines(path: Path) -> list[tuple[str, str]]:
    """`key=value` pairs of a .properties file, comments and blanks skipped."""
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs.append((key.strip(), value.strip()))
    return pairs


def test_realm_names_a_login_theme_that_exists() -> None:
    login = _login_dir()
    assert (login / "theme.properties").is_file(), f"no theme.properties under {login}"
    assert (login / "template.ftl").is_file(), f"no template.ftl under {login}"


def test_every_place_that_names_the_theme_agrees() -> None:
    name = _theme_name()
    hardening = (KEYCLOAK / "apply-realm-hardening.sh").read_text(encoding="utf-8")
    assert f"-s loginTheme={name}" in hardening, (
        "apply-realm-hardening.sh must pin the theme on the live realm - the realm JSON "
        "only imports into an empty database"
    )
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    mount = f"./infra/keycloak/themes/{name}:/opt/keycloak/themes/{name}"
    assert mount in compose, f"docker-compose.yml must mount the theme ({mount})"


def test_every_class_the_theme_hooks_up_is_styled() -> None:
    login = _login_dir()
    css = (login / "resources" / "css" / "mate.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"\.(mate-[a-z0-9-]+)", css))

    hooked: set[str] = set()
    for _, value in _property_lines(login / "theme.properties"):
        hooked.update(re.findall(r"\bmate-[a-z0-9-]+", value))
    template = (login / "template.ftl").read_text(encoding="utf-8")
    for attr in re.findall(r'class="([^"]*)"', template):
        hooked.update(re.findall(r"\bmate-[a-z0-9-]+", attr))

    unstyled = sorted(hooked - defined)
    assert not unstyled, f"classes with no rule in mate.css (typo, or a dead mapping): {unstyled}"


def test_messages_double_their_apostrophes() -> None:
    messages = _login_dir() / "messages" / "messages_en.properties"
    bad = [key for key, value in _property_lines(messages) if re.search(r"(?<!')'(?!')", value)]
    assert not bad, f"messages with a lone apostrophe (write ''): {bad}"


def test_meta_entries_are_name_content_pairs() -> None:
    """template.ftl splits `meta` on spaces, then each token on `==`, and indexes [1]."""
    meta = dict(_property_lines(_login_dir() / "theme.properties")).get("meta", "")
    assert meta, "theme.properties has no meta entry (the viewport tag)"
    bad = [token for token in meta.split(" ") if token.count("==") != 1]
    assert not bad, f"meta tokens that are not name==content: {bad}"
