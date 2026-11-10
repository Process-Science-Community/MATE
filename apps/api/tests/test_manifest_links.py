"""Optional `links` in a module manifest.

A manifest may list up to 20 `links` - named links to whatever else belongs to
the module (the reference implementation's repo, a dataset, a demo, a released
model). A link is `{name, url}` with both required and carries no citation
(that's `source`, see `test_manifest_source.py`); the field is absent from most
manifests, so the default must stay empty. The pre-rename key `artifacts:` is
still read as a deprecated alias.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mate.sdk.manifest import MAX_LINKS, Manifest

_MODULES_DIR = Path(__file__).resolve().parents[3] / "modules"


def _base(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "category": "advanced",
    }
    data.update(overrides)
    return data


# ── optional + shape ────────────────────────────────────────────────────────


def test_links_default_to_an_empty_list() -> None:
    # Every pre-existing manifest omits the field; it must not become required.
    assert Manifest.model_validate(_base()).links == []


def test_links_are_parsed_in_declaration_order() -> None:
    m = Manifest.model_validate(
        _base(
            links=[
                {"name": "Reference implementation", "url": "https://github.com/x/y"},
                {"name": "Benchmark dataset", "url": "https://doi.org/10.5281/zenodo.1"},
            ]
        )
    )
    assert [(a.name, a.url) for a in m.links] == [
        ("Reference implementation", "https://github.com/x/y"),
        ("Benchmark dataset", "https://doi.org/10.5281/zenodo.1"),
    ]


def test_link_requires_a_name() -> None:
    # The name is the label the UI renders - a bare url has nothing to show.
    with pytest.raises(ValidationError):
        Manifest.model_validate(_base(links=[{"url": "https://github.com/x/y"}]))


def test_link_requires_a_url() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(_base(links=[{"name": "No link"}]))


def test_cited_sources_do_not_leak_into_links() -> None:
    m = Manifest.model_validate(
        _base(source=[{"title": "A paper", "fullCitation": "N. N., 2025."}])
    )
    assert m.links == []


# ── deprecated `artifacts:` key ─────────────────────────────────────────────


def test_legacy_artifacts_key_still_loads_into_links(tmp_path: Path) -> None:
    # Uploaded modules written before the rename keep their links.
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "id: demo\nname: Demo\nversion: 1.0.0\ncategory: advanced\n"
        "artifacts:\n  - name: Repo\n    url: https://github.com/x/y\n",
        encoding="utf-8",
    )
    m = Manifest.load_yaml(path)
    assert [(a.name, a.url) for a in m.links] == [("Repo", "https://github.com/x/y")]
    # Serialised under the new name only - the API never echoes the old key.
    dumped = m.model_dump(by_alias=True)
    assert "links" in dumped and "artifacts" not in dumped


def test_legacy_artifacts_key_keeps_the_cap() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            _base(artifacts=[{"name": f"A{i}", "url": f"https://a/{i}"} for i in range(21)])
        )


# ── max-20 cap ──────────────────────────────────────────────────────────────


def test_exactly_20_links_is_allowed() -> None:
    m = Manifest.model_validate(
        _base(links=[{"name": f"A{i}", "url": f"https://a/{i}"} for i in range(MAX_LINKS)])
    )
    assert len(m.links) == MAX_LINKS == 20


def test_more_than_20_links_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            _base(links=[{"name": f"A{i}", "url": f"https://a/{i}"} for i in range(21)])
        )


# ── bundled manifest ────────────────────────────────────────────────────────


def test_bundled_pcomp_manifest_declares_its_links() -> None:
    m = Manifest.load_yaml(_MODULES_DIR / "pcomp" / "manifest.yaml")
    assert [a.name for a in m.links] == ["pcomp reference implementation", "pm4py"]
    assert m.links[0].url == "https://github.com/cpitsch/pcomp"
