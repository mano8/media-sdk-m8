"""
media-sdk-m8 — shared, settings-agnostic media primitives.

Exposes the object-storage client and the producer↔consumer job contracts used
by media-service-m8 (producer) and media-worker-m8 (consumer).
"""

from media_sdk_m8.contracts import (
    ExportArchiveEntry,
    ExportArchiveJobPayload,
    OutboxEventPayload,
    ScanJobPayload,
    VariantJobPayload,
    VariantSpec,
)
from media_sdk_m8.storage import (
    DEFAULT_PRESIGNED_EXPIRE_SECONDS,
    ObjectStorage,
    ObjectStorageConfig,
    get_minio_client,
)

__all__ = [
    "DEFAULT_PRESIGNED_EXPIRE_SECONDS",
    "ExportArchiveEntry",
    "ExportArchiveJobPayload",
    "ObjectStorage",
    "ObjectStorageConfig",
    "OutboxEventPayload",
    "ScanJobPayload",
    "VariantJobPayload",
    "VariantSpec",
    "get_minio_client",
]
