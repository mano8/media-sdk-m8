"""
OP-01 - OP-13: every S3 operation the media stack actually issues.

One test per row of the contract's surface table, run against a live pinned
backend. A backend that cannot pass this module cannot host the stack, so each
assertion checks the *requirement* recorded in the row — not merely that the
call returned without raising.

All calls go through the scoped ``media-rw`` identity; the root handle appears
only where a case genuinely needs an operation the application never performs.
"""

import hashlib
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

import pytest

from media_sdk_m8 import ObjectStorage

from . import probe
from .backends import BackendUnderTest

pytestmark = pytest.mark.conformance


def _put(
    backend: BackendUnderTest,
    bucket: str,
    object_key: str,
    content: bytes,
    content_type: str = "image/png",
) -> None:
    """Store *content* with the scoped identity."""
    backend.storage.put_object(
        bucket=bucket,
        object_key=object_key,
        data=content,
        content_type=content_type,
    )


# -- OP-01 ------------------------------------------------------------------


def test_post_object_accepts_conforming_upload(
    backend: BackendUnderTest, object_key: str
) -> None:
    """A POST that satisfies every signed condition is accepted and stored."""
    bucket = backend.buckets.temp
    content = probe.payload(4096)
    url, fields = backend.storage.presigned_post_object(
        bucket=bucket,
        object_key=object_key,
        content_type="image/png",
        max_size_bytes=1_000_000,
        min_size_bytes=16,
    )
    assert fields["key"] == object_key
    assert fields["Content-Type"] == "image/png"

    response = probe.post_multipart(
        url, fields, content=content, content_type="image/png"
    )
    assert response.status in (200, 201, 204), response.text()

    stat = backend.storage.stat_object(bucket=bucket, object_key=object_key)
    assert stat.size == len(content)
    assert stat.content_type == "image/png"


# -- OP-02 ------------------------------------------------------------------


def test_presigned_get_returns_object(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    A presigned GET returns the object, and its signature is bound to the Host.

    The Host binding is what forces ``passHostHeader: true`` on the Traefik
    router (S6), so it is proved here rather than assumed: a URL signed for one
    host must not validate when replayed against another.
    """
    bucket = backend.buckets.private
    content = probe.payload(2048)
    _put(backend, bucket, object_key, content)

    url = backend.storage.presigned_get_object(bucket=bucket, object_key=object_key)
    response = probe.request(url)
    assert response.status == 200, response.text()
    assert response.body == content

    foreign = ObjectStorage(
        replace(
            backend.storage.config,
            public_endpoint="storage.example:9000",
            public_secure=False,
        )
    )
    signed_elsewhere = foreign.presigned_get_object(
        bucket=bucket, object_key=object_key
    )
    parts = urlsplit(signed_elsewhere)
    assert parts.netloc == "storage.example:9000"
    replayed = probe.request(
        urlunsplit(("http", backend.endpoint, parts.path, parts.query, ""))
    )
    assert replayed.status == 403, (
        "a presigned GET signed for another host validated here: SigV4 is not "
        f"binding Host, so S6 cannot hold. Got {replayed.status}"
    )


# -- OP-03 ------------------------------------------------------------------


def test_ranged_get_returns_exact_prefix(
    backend: BackendUnderTest, object_key: str
) -> None:
    """``get_object_head`` returns exactly the first 512 bytes, and no more."""
    bucket = backend.buckets.private
    content = probe.payload(64 * 1024)
    _put(backend, bucket, object_key, content)

    head = backend.storage.get_object_head(
        bucket=bucket, object_key=object_key, length=512
    )
    assert len(head) == 512
    assert head == content[:512]


# -- OP-04 ------------------------------------------------------------------


def test_streamed_get_reassembles_object(
    backend: BackendUnderTest, object_key: str
) -> None:
    """A chunked read yields more than one chunk and reassembles byte-exactly."""
    bucket = backend.buckets.private
    content = probe.payload(2_500_000)
    _put(backend, bucket, object_key, content, content_type="application/octet-stream")

    chunks = list(
        backend.storage.stream_object(
            bucket=bucket, object_key=object_key, chunk_size=1024 * 1024
        )
    )
    assert len(chunks) > 1, "a 2.4 MiB object read in 1 MiB chunks came back whole"
    assert b"".join(chunks) == content


# -- OP-05 ------------------------------------------------------------------


def test_head_object_returns_opaque_etag(
    backend: BackendUnderTest, multipart_object: tuple[str, bytes]
) -> None:
    """
    HeadObject reports size and content-type, and the etag stays opaque.

    The stack stores the etag without interpreting it. Proving that here means
    showing a multipart object's etag is *not* the payload's MD5 — any code
    that assumed otherwise would break on this very object.
    """
    key, content = multipart_object
    stat = backend.storage.stat_object(bucket=backend.buckets.archive, object_key=key)

    assert stat.size == len(content)
    assert stat.content_type == "application/octet-stream"
    etag = str(stat.etag).strip('"')
    assert etag, "HeadObject returned no etag"
    assert etag != hashlib.md5(content, usedforsecurity=False).hexdigest(), (
        "a multipart object's etag equals its MD5; the harness would be "
        "measuring a single-part upload"
    )


# -- OP-06 ------------------------------------------------------------------


def test_put_object_roundtrips_bytes(
    backend: BackendUnderTest, object_key: str
) -> None:
    """PutObject stores the exact bytes and the declared content-type."""
    bucket = backend.buckets.public
    content = probe.payload(8192)
    _put(backend, bucket, object_key, content, content_type="image/png")

    assert backend.storage.get_object(bucket=bucket, object_key=object_key) == content
    stat = backend.storage.stat_object(bucket=bucket, object_key=object_key)
    assert stat.content_type == "image/png"
    assert stat.size == len(content)


# -- OP-07 ------------------------------------------------------------------


def test_put_object_stream_roundtrips_handle(
    backend: BackendUnderTest, object_key: str, tmp_path
) -> None:
    """PutObject from an open handle writes exactly *length* bytes."""
    bucket = backend.buckets.archive
    content = probe.payload(1_500_000)
    source = tmp_path / "export.zip"
    source.write_bytes(content)

    with source.open("rb") as handle:
        backend.storage.put_object_stream(
            bucket=bucket,
            object_key=object_key,
            data=handle,
            length=len(content),
            content_type="application/zip",
        )

    assert backend.storage.get_object(bucket=bucket, object_key=object_key) == content
    stat = backend.storage.stat_object(bucket=bucket, object_key=object_key)
    assert stat.content_type == "application/zip"


# -- OP-08 ------------------------------------------------------------------


def test_copy_replace_rewrites_content_type_in_place(
    backend: BackendUnderTest, object_key: str
) -> None:
    """A metadata-only self-copy with REPLACE rewrites the stored type."""
    bucket = backend.buckets.public
    content = probe.payload(4096)
    _put(backend, bucket, object_key, content, content_type="text/html")

    backend.storage.set_object_content_type(
        bucket=bucket, object_key=object_key, content_type="application/octet-stream"
    )

    stat = backend.storage.stat_object(bucket=bucket, object_key=object_key)
    assert stat.content_type == "application/octet-stream"
    assert backend.storage.get_object(bucket=bucket, object_key=object_key) == content


# -- OP-09 ------------------------------------------------------------------


def test_copy_object_across_buckets(backend: BackendUnderTest, object_key: str) -> None:
    """A visibility move copies bytes and type server-side, leaving the source."""
    source_bucket = backend.buckets.private
    target_bucket = backend.buckets.public
    content = probe.payload(4096)
    _put(backend, source_bucket, object_key, content, content_type="image/png")

    backend.storage.copy_object(
        src_bucket=source_bucket,
        src_object_key=object_key,
        dest_bucket=target_bucket,
        dest_object_key=object_key,
    )

    copied = backend.storage.stat_object(bucket=target_bucket, object_key=object_key)
    assert copied.size == len(content)
    assert copied.content_type == "image/png"
    assert (
        backend.storage.get_object(bucket=target_bucket, object_key=object_key)
        == content
    )
    assert backend.storage.stat_object(
        bucket=source_bucket, object_key=object_key
    ).size == len(content)


# -- OP-10 ------------------------------------------------------------------


def test_delete_object_removes_key(backend: BackendUnderTest, object_key: str) -> None:
    """A deleted key 404s on both the client's HeadObject and a presigned GET."""
    bucket = backend.buckets.temp
    content = probe.payload(1024)
    _put(backend, bucket, object_key, content)
    url = backend.storage.presigned_get_object(bucket=bucket, object_key=object_key)
    assert probe.request(url).status == 200

    backend.storage.remove_object(bucket=bucket, object_key=object_key)

    assert probe.request(url).status == 404
    with pytest.raises(Exception) as raised:
        backend.storage.stat_object(bucket=bucket, object_key=object_key)
    # A HEAD response carries no body, so botocore reports the HTTP status
    # where a body-carrying error would name the S3 code; both are the same
    # verdict from the server, and the presigned GET above already read it
    # off the wire.
    assert probe.s3_error_code(raised.value) in ("NoSuchKey", "NoSuchObject", "404")


# -- OP-11 ------------------------------------------------------------------


def test_list_object_keys_is_complete_and_paged(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    Recursive listing pages past 1000 keys and yields each key exactly once.

    The orphan reconciler treats a key's absence from this listing as "these
    bytes do not exist", so an incomplete page is a data-loss bug, not a
    performance detail. 1001 keys is the smallest count that forces a second
    ListObjectsV2 page.
    """
    bucket = backend.buckets.temp
    prefix = f"listing/{object_key.rsplit('/', 1)[-1]}/"
    expected = {f"{prefix}{index:05d}.bin" for index in range(1001)}
    for key in sorted(expected):
        backend.storage.put_object(
            bucket=bucket,
            object_key=key,
            data=b"x",
            content_type="application/octet-stream",
        )

    listed = list(backend.storage.list_object_keys(bucket=bucket, prefix=prefix))
    assert len(listed) == len(set(listed)), "a key was listed more than once"
    assert set(listed) == expected


# -- OP-12 ------------------------------------------------------------------


def test_head_bucket_reports_presence(backend: BackendUnderTest) -> None:
    """
    HeadBucket answers present/absent without raising.

    The service health check reports DEGRADED rather than FAIL on a missing
    bucket, which only works if absence is a return value. The absent-bucket
    probe uses the root handle deliberately: the scoped credential is *not*
    entitled to answer for a bucket outside its policy, and that denial is
    S4 working, not a HeadBucket failure.
    """
    for bucket in backend.buckets.as_tuple():
        assert backend.storage.bucket_exists(bucket=bucket) is True

    assert backend.admin.bucket_exists(bucket="no-such-media-bucket") is False


# -- OP-13 ------------------------------------------------------------------


def test_multipart_upload_roundtrips(
    backend: BackendUnderTest, multipart_object: tuple[str, bytes]
) -> None:
    """A payload above the part size uploads multipart and reads back exactly."""
    key, content = multipart_object
    bucket = backend.buckets.archive

    assert backend.storage.get_object(bucket=bucket, object_key=key) == content

    streamed = b"".join(
        backend.storage.stream_object(
            bucket=bucket, object_key=key, chunk_size=1024 * 1024
        )
    )
    assert streamed == content
