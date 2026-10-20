"""The compulsory per-module ``HOW_TO_USE.md`` guide.

Two halves. The validator (``mate.sdk.guide``): one case per rule, each starting
from a known-valid guide and breaking exactly one thing, so a regression names
the rule that moved. And the platform's policy around it: loading is lenient (a
module already on disk keeps working), uploading is not, and the guide route
serves the file safely.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from structlog.testing import capture_logs

from mate.api.modules.discovery import discover
from mate.api.modules.guides import guide_path, has_guide, read_guide, warn_on_guide_problems
from mate.api.modules.hot_reload import _is_ignored  # pyright: ignore[reportPrivateUsage]
from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import (
    GUIDE_FILENAME,
    GUIDE_LEAD_MAX_CHARS,
    GUIDE_MAX_BYTES,
    GUIDE_SECTIONS,
    check_guide,
    main,
    require_guide,
)
from mate.sdk.manifest import Manifest
from tests.guide_text import minimal_guide
from tests.test_modules_per_user import (  # pyright: ignore[reportPrivateUsage]
    _reset_user_modules,
    _upload,
)

NAME = "Sample Module"


def _write(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / GUIDE_FILENAME).write_text(text, encoding="utf-8")
    return folder


def _problems(tmp_path: Path, text: str, name: str = NAME) -> list[str]:
    return check_guide(_write(tmp_path / "mod", text), name)


def _joined(problems: list[str]) -> str:
    return "\n".join(problems)


def _replace(text: str, old: str, new: str) -> str:
    assert old in text, f"fixture drifted: {old!r} not in the minimal guide"
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# The valid shape
# ---------------------------------------------------------------------------


def test_minimal_guide_is_valid(tmp_path: Path) -> None:
    assert _problems(tmp_path, minimal_guide(NAME)) == []


def test_section_order_constant_matches_the_documented_format() -> None:
    assert GUIDE_SECTIONS == (
        "When to use it",
        "Before you start",
        "Settings",
        "Reading the results",
        "Tips and limits",
        "Method",
    )


def test_rich_markdown_the_renderers_share_is_accepted(tmp_path: Path) -> None:
    text = minimal_guide(NAME)
    text = _replace(
        text,
        "- It is a fixture.\n",
        "- It is a **fixture**, with `inline <code>` and a "
        "[link](https://example.org/a_(b)) plus **Esc** and mail "
        "[me](mailto:a@b.co).\n"
        "- A second item.\n"
        "\n"
        "> [!TIP]\n"
        "> Callouts are fine.\n"
        "\n"
        '```bash title="Terminal"\n'
        "# a comment that is not a heading\n"
        "- not a list <b>not html</b> ![not an image](x.png)\n"
        "```\n",
    )
    text = _replace(
        text,
        "There is nothing to read.\n",
        "### A view\n\nExplained.\n\n#### A detail\n\nMore.\n",
    )
    assert _problems(tmp_path, text) == []


def test_settings_table_with_subsections_is_accepted(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "This module has no settings.\n",
        "### In the panel\n\n"
        "| Setting | Default | What it changes |\n"
        "| --- | --- | --- |\n"
        "| Granularity | Day | The bucket size. |\n\n"
        "### On the module page\n\n"
        "| setting | DEFAULT | What it changes |\n"
        "| :--- | ---: | --- |\n"
        "| `k` | 1 | A knob. |\n",
    )
    assert _problems(tmp_path, text) == []


# ---------------------------------------------------------------------------
# File-level problems
# ---------------------------------------------------------------------------


def test_missing_file(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    assert check_guide(folder, NAME) == [f"{GUIDE_FILENAME} is missing."]


def test_oversized_file(tmp_path: Path) -> None:
    padding = "x" * (GUIDE_MAX_BYTES + 1)
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", f"- {padding}\n")
    assert "the limit is" in _joined(_problems(tmp_path, text))


def test_invalid_utf8(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / GUIDE_FILENAME).write_bytes(b"# How to use Sample Module\n\n\xff\xfe\n")
    assert _joined(check_guide(folder, NAME)).endswith("file is not valid UTF-8.")


def test_byte_order_mark_is_called_out(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / GUIDE_FILENAME).write_bytes(b"\xef\xbb\xbf" + minimal_guide(NAME).encode())
    assert _joined(check_guide(folder, NAME)) == (
        "remove the UTF-8 byte-order mark at the start of the file."
    )


def test_crlf_line_endings_are_fine(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / GUIDE_FILENAME).write_bytes(minimal_guide(NAME).replace("\n", "\r\n").encode())
    assert check_guide(folder, NAME) == []


def test_empty_file(tmp_path: Path) -> None:
    assert _problems(tmp_path, "\n\n") == ["file is empty."]


# ---------------------------------------------------------------------------
# Title and lead
# ---------------------------------------------------------------------------


def test_title_must_name_the_module(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), f"# How to use {NAME}", "# How to use Other Module")
    assert "must read `# How to use Sample Module`" in _joined(_problems(tmp_path, text))


def test_title_must_be_the_first_line(tmp_path: Path) -> None:
    text = "<!-- group: Hijack -->\n" + minimal_guide(NAME)
    assert "the first line must be `# How to use Sample Module`" in _joined(
        _problems(tmp_path, text)
    )


def test_front_matter_is_rejected(tmp_path: Path) -> None:
    text = "---\ntitle: x\n---\n" + minimal_guide(NAME)
    assert "the first line must be" in _joined(_problems(tmp_path, text))


def test_second_h1_is_rejected(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "## Method", "# Another title\n\n## Method")
    assert "only one `#` title is allowed" in _joined(_problems(tmp_path, text))


def test_missing_lead(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "A test module that answers one question, so its guide stays short.\n\n",
        "",
    )
    assert "missing the lead paragraph" in _joined(_problems(tmp_path, text))


def test_lead_must_be_one_paragraph(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "stays short.\n", "stays short.\n\nAnd a second one.\n")
    assert "single paragraph" in _joined(_problems(tmp_path, text))


def test_lead_must_not_be_a_list(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "A test module that answers one question, so its guide stays short.",
        "- a list instead of a paragraph",
    )
    assert "plain paragraph, not a list" in _joined(_problems(tmp_path, text))


def test_lead_length_cap(tmp_path: Path) -> None:
    long_lead = "word " * (GUIDE_LEAD_MAX_CHARS // 5 + 5)
    text = _replace(
        minimal_guide(NAME),
        "A test module that answers one question, so its guide stays short.",
        long_lead.strip(),
    )
    assert "keep it to 1-3 sentences" in _joined(_problems(tmp_path, text))


def test_heading_before_first_section_is_rejected(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "## When to use it", "### Stray\n\n## When to use it")
    assert "no heading may come before the first `##` section" in _joined(_problems(tmp_path, text))


def test_code_block_in_lead_is_rejected(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "## When to use it", "```\ncode\n```\n\n## When to use it")
    assert "no code block before the first `##`" in _joined(_problems(tmp_path, text))


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def test_missing_section(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "## Tips and limits\n\n- It is a fixture.\n\n", "")
    assert "missing section(s): `## Tips and limits`" in _joined(_problems(tmp_path, text))


def test_unexpected_section(tmp_path: Path) -> None:
    text = minimal_guide(NAME) + "\n## Quick start\n\n1. Do it.\n"
    assert "unexpected `##` section(s): `Quick start`" in _joined(_problems(tmp_path, text))


def test_duplicate_section(tmp_path: Path) -> None:
    text = minimal_guide(NAME) + "\n## Method\n\nAgain.\n"
    assert "duplicate section(s): `Method`" in _joined(_problems(tmp_path, text))


def test_sections_out_of_order(tmp_path: Path) -> None:
    text = minimal_guide(NAME)
    swapped = _replace(text, "## Method", "## @@@")
    swapped = _replace(swapped, "## When to use it", "## Method")
    swapped = _replace(swapped, "## @@@", "## When to use it")
    assert "sections are out of order" in _joined(_problems(tmp_path, swapped))


def test_section_names_are_exact(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "## Settings", "## Settings and options")
    joined = _joined(_problems(tmp_path, text))
    assert "missing section(s): `## Settings`" in joined
    assert "unexpected `##` section(s): `Settings and options`" in joined


def test_empty_section(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "")
    assert "section `Tips and limits` is empty" in _joined(_problems(tmp_path, text))


def test_section_holding_only_a_subheading_is_empty(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "### Just a heading\n")
    assert "section `Tips and limits` is empty" in _joined(_problems(tmp_path, text))


def test_section_holding_only_a_code_block_has_content(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "```text\nx\n```\n")
    assert _problems(tmp_path, text) == []


def test_heading_depth_limit(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "##### Too deep\n\nText.\n")
    assert "at most four levels deep" in _joined(_problems(tmp_path, text))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_needs_a_table_or_the_sentence(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "This module has no settings.", "Tweak things.")
    assert "`Settings` needs a `Setting | Default | What it changes` table" in _joined(
        _problems(tmp_path, text)
    )


def test_settings_table_columns_are_fixed(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "This module has no settings.\n",
        "| Name | Value |\n| --- | --- |\n| a | b |\n",
    )
    assert "needs the columns `Setting | Default | What it changes`" in _joined(
        _problems(tmp_path, text)
    )


def test_other_sections_may_use_any_table(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "There is nothing to read.\n",
        "| Class | Meaning |\n| --- | --- |\n| A | b |\n",
    )
    assert _problems(tmp_path, text) == []


# ---------------------------------------------------------------------------
# Portability rules (one per rule)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("![alt](https://example.org/x.png)", "images are not supported"),
        ("[relative](./other.md)", "link target `./other.md` must be an absolute"),
        ("[anchor](#settings)", "link target `#settings` must be an absolute"),
        ("[ftp](ftp://example.org/x)", "link target `ftp://example.org/x` must be an absolute"),
        ("<div>raw</div>", "raw HTML or angle brackets (<div>)"),
        ("<!-- a comment -->", "raw HTML or angle brackets (<!-- a comment -->)"),
        ("<https://example.org>", "raw HTML or angle brackets (<https://example.org>)"),
        ("press <kbd>Esc</kbd>", "raw HTML or angle brackets (<kbd>)"),
        ('<kbd class="x">Esc</kbd>', 'raw HTML or angle brackets (<kbd class="x">)'),
        ("a <module_id> placeholder", "raw HTML or angle brackets (<module_id>)"),
        ("see [ref][1] for more", "use inline links"),
        ("[1]: https://example.org", "use inline links"),
        ("a footnote[^1] here", "footnotes are not supported"),
        ("this is ~~gone~~", "strikethrough is not supported"),
        ("---", "horizontal rules are not supported"),
        ("***", "horizontal rules are not supported"),
        ("- [ ] a task", "task-list checkboxes are not supported"),
        ("- [x] a done task", "task-list checkboxes are not supported"),
    ],
)
def test_unsupported_syntax_is_rejected(tmp_path: Path, snippet: str, expected: str) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", f"{snippet}\n")
    assert expected in _joined(_problems(tmp_path, text))


def test_nested_lists_are_rejected(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME), "- It is a fixture.\n", "- parent\n  - child\n1. one\n   1. nested\n"
    )
    joined = _joined(_problems(tmp_path, text))
    assert joined.count("nested or indented list items are not supported") == 2


def test_backticked_text_is_never_flagged(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "- It is a fixture.\n",
        "- `<module_id>` and `![x](y)` and `[r](./z)` and ``a ` b`` are literal.\n",
    )
    assert _problems(tmp_path, text) == []


def test_fenced_text_is_never_flagged(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "- It is a fixture.\n",
        "```yaml\n# ## not a heading\n- [ ] not a task\n  - nested\n---\n<b>x</b>\n```\n",
    )
    assert _problems(tmp_path, text) == []


def test_tilde_fences_are_rejected(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "~~~\ncode\n~~~\n")
    assert "fence code with ``` (not ~~~)" in _joined(_problems(tmp_path, text))


def test_unclosed_fence_is_reported_with_its_line(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "```text\nnever closed\n")
    assert "code fence is never closed" in _joined(_problems(tmp_path, text))


def test_a_fence_hides_headings_from_the_section_check(tmp_path: Path) -> None:
    text = _replace(
        minimal_guide(NAME),
        "- It is a fixture.\n",
        "```md\n## Quick start\n```\n",
    )
    assert _problems(tmp_path, text) == []


def test_all_problems_are_reported_in_one_pass(tmp_path: Path) -> None:
    text = _replace(minimal_guide(NAME), "- It is a fixture.\n", "![x](y)\n---\n")
    text = _replace(text, "## Method", "## Notes")
    problems = _problems(tmp_path, text)
    assert len(problems) >= 4, problems


# ---------------------------------------------------------------------------
# require_guide + CLI
# ---------------------------------------------------------------------------


def test_require_guide_lists_every_problem(tmp_path: Path) -> None:
    folder = _write(tmp_path / "mod", "# How to use Wrong\n\nLead.\n")
    with pytest.raises(ModuleManifestError) as err:
        require_guide(folder, NAME)
    message = str(err.value)
    assert GUIDE_FILENAME in message
    assert "must read `# How to use Sample Module`" in message
    assert "missing section(s)" in message


def test_require_guide_passes_a_valid_guide(tmp_path: Path) -> None:
    require_guide(_write(tmp_path / "mod", minimal_guide(NAME)), NAME)


def _module(root: Path, folder: str, *, name: str, guide: str | None) -> None:
    mod = root / folder
    mod.mkdir(parents=True)
    (mod / "manifest.yaml").write_text(
        f"id: {folder}\nname: {name}\nversion: 0.0.1\ncategory: foundation\n"
        "requirements:\n  event_log:\n    required_columns: [case_id, activity, timestamp]\n"
        "provides: []\nconsumes: []\n"
        "dependencies:\n  python:\n    inherit: []\n    isolation: in_process\n",
        encoding="utf-8",
    )
    if guide is not None:
        (mod / GUIDE_FILENAME).write_text(guide, encoding="utf-8")


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "modules"
    _module(root, "good", name="Good", guide=minimal_guide("Good"))
    (root / ".hidden").mkdir()
    (root / "_private").mkdir()
    (root / "plain_folder").mkdir()  # no manifest: not a module

    assert main([str(root)]) == 0
    out = capsys.readouterr().out
    assert "ok   good" in out and "1/1 guides valid." in out

    _module(root, "bad", name="Bad", guide=None)
    assert main([str(root)]) == 1
    out = capsys.readouterr().out
    assert "FAIL bad" in out and "HOW_TO_USE.md is missing." in out and "1/2 guides valid." in out

    # A single module folder works too, and a folder with nothing to check is a usage error.
    assert main([str(root / "good")]) == 0
    assert main([str(tmp_path / "empty")]) == 2
    assert main([]) == 2


def test_cli_reports_a_broken_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = tmp_path / "modules" / "broken"
    mod.mkdir(parents=True)
    (mod / "manifest.yaml").write_text("id: broken\n", encoding="utf-8")
    assert main([str(tmp_path / "modules")]) == 1
    assert "manifest.yaml is invalid" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Platform behaviour: loading is lenient, uploading is not
# ---------------------------------------------------------------------------


def test_guide_path_rejects_a_symlink_that_leaves_the_folder(tmp_path: Path) -> None:
    """The guide route must never become an arbitrary-file read."""
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / GUIDE_FILENAME).symlink_to(outside)
    assert guide_path(folder) is None
    assert read_guide(folder) is None
    assert not has_guide(folder)


def test_guide_path_allows_a_symlink_inside_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / "docs.md").write_text(minimal_guide(NAME), encoding="utf-8")
    (folder / GUIDE_FILENAME).symlink_to(folder / "docs.md")
    assert has_guide(folder)
    assert read_guide(folder) == minimal_guide(NAME)


def test_read_guide_is_none_without_a_file(tmp_path: Path) -> None:
    assert read_guide(tmp_path) is None
    assert not has_guide(tmp_path)


def test_read_guide_is_bounded_and_tolerates_bad_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "mod"
    folder.mkdir()
    (folder / GUIDE_FILENAME).write_bytes(b"ok \xff " + b"x" * (GUIDE_MAX_BYTES * 3))
    text = read_guide(folder)
    assert text is not None
    assert text.startswith("ok � ")
    assert len(text) <= GUIDE_MAX_BYTES * 2


def test_a_missing_guide_only_warns_at_discovery(tmp_path: Path) -> None:
    """Modules already on disk (uploads that predate the rule) must keep loading."""
    root = tmp_path / "modules"
    _module(root, "legacy", name="Legacy", guide=None)
    _module(root, "guided", name="Guided", guide=minimal_guide("Guided"))
    with capture_logs() as logs:
        found = discover(root)
    assert sorted(d.id for d in found) == ["guided", "legacy"]
    warned = [e for e in logs if e["event"] == "modules.guide.invalid"]
    assert [e["module_id"] for e in warned] == ["legacy"]
    assert warned[0]["log_level"] == "warning"
    assert "HOW_TO_USE.md is missing." in warned[0]["problems"]


def test_warn_on_guide_problems_never_raises(tmp_path: Path) -> None:
    _module(tmp_path, "broken_guide", name="Broken", guide="not a guide at all")
    broken = Manifest.load_yaml(tmp_path / "broken_guide" / "manifest.yaml")
    with capture_logs() as logs:
        warn_on_guide_problems(tmp_path / "broken_guide", broken)
    assert len(logs) == 1
    assert logs[0]["module_id"] == "broken_guide"

    _module(tmp_path, "fine", name="Fine", guide=minimal_guide("Fine"))
    fine = Manifest.load_yaml(tmp_path / "fine" / "manifest.yaml")
    with capture_logs() as logs:  # a valid guide logs nothing
        warn_on_guide_problems(tmp_path / "fine", fine)
    assert logs == []


def test_editing_a_guide_does_not_hot_reload_the_module() -> None:
    assert _is_ignored(Path("sample_mod") / GUIDE_FILENAME)
    assert _is_ignored(Path("sample_mod") / "docs" / GUIDE_FILENAME)
    assert not _is_ignored(Path("sample_mod") / "module.py")
    assert not _is_ignored(Path("sample_mod") / "README.md")


@pytest.mark.asyncio
async def test_upload_without_a_guide_is_rejected(client_with_sample_mod: AsyncClient) -> None:
    body = await _upload(client_with_sample_mod, "noguide_mod", with_guide=False)
    assert body["status"] == "failed", body
    message = (body.get("message") or "") + (body.get("error") or "")
    assert "HOW_TO_USE.md is required" in message
    assert "HOW_TO_USE.md is missing." in message

    listed = (await client_with_sample_mod.get("/api/v1/modules")).json()
    assert "noguide_mod" not in [m["id"] for m in listed]
    assert (
        await client_with_sample_mod.get("/api/v1/modules/noguide_mod/guide")
    ).status_code == 404


@pytest.mark.asyncio
async def test_upload_with_an_invalid_guide_names_every_problem(
    client_with_sample_mod: AsyncClient,
) -> None:
    body = await _upload(
        client_with_sample_mod, "badguide_mod", guide_text="# How to use Wrong\n\nLead.\n"
    )
    assert body["status"] == "failed", body
    message = (body.get("message") or "") + (body.get("error") or "")
    assert "must read `# How to use badguide_mod`" in message
    assert "missing section(s)" in message
    listed = (await client_with_sample_mod.get("/api/v1/modules")).json()
    assert "badguide_mod" not in [m["id"] for m in listed]


@pytest.mark.asyncio
async def test_uploaded_guide_is_listed_and_served(client_with_sample_mod: AsyncClient) -> None:
    body = await _upload(client_with_sample_mod, "guided_mod")
    assert body["status"] == "completed", body

    listed = (await client_with_sample_mod.get("/api/v1/modules")).json()
    assert next(m for m in listed if m["id"] == "guided_mod")["has_guide"] is True

    resp = await client_with_sample_mod.get("/api/v1/modules/guided_mod/guide")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"module_id": "guided_mod", "markdown": minimal_guide("guided_mod")}


@pytest.mark.asyncio
async def test_bundled_fixture_guide_is_served(client_with_sample_mod: AsyncClient) -> None:
    listed = (await client_with_sample_mod.get("/api/v1/modules")).json()
    assert next(m for m in listed if m["id"] == "sample_mod")["has_guide"] is True
    resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/guide")
    assert resp.status_code == 200
    assert resp.json()["markdown"].startswith("# How to use Sample Module\n")


@pytest.mark.asyncio
async def test_guide_is_read_from_disk_on_every_request(
    client_with_sample_mod: AsyncClient,
) -> None:
    from mate.api.config import get_settings

    guide = get_settings().modules_dir / "sample_mod" / GUIDE_FILENAME
    original = guide.read_text(encoding="utf-8")
    try:
        guide.write_text(original + "\nEdited.\n", encoding="utf-8")
        resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/guide")
        assert resp.json()["markdown"].endswith("Edited.\n")
    finally:
        guide.write_text(original, encoding="utf-8")


@pytest.mark.asyncio
async def test_a_module_without_a_guide_has_none(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    """`sample_cards` deliberately ships no guide: an installed module that predates
    the rule keeps working and simply has nothing to show."""
    client = admin_client_with_sample_cards
    listed = (await client.get("/api/v1/modules")).json()
    assert next(m for m in listed if m["id"] == "sample_cards")["has_guide"] is False
    resp = await client.get("/api/v1/modules/sample_cards/guide")
    assert resp.status_code == 404
    assert "has no how-to-use guide" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_guide_route_is_owner_only(client_with_sample_mod_fresh: AsyncClient) -> None:
    """Same rule as ``/manifest``: a module you haven't installed answers 404."""
    # The metadata DB is shared across the session, so earlier tests may have
    # granted this user the default modules - start from a clean slate.
    await _reset_user_modules()
    resp = await client_with_sample_mod_fresh.get("/api/v1/modules/sample_mod/guide")
    assert resp.status_code == 404
    assert "not installed" in resp.json()["detail"]

    # Once the lazy default seeding hands the module over, the same call succeeds.
    assert (await client_with_sample_mod_fresh.get("/api/v1/modules")).status_code == 200
    resp = await client_with_sample_mod_fresh.get("/api/v1/modules/sample_mod/guide")
    assert resp.status_code == 200
