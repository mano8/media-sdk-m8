# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
