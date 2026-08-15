"""Changelog/version parity — A32-changelog-version-parity.

`media_sdk_m8` exposes no package `__version__` (per the A33 version-source
map, this repo's authoritative version is the `pyproject.toml` `[project]`
literal). This locks the current version to a matching `CHANGELOG.md`
heading so a release cannot ship undocumented.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"

_HEADING_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)


def _current_version() -> str:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]  # type: ignore[no-any-return]


def test_changelog_exists() -> None:
    assert CHANGELOG.exists(), "CHANGELOG.md must exist at the repo root."


def test_current_version_has_a_changelog_entry() -> None:
    """The version in pyproject.toml's `[project] version` must head a CHANGELOG entry."""
    version = _current_version()
    headings = _HEADING_RE.findall(CHANGELOG.read_text(encoding="utf-8"))
    assert version in headings, (
        f"CHANGELOG.md has no '## [{version}]' heading for the current "
        f"version (pyproject.toml [project].version = {version!r}); every "
        "published version must be documented."
    )


def test_changelog_headings_are_unique() -> None:
    """No two entries may claim the same version (the imgtools_m8 A32 finding)."""
    headings = _HEADING_RE.findall(CHANGELOG.read_text(encoding="utf-8"))
    duplicates = {v for v in headings if headings.count(v) > 1}
    assert not duplicates, (
        f"CHANGELOG.md has duplicate '## [x.y.z]' headings for: {sorted(duplicates)}"
    )
