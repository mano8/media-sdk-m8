# Object-storage conformance contract

Status: **specification only.** This file and [`contract.py`](contract.py) are
the executable spec produced by `T0-s3-surface-contract`. The docker-backed
suite that runs it arrives with `T1-conformance-harness`; the columns of
`MATRIX.md` are filled by `T2`–`T4` and ratified by `T5`.

## What this is for

The media stack is migrating off `minio/minio`, which was archived by its owner
on 2026-04-25 and has no upstream security-patch path. The replacement is
chosen by measurement, not by compatibility table. This document is the
measuring instrument: it states exactly what the stack asks of an S3 server, and
exactly which of those behaviours are security controls rather than features.

The contract is **provider-neutral**. It names buckets, operations and response
headers — never MinIO, SeaweedFS or Garage — except in `CANDIDATE_BACKENDS`,
where each candidate is pinned to the exact tag it was measured at.

## Rules

1. **Every row has a test id.** A behaviour described here and nowhere else is a
   contract defect, and `test_contract_spec.py` fails on it.
2. **Server-observed, never client-asserted.** A row about a server-side control
   (S9, S10, S11, S12) is proven by observing the server's rejection or its
   response headers. A client-side pre-check that never reaches the server
   proves nothing, and a test written that way is a harness bug.
3. **Stable ids.** `OP-nn` and `Sn` are never renumbered; matrix rows and
   deviation write-ups cite them.
4. **Severity is triage, not decoration.** Any `blocker` failing on a candidate
   disqualifies that candidate as the default backend. `T5-ratify-backend`
   switches to the validated alternative rather than re-planning.
5. **The forbidden list is asserted negatively.** A backend is never ratified on
   a capability this stack has decided not to use.

## S3 surface actually used

Extracted from `media_sdk_m8/storage/client.py`, `media_service/storage/*`,
`media_service/main.py` and `media-worker-m8/worker/config.py`. Source of truth:
`SURFACE_CASES` in [`contract.py`](contract.py).

| Case | Operation | Call site | Requirement | Test id | Suite | Severity |
| --- | --- | --- | --- | --- | --- | --- |
| OP-01 | PostObject | `ObjectStorage.presigned_post_object` | POST policy signed with `key` eq, `Content-Type` eq and `content-length-range`; all three enforced server-side | `test_post_object_accepts_conforming_upload` | conformance | blocker |
| OP-02 | GetObject (presigned) | `ObjectStorage.presigned_get_object` | SigV4 presigned GET binds Host and honours the `response-content-disposition` override | `test_presigned_get_returns_object` | conformance | blocker |
| OP-03 | GetObject (ranged) | `ObjectStorage.get_object_head` | `offset=0, length=512` returns exactly the first 512 bytes | `test_ranged_get_returns_exact_prefix` | conformance | blocker |
| OP-04 | GetObject (streamed) | `ObjectStorage.stream_object` | chunked read of 1 MiB reassembles to the original bytes | `test_streamed_get_reassembles_object` | conformance | blocker |
| OP-05 | HeadObject | `ObjectStorage.stat_object` | returns size, content-type and an **opaque** `etag`; no MD5 semantics may be assumed | `test_head_object_returns_opaque_etag` | conformance | blocker |
| OP-06 | PutObject (bytes) | `ObjectStorage.put_object` | stores the exact bytes and the declared content-type | `test_put_object_roundtrips_bytes` | conformance | blocker |
| OP-07 | PutObject (streamed) | `ObjectStorage.put_object_stream` | writes `length` bytes from an open handle without buffering the payload whole | `test_put_object_stream_roundtrips_handle` | conformance | blocker |
| OP-08 | CopyObject (same key, REPLACE) | `ObjectStorage.set_object_content_type` | metadata-only self-copy with `x-amz-metadata-directive: REPLACE` rewrites the stored Content-Type in place | `test_copy_replace_rewrites_content_type_in_place` | conformance | blocker |
| OP-09 | CopyObject (cross-bucket) | `ObjectStorage.copy_object` | server-side copy between buckets preserves bytes and type | `test_copy_object_across_buckets` | conformance | blocker |
| OP-10 | DeleteObject | `ObjectStorage.remove_object` | a deleted key subsequently 404s on HeadObject | `test_delete_object_removes_key` | conformance | blocker |
| OP-11 | ListObjectsV2 (recursive) | `ObjectStorage.list_object_keys` | recursive prefix listing pages beyond 1000 keys and yields every key exactly once | `test_list_object_keys_is_complete_and_paged` | conformance | blocker |
| OP-12 | HeadBucket | `media_service/main.py` health check | existing bucket reports present, missing bucket reports absent without raising | `test_head_bucket_reports_presence` | conformance | blocker |
| OP-13 | Multipart upload (implicit) | `put_object` / `put_object_stream` | a payload at or above the client part size uploads via multipart and reads back byte-identical | `test_multipart_upload_roundtrips` | conformance | blocker |

### Deliberately not used

Not used today, and the migration must not introduce them
(`FORBIDDEN_OPERATIONS`):

* `PutBucketPolicy`
* anonymous/public bucket read
* `PutBucketLifecycleConfiguration`
* `PutBucketVersioning`
* `PutObjectLockConfiguration`
* `PutObjectAcl`
* `PutObjectTagging`
* server-side encryption configuration

`public-media` is **not** anonymously readable: public objects are served
through a presigned GET gated on `scan_status == CLEAN`
(`controllers/objects.py:479`). Keeping it that way is why a backend without
`PutBucketPolicy` is not disqualified.

Retention, hard-purge, stale-upload expiry and orphan reconciliation are
application-side arq crons, not S3 lifecycle rules. A backend's lifecycle
maturity is therefore **not** a selection criterion here.

Proven by: `test_forbidden_operations_are_never_issued`.

## Security invariants

Each is enforced today; each needs an explicit post-migration proof. Source of
truth: `INVARIANT_CASES` in [`contract.py`](contract.py). `Delivered by` names
the plan step that must ship the test where it does not exist yet.

| Case | Invariant | Enforced today at | Test id | Suite | Severity | Delivered by |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | Storage publishes no host port in hardened; dev binds loopback only, never `0.0.0.0` | `test_compose_minio_policy.py::TestHardenedMinioNoHostPorts` | `test_hardened_storage_publishes_no_host_ports` | compose-policy | blocker | T18-compose-policy-tests |
| S2 | Admin/console/filer surface unreachable from Traefik and from any sibling on `app_net` | `traefik/dynamic_conf.yml` `!PathPrefix(/minio)` | `test_storage_admin_surface_unreachable_from_siblings` | conformance | blocker | T3-run-seaweedfs |
| S3 | CORS scoped to the UI origin, never a wildcard | `MINIO_API_CORS_ALLOW_ORIGIN` env | `test_storage_cors_is_scoped_to_ui_origin` | compose-policy | blocker | T18-compose-policy-tests |
| S4 | App credential scoped to the five media buckets (Get/Put/Delete/List); root creds only in the one-shot bootstrap | `minio-init` policy JSON | `test_scoped_credential_denies_unlisted_bucket` | conformance | blocker | T1-conformance-harness |
| S5 | Data path is presigned-only; an unsigned object request is refused on every bucket | absence of any anonymous policy | `test_unsigned_object_request_is_denied` | conformance | blocker | T1-conformance-harness |
| S6 | Public storage route is TLS-only, Host-pinned, `passHostHeader: true` | `traefik/dynamic_conf.yml:121-145` | `test_public_storage_route_is_tls_and_host_pinned` | compose-policy | blocker | T18-compose-policy-tests |
| S7 | Public storage endpoint is `https` in hardened, loopback in dev, validated at config load | `media_service/core/config.py:208-245` | `test_public_endpoint_scheme_rules_per_stack` | service-config | blocker | T10-settings-s3-rename |
| S8 | Storage secret is redacted via `secret_fields`; worker rejects internal-token == storage-secret | `config.py:53`, `worker/config.py:188-191` | `test_storage_secret_is_redacted_and_distinct` | service-config | blocker | T10-settings-s3-rename |
| S9 | **Server-side** `content-length-range`: an oversized (and an undersized) POST body is refused by the backend | POST policy condition | `test_post_policy_oversized_body_rejected_by_server` | conformance | blocker | T1-conformance-harness |
| S10 | **Server-side** Content-Type pinning: a mismatched POST is refused, and the REPLACE self-copy rewrites the stored type | POST policy + `set_object_content_type` | `test_post_policy_content_type_mismatch_rejected_by_server` | conformance | blocker | T1-conformance-harness |
| S11 | `response-content-disposition: attachment` honoured on presigned GET (anti stored-XSS) | `presign.py::_safe_content_disposition` | `test_presigned_get_honours_response_content_disposition` | conformance | blocker | T1-conformance-harness |
| S12 | Ranged GET returns `206 Partial Content`, so the magic-byte gate reads a prefix | `ObjectStorage.get_object_head` | `test_ranged_get_returns_partial_content` | conformance | blocker | T1-conformance-harness |
| S13 | Runtime data dir name blocked from every release surface | `release_hygiene.py:43` | `test_runtime_data_dir_name_is_blocked` | release-hygiene | blocker | T22-hygiene-dir-names |
| S14 | Every storage image pinned to an exact tag; never `:latest` | `test_compose_image_pins.py:78-79` | `test_storage_images_are_pinned` | image-pins | blocker | T19-image-pin-allowlist |
| S15 | Storage container hardened like every other service: `no-new-privileges`, `cap_drop: ALL`, `read_only`, `deploy.resources.limits` | ⚠️ not enforced today — gap this migration must close | `test_storage_service_carries_standard_hardening` | compose-policy | blocker | T18-compose-policy-tests |

### The four that decide the migration

S9, S10, S11 and S12 are the rows a compatibility table cannot answer. Each is a
security control implemented **by the storage server**, and each fails silently
if the server ignores it:

* **S9** — `content-length-range` is the only server-side upload size cap.
  Ignored ⇒ unbounded upload and storage exhaustion.
* **S10** — exact `Content-Type` equality plus the `REPLACE` rewrite force the
  server-validated type. Ignored ⇒ attacker-chosen content type served back.
* **S11** — `response-content-disposition` forces `attachment; filename=…`.
  Ignored ⇒ user-uploaded content renders inline on the storage origin ⇒
  **stored XSS**. Honoured by SeaweedFS only from 4.01 (PR #7559); anything
  older is exploitable here.
* **S12** — without ranged GET, the magic-byte sniffing gate degrades to a
  whole-object read.

A green tick on any of these that was not observed from the server's own
response is exactly the failure mode this contract exists to prevent.

## Backends under measurement

Every candidate is measured at an exact tag (`CANDIDATE_BACKENDS`):

| Backend | Pinned image | Role |
| --- | --- | --- |
| `minio` | `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772` | baseline — the golden column (`T2`) |
| `seaweedfs` | `chrislusf/seaweedfs:4.45` | proposed default (`T3`) |
| `garage` | `dxflrs/garage:v2.3.0` | validated alternative / fallback (`T4`) |

Any test that cannot pass on the MinIO baseline is a harness bug and is fixed in
`T2`, never carried into a candidate column as a deviation.

## Stacks in scope

The compose-policy rows apply to all seven stacks (`COVERED_STACKS`): four in
`media-service-m8` and three in `fa-ui-m8`, whose policy-test mirror must move in
step with the media suite.

## How the tables stay honest

`test_contract_spec.py` runs in the ordinary suite (no docker, no backend) and
asserts that:

* every surface row and every invariant row names a test id;
* test ids are unique across both tables, so no two rows share a proof;
* the case ids are exactly `OP-01`…`OP-13` and `S1`…`S15`, contiguous and
  unduplicated;
* this file and `contract.py` name the same `(case id, test id)` pairs — editing
  one without the other fails;
* every forbidden operation is listed in both, and none of them appears in the
  SDK's storage client;
* every candidate backend is pinned to an exact tag.
