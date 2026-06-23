# media-sdk-m8

Shared, **settings-agnostic** media primitives for the m8 media stack. Consumed by:

- **media-service-m8** — the live API (job producer)
- **media-worker-m8** — the async ARQ worker (job consumer)

The SDK is framework-agnostic (no FastAPI, no auth-sdk, no pydantic-settings) and
**imgtools-free**. It owns no business logic, no database, and no preset knowledge —
callers pass an explicit config object; the SDK never reads settings or env.

## Contents

### Object storage — `media_sdk_m8.storage`

`ObjectStorage` is a thin wrapper over the MinIO SDK. It is constructed from an
explicit `ObjectStorageConfig` (endpoint, credentials, region, TLS, and the default
presigned-URL lifetime), so it has no dependency on any service's settings module.

```python
from media_sdk_m8 import ObjectStorage, ObjectStorageConfig

config = ObjectStorageConfig(
    endpoint="minio:9000",
    access_key="...",
    secret_key="...",
    secure=False,
    region="us-east-1",
    presigned_expire_seconds=300,
)
storage = ObjectStorage(config)

data = storage.get_object(bucket="private-media", object_key="key")
storage.put_object(
    bucket="public-media",
    object_key="key/variants/thumb_webp/thumb.webp",
    data=variant_bytes,
    content_type="image/webp",
)
```

Methods: `stat_object`, `remove_object`, `get_object_head`, `get_object`,
`list_object_keys`, `put_object`, `set_object_content_type`, `copy_object`,
`post_upload_url`, `presigned_post_object`, `presigned_get_object`.

`list_object_keys(*, bucket, prefix="")` recursively yields stored keys — the
read primitive an orphan reconciler uses to find bytes that have no DB row.

Presigned-URL expiry defaults to `config.presigned_expire_seconds` and can be
overridden per call via `expires_seconds`.

#### Browser-direct presigned URLs (public endpoint)

When the browser cannot resolve the internal MinIO host (e.g. `minio:9000` in a
container stack), set the two optional endpoint fields:

```python
config = ObjectStorageConfig(
    endpoint="minio:9000",       # internal — service and worker only
    access_key="...",
    secret_key="...",
    secure=False,
    region="us-east-1",
    public_endpoint="127.0.0.1:9005",  # host:port, no scheme — browser-reachable
    public_secure=False,               # scheme for public URLs; falls back to `secure` when None
)
```

- `public_endpoint` — the host the browser hits, in `host:port` form (no scheme).
  `post_upload_url` returns a URL built from this host/scheme; `presigned_get_object`
  is signed by a client bound to this endpoint (SigV4 GET signatures bind the Host,
  so the signing client must match the endpoint the browser sends the request to).
- `public_secure` — TLS flag for the public endpoint. Falls back to `secure` when
  omitted.

All internal ops (`stat_object`, `get_object`, `copy_object`, etc.) always use
`endpoint`, not `public_endpoint`. When `public_endpoint` is `None` (default),
`post_upload_url` and `presigned_get_object` behave identically to before —
compatible with proxy-through deployments and `media-worker-m8`.

**Reverse-proxy requirement:** a proxy (e.g. Traefik) forwarding requests to MinIO
must preserve the Host header (`passHostHeader: true` in Traefik, which is its
default) so the SigV4 signature validates on arrival.

### Job contracts — `media_sdk_m8.contracts`

Self-contained Pydantic v2 models that form the producer↔consumer contract. The
service builds and enqueues them; the worker deserializes and acts on them.

- `ScanJobPayload` — `{ object_id, bucket, object_key, owner_user_id }`
- `VariantSpec` — `{ variant_name, output_options, target_bucket, target_key }`;
  `output_options` is the imgtools-shaped dict (one format + `name`) built by the
  service, so the worker needs no preset or key knowledge.
- `VariantJobPayload` — `{ job_id, media_object_id, source_bucket, source_object_key, specs }`
- `OutboxEventPayload` — `{ event_id, event_type, object_id, payload, created_at }`;
  the outbound webhook contract. media-service-m8 writes one per state change to
  its transactional outbox and POSTs this HMAC-signed body to subscriber URLs, so
  a subscriber needs only this shape to verify and consume an event.

## Development

```bash
pip install -e ".[dev]"
ruff format . && ruff check .
mypy media_sdk_m8 --ignore-missing-imports
bandit -r media_sdk_m8 --severity-level medium
pytest --cov=media_sdk_m8 --cov-report=term-missing --cov-fail-under=100
```

Published to PyPI on tagged release.
