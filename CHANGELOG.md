# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
