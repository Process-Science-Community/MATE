"""The compulsory per-module "How to use" guide (``HOW_TO_USE.md``).

Every module ships one markdown file, ``<module folder>/HOW_TO_USE.md``, in one
fixed format. The platform renders it in the app (the topbar's "How to use"
panel) and in the published manual, so the format is deliberately a portable
subset of markdown that both renderers display identically::

    # How to use <manifest name>

    <Lead: one short paragraph - what question the module answers.>

    ## When to use it
    ## Before you start
    ## Settings
    ## Reading the results
    ## Tips and limits
    ## Method

``check_guide`` returns *every* problem in one pass (so an author fixes the file
once, not once per error); ``require_guide`` raises ``ModuleManifestError``
listing them. ``python -m mate.sdk.guide <module dir | modules root>...`` runs
the same check from a shell or CI and exits ``1`` when any guide is invalid.

Deliberately **not** re-exported from ``mate.sdk``: running a package submodule
with ``-m`` after the package ``__init__`` already imported it makes ``runpy``
warn on every CLI call.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

GUIDE_FILENAME = "HOW_TO_USE.md"
"""Fixed file name, next to ``manifest.yaml``. ``README.md`` stays free for developer notes."""

GUIDE_MAX_BYTES = 64 * 1024
GUIDE_LEAD_MAX_CHARS = 600

GUIDE_SECTIONS: tuple[str, ...] = (
    "When to use it",
    "Before you start",
    "Settings",
    "Reading the results",
    "Tips and limits",
    "Method",
)
"""The ``##`` sections, in order. Each is required and must not be empty."""

NO_SETTINGS_SENTENCE = "This module has no settings."
SETTINGS_TABLE_HEADER: tuple[str, ...] = ("setting", "default", "what it changes")
"""Compared case-insensitively against the header row of every table in ``## Settings``."""

# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*$")
_INLINE_CODE_RE = re.compile(r"(`+).+?\1")
_IMAGE_OR_LINK_RE = re.compile(r"(!?)\[[^\]]*\]\(\s*<?([^)\s>]*)")
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S")
_REF_USE_RE = re.compile(r"\[[A-Za-z][^\]]*\]\[[^\]]*\]")
_FOOTNOTE_RE = re.compile(r"\[\^[^\]]+\]")
_HTML_RE = re.compile(r"</?[A-Za-z]|<!")
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
# `_x_` / `__x__`: markdown renders them as emphasis, the manual's builder does not.
# Underscores inside a word (`case_id`) never match.
_UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<!\w)(_{1,2})(?=[^\s_])[^_]+?(?<=[^\s_])\1(?!\w)")
_LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
_TASK_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\](?:\s|$)")
_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_LEAD_BLOCK_START_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|\||>)")
_ALLOWED_LINK_RE = re.compile(r"^(?:https?://|mailto:)\S", re.IGNORECASE)


@dataclass(slots=True)
class _Line:
    no: int  # 1-based
    text: str
    in_fence: bool  # on a fence delimiter or inside a fenced block


def _classify(text: str) -> tuple[list[_Line], list[str]]:
    """Split *text* into lines and mark which ones belong to fenced code.

    Returns ``(lines, problems)``; the only problems found here are ``~~~``
    fences and a fence that is never closed.
    """
    problems: list[str] = []
    lines: list[_Line] = []
    open_marker: str | None = None
    open_line = 0
    for no, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        match = _FENCE_RE.match(raw)
        if open_marker is None:
            if match:
                marker = match.group(1)
                open_marker, open_line = marker, no
                if marker.startswith("~"):
                    problems.append(f"line {no}: fence code with ``` (not ~~~).")
                lines.append(_Line(no, raw, True))
            else:
                lines.append(_Line(no, raw, False))
            continue
        lines.append(_Line(no, raw, True))
        if (
            match
            and match.group(1)[0] == open_marker[0]
            and len(match.group(1)) >= len(open_marker)
        ):
            open_marker = None
    if open_marker is not None:
        problems.append(f"line {open_line}: code fence is never closed.")
    return lines, problems


def _prose(line: _Line) -> str:
    """The line with inline code spans removed, so ``<module_id>`` in backticks is fine."""
    return _INLINE_CODE_RE.sub("", line.text)


def _is_blank(text: str) -> bool:
    return not text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_guide(folder: Path, name: str) -> list[str]:
    """Return every way ``<folder>/HOW_TO_USE.md`` breaks the format (empty = valid).

    *name* is the manifest ``name``; the title must read ``# How to use <name>``,
    which also catches a guide copied from another module and never edited.
    """
    path = folder / GUIDE_FILENAME
    if not path.is_file():
        return [f"{GUIDE_FILENAME} is missing."]

    raw = path.read_bytes()
    problems: list[str] = []
    if len(raw) > GUIDE_MAX_BYTES:
        problems.append(f"file is {len(raw)} bytes; the limit is {GUIDE_MAX_BYTES} (64 KiB).")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [*problems, "file is not valid UTF-8."]
    if text.startswith("﻿"):
        problems.append("remove the UTF-8 byte-order mark at the start of the file.")
        text = text.lstrip("﻿")

    lines, fence_problems = _classify(text)
    problems.extend(fence_problems)

    headings: list[tuple[_Line, int, str]] = []
    for line in lines:
        if line.in_fence:
            continue
        m = _HEADING_RE.match(line.text)
        if m:
            headings.append((line, len(m.group(1)), m.group(2)))

    problems.extend(_check_structure(lines, headings, name))
    problems.extend(_check_settings(lines, headings))
    for line in lines:
        if not line.in_fence:
            problems.extend(_check_line(line))
    return problems


def require_guide(folder: Path, name: str) -> None:
    """Raise :class:`ModuleManifestError` listing every problem with the guide."""
    problems = check_guide(folder, name)
    if problems:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ModuleManifestError(
            f"{GUIDE_FILENAME} is required and must follow the module guide format "
            f"(see 'The how-to-use guide' in modules/README.md):\n{bullets}"
        )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_structure(
    lines: list[_Line], headings: list[tuple[_Line, int, str]], name: str
) -> list[str]:
    problems: list[str] = []

    first = next((ln for ln in lines if not ln.in_fence and not _is_blank(ln.text)), None)
    if first is None:
        return [*problems, "file is empty."]

    expected_title = f"How to use {name}"
    h1s = [(ln, title) for ln, level, title in headings if level == 1]
    first_heading = _HEADING_RE.match(first.text)
    if first_heading is None or len(first_heading.group(1)) != 1:
        problems.append(f"line {first.no}: the first line must be `# {expected_title}`.")
    elif first_heading.group(2) != expected_title:
        problems.append(
            f"line {first.no}: the title must read `# {expected_title}` "
            f"(found `# {first_heading.group(2)}`)."
        )
    for ln, _ in h1s:
        if ln.no != first.no:
            problems.append(f"line {ln.no}: only one `#` title is allowed - use `##` for sections.")

    for ln, level, _ in headings:
        if level > 4:
            problems.append(f"line {ln.no}: headings may go at most four levels deep (`####`).")

    sections = [(ln, title) for ln, level, title in headings if level == 2]
    titles = [title for _, title in sections]
    expected = list(GUIDE_SECTIONS)
    if titles != expected:
        missing = [s for s in expected if s not in titles]
        unexpected = [t for t in titles if t not in expected]
        duplicates = sorted({t for t in titles if titles.count(t) > 1})
        if missing:
            problems.append("missing section(s): " + ", ".join(f"`## {s}`" for s in missing) + ".")
        if unexpected:
            problems.append(
                "unexpected `##` section(s): " + ", ".join(f"`{t}`" for t in unexpected) + "."
            )
        if duplicates:
            problems.append(
                "duplicate section(s): " + ", ".join(f"`{t}`" for t in duplicates) + "."
            )
        if not (missing or unexpected or duplicates):
            problems.append("sections are out of order; expected " + " > ".join(expected) + ".")

    problems.extend(_check_lead(lines, headings, first))

    for index, (ln, title) in enumerate(sections):
        end = sections[index + 1][0].no if index + 1 < len(sections) else len(lines) + 1
        body = [b for b in lines if ln.no < b.no < end]
        if title in expected and not any(_has_content(b) for b in body):
            problems.append(f"line {ln.no}: section `{title}` is empty.")
    return problems


def _has_content(line: _Line) -> bool:
    """Non-blank and not a heading (fence lines and fenced text count as content)."""
    if _is_blank(line.text):
        return False
    return line.in_fence or _HEADING_RE.match(line.text) is None


def _check_lead(
    lines: list[_Line], headings: list[tuple[_Line, int, str]], first: _Line
) -> list[str]:
    """The lead is the single plain paragraph between the title and the first ``##``."""
    first_section = next((ln.no for ln, level, _ in headings if level == 2), len(lines) + 1)
    region = [ln for ln in lines if first.no < ln.no < first_section]

    problems: list[str] = []
    for ln, level, _ in headings:
        if first.no < ln.no < first_section and level != 1:
            problems.append(f"line {ln.no}: no heading may come before the first `##` section.")
    if any(ln.in_fence for ln in region):
        problems.append("the lead must be plain prose - no code block before the first `##`.")

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for ln in region:
        if ln.in_fence or _HEADING_RE.match(ln.text):
            continue
        if _is_blank(ln.text):
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(ln.text.strip())
    if current:
        paragraphs.append(current)

    if not paragraphs:
        problems.append("missing the lead paragraph between the title and the first `##` section.")
        return problems
    if len(paragraphs) > 1:
        problems.append("the lead must be a single paragraph (1-3 sentences).")
    if _LEAD_BLOCK_START_RE.match(paragraphs[0][0]):
        problems.append("the lead must be a plain paragraph, not a list, table or quote.")
    length = len(" ".join(paragraphs[0]))
    if length > GUIDE_LEAD_MAX_CHARS:
        problems.append(
            f"the lead is {length} characters; keep it to 1-3 sentences "
            f"(at most {GUIDE_LEAD_MAX_CHARS})."
        )
    return problems


def _table_header_cells(line: str) -> list[str]:
    return [cell.strip().lower() for cell in line.strip().strip("|").split("|")]


def _check_settings(lines: list[_Line], headings: list[tuple[_Line, int, str]]) -> list[str]:
    """``## Settings`` is a ``Setting | Default | What it changes`` table or one sentence."""
    sections = [(ln, title) for ln, level, title in headings if level == 2]
    for index, (ln, title) in enumerate(sections):
        if title != "Settings":
            continue
        end = sections[index + 1][0].no if index + 1 < len(sections) else len(lines) + 1
        body = [b for b in lines if ln.no < b.no < end]
        problems: list[str] = []
        tables = 0
        for pos, b in enumerate(body):
            if b.in_fence or pos == 0 or not _TABLE_SEP_RE.match(b.text) or "|" not in b.text:
                continue
            header = body[pos - 1]
            if header.in_fence or "|" not in header.text:
                continue
            tables += 1
            if tuple(_table_header_cells(header.text)) != SETTINGS_TABLE_HEADER:
                problems.append(
                    f"line {header.no}: a settings table needs the columns "
                    "`Setting | Default | What it changes`."
                )
        says_none = any(NO_SETTINGS_SENTENCE in b.text for b in body if not b.in_fence)
        if tables == 0 and not says_none:
            problems.append(
                f"line {ln.no}: `Settings` needs a `Setting | Default | What it changes` table, "
                f'or the sentence "{NO_SETTINGS_SENTENCE}"'
            )
        return problems
    return []


def _check_line(line: _Line) -> list[str]:
    """Per-line portability rules for text outside code fences."""
    problems: list[str] = []
    prose = _prose(line)

    if _RULE_RE.match(line.text):
        problems.append(f"line {line.no}: horizontal rules are not supported - remove it.")
    if _REF_DEF_RE.match(line.text) or _REF_USE_RE.search(prose):
        problems.append(
            f"line {line.no}: use inline links `[text](https://...)`, not reference links."
        )
    if _FOOTNOTE_RE.search(prose):
        problems.append(f"line {line.no}: footnotes are not supported.")
    if "~~" in prose:
        problems.append(f"line {line.no}: strikethrough is not supported.")
    if _UNDERSCORE_EMPHASIS_RE.search(_LINK_TARGET_RE.sub("]", prose)):
        problems.append(
            f"line {line.no}: emphasis with underscores is not supported - use *asterisks* "
            "(and put identifiers such as `case_id` in backticks)."
        )

    list_match = _LIST_ITEM_RE.match(line.text)
    if list_match and list_match.group(1):
        problems.append(
            f"line {line.no}: nested or indented list items are not supported - "
            "keep lists flat, with the marker in the first column."
        )
    if _TASK_ITEM_RE.match(line.text):
        problems.append(f"line {line.no}: task-list checkboxes are not supported.")

    for m in _IMAGE_OR_LINK_RE.finditer(prose):
        if m.group(1):
            problems.append(f"line {line.no}: images are not supported.")
        elif not _ALLOWED_LINK_RE.match(m.group(2)):
            problems.append(
                f"line {line.no}: link target `{m.group(2)}` must be an absolute "
                "`https://`, `http://` or `mailto:` URL."
            )

    for m in _HTML_RE.finditer(prose):
        close = prose.find(">", m.start())
        token = prose[m.start() : close + 1] if close != -1 else m.group(0)
        shown = token if len(token) <= 40 else token[:37] + "..."
        problems.append(
            f"line {line.no}: raw HTML or angle brackets ({shown}) are not supported - "
            "put the text in backticks (the app shows HTML as literal text)."
        )
    return problems


# ---------------------------------------------------------------------------
# CLI: python -m mate.sdk.guide <module dir | modules root>...
# ---------------------------------------------------------------------------


def module_dirs(paths: Sequence[str]) -> Iterator[Path]:
    """A path holding ``manifest.yaml`` is one module; otherwise its child folders are.

    Shared by the module checkers' CLIs (``mate.sdk.guide``, ``mate.sdk.logcheck``).
    """
    for raw in paths:
        path = Path(raw)
        if (path / "manifest.yaml").is_file():
            yield path
            continue
        if not path.is_dir():
            print(f"error: {path} is not a directory", file=sys.stderr)
            continue
        for child in sorted(path.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith((".", "_"))
                and (child / "manifest.yaml").is_file()
            ):
                yield child


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m mate.sdk.guide <module folder | modules root> [...]\n\n"
            f"Checks each module's {GUIDE_FILENAME} against the module guide format.\n"
            "Exit status: 0 all valid, 1 at least one problem, 2 nothing to check."
        )
        return 0 if args else 2

    checked = 0
    failed = 0
    for folder in module_dirs(args):
        checked += 1
        try:
            manifest = Manifest.load_yaml(folder / "manifest.yaml")
        except ModuleManifestError as exc:
            failed += 1
            print(f"FAIL {folder.name}\n  - manifest.yaml is invalid: {exc}")
            continue
        problems = check_guide(folder, manifest.name)
        if problems:
            failed += 1
            print(f"FAIL {folder.name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {folder.name}")

    if checked == 0:
        print("error: no module folders found", file=sys.stderr)
        return 2
    print(f"\n{checked - failed}/{checked} guides valid.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
