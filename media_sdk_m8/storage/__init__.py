"""Object-storage primitives for the shared media SDK."""

from media_sdk_m8.storage.client import (
    DEFAULT_PRESIGNED_EXPIRE_SECONDS,
    ObjectStorage,
    ObjectStorageConfig,
    get_minio_client,
)

__all__ = [
    "DEFAULT_PRESIGNED_EXPIRE_SECONDS",
    "ObjectStorage",
    "ObjectStorageConfig",
    "get_minio_client",
]
