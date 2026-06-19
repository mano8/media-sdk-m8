"""
Settings-agnostic MinIO client boundary for the shared media SDK.

The consuming service (media-service-m8) or worker (media-worker-m8) builds an
:class:`ObjectStorageConfig` from its own environment and passes it in
explicitly — the SDK never reads settings or env on its own.
"""

import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Fallback lifetime (seconds) for presigned URLs when a caller omits one.
DEFAULT_PRESIGNED_EXPIRE_SECONDS = 300

#: Default chunk size (bytes) for :meth:`ObjectStorage.stream_object` — 1 MiB.
DEFAULT_STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ObjectStorageConfig:
    """Connection settings for S3-compatible object storage."""

    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    region: str
    presigned_expire_seconds: int = DEFAULT_PRESIGNED_EXPIRE_SECONDS


def get_minio_client(config: ObjectStorageConfig) -> Any:
    """Create a MinIO SDK client from an explicit config."""
    from minio import Minio

    return Minio(
        endpoint=config.endpoint,
        access_key=config.access_key,
        secret_key=config.secret_key,
        secure=config.secure,
        region=config.region,
    )


class ObjectStorage:
    """Small wrapper around MinIO operations shared across media services."""

    def __init__(self, config: ObjectStorageConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = client or get_minio_client(config)

    def stat_object(self, *, bucket: str, object_key: str) -> Any:
        """Return object metadata from storage."""
        return self.client.stat_object(bucket, object_key)

    def remove_object(self, *, bucket: str, object_key: str) -> None:
        """Remove an object from storage."""
        self.client.remove_object(bucket, object_key)

    def get_object_head(
        self, *, bucket: str, object_key: str, length: int = 512
    ) -> bytes:
        """Read the first *length* bytes of an object for content-type sniffing."""
        response = self.client.get_object(bucket, object_key, offset=0, length=length)
        try:
            return response.read(length)
        finally:
            response.close()
            response.release_conn()

    def get_object(self, *, bucket: str, object_key: str) -> bytes:
        """Download an entire object and return its raw bytes."""
        response = self.client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def stream_object(
        self,
        *,
        bucket: str,
        object_key: str,
        chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE,
    ) -> Iterator[bytes]:
        """
        Yield an object's bytes in chunks of *chunk_size* without buffering it whole.

        The streaming read primitive a consumer needs to hash or scan a large
        (size-capped) object incrementally — the full payload is never
        materialised in memory at once. The connection is held open for the
        lifetime of the iterator and released when iteration finishes or the
        generator is closed.
        """
        response = self.client.get_object(bucket, object_key)
        try:
            yield from response.stream(chunk_size)
        finally:
            response.close()
            response.release_conn()

    def list_object_keys(self, *, bucket: str, prefix: str = "") -> Iterator[str]:
        """
        Yield every object key in *bucket* (optionally under *prefix*).

        Recursive listing of the storage keyspace — the read primitive an
        orphan reconciler needs to find bytes that have no DB row (a key the
        service never recorded). Streamed lazily so a large bucket is not
        materialised in memory.
        """
        for obj in self.client.list_objects(bucket, prefix=prefix, recursive=True):
            yield obj.object_name

    def put_object(
        self, *, bucket: str, object_key: str, data: bytes, content_type: str
    ) -> Any:
        """Write raw bytes to storage (e.g. a generated image variant)."""
        return self.client.put_object(
            bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def set_object_content_type(
        self, *, bucket: str, object_key: str, content_type: str
    ) -> Any:
        """
        Rewrite an object's stored ``Content-Type`` in place.

        The presigned PUT lets the client choose the ``Content-Type`` sent to
        storage, and for public-read buckets that type is served verbatim on
        direct access. Forcing the server-validated type here (via a metadata-
        only server-side copy) prevents a client from having an object served
        as an active type — e.g. ``text/html`` declared as ``text/plain`` —
        regardless of what it sent on upload. Returns the write result so the
        caller can pick up the authoritative post-copy etag.
        """
        from minio.commonconfig import REPLACE, CopySource

        return self.client.copy_object(
            bucket,
            object_key,
            CopySource(bucket, object_key),
            metadata={"Content-Type": content_type},
            metadata_directive=REPLACE,
        )

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object_key: str,
        dest_bucket: str,
        dest_object_key: str,
    ) -> Any:
        """
        Server-side copy an object to another bucket/key.

        Used to relocate bytes when an object's visibility changes and it must
        move between the public/private/sensitive buckets. Returns the write
        result so the caller can pick up the post-copy etag.
        """
        from minio.commonconfig import CopySource

        return self.client.copy_object(
            dest_bucket,
            dest_object_key,
            CopySource(src_bucket, src_object_key),
        )

    def post_upload_url(self, *, bucket: str) -> str:
        """
        Return the POST endpoint URL for a bucket (path-style addressing).

        ``presigned_post_policy`` only returns the signed form fields, not the
        target URL; MinIO uses path-style addressing, so the form is POSTed to
        ``{scheme}://{host}:{port}/{bucket}``.
        """
        scheme = "https" if self.config.secure else "http"
        return f"{scheme}://{self.config.endpoint}/{bucket}"

    def presigned_post_object(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        max_size_bytes: int,
        min_size_bytes: int = 1,
        expires_seconds: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        """
        Generate a presigned POST policy that constrains size and content-type.

        Unlike a presigned PUT — which lets the client write an object of any
        size and any ``Content-Type`` — an S3 POST policy is enforced by storage
        at upload time: the ``content-length-range`` and exact ``Content-Type``
        conditions cause MinIO to reject an oversized or wrong-typed body
        *before* it lands, closing the window in which garbage occupies a bucket
        until ``complete`` rejects it.

        Returns the POST URL and the form fields the client must submit
        alongside the ``file`` part (the ``key`` and ``Content-Type`` fields are
        pinned to the values the policy was signed for).
        """
        from minio.datatypes import PostPolicy

        expires = expires_seconds or self.config.presigned_expire_seconds
        expiration = datetime.now(UTC) + timedelta(seconds=expires)
        policy = PostPolicy(bucket, expiration)
        policy.add_equals_condition("key", object_key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(min_size_bytes, max_size_bytes)
        fields = self.client.presigned_post_policy(policy)
        # The policy only signs the conditions; echo the pinned values back so
        # the client submits them verbatim (any deviation fails the signature).
        fields["key"] = object_key
        fields["Content-Type"] = content_type
        return self.post_upload_url(bucket=bucket), fields

    def presigned_get_object(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_seconds: int | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> str:
        """Generate a presigned GET URL."""
        expires = timedelta(
            seconds=expires_seconds or self.config.presigned_expire_seconds
        )
        return self.client.presigned_get_object(
            bucket,
            object_key,
            expires=expires,
            response_headers=response_headers,
        )
