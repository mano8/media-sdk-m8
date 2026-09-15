"""
Executable checks on the harness itself — no docker, no backend.

``test_contract_spec.py`` keeps the contract honest; this module keeps the
*harness* honest against it. It answers three questions that would otherwise
only be answered by someone remembering to look:

* does every row the contract assigns to this step actually exist as a test?
* is the suite really excluded from the ordinary run and its coverage gate?
* is every image the harness boots pinned to an exact tag (S14)?
"""

import ast
import tomllib
from pathlib import Path
from typing import Final

from .backends import BOOTSTRAP_IMAGES, DRIVERS, PENDING_DRIVERS
from .contract import (
    CANDIDATE_BACKENDS,
    INVARIANT_CASES,
    SUITE_CONFORMANCE,
    SURFACE_CASES,
    conformance_test_ids,
)

#: This plan step. Rows naming it must be implemented here and now.
THIS_STEP: Final = "T1-conformance-harness"

#: Mark that keeps the docker-backed cases out of the ordinary run.
MARK: Final = "conformance"

HERE = Path(__file__).parent
PYPROJECT = HERE.parents[1] / "pyproject.toml"

#: Modules holding the docker-backed cases.
HARNESS_MODULES: Final = ("test_s3_surface.py", "test_security_invariants.py")


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _pytest_config() -> dict:
    return _pyproject()["tool"]["pytest"]["ini_options"]


def _defined_tests(module: str) -> set[str]:
    """Return the test function names defined in a harness module."""
    tree = ast.parse((HERE / module).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _harness_tests() -> set[str]:
    return set().union(*(_defined_tests(module) for module in HARNESS_MODULES))


def _expected_tests() -> set[str]:
    """Return the conformance rows this step is contractually required to deliver."""
    surface = {case.test_id for case in SURFACE_CASES}
    invariants = {
        case.test_id
        for case in INVARIANT_CASES
        if case.suite == SUITE_CONFORMANCE and case.delivered_by == THIS_STEP
    }
    return surface | invariants


# -- the rows this step owes -------------------------------------------------


def test_every_assigned_row_is_implemented() -> None:
    """No row assigned to this step may survive as prose."""
    missing = sorted(_expected_tests() - _harness_tests())
    assert not missing, f"{THIS_STEP} does not implement: {missing}"


def test_harness_implements_only_contract_rows() -> None:
    """A harness test that no contract row names cannot appear in the matrix."""
    stray = sorted(_harness_tests() - set(conformance_test_ids()))
    assert not stray, f"harness tests with no contract row: {stray}"


def test_rows_left_to_later_steps_are_named() -> None:
    """A live row this step does not deliver must name the step that will."""
    outstanding = {
        case.case_id: case.delivered_by
        for case in INVARIANT_CASES
        if case.suite == SUITE_CONFORMANCE and case.test_id not in _harness_tests()
    }
    assert all(step != THIS_STEP for step in outstanding.values()), (
        f"rows claimed by {THIS_STEP} but not implemented: {outstanding}"
    )


def test_harness_modules_carry_the_mark() -> None:
    """Every harness module marks its cases, or the default run would boot docker."""
    for module in HARNESS_MODULES:
        source = (HERE / module).read_text(encoding="utf-8")
        assert f"pytestmark = pytest.mark.{MARK}" in source, (
            f"{module} does not apply the {MARK} mark"
        )


# -- excluded from the ordinary run and its coverage gate --------------------


def test_mark_is_registered() -> None:
    markers = _pytest_config().get("markers", [])
    assert any(marker.startswith(f"{MARK}:") for marker in markers), (
        f"the {MARK} mark is not registered in pyproject.toml"
    )


def test_mark_is_deselected_by_default() -> None:
    """The default run must not need a container engine — nor count this suite."""
    addopts = _pytest_config()["addopts"]
    assert f"not {MARK}" in addopts, (
        "pyproject.toml addopts does not deselect the conformance suite, so "
        "the ordinary run and its coverage gate would try to boot a backend"
    )


# -- measurement setup -------------------------------------------------------


def test_every_image_the_harness_runs_is_pinned() -> None:
    """S14 applies to the harness too: an unpinned image is not a measurement."""
    for name, image in {**CANDIDATE_BACKENDS, **BOOTSTRAP_IMAGES}.items():
        assert ":" in image, f"{name} is not pinned to a tag"
        assert not image.endswith(":latest"), f"{name} is pinned to :latest"


def test_bootstrap_image_matches_the_shipped_one_shot() -> None:
    """Bootstrapping with a different mc than production would prove nothing."""
    assert BOOTSTRAP_IMAGES["minio"].startswith("quay.io/minio/mc:RELEASE.")


def test_every_candidate_backend_is_routed() -> None:
    """Each candidate either has a driver or names the step that adds one."""
    routed = set(DRIVERS) | set(PENDING_DRIVERS)
    assert routed == set(CANDIDATE_BACKENDS)
    assert not set(DRIVERS) & set(PENDING_DRIVERS)


def test_baseline_backend_has_a_driver() -> None:
    """The MinIO baseline is what this step must be able to run against."""
    assert "minio" in DRIVERS
