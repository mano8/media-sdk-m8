"""
Machine-readable S3 conformance contract.

Two case tables, both parametrisable:

* :data:`SURFACE_CASES` — every S3 operation the media stack actually issues
  (the "S3 surface"). A backend that does not serve all of them cannot host
  this stack.
* :data:`INVARIANT_CASES` — the security invariants S1–S15. Each is enforced
  today; each needs an explicit post-migration proof.

Every row names the test id that proves it and the suite that owns that test.
Rows whose ``suite`` is :data:`SUITE_CONFORMANCE` are executed by the
docker-backed harness against a live backend; the others are static-policy
assertions living in a consuming repository. Nothing here is prose-only:
``test_contract_spec.py`` fails if a row loses its test id, if two rows claim
the same id, or if ``CONTRACT.md`` and this table disagree.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: Suite that runs a case against a live backend container.
SUITE_CONFORMANCE: Final = "media-sdk-m8/tests/conformance"
#: Compose-topology policy suite (mirrored by fa-ui-m8 for its three stacks).
SUITE_COMPOSE_POLICY: Final = "media-service-m8/docker_compose/compose_policy_tests"
#: Image-pin policy suite.
SUITE_IMAGE_PINS: Final = "media-service-m8/tests/test_compose_image_pins.py"
#: Service configuration suite.
SUITE_SERVICE_CONFIG: Final = "media-service-m8/tests/test_config.py"
#: Release-hygiene suite.
SUITE_RELEASE_HYGIENE: Final = "security-tests-m8/tests"


class Severity(StrEnum):
    """How a failing row is triaged when a candidate backend is measured."""

    #: A backend failing this row cannot be ratified. No workaround.
    BLOCKER = "blocker"
    #: The stack still works, but only with a documented compensating change.
    WORKAROUND = "workaround"
    #: Observable difference with no security or functional consequence.
    COSMETIC = "cosmetic"


@dataclass(frozen=True, slots=True)
class SurfaceCase:
    """One S3 operation the stack issues, and the test that proves it works."""

    #: Stable identifier, ``OP-<n>``. Never renumbered — matrix rows cite it.
    case_id: str
    #: S3 operation name as the API spells it.
    operation: str
    #: ``ObjectStorage`` method (or other call site) that issues it.
    call_site: str
    #: What must hold beyond "the call returns 2xx".
    requirement: str
    #: Name of the test function proving this row.
    test_id: str
    #: Suite owning ``test_id``.
    suite: str
    #: Triage class when a candidate backend fails this row.
    severity: Severity


@dataclass(frozen=True, slots=True)
class InvariantCase:
    """One security invariant from the plan's §4.2, and its proof."""

    #: Stable identifier, ``S<n>``, matching the migration plan.
    case_id: str
    #: The invariant, stated so that its negation is testable.
    statement: str
    #: Where it is enforced today (pre-migration).
    enforced_today_at: str
    #: Name of the test function proving this row after migration.
    test_id: str
    #: Suite owning ``test_id``.
    suite: str
    #: Triage class when a candidate backend or stack fails this row.
    severity: Severity
    #: Plan step that must deliver ``test_id`` if it does not exist yet.
    delivered_by: str


# ── S3 surface actually used ────────────────────────────────────────────────

SURFACE_CASES: Final[tuple[SurfaceCase, ...]] = (
    SurfaceCase(
        case_id="OP-01",
        operation="PostObject",
        call_site="ObjectStorage.presigned_post_object",
        requirement=(
            "POST policy signed with key eq, Content-Type eq and "
            "content-length-range; all three enforced server-side"
        ),
        test_id="test_post_object_accepts_conforming_upload",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-02",
        operation="GetObject (presigned)",
        call_site="ObjectStorage.presigned_get_object",
        requirement=(
            "SigV4 presigned GET binds Host and honours the "
            "response-content-disposition override"
        ),
        test_id="test_presigned_get_returns_object",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-03",
        operation="GetObject (ranged)",
        call_site="ObjectStorage.get_object_head",
        requirement="offset=0, length=512 returns exactly the first 512 bytes",
        test_id="test_ranged_get_returns_exact_prefix",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-04",
        operation="GetObject (streamed)",
        call_site="ObjectStorage.stream_object",
        requirement="chunked read of 1 MiB reassembles to the original bytes",
        test_id="test_streamed_get_reassembles_object",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-05",
        operation="HeadObject",
        call_site="ObjectStorage.stat_object",
        requirement=(
            "returns size, content-type and an opaque etag; no MD5 semantics "
            "may be assumed of the etag"
        ),
        test_id="test_head_object_returns_opaque_etag",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-06",
        operation="PutObject (bytes)",
        call_site="ObjectStorage.put_object",
        requirement="stores the exact bytes and the declared content-type",
        test_id="test_put_object_roundtrips_bytes",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-07",
        operation="PutObject (streamed)",
        call_site="ObjectStorage.put_object_stream",
        requirement=(
            "writes length bytes from an open handle without buffering the "
            "payload whole"
        ),
        test_id="test_put_object_stream_roundtrips_handle",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-08",
        operation="CopyObject (same key, REPLACE)",
        call_site="ObjectStorage.set_object_content_type",
        requirement=(
            "metadata-only self-copy with x-amz-metadata-directive: REPLACE "
            "rewrites the stored Content-Type in place"
        ),
        test_id="test_copy_replace_rewrites_content_type_in_place",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-09",
        operation="CopyObject (cross-bucket)",
        call_site="ObjectStorage.copy_object",
        requirement="server-side copy between buckets preserves bytes and type",
        test_id="test_copy_object_across_buckets",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-10",
        operation="DeleteObject",
        call_site="ObjectStorage.remove_object",
        requirement="a deleted key subsequently 404s on HeadObject",
        test_id="test_delete_object_removes_key",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-11",
        operation="ListObjectsV2 (recursive)",
        call_site="ObjectStorage.list_object_keys",
        requirement=(
            "recursive prefix listing pages beyond 1000 keys and yields every "
            "key exactly once — the orphan reconciler depends on completeness"
        ),
        test_id="test_list_object_keys_is_complete_and_paged",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-12",
        operation="HeadBucket",
        call_site="media_service/main.py health check (bucket_exists)",
        requirement=(
            "an existing bucket reports present and a missing bucket reports "
            "absent without raising — DEGRADED-not-FAIL health semantics"
        ),
        test_id="test_head_bucket_reports_presence",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
    SurfaceCase(
        case_id="OP-13",
        operation="Multipart upload (implicit)",
        call_site="ObjectStorage.put_object / put_object_stream",
        requirement=(
            "a payload at or above the client part size uploads via multipart "
            "and reads back byte-identical"
        ),
        test_id="test_multipart_upload_roundtrips",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
    ),
)

#: Operations the migration must **not** introduce. Asserted negatively so a
#: backend is never ratified on a capability the stack has decided not to use.
FORBIDDEN_OPERATIONS: Final[tuple[str, ...]] = (
    "PutBucketPolicy",
    "anonymous/public bucket read",
    "PutBucketLifecycleConfiguration",
    "PutBucketVersioning",
    "PutObjectLockConfiguration",
    "PutObjectAcl",
    "PutObjectTagging",
    "server-side encryption configuration",
)

#: Test proving :data:`FORBIDDEN_OPERATIONS` stay unused by the SDK surface.
FORBIDDEN_OPERATIONS_TEST_ID: Final = "test_forbidden_operations_are_never_issued"


# ── Security invariants ─────────────────────────────────────────────────────

INVARIANT_CASES: Final[tuple[InvariantCase, ...]] = (
    InvariantCase(
        case_id="S1",
        statement=(
            "Storage publishes no host port in the hardened stack; dev stacks "
            "bind loopback only, never 0.0.0.0"
        ),
        enforced_today_at="test_compose_minio_policy.py::TestHardenedMinioNoHostPorts",
        test_id="test_hardened_storage_publishes_no_host_ports",
        suite=SUITE_COMPOSE_POLICY,
        severity=Severity.BLOCKER,
        delivered_by="T18-compose-policy-tests",
    ),
    InvariantCase(
        case_id="S2",
        statement=(
            "The admin/console/filer surface is unreachable from Traefik and "
            "from any sibling container on app_net"
        ),
        enforced_today_at="traefik/dynamic_conf.yml !PathPrefix(/minio) rule",
        test_id="test_storage_admin_surface_unreachable_from_siblings",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T3-run-seaweedfs",
    ),
    InvariantCase(
        case_id="S3",
        statement="CORS is scoped to the UI origin and is never a wildcard",
        enforced_today_at="MINIO_API_CORS_ALLOW_ORIGIN env",
        test_id="test_storage_cors_is_scoped_to_ui_origin",
        suite=SUITE_COMPOSE_POLICY,
        severity=Severity.BLOCKER,
        delivered_by="T18-compose-policy-tests",
    ),
    InvariantCase(
        case_id="S4",
        statement=(
            "The application credential is scoped to the five media buckets "
            "with Get/Put/Delete/List only; root credentials appear solely in "
            "the one-shot bootstrap"
        ),
        enforced_today_at="minio-init policy JSON",
        test_id="test_scoped_credential_denies_unlisted_bucket",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S5",
        statement=(
            "The data path is presigned-only: an unsigned request for a stored "
            "object is refused on every bucket, public-media included"
        ),
        enforced_today_at="absence of any anonymous bucket policy",
        test_id="test_unsigned_object_request_is_denied",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S6",
        statement=(
            "The public storage route is TLS-only, Host-pinned and forwards the "
            "original Host (passHostHeader: true)"
        ),
        enforced_today_at="traefik/dynamic_conf.yml:121-145",
        test_id="test_public_storage_route_is_tls_and_host_pinned",
        suite=SUITE_COMPOSE_POLICY,
        severity=Severity.BLOCKER,
        delivered_by="T18-compose-policy-tests",
    ),
    InvariantCase(
        case_id="S7",
        statement=(
            "The public storage endpoint is https in hardened and loopback in "
            "dev, validated at config load"
        ),
        enforced_today_at="media_service/core/config.py:208-245",
        test_id="test_public_endpoint_scheme_rules_per_stack",
        suite=SUITE_SERVICE_CONFIG,
        severity=Severity.BLOCKER,
        delivered_by="T10-settings-s3-rename",
    ),
    InvariantCase(
        case_id="S8",
        statement=(
            "The storage secret is redacted via secret_fields, and the worker "
            "refuses to start when the internal token equals the storage secret"
        ),
        enforced_today_at="config.py:53, worker/config.py:188-191",
        test_id="test_storage_secret_is_redacted_and_distinct",
        suite=SUITE_SERVICE_CONFIG,
        severity=Severity.BLOCKER,
        delivered_by="T10-settings-s3-rename",
    ),
    InvariantCase(
        case_id="S9",
        statement=(
            "content-length-range is enforced by the server: an oversized (and "
            "an undersized) POST body is refused by the backend, not the client"
        ),
        enforced_today_at="POST policy condition",
        test_id="test_post_policy_oversized_body_rejected_by_server",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S10",
        statement=(
            "Content-Type is server-pinned: a POST whose Content-Type differs "
            "from the signed value is refused, and the REPLACE self-copy "
            "rewrites the stored type authoritatively"
        ),
        enforced_today_at="POST policy condition + set_object_content_type",
        test_id="test_post_policy_content_type_mismatch_rejected_by_server",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S11",
        statement=(
            "A presigned GET carrying response-content-disposition returns the "
            "Content-Disposition: attachment response header verbatim — the "
            "anti-stored-XSS control"
        ),
        enforced_today_at="media_service/storage/presign.py::_safe_content_disposition",
        test_id="test_presigned_get_honours_response_content_disposition",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S12",
        statement=(
            "Ranged GET returns 206 partial content, so the magic-byte sniffing "
            "gate reads a prefix and not the whole object"
        ),
        enforced_today_at="ObjectStorage.get_object_head",
        test_id="test_ranged_get_returns_partial_content",
        suite=SUITE_CONFORMANCE,
        severity=Severity.BLOCKER,
        delivered_by="T1-conformance-harness",
    ),
    InvariantCase(
        case_id="S13",
        statement=(
            "The backend's runtime data directory name is blocked from every "
            "release surface"
        ),
        enforced_today_at="security_tests_m8/release_hygiene.py:43",
        test_id="test_runtime_data_dir_name_is_blocked",
        suite=SUITE_RELEASE_HYGIENE,
        severity=Severity.BLOCKER,
        delivered_by="T22-hygiene-dir-names",
    ),
    InvariantCase(
        case_id="S14",
        statement="Every storage image is pinned to an exact tag; never latest",
        enforced_today_at="tests/test_compose_image_pins.py:78-79",
        test_id="test_storage_images_are_pinned",
        suite=SUITE_IMAGE_PINS,
        severity=Severity.BLOCKER,
        delivered_by="T19-image-pin-allowlist",
    ),
    InvariantCase(
        case_id="S15",
        statement=(
            "The storage container carries the same hardening as every other "
            "service: no-new-privileges, cap_drop ALL, read_only, and "
            "deploy.resources.limits"
        ),
        enforced_today_at="not enforced today — gap this migration must close",
        test_id="test_storage_service_carries_standard_hardening",
        suite=SUITE_COMPOSE_POLICY,
        severity=Severity.BLOCKER,
        delivered_by="T18-compose-policy-tests",
    ),
)

#: Stacks that must satisfy every compose-policy row. fa-ui-m8 mirrors the
#: media policy suite, so its three stacks are in scope too.
COVERED_STACKS: Final[tuple[str, ...]] = (
    "media-service-m8/docker_compose/hardened_media_m8",
    "media-service-m8/docker_compose/dev_media_m8",
    "media-service-m8/docker_compose/dev_local_media_m8",
    "media-service-m8/docker_compose/worspace_dev_media_m8",
    "fa-ui-m8/docker_compose/hardened_ui_m8",
    "fa-ui-m8/docker_compose/dev_ui_m8",
    "fa-ui-m8/docker_compose/dev_local_full_ui_m8",
)

#: Backends the harness is parametrised over. The pinned tag is part of the
#: contract: an unpinned backend is not a measured backend.
CANDIDATE_BACKENDS: Final[dict[str, str]] = {
    "minio": "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772",
    "seaweedfs": "chrislusf/seaweedfs:4.45",
    "garage": "dxflrs/garage:v2.3.0",
}


def all_test_ids() -> tuple[str, ...]:
    """Return every test id the contract names, in table order."""
    return (
        *(case.test_id for case in SURFACE_CASES),
        FORBIDDEN_OPERATIONS_TEST_ID,
        *(case.test_id for case in INVARIANT_CASES),
    )


def conformance_test_ids() -> tuple[str, ...]:
    """Return the test ids the docker-backed harness must implement."""
    return tuple(
        case.test_id
        for case in (*SURFACE_CASES, *INVARIANT_CASES)
        if case.suite == SUITE_CONFORMANCE
    )
