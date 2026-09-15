"""
Executable checks on the conformance contract itself.

These run in the ordinary suite: no docker, no backend, no ``conformance``
mark. They exist so the contract cannot rot — a row that loses its test id, a
duplicated proof, or a ``CONTRACT.md`` edited out of step with ``contract.py``
fails here rather than being discovered during a backend cutover.
"""

import re
import tokenize
from pathlib import Path

import pytest

from .contract import (
    CANDIDATE_BACKENDS,
    COVERED_STACKS,
    FORBIDDEN_OPERATIONS,
    FORBIDDEN_OPERATIONS_TEST_ID,
    INVARIANT_CASES,
    SUITE_COMPOSE_POLICY,
    SUITE_CONFORMANCE,
    SUITE_IMAGE_PINS,
    SUITE_RELEASE_HYGIENE,
    SUITE_SERVICE_CONFIG,
    SURFACE_CASES,
    Severity,
    all_test_ids,
    conformance_test_ids,
)

CONTRACT_MD = Path(__file__).with_name("CONTRACT.md")
STORAGE_CLIENT = (
    Path(__file__).resolve().parents[2] / "media_sdk_m8" / "storage" / "client.py"
)

#: Short suite labels used in the CONTRACT.md tables, and what they resolve to.
SUITE_LABELS = {
    "conformance": SUITE_CONFORMANCE,
    "compose-policy": SUITE_COMPOSE_POLICY,
    "service-config": SUITE_SERVICE_CONFIG,
    "image-pins": SUITE_IMAGE_PINS,
    "release-hygiene": SUITE_RELEASE_HYGIENE,
}

#: Client-side spellings of each forbidden operation. Presence of any of these
#: in the storage client means the SDK grew a capability the contract excludes.
FORBIDDEN_CLIENT_TOKENS = {
    "PutBucketPolicy": ("set_bucket_policy", "put_bucket_policy"),
    "anonymous/public bucket read": ("make_public", "public-read"),
    "PutBucketLifecycleConfiguration": ("set_bucket_lifecycle", "LifecycleConfig"),
    "PutBucketVersioning": ("set_bucket_versioning", "VersioningConfig"),
    "PutObjectLockConfiguration": ("set_object_lock_config", "ObjectLockConfig"),
    "PutObjectAcl": ("set_object_acl", "put_object_acl"),
    "PutObjectTagging": ("set_object_tags", "put_object_tagging"),
    "server-side encryption configuration": ("set_bucket_encryption", "SseCustomerKey"),
}

_ROW = re.compile(r"^\|\s*(OP-\d+|S\d+)\s*\|(.*)\|\s*$")


def _md_rows() -> dict[str, list[str]]:
    """Return ``{case id: [cell, ...]}`` for every case row in CONTRACT.md."""
    rows: dict[str, list[str]] = {}
    for line in CONTRACT_MD.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if match is None:
            continue
        case_id = match.group(1)
        assert case_id not in rows, f"{case_id} appears twice in CONTRACT.md"
        rows[case_id] = [cell.strip() for cell in match.group(2).split("|")]
    return rows


def _code(cell: str) -> str:
    """Strip the backticks a CONTRACT.md cell wraps an identifier in."""
    return cell.strip().strip("`")


# ── Table integrity ─────────────────────────────────────────────────────────


def test_contract_md_exists_and_is_non_trivial() -> None:
    assert CONTRACT_MD.is_file()
    assert len(CONTRACT_MD.read_text(encoding="utf-8")) > 2000


def test_surface_case_ids_are_contiguous() -> None:
    expected = tuple(f"OP-{n:02d}" for n in range(1, len(SURFACE_CASES) + 1))
    assert tuple(case.case_id for case in SURFACE_CASES) == expected


def test_invariant_case_ids_are_s1_through_s15() -> None:
    expected = tuple(f"S{n}" for n in range(1, 16))
    assert tuple(case.case_id for case in INVARIANT_CASES) == expected


def test_every_test_id_is_unique() -> None:
    ids = all_test_ids()
    duplicates = {name for name in ids if ids.count(name) > 1}
    assert not duplicates, f"test ids claimed by more than one row: {duplicates}"


@pytest.mark.parametrize("case", SURFACE_CASES, ids=lambda c: c.case_id)
def test_surface_row_is_fully_specified(case) -> None:
    assert case.operation and case.call_site and case.requirement
    assert case.test_id.startswith("test_")
    assert case.suite in SUITE_LABELS.values()
    assert isinstance(case.severity, Severity)


@pytest.mark.parametrize("case", INVARIANT_CASES, ids=lambda c: c.case_id)
def test_invariant_row_is_fully_specified(case) -> None:
    assert case.statement and case.enforced_today_at
    assert case.test_id.startswith("test_")
    assert case.suite in SUITE_LABELS.values()
    assert isinstance(case.severity, Severity)
    assert case.delivered_by.startswith("T"), (
        f"{case.case_id} must name the plan step that delivers its test"
    )


def test_security_controls_are_proven_against_a_live_backend() -> None:
    """S9-S12 are server-side controls; a static policy test cannot prove them."""
    server_side = {"S9", "S10", "S11", "S12"}
    for case in INVARIANT_CASES:
        if case.case_id in server_side:
            assert case.suite == SUITE_CONFORMANCE, (
                f"{case.case_id} must be observed from a live backend"
            )


def test_every_invariant_is_a_blocker() -> None:
    """No invariant may be downgraded below blocker without editing the plan."""
    assert {case.severity for case in INVARIANT_CASES} == {Severity.BLOCKER}


# ── CONTRACT.md ↔ contract.py agreement ─────────────────────────────────────


def test_markdown_and_table_name_the_same_cases() -> None:
    rows = _md_rows()
    expected = {case.case_id for case in (*SURFACE_CASES, *INVARIANT_CASES)}
    assert set(rows) == expected


@pytest.mark.parametrize(
    "case", (*SURFACE_CASES, *INVARIANT_CASES), ids=lambda c: c.case_id
)
def test_markdown_row_matches_the_table(case) -> None:
    cells = _md_rows()[case.case_id]
    test_ids = {_code(cell) for cell in cells}
    assert case.test_id in test_ids, (
        f"CONTRACT.md row {case.case_id} does not name {case.test_id}"
    )
    labels = {SUITE_LABELS[cell] for cell in cells if cell in SUITE_LABELS}
    assert case.suite in labels, (
        f"CONTRACT.md row {case.case_id} does not name suite {case.suite}"
    )
    assert case.severity.value in cells


def test_forbidden_operations_are_documented() -> None:
    text = CONTRACT_MD.read_text(encoding="utf-8")
    assert FORBIDDEN_OPERATIONS_TEST_ID in text
    for operation in FORBIDDEN_OPERATIONS:
        head = operation.split("/")[0].split(" ")[0]
        assert head.lower() in text.lower(), f"{operation} undocumented"


# ── The contract's own negative assertion ───────────────────────────────────


def _client_code() -> str:
    """
    Return the storage client's source with comments and docstrings removed.

    The forbidden-capability scan looks for calls, not for prose: the client's
    own docstrings discuss ``public-read`` semantics precisely because it does
    not use them, and matching that text would be a false positive. Ordinary
    string literals are kept — a forbidden capability passed as a literal (an
    ACL string, say) must still be caught.
    """
    statement_starts = {
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    kept: list[str] = []
    previous = tokenize.ENCODING
    with STORAGE_CLIENT.open(encoding="utf-8") as handle:
        for token in tokenize.generate_tokens(handle.readline):
            is_docstring = (
                token.type == tokenize.STRING and previous in statement_starts
            )
            if token.type != tokenize.COMMENT and not is_docstring:
                kept.append(token.string)
            if token.type not in (tokenize.COMMENT,):
                previous = token.type
    return "\n".join(kept)


def test_forbidden_operations_are_never_issued() -> None:
    """No forbidden capability has crept into the SDK storage client."""
    source = _client_code()
    for operation in FORBIDDEN_OPERATIONS:
        for token in FORBIDDEN_CLIENT_TOKENS[operation]:
            assert token not in source, (
                f"{STORAGE_CLIENT.name} issues {operation} via {token!r}, "
                f"which the contract excludes"
            )


def test_every_forbidden_operation_has_client_tokens() -> None:
    assert set(FORBIDDEN_CLIENT_TOKENS) == set(FORBIDDEN_OPERATIONS)
    assert all(FORBIDDEN_CLIENT_TOKENS[op] for op in FORBIDDEN_OPERATIONS)


# ── Measurement setup ───────────────────────────────────────────────────────


def test_candidate_backends_are_pinned() -> None:
    """An unpinned backend is not a measured backend."""
    for name, image in CANDIDATE_BACKENDS.items():
        assert ":" in image, f"{name} is not pinned to a tag"
        assert not image.endswith(":latest"), f"{name} is pinned to :latest"


def test_baseline_backend_is_the_currently_deployed_image() -> None:
    assert CANDIDATE_BACKENDS["minio"].startswith("quay.io/minio/minio:RELEASE.")


def test_seaweedfs_candidate_is_at_least_4_01() -> None:
    """Below 4.01 `response-content-disposition` is ignored (S11 ⇒ stored XSS)."""
    tag = CANDIDATE_BACKENDS["seaweedfs"].rsplit(":", 1)[1]
    major, minor = (int(part) for part in tag.split(".")[:2])
    assert (major, minor) >= (4, 1)


def test_covered_stacks_span_both_shipping_repos() -> None:
    assert len(COVERED_STACKS) == 7
    assert sum(s.startswith("media-service-m8/") for s in COVERED_STACKS) == 4
    assert sum(s.startswith("fa-ui-m8/") for s in COVERED_STACKS) == 3


def test_harness_scope_is_the_conformance_suite() -> None:
    """T1 must implement exactly the rows this contract assigns to it."""
    harness = conformance_test_ids()
    assert len(harness) == len(set(harness))
    assert set(harness) <= set(all_test_ids())
    assert len(harness) >= len(SURFACE_CASES)
