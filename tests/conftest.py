"""
Repository-wide pytest options.

``pytest_addoption`` is only honoured in an *initial* conftest, so the
conformance harness's ``--backend`` switch is declared here rather than beside
the fixtures that consume it in ``tests/conformance/conftest.py``.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the conformance harness's backend selector."""
    parser.addoption(
        "--backend",
        action="store",
        default="minio",
        help=(
            "object-storage backend the conformance suite runs against; "
            "one of the pinned names in tests/conformance/contract.py "
            "(CANDIDATE_BACKENDS). Only used with -m conformance."
        ),
    )
