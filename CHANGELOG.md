# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-09-05

### Added

- `tests/conformance/` — the executable object-storage conformance contract
  (`T0-s3-surface-contract`). `CONTRACT.md` and `contract.py` are one
  specification in two renderings: 13 surface cases (`OP-01`–`OP-13`) covering
  every S3 operation the media stack issues, 15 security invariants
  (`S1`–`S15`), and the list of operations the stack deliberately does not use.
  Every row names the test id that proves it and the suite that owns that test,
  so no invariant survives as prose only. `test_contract_spec.py` runs in the
  ordinary suite — no docker, no backend — and fails if a row loses its proof,
  if two rows share one, if the two renderings drift apart, if a candidate
  backend is not pinned to an exact tag, or if a forbidden capability appears in
  the storage client. Specification only: the docker-backed harness that
  executes it lands with `T1-conformance-harness`.
- `tests/conformance/` docker-backed harness (`T1-conformance-harness`) —
  executes the contract against a live backend. `backends.py` boots one pinned
  image on a throwaway docker network, publishes only the S3 port on loopback,
  and bootstraps it with the same five buckets and the same scoped `media-rw`
  policy JSON as the shipped `minio-init`; `probe.py` speaks raw HTTP so a
  refusal is a status line rather than a client-side guard. `test_s3_surface.py`
  covers `OP-01`–`OP-13` and `test_security_invariants.py` covers the live rows
  `S4`, `S5`, `S9`–`S12`, each asserting the server's verdict: an oversized POST
  refused by the backend with the object absent afterwards, a `Content-Type`
  mismatch refused against the signed policy, `Content-Disposition: attachment`
  returned verbatim, and `206 Partial Content` on a ranged GET. `S9`'s negative
  cases carry a conforming positive control so a refusal cannot be an unrelated
  4xx. Marked `conformance` and deselected by default (`addopts`), so the
  ordinary run and its 100% coverage gate need no container engine;
  `test_harness_spec.py` asserts that deselection, that every contract row this
  step owes exists as a test, and that every image the harness runs is pinned.
  Run with `pytest -m conformance --backend=minio`; `HARNESS.md` documents the
  setup and what counts as proof. Tests only — no change to `media_sdk_m8`.
- `S3StorageConfig` — the provider-neutral name for the storage connection
  settings. `ObjectStorageConfig` remains as an alias, so consumers pinned to an
  older SDK keep importing successfully.
- `get_s3_client(config)` — the client factory under its new name.
  `get_minio_client` remains as a deprecated alias delegating to it.
- `ObjectStorage.bucket_exists(*, bucket)` — `HeadBucket` with present/absent as
  a return value rather than an exception, which is what lets the consuming
  service's health check report DEGRADED instead of FAIL. A `403` is re-raised:
  a refusal is a grant problem, not an absence, and reporting it as "missing"
  would hide a misconfigured credential behind a health warning.
- `ObjectStat` and `ObjectWriteResult` — the explicit result shapes
  `stat_object`, the write methods and the copy methods return, carrying the
  same attributes (`etag` with quotes stripped, `size`, `content_type`,
  `last_modified`, `version_id`) the previous client's objects exposed.
- `DEFAULT_MULTIPART_THRESHOLD` / `DEFAULT_MULTIPART_CHUNK_SIZE` — the
  single-part/multipart boundary, now stated in the SDK instead of being an
  implicit property of the client library.

### Changed

- `ObjectStorage` internals reimplemented on boto3/botocore
  (`T6-boto3-storage-core`). Every public method keeps its signature and its
  return shape; only the library underneath changed. The reason is neutrality,
  not maintenance: SeaweedFS and Garage are tested against boto3 and the AWS
  CLI, so signing, POST-policy field encoding and response-header overrides go
  over the wire the way the reference client sends them, removing a class of
  "works on one server, subtly wrong on another" risk before the backend swap
  in Wave 3. `presigned_post_object` uses `generate_presigned_post` with the
  `key`, `Content-Type` and `content-length-range` conditions;
  `presigned_get_object` uses `ResponseContentDisposition` and refuses an
  unrecognised override rather than dropping it from the signature (a silently
  dropped `Content-Disposition` is stored XSS); `set_object_content_type` uses
  `MetadataDirective="REPLACE"`. Dual-endpoint presigning is unchanged: the
  POST policy is still signed by the internal client and posted to the public
  endpoint, and the presigned GET is still signed by a client bound to the
  public host, because SigV4 binds Host and a POST policy does not.
  Provider-neutral client settings are pinned rather than left at botocore's
  AWS-shaped defaults: SigV4, path-style addressing, and checksums only
  `when_required` — botocore otherwise sends an `x-amz-checksum-*` trailer on
  every upload that several S3-compatible servers reject.
  Verified by re-running the whole conformance matrix against all three pinned
  backends with the new client: MinIO 19 passed / 1 skipped, SeaweedFS 4.45
  20 passed, Garage v2.3.0 20 passed — cell for cell identical to the Wave 0
  readings (`tests/conformance/MATRIX.md`).
- `boto3>=1.36` added as a direct dependency. `minio` stayed declared through
  this step so no consumer's install broke mid-rewrite.
- **`minio` dropped as a direct dependency** (`T8-sdk-release-cut`), completing
  the client swap `T6-boto3-storage-core` started: the package speaks only
  boto3/botocore now. `constraints-all.txt` was regenerated with `pip-compile`
  (not done at `T6`) — it now pins `boto3`/`botocore` and their transitive
  closure (`s3transfer`, `jmespath`, `python-dateutil`, …) for the first time,
  and drops `minio` and its exclusive closure (`argon2-cffi`,
  `argon2-cffi-bindings`, `cffi`, `pycparser`, `pycryptodome`, plus `certifi`
  once no remaining pin needed it). `get_minio_client` and
  `ObjectStorageConfig` remain as deprecated aliases of `get_s3_client` /
  `S3StorageConfig` — this is a dependency change, not an API break.
- `pyproject.toml` `keywords` dropped `minio`, added `s3` and `boto3` — the
  package indexes as provider-neutral, not MinIO-specific.
- `README.md` and `REPOSITORY_CONTEXT.md` now describe `ObjectStorage` as a
  provider-neutral S3 client (SigV4) built on boto3, name the two validated
  backends (SeaweedFS 4.x default, Garage 2.x fallback — see
  `.workspace/context/object-storage.md` in the workspace host, when present)
  and any other S3-compatible provider including MinIO, and replace the
  illustrative `minio:9000` example endpoint with the generic `storage:9000`.
- `tests/test_ci_policy.py::test_constraints_all_pins_key_runtime_deps` now
  asserts `constraints-all.txt` pins `boto3==` instead of `minio==`.

### Fixed

- **`set_object_content_type` docstring drift** (`T14-readme-post-policy-fix`,
  object-storage backend migration plan, Wave 2). The docstring said upload
  goes through a "presigned PUT", which the flow never used — it is an S3
  **POST policy** whose signed `Content-Type` condition already pins the value
  server-side at upload time. The docstring now describes this method
  accurately as a narrower, post-write correction (e.g. after a server-side
  MIME re-sniff), not the upload path's own type control. Docs-only; no
  behaviour or signature change. Full suite 184 passed, 20 deselected, 100%
  coverage; ruff format/check and mypy clean.

### Breaking

- None. Despite the dependency removal, every public symbol
  (`ObjectStorage`, `S3StorageConfig`/`ObjectStorageConfig`, `get_s3_client`/
  `get_minio_client`, `bucket_exists`, …) keeps its signature and return
  shape from `0.7.0`; `T6-boto3-storage-core` already made that swap
  byte-compatible. Minor bump under this project's 0.x SemVer reflects the
  additive surface from `0.7.0`'s `[Unreleased]` entries (`S3StorageConfig`,
  `bucket_exists`, `ObjectStat`/`ObjectWriteResult`), not an incompatibility.

## [0.7.0] - 2026-08-23

### Added

- `ObjectStorage.put_object_stream(*, bucket, object_key, data, length,
  content_type)` — write-side counterpart of `stream_object`. Hands an open
  file-like object to the underlying client unbuffered, so a consumer that has
  already assembled a payload on disk (media-service-m8's archive export) does
  not have to make it resident in memory just to pass it to `put_object`, whose
  `bytes` argument is right for a generated image variant and wrong here. The
  caller owns the handle and its position, and passes the exact byte count.
  This lands the primitive in the platform layer beside the rest of
  `ObjectStorage` rather than leaving a private copy at a service's storage
  boundary (`ARCH-LAYER-DIRECTION`).
- `ExportArchiveEntry` and `ExportArchiveJobPayload` — the immutable
  producer↔consumer contract for `P2 U11`. media-service resolves authorization
  and storage references before enqueueing; the DB-free media-worker validates
  the payload, streams the ZIP, and reports through the service's internal HTTP
  boundary.

Minor rather than patch under this project's 0.x SemVer: consumers pin
`media-sdk-m8>=0.6.0,<0.7.0`, so both media-service-m8 and media-worker-m8 raise
their floor to `>=0.7.0,<0.8.0` with this release.

## [0.6.0] - 2026-08-16

Renumbered from the unreleased `0.5.2` heading dated 2026-08-15. Nothing shipped
under `0.5.2` — the latest published release is `0.5.1` — and the Python floor
raise below is a breaking change, which under this project's 0.x SemVer is a
minor bump, not a patch. Content is unchanged from the `0.5.2` entry.

### Added

- `tests/test_changelog_version_parity.py` — asserts the current
  `pyproject.toml` `[project]` version has a matching `## [x.y.z]` heading in
  `CHANGELOG.md`, so a release can no longer ship undocumented
  (`A32-changelog-version-parity`).

### Changed

- **Breaking:** floor raised to Python 3.12 (`requires-python`, classifiers,
  and the CI test matrix all dropped 3.11); 3.14 added to classifiers,
  matching the range CI already exercises.
- Python samples in `README.md` and `REPOSITORY_CONTEXT.md` reformatted to
  satisfy `ruff format`: ruff 0.16 formats fenced Python blocks inside Markdown,
  and both files' samples predated that (aligned trailing comments, condensed
  call arguments) so `ruff format --check .` failed CI on documentation alone.

## [0.5.1] - 2026-07-03

### Changed

- Finalize CHANGELOG: document all shipped features and security hardening
  from the 0.4.0 and 0.5.0 releases that were omitted from prior entries.
  No code change.

## [0.5.0] - 2026-07-02

### Added

- `ObjectStorageConfig.public_endpoint` / `public_secure` — optional browser-reachable
  endpoint for presigned URLs. When set, `post_upload_url` returns a URL using the
  public host/scheme and `presigned_get_object` is signed by a client bound to that
  endpoint (required because SigV4 GET signatures bind the Host). All internal ops
  (`stat_object`, `remove_object`, `get_object`, `copy_object`, etc.) continue to
  use the internal endpoint unchanged. Setting `public_endpoint=None` (the default)
  preserves byte-identical behaviour. Reverse proxies forwarding to MinIO must
  preserve the Host header (`passHostHeader: true` in Traefik, its default) so
  the SigV4 signature validates.
- `_validate_public_endpoint` guard on `ObjectStorageConfig` rejects values
  containing a scheme (`://`), embedded userinfo (`@`), fragment (`#`), or
  query string (`?`) — patterns indicating an accidentally-passed full URL
  or credential-carrying netloc that would corrupt presigned URL construction.
- Hash-pinned `constraints-all.txt` snapshot of the full public-PyPI dependency
  closure, consumed by the Dockerfile release build with `--require-hashes`.
- `tests/test_ci_policy.py` — locks CI invariants: no long-lived `PYPI_API_TOKEN`,
  `id-token: write` present, protected PyPI environment, no duplicate `ci.yml`,
  SHA-pinned action refs in both workflows, `constraints-all.txt` exists and pins
  runtime deps without custom index URLs.

### Changed

- PyPI publish workflow migrated to OIDC Trusted Publishing; removed long-lived
  `PYPI_API_TOKEN` secret.

## [0.4.0] - 2026-06-19

### Added

- `ObjectStorage.stream_object(*, bucket, object_key, chunk_size=1 MiB)` — yields
  an object's bytes in chunks without buffering it whole, the streaming read
  primitive media-service-m8 needs to verify a SHA-256 over a large (size-capped)
  upload without allocating the full object in memory (plan item 6.x.3). The
  connection is held open for the iterator's lifetime and released on completion
  or close. Stays DB-free and framework-agnostic.

## [0.3.0] - 2026-06-15

### Added

- `OutboxEventPayload` — the self-contained Pydantic v2 contract for outbound
  webhook events (`event_id`, `event_type`, `object_id`, `payload`, `created_at`).
  media-service-m8 writes one row per state change to its transactional outbox
  and POSTs this HMAC-signed body to subscriber URLs; a subscriber needs only
  this shape to verify and consume an event. Frozen and DB/framework-agnostic,
  consistent with the existing job contracts.

## [0.2.0] - 2026-06-15

### Added

- `ObjectStorage.list_object_keys(*, bucket, prefix="")` — recursively streams
  every stored object key, the read primitive media-service-m8's orphan
  reconciler (Phase 14) needs to detect bytes that have no DB row. Stays
  DB-free and framework-agnostic.

## [0.1.0] - 2026-06-13

### Added

- Initial `media_sdk_m8` package — the shared, settings-agnostic media SDK
  backbone consumed by media-service-m8 (producer) and media-worker-m8 (consumer).
- `media_sdk_m8.storage` — `ObjectStorage` MinIO wrapper plus `ObjectStorageConfig`
  and `get_minio_client`. The client is built from an explicit config (no service
  settings/env dependency); config carries the default presigned-URL lifetime
  (`presigned_expire_seconds`), overridable per call. Includes `put_object` for
  writing variant bytes.
- `media_sdk_m8.contracts` — Pydantic v2 producer↔consumer job contracts:
  `ScanJobPayload`, `VariantSpec`, `VariantJobPayload`.
- Packaging and quality config (`pyproject.toml`, CI workflow, PyPI workflow)
  targeting `media_sdk_m8`. Runtime dependencies: `minio` and `pydantic>=2`.
