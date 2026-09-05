# media-sdk-m8

## Layer

Platform (shared media SDK).

## Purpose

Provide shared, settings-agnostic media primitives for `media-service-m8` (job
producer) and `media-worker-m8` (job consumer). The package owns the object-storage
client (`ObjectStorage` / `ObjectStorageConfig`) and producer-to-consumer job
contracts (`ScanJobPayload`, `VariantSpec`, `VariantJobPayload`,
`ExportArchiveEntry`, `ExportArchiveJobPayload`).

## Repository boundaries

- Keep the SDK framework-agnostic: do not add FastAPI, auth-SDK, or
  pydantic-settings dependencies.
- Keep it free of `imgtools_m8`, which is a worker-only dependency.
- Callers pass an explicit configuration object; the SDK does not read settings or
  environment variables.
- Do not add consuming-service business logic, database knowledge, or preset
  knowledge.
- Ship only reusable media, storage, and client primitives. Current direct
  dependencies are boto3/botocore, MinIO and Pydantic v2. The object-storage
  contract is the S3 API itself, never a particular server: the client speaks
  boto3 because that is the reference implementation every S3-compatible
  server is tested against. The `minio` dependency is retained only until
  `T8-sdk-release-cut` removes it.

These are repository architecture constraints. If a requested change appears to
break one, record the decision in the workspace context when that optional
enhancement is available and confirm the exception before proceeding.

## Example

Build the client from an explicit config rather than a service settings module:

```python
from media_sdk_m8 import ObjectStorage, ObjectStorageConfig, ScanJobPayload

storage = ObjectStorage(
    ObjectStorageConfig(
        endpoint="minio:9000",
        access_key="...",
        secret_key="...",
        secure=False,
        region="us-east-1",
    )
)
payload = ScanJobPayload(
    object_id=object_id,
    bucket="private-media",
    object_key=key,
    owner_user_id=user_id,
)
```

## Conditional quality and release guidance

When a task requires quality validation, use the repository documentation and CI
with the applicable selected Python policy. The documented checks are:

```bash
ruff format . && ruff check .
mypy media_sdk_m8 --ignore-missing-imports
bandit -r media_sdk_m8 --severity-level medium
pytest --cov=media_sdk_m8 --cov-report=term-missing --cov-fail-under=100
```

When a release is explicitly in scope, publish to PyPI through the tag-driven
release process.

## Standalone authority

This file, `pyproject.toml`, repository documentation, and existing CI are the
authoritative local context. A verified nearest workspace can optionally add
launcher-selected policies and tasks; its absence is a successful standalone
condition and does not reduce this repository's local context.
