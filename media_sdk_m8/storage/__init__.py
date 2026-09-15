"""Object-storage primitives for the shared media SDK."""

from media_sdk_m8.storage.client import (
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
    "ObjectStat",
    "ObjectStorage",
    "ObjectStorageConfig",
    "ObjectWriteResult",
    "S3StorageConfig",
    "get_minio_client",
    "get_s3_client",
]
