"""The curated dataset license list.

``open`` marks licenses a sandboxed (reviewer) account may see a public dataset
under; everything else - the 4TU general terms included - is hidden from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class License:
    id: str
    spdx: str | None
    name: str
    url: str | None
    open: bool


LICENSES: tuple[License, ...] = (
    License(
        "cc0-1.0",
        "CC0-1.0",
        "CC0 1.0 Universal",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        True,
    ),
    License(
        "cc-by-4.0",
        "CC-BY-4.0",
        "Creative Commons Attribution 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        True,
    ),
    License(
        "cc-by-sa-4.0",
        "CC-BY-SA-4.0",
        "Creative Commons Attribution-ShareAlike 4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        True,
    ),
    License(
        "cc-by-nc-4.0",
        "CC-BY-NC-4.0",
        "Creative Commons Attribution-NonCommercial 4.0",
        "https://creativecommons.org/licenses/by-nc/4.0/",
        False,
    ),
    License(
        "cc-by-nc-sa-4.0",
        "CC-BY-NC-SA-4.0",
        "Creative Commons Attribution-NonCommercial-ShareAlike 4.0",
        "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        False,
    ),
    License(
        "odc-by-1.0",
        "ODC-By-1.0",
        "Open Data Commons Attribution License 1.0",
        "https://opendatacommons.org/licenses/by/1-0/",
        True,
    ),
    License(
        "odbl-1.0",
        "ODbL-1.0",
        "Open Data Commons Open Database License 1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
        True,
    ),
    License(
        "pddl-1.0",
        "PDDL-1.0",
        "Open Data Commons Public Domain Dedication and License 1.0",
        "https://opendatacommons.org/licenses/pddl/1-0/",
        True,
    ),
    License(
        "4tu-general-terms",
        None,
        "4TU.ResearchData General Terms of Use",
        "https://data.4tu.nl/info/about-your-data/policies/general-terms-of-use",
        False,
    ),
    License("other", None, "Other license", None, False),
    License("unknown", None, "Unknown license", None, False),
)

_BY_ID: dict[str, License] = {lic.id: lic for lic in LICENSES}

OPEN_LICENSE_IDS: frozenset[str] = frozenset(lic.id for lic in LICENSES if lic.open)


def get_license(license_id: str) -> License | None:
    return _BY_ID.get(license_id)


def license_dict(
    license_id: str, license_name: str | None, license_url: str | None
) -> dict[str, Any]:
    """``LicenseOut`` fields for a dataset's license. For ``other`` the name and
    url come from the dataset card."""
    lic = _BY_ID.get(license_id) or _BY_ID["unknown"]
    name, url = lic.name, lic.url
    if lic.id == "other":
        name = license_name or lic.name
        url = license_url
    return {"id": lic.id, "spdx": lic.spdx, "name": name, "url": url, "open": lic.open}
