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
    DEFAULT_MULTIPART_CHUNK_SIZE,
    DEFAULT_MULTIPART_THRESHOLD,
    DEFAULT_PRESIGNED_EXPIRE_SECONDS,
    DEFAULT_STREAM_CHUNK_SIZE,
    ObjectStat,
    ObjectStorage,
    ObjectStorageConfig,
    ObjectWriteResult,
    S3StorageConfig,
    get_minio_client,
    get_s3_client,
)

__all__ = [
    "DEFAULT_MULTIPART_CHUNK_SIZE",
    "DEFAULT_MULTIPART_THRESHOLD",
    "DEFAULT_PRESIGNED_EXPIRE_SECONDS",
    "DEFAULT_STREAM_CHUNK_SIZE",
    "ExportArchiveEntry",
    "ExportArchiveJobPayload",
    "ObjectStat",
    "ObjectStorage",
    "ObjectStorageConfig",
    "ObjectWriteResult",
    "OutboxEventPayload",
    "S3StorageConfig",
    "ScanJobPayload",
    "VariantJobPayload",
    "VariantSpec",
    "get_minio_client",
    "get_s3_client",
]
