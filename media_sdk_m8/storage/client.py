"""
Settings-agnostic S3 client boundary for the shared media SDK.

The consuming service (media-service-m8) or worker (media-worker-m8) builds an
:class:`S3StorageConfig` from its own environment and passes it in explicitly —
the SDK never reads settings or env on its own.

The wire contract is the Amazon S3 API (SigV4, path-style addressing); the
implementation behind it is a deployment choice, never a code dependency. The
internals speak boto3/botocore because that is the client every S3-compatible
server is tested against, which removes a class of "works on one server, subtly
wrong on another" risk from signing, POST-policy field encoding and response
header overrides.
"""

import io
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import IO, Any, Final

#: Fallback lifetime (seconds) for presigned URLs when a caller omits one.
DEFAULT_PRESIGNED_EXPIRE_SECONDS = 300

#: Default chunk size (bytes) for :meth:`ObjectStorage.stream_object` — 1 MiB.
DEFAULT_STREAM_CHUNK_SIZE = 1024 * 1024

#: Payload size (bytes) above which a write is sent as a multipart upload.
#: 5 MiB is the S3 minimum part size, and it is also the boundary the previous
#: client used, so the single-part/multipart split stays where it was.
DEFAULT_MULTIPART_THRESHOLD = 5 * 1024 * 1024

#: Size (bytes) of every part but the last in a multipart upload.
DEFAULT_MULTIPART_CHUNK_SIZE = 5 * 1024 * 1024

# Characters that must not appear in a bare host/host:port public endpoint.
# Their presence indicates an accidentally-passed full URL or embedded credentials.
_PUBLIC_ENDPOINT_FORBIDDEN = ("://", "@", "#", "?")

#: S3 response-override query parameters, keyed by the wire spelling callers
#: pass to :meth:`ObjectStorage.presigned_get_object`. An unknown name is a
#: caller bug and is refused rather than silently dropped from the signature —
#: a dropped ``response-content-disposition`` is stored XSS (`S11`).
_RESPONSE_OVERRIDE_PARAMS: Final[Mapping[str, str]] = {
    "response-cache-control": "ResponseCacheControl",
    "response-content-disposition": "ResponseContentDisposition",
    "response-content-encoding": "ResponseContentEncoding",
    "response-content-language": "ResponseContentLanguage",
    "response-content-type": "ResponseContentType",
    "response-expires": "ResponseExpires",
}


def _validate_public_endpoint(endpoint: str) -> None:
    """
    Reject malformed bare-host public endpoint strings.

    ``public_endpoint`` is a ``host`` or ``host:port`` string (no scheme).
    Scheme, userinfo, fragment, and query components must never appear here —
    they indicate an accidentally-passed full URL or embedded credentials that
    would corrupt presigned URL construction.
    """
    if not endpoint.strip():
        raise ValueError("public_endpoint must not be empty")
    for pat in _PUBLIC_ENDPOINT_FORBIDDEN:
        if pat in endpoint:
            raise ValueError(
                f"public_endpoint must be a bare host or host:port with no scheme, "
                f"credentials, or query components; got {endpoint!r} (contains {pat!r})"
            )


@dataclass(frozen=True)
class S3StorageConfig:
    """Connection settings for S3-compatible object storage."""

    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    region: str
    presigned_expire_seconds: int = DEFAULT_PRESIGNED_EXPIRE_SECONDS
    #: Optional browser-reachable endpoint (``host:port``, no scheme) used **only**
    #: when minting presigned URLs. Leave ``None`` to sign every URL for the
    #: internal ``endpoint`` (current behaviour, e.g. proxy-through deployments).
    public_endpoint: str | None = None
    #: TLS setting for ``public_endpoint``; falls back to ``secure`` when ``None``.
    public_secure: bool | None = None

    def __post_init__(self) -> None:
        if self.public_endpoint is not None:
            _validate_public_endpoint(self.public_endpoint)

    @property
    def endpoint_url(self) -> str:
        """Return the internal endpoint as a scheme-qualified URL."""
        return f"{'https' if self.secure else 'http'}://{self.endpoint}"


#: Historical name for :class:`S3StorageConfig`. Kept so consumers pinned to an
#: older SDK keep importing successfully across the client swap.
ObjectStorageConfig = S3StorageConfig


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """Object metadata as :meth:`ObjectStorage.stat_object` reports it."""

    bucket_name: str
    object_name: str
    #: Server-assigned entity tag with its surrounding quotes stripped. Opaque:
    #: it is a payload MD5 only for some single-part uploads on some servers,
    #: so nothing in the stack may derive a checksum from it.
    etag: str
    size: int
    content_type: str | None
    last_modified: datetime | None
    #: Every response header, lower-cased, as the server returned it.
    metadata: Mapping[str, str]
    version_id: str | None


@dataclass(frozen=True, slots=True)
class ObjectWriteResult:
    """Result of a write (``PutObject``/``CopyObject``/multipart complete)."""

    bucket_name: str
    object_name: str
    #: Post-write entity tag, quotes stripped. See :attr:`ObjectStat.etag`.
    etag: str
    version_id: str | None


class _BoundedReader:
    """
    Read at most *limit* bytes from *stream*, starting at its current position.

    The caller owns the handle and states the exact byte count, so a handle
    holding more bytes than the caller declared must not have the surplus
    uploaded.

    Rewinding is delegated rather than merely tolerated: over a plain-HTTP
    endpoint botocore signs the payload, which means hashing the body once and
    then seeking back to send it. A reader that could not rewind would force
    payload signing off — a weaker signature than the previous client
    produced — so the position bookkeeping here is what keeps the signature
    covering the bytes.
    """

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._remaining = limit
        self._start: int | None = None
        if getattr(stream, "seekable", None) is not None and stream.seekable():
            self._start = stream.tell()

    def seekable(self) -> bool:
        """Return whether the underlying stream can be rewound."""
        return self._start is not None

    def tell(self) -> int:
        """Return the underlying stream's absolute position."""
        return self._stream.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        """Move the underlying stream and re-derive the remaining allowance."""
        position = self._stream.seek(offset, whence)
        start = 0 if self._start is None else self._start
        self._remaining = max(self._limit - (position - start), 0)
        return position

    def read(self, size: int = -1) -> bytes:
        """Return up to *size* bytes, never crossing the declared limit."""
        if self._remaining <= 0:
            return b""
        want = self._remaining if size < 0 else min(size, self._remaining)
        chunk = self._stream.read(want)
        self._remaining -= len(chunk)
        return chunk


def _read_exactly(reader: _BoundedReader, size: int) -> bytes:
    """Return exactly *size* bytes from *reader*, or fewer at end of stream."""
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _unquote_etag(etag: str | None) -> str:
    """Return an entity tag without the quotes S3 wraps it in."""
    return (etag or "").replace('"', "")


def _http_status(error: BaseException) -> int:
    """Return the HTTP status a botocore ``ClientError`` carries, or 0."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        metadata = response.get("ResponseMetadata", {})
        if isinstance(metadata, dict):
            return int(metadata.get("HTTPStatusCode", 0))
    return 0


def get_s3_client(config: S3StorageConfig) -> Any:
    """
    Create a boto3 S3 client from an explicit config.

    Everything that makes the client provider-neutral is pinned here rather
    than left to boto3's AWS-shaped defaults:

    * ``signature_version="s3v4"`` — the contract is SigV4, never SigV2.
    * path-style addressing — a self-hosted endpoint has no virtual-host DNS.
    * checksums only ``when_required`` — botocore otherwise sends an
      ``x-amz-checksum-*`` trailer on every upload, which several
      S3-compatible servers reject outright. The stack has never depended on
      those trailers, so switching them off keeps one wire format for every
      backend instead of one per server.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def get_minio_client(config: S3StorageConfig) -> Any:
    """Deprecated alias of :func:`get_s3_client`, kept for existing callers."""
    return get_s3_client(config)


class ObjectStorage:
    """Small wrapper around the S3 operations shared across media services."""

    def __init__(self, config: S3StorageConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = client or get_s3_client(config)
        # Presigning is the only surface that must point at the browser-reachable
        # host. When no public endpoint is configured the presign client *is* the
        # internal client (byte-identical behaviour); otherwise it is a second
        # client bound to the public endpoint, sharing the same creds/region.
        if config.public_endpoint is None:
            self._presign_client = self.client
        else:
            self._presign_client = get_s3_client(self._presign_target)

    @property
    def _presign_target(self) -> S3StorageConfig:
        """Config whose endpoint/scheme presigned URLs are signed for."""
        public_endpoint = self.config.public_endpoint
        if public_endpoint is None:
            return self.config
        public_secure = self.config.public_secure
        secure = self.config.secure if public_secure is None else public_secure
        return replace(self.config, endpoint=public_endpoint, secure=secure)

    def stat_object(self, *, bucket: str, object_key: str) -> ObjectStat:
        """Return object metadata from storage."""
        response = self.client.head_object(Bucket=bucket, Key=object_key)
        headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
        return ObjectStat(
            bucket_name=bucket,
            object_name=object_key,
            etag=_unquote_etag(response.get("ETag")),
            size=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            last_modified=response.get("LastModified"),
            metadata=headers,
            version_id=response.get("VersionId"),
        )

    def bucket_exists(self, *, bucket: str) -> bool:
        """
        Return whether *bucket* exists, without raising on absence.

        Absence is a return value rather than an exception so the service
        health check can report DEGRADED instead of FAIL. A refusal is not an
        absence: a 403 means the credential may not answer for this bucket and
        is re-raised, because reporting "missing" there would hide a
        misconfigured grant behind a health warning.
        """
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as error:
            if _http_status(error) == 404:
                return False
            raise
        return True

    def remove_object(self, *, bucket: str, object_key: str) -> None:
        """Remove an object from storage."""
        self.client.delete_object(Bucket=bucket, Key=object_key)

    def get_object_head(
        self, *, bucket: str, object_key: str, length: int = 512
    ) -> bytes:
        """Read the first *length* bytes of an object for content-type sniffing."""
        response = self.client.get_object(
            Bucket=bucket, Key=object_key, Range=f"bytes=0-{length - 1}"
        )
        body = response["Body"]
        try:
            return bytes(body.read(length))
        finally:
            body.close()

    def get_object(self, *, bucket: str, object_key: str) -> bytes:
        """Download an entire object and return its raw bytes."""
        response = self.client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()

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
        response = self.client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def list_object_keys(self, *, bucket: str, prefix: str = "") -> Iterator[str]:
        """
        Yield every object key in *bucket* (optionally under *prefix*).

        Recursive listing of the storage keyspace — the read primitive an
        orphan reconciler needs to find bytes that have no DB row (a key the
        service never recorded). Streamed lazily so a large bucket is not
        materialised in memory, and paged to the end: the reconciler reads a
        key's absence here as "these bytes do not exist", so a truncated
        listing would be a data-loss bug rather than a performance detail.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def put_object(
        self, *, bucket: str, object_key: str, data: bytes, content_type: str
    ) -> ObjectWriteResult:
        """Write raw bytes to storage (e.g. a generated image variant)."""
        return self._write(
            bucket=bucket,
            object_key=object_key,
            reader=_BoundedReader(io.BytesIO(data), len(data)),
            length=len(data),
            content_type=content_type,
        )

    def put_object_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        data: IO[bytes],
        length: int,
        content_type: str,
    ) -> ObjectWriteResult:
        """
        Write *length* bytes from an open file-like object without buffering them.

        The write-side counterpart of :meth:`stream_object`. :meth:`put_object`
        takes ``bytes``, which is right for a generated image variant but wrong
        for a payload a consumer has already assembled on disk — an archive
        export, say — where taking ``bytes`` would force the whole payload
        resident in memory just to hand it over. *data* is read incrementally
        from its current position; the caller owns the handle and its lifetime,
        and must pass the exact byte count.
        """
        return self._write(
            bucket=bucket,
            object_key=object_key,
            reader=_BoundedReader(data, length),
            length=length,
            content_type=content_type,
        )

    def _write(
        self,
        *,
        bucket: str,
        object_key: str,
        reader: _BoundedReader,
        length: int,
        content_type: str,
    ) -> ObjectWriteResult:
        """Send *length* bytes as a single PUT, or multipart above the threshold."""
        if length > DEFAULT_MULTIPART_THRESHOLD:
            return self._write_multipart(
                bucket=bucket,
                object_key=object_key,
                reader=reader,
                content_type=content_type,
            )
        # A single PUT is signed over its payload, so the body must be
        # re-readable. A non-seekable handle is buffered instead — bounded by
        # the multipart threshold, and the same thing the previous client did
        # with a single-part stream.
        body: Any = reader if reader.seekable() else io.BytesIO(reader.read())
        response = self.client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=body,
            ContentLength=length,
            ContentType=content_type,
        )
        return ObjectWriteResult(
            bucket_name=bucket,
            object_name=object_key,
            etag=_unquote_etag(response.get("ETag")),
            version_id=response.get("VersionId"),
        )

    def _write_multipart(
        self,
        *,
        bucket: str,
        object_key: str,
        reader: _BoundedReader,
        content_type: str,
    ) -> ObjectWriteResult:
        """
        Upload sequentially in parts, aborting the upload if any part fails.

        The abort is not tidiness: parts of an incomplete multipart upload
        occupy storage that no bucket listing shows, so leaving them behind on
        failure is an invisible leak the orphan reconciler cannot find.
        """
        started = self.client.create_multipart_upload(
            Bucket=bucket, Key=object_key, ContentType=content_type
        )
        upload_id = started["UploadId"]
        parts: list[dict[str, Any]] = []
        try:
            while True:
                chunk = _read_exactly(reader, DEFAULT_MULTIPART_CHUNK_SIZE)
                if not chunk:
                    break
                part = self.client.upload_part(
                    Bucket=bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    PartNumber=len(parts) + 1,
                    Body=chunk,
                )
                parts.append({"ETag": part["ETag"], "PartNumber": len(parts) + 1})
            completed = self.client.complete_multipart_upload(
                Bucket=bucket,
                Key=object_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            self.client.abort_multipart_upload(
                Bucket=bucket, Key=object_key, UploadId=upload_id
            )
            raise
        return ObjectWriteResult(
            bucket_name=bucket,
            object_name=object_key,
            etag=_unquote_etag(completed.get("ETag")),
            version_id=completed.get("VersionId"),
        )

    def set_object_content_type(
        self, *, bucket: str, object_key: str, content_type: str
    ) -> ObjectWriteResult:
        """
        Rewrite an object's stored ``Content-Type`` in place.

        Upload goes through a presigned S3 **POST policy**, not a bare
        presigned PUT: the policy's ``Content-Type`` condition already pins
        the value server-side at upload time, so storage rejects a mismatched
        type before the object ever lands. This method exists for the
        narrower case of correcting the stored type *after* the object is
        already written — e.g. a server-side MIME re-sniff — via a metadata-
        only server-side copy, so an object can never be served as an active
        type (e.g. ``text/html`` declared as ``text/plain``) regardless of
        what was pinned on upload. Returns the write result so the caller can
        pick up the authoritative post-copy etag.
        """
        response = self.client.copy_object(
            Bucket=bucket,
            Key=object_key,
            CopySource={"Bucket": bucket, "Key": object_key},
            MetadataDirective="REPLACE",
            ContentType=content_type,
        )
        return self._copy_result(bucket, object_key, response)

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object_key: str,
        dest_bucket: str,
        dest_object_key: str,
    ) -> ObjectWriteResult:
        """
        Server-side copy an object to another bucket/key.

        Used to relocate bytes when an object's visibility changes and it must
        move between the public/private/sensitive buckets. The metadata
        directive is left at its ``COPY`` default so the stored
        ``Content-Type`` travels with the bytes. Returns the write result so
        the caller can pick up the post-copy etag.
        """
        response = self.client.copy_object(
            Bucket=dest_bucket,
            Key=dest_object_key,
            CopySource={"Bucket": src_bucket, "Key": src_object_key},
        )
        return self._copy_result(dest_bucket, dest_object_key, response)

    @staticmethod
    def _copy_result(
        bucket: str, object_key: str, response: Mapping[str, Any]
    ) -> ObjectWriteResult:
        """Build the write result from a ``CopyObject`` response."""
        result = response.get("CopyObjectResult", {})
        return ObjectWriteResult(
            bucket_name=bucket,
            object_name=object_key,
            etag=_unquote_etag(result.get("ETag")),
            version_id=response.get("VersionId"),
        )

    def post_upload_url(self, *, bucket: str) -> str:
        """
        Return the POST endpoint URL for a bucket (path-style addressing).

        The signed form fields do not carry the target URL, and the client is
        configured for path-style addressing, so the form is POSTed to
        ``{scheme}://{host}:{port}/{bucket}``.

        A POST policy signs the conditions, **not** the host, so this only
        swaps the returned URL string to the public endpoint when one is set —
        the signature stays valid for the browser-direct POST.
        """
        target = self._presign_target
        return f"{target.endpoint_url}/{bucket}"

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
        conditions cause the server to reject an oversized or wrong-typed body
        *before* it lands, closing the window in which garbage occupies a bucket
        until ``complete`` rejects it.

        Returns the POST URL and the form fields the client must submit
        alongside the ``file`` part (the ``key`` and ``Content-Type`` fields are
        pinned to the values the policy was signed for).
        """
        expires = expires_seconds or self.config.presigned_expire_seconds
        signed = self.client.generate_presigned_post(
            Bucket=bucket,
            Key=object_key,
            Fields={"Content-Type": content_type},
            # A fresh list every call: botocore appends its own bucket and key
            # conditions to whatever it is handed.
            Conditions=[
                {"key": object_key},
                {"Content-Type": content_type},
                ["content-length-range", min_size_bytes, max_size_bytes],
            ],
            ExpiresIn=expires,
        )
        fields = dict(signed["fields"])
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
        """
        Generate a presigned GET URL.

        Unlike a POST policy, a SigV4 presigned GET **binds the Host**, so the
        URL must be signed by a client whose endpoint equals the host the
        browser hits — hence ``self._presign_client`` (the public endpoint when
        configured). Behind a reverse proxy the proxy **must preserve the Host
        header** (Traefik ``passHostHeader: true``, its default) or the
        signature will not validate.

        *response_headers* takes the S3 wire spellings of the response-override
        query parameters, e.g. ``{"response-content-disposition": ...}``.
        """
        expires = expires_seconds or self.config.presigned_expire_seconds
        params: dict[str, Any] = {"Bucket": bucket, "Key": object_key}
        for name, value in (response_headers or {}).items():
            override = _RESPONSE_OVERRIDE_PARAMS.get(name.lower())
            if override is None:
                raise ValueError(
                    f"unsupported response override {name!r}; expected one of "
                    f"{sorted(_RESPONSE_OVERRIDE_PARAMS)}"
                )
            params[override] = value
        return str(
            self._presign_client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=expires
            )
        )
