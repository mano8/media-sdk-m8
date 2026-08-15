# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.2] - 2026-08-15

### Added

- `tests/test_changelog_version_parity.py` — asserts the current
  `pyproject.toml` `[project]` version has a matching `## [x.y.z]` heading in
  `CHANGELOG.md`, so a release can no longer ship undocumented
  (`A32-changelog-version-parity`).

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
