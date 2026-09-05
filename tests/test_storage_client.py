"""Tests for media_sdk_m8.storage.client.ObjectStorage."""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from media_sdk_m8.storage.client import (
    DEFAULT_MULTIPART_CHUNK_SIZE,
    DEFAULT_MULTIPART_THRESHOLD,
    DEFAULT_PRESIGNED_EXPIRE_SECONDS,
    DEFAULT_STREAM_CHUNK_SIZE,
    ObjectStorage,
    ObjectStorageConfig,
    S3StorageConfig,
    _BoundedReader,
    _http_status,
    _read_exactly,
    _unquote_etag,
    get_minio_client,
    get_s3_client,
)


def _config(
    *,
    secure: bool = False,
    expire: int = 120,
    public_endpoint: str | None = None,
    public_secure: bool | None = None,
) -> S3StorageConfig:
    return S3StorageConfig(
        endpoint="minio:9000",
        access_key="ak",
        secret_key="sk",
        secure=secure,
        region="us-east-1",
        presigned_expire_seconds=expire,
        public_endpoint=public_endpoint,
        public_secure=public_secure,
    )


def _storage(s3: MagicMock, **kw: object) -> ObjectStorage:
    return ObjectStorage(_config(**kw), client=s3)


def _client_error(status: int, code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadBucket",
    )


def _body(content: bytes) -> MagicMock:
    """Return a botocore StreamingBody-alike over *content*."""
    stream = MagicMock()
    stream.read.side_effect = io.BytesIO(content).read
    return stream


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_default_expiry():
    config = S3StorageConfig(
        endpoint="e", access_key="a", secret_key="s", secure=True, region="r"
    )
    assert config.presigned_expire_seconds == DEFAULT_PRESIGNED_EXPIRE_SECONDS


def test_object_storage_config_is_an_alias_of_s3_storage_config():
    """The historical name still resolves, so pinned consumers keep importing."""
    assert ObjectStorageConfig is S3StorageConfig


def test_endpoint_url_derives_scheme_from_secure():
    assert _config().endpoint_url == "http://minio:9000"
    assert _config(secure=True).endpoint_url == "https://minio:9000"


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def test_get_s3_client_pins_sigv4_path_style_and_lazy_checksums():
    boto3 = MagicMock()
    botocore = MagicMock()
    config_mod = MagicMock()
    with patch.dict(
        sys.modules,
        {"boto3": boto3, "botocore": botocore, "botocore.config": config_mod},
    ):
        get_s3_client(_config(secure=True))

    boto3.client.assert_called_once()
    (service,), kwargs = boto3.client.call_args
    assert service == "s3"
    assert kwargs["endpoint_url"] == "https://minio:9000"
    assert kwargs["aws_access_key_id"] == "ak"
    assert kwargs["aws_secret_access_key"] == "sk"
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"] is config_mod.Config.return_value

    _, boto_config = config_mod.Config.call_args
    assert boto_config["signature_version"] == "s3v4"
    assert boto_config["s3"] == {"addressing_style": "path"}
    # botocore would otherwise send an x-amz-checksum-* trailer on every
    # upload, which several S3-compatible servers refuse outright.
    assert boto_config["request_checksum_calculation"] == "when_required"
    assert boto_config["response_checksum_validation"] == "when_required"


def test_get_minio_client_is_a_deprecated_alias():
    with patch(
        "media_sdk_m8.storage.client.get_s3_client", return_value="client"
    ) as factory:
        assert get_minio_client(_config()) == "client"
    factory.assert_called_once()


def test_default_constructor_builds_an_s3_client():
    built = MagicMock()
    with patch(
        "media_sdk_m8.storage.client.get_s3_client", return_value=built
    ) as factory:
        storage = ObjectStorage(_config())
    factory.assert_called_once()
    assert storage.client is built


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_bounded_reader_never_crosses_the_declared_length():
    reader = _BoundedReader(io.BytesIO(b"0123456789"), 4)
    assert reader.read(3) == b"012"
    assert reader.read(3) == b"3"
    assert reader.read(3) == b""


def test_bounded_reader_reads_to_the_limit_when_size_is_negative():
    assert _BoundedReader(io.BytesIO(b"0123456789"), 4).read() == b"0123"


def test_bounded_reader_starts_at_the_current_position():
    handle = io.BytesIO(b"skipme-payload")
    handle.seek(6)
    assert _BoundedReader(handle, 8).read() == b"-payload"


class _WriteOnlyStream:
    """A stream that can only be read forward — a pipe, not a file."""

    def __init__(self, content: bytes) -> None:
        self._buffer = io.BytesIO(content)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def test_bounded_reader_rewinds_so_the_payload_can_be_signed():
    """botocore hashes the body, then seeks back to send it."""
    handle = io.BytesIO(b"skipme-payload-and-more")
    handle.seek(6)
    reader = _BoundedReader(handle, 8)
    assert reader.seekable() is True

    position = reader.tell()
    assert reader.read() == b"-payload"
    assert reader.read() == b""

    reader.seek(position)
    assert reader.read() == b"-payload"


def test_bounded_reader_over_a_non_seekable_stream_cannot_rewind():
    reader = _BoundedReader(_WriteOnlyStream(b"0123456789"), 4)
    assert reader.seekable() is False
    assert reader.read() == b"0123"


def test_read_exactly_reassembles_short_reads():
    reader = _BoundedReader(io.BytesIO(b"abcdefgh"), 8)
    with patch.object(reader, "read", side_effect=[b"abc", b"de", b"fgh", b""]):
        assert _read_exactly(reader, 8) == b"abcdefgh"


def test_read_exactly_stops_at_end_of_stream():
    assert _read_exactly(_BoundedReader(io.BytesIO(b"ab"), 8), 8) == b"ab"


def test_unquote_etag_strips_quotes_and_tolerates_absence():
    assert _unquote_etag('"abc"') == "abc"
    assert _unquote_etag(None) == ""


def test_http_status_reads_botocore_response_metadata():
    assert _http_status(_client_error(404, "404")) == 404


def test_http_status_of_a_foreign_exception_is_zero():
    assert _http_status(RuntimeError("boom")) == 0

    malformed = RuntimeError("boom")
    malformed.response = {"ResponseMetadata": "not-a-mapping"}  # type: ignore[attr-defined]
    assert _http_status(malformed) == 0


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_stat_object_maps_head_object_onto_the_stat_shape():
    s3 = MagicMock()
    s3.head_object.return_value = {
        "ETag": '"deadbeef"',
        "ContentLength": 4096,
        "ContentType": "image/png",
        "LastModified": "then",
        "VersionId": "v1",
        "ResponseMetadata": {"HTTPHeaders": {"etag": '"deadbeef"'}},
    }
    stat = _storage(s3).stat_object(bucket="b", object_key="k")
    s3.head_object.assert_called_once_with(Bucket="b", Key="k")
    assert stat.bucket_name == "b"
    assert stat.object_name == "k"
    # Quotes stripped, exactly as the previous client reported an etag.
    assert stat.etag == "deadbeef"
    assert stat.size == 4096
    assert stat.content_type == "image/png"
    assert stat.last_modified == "then"
    assert stat.version_id == "v1"
    assert stat.metadata == {"etag": '"deadbeef"'}


def test_stat_object_tolerates_a_sparse_head_response():
    s3 = MagicMock()
    s3.head_object.return_value = {}
    stat = _storage(s3).stat_object(bucket="b", object_key="k")
    assert stat.etag == ""
    assert stat.size == 0
    assert stat.content_type is None
    assert stat.last_modified is None
    assert stat.version_id is None
    assert stat.metadata == {}


def test_bucket_exists_true_for_a_present_bucket():
    s3 = MagicMock()
    assert _storage(s3).bucket_exists(bucket="public-media") is True
    s3.head_bucket.assert_called_once_with(Bucket="public-media")


def test_bucket_exists_false_on_404():
    """Absence is a return value — the health check reports DEGRADED, not FAIL."""
    s3 = MagicMock()
    s3.head_bucket.side_effect = _client_error(404, "404")
    assert _storage(s3).bucket_exists(bucket="missing") is False


def test_bucket_exists_reraises_a_refusal():
    """A 403 is a grant problem, not an absence; hiding it would mask S4."""
    s3 = MagicMock()
    s3.head_bucket.side_effect = _client_error(403, "AccessDenied")
    with pytest.raises(ClientError):
        _storage(s3).bucket_exists(bucket="forbidden")


def test_remove_object_deletes_the_key():
    s3 = MagicMock()
    _storage(s3).remove_object(bucket="b", object_key="k")
    s3.delete_object.assert_called_once_with(Bucket="b", Key="k")


def test_get_object_head_requests_a_byte_range_and_closes_the_body():
    s3 = MagicMock()
    body = _body(b"\x89PNG\r\n\x1a\n")
    s3.get_object.return_value = {"Body": body}
    result = _storage(s3).get_object_head(bucket="b", object_key="k")
    s3.get_object.assert_called_once_with(Bucket="b", Key="k", Range="bytes=0-511")
    body.close.assert_called_once()
    assert result == b"\x89PNG\r\n\x1a\n"


def test_get_object_head_custom_length():
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": _body(b"\xff\xd8")}
    _storage(s3).get_object_head(bucket="b", object_key="k", length=128)
    s3.get_object.assert_called_once_with(Bucket="b", Key="k", Range="bytes=0-127")


def test_get_object_reads_full_content():
    s3 = MagicMock()
    body = _body(b"full-bytes")
    s3.get_object.return_value = {"Body": body}
    result = _storage(s3).get_object(bucket="b", object_key="k")
    s3.get_object.assert_called_once_with(Bucket="b", Key="k")
    body.close.assert_called_once()
    assert result == b"full-bytes"


def test_stream_object_yields_chunks_and_releases():
    s3 = MagicMock()
    body = MagicMock()
    body.iter_chunks.return_value = iter([b"aa", b"bb", b"cc"])
    s3.get_object.return_value = {"Body": body}
    chunks = list(_storage(s3).stream_object(bucket="b", object_key="k", chunk_size=2))
    s3.get_object.assert_called_once_with(Bucket="b", Key="k")
    body.iter_chunks.assert_called_once_with(2)
    body.close.assert_called_once()
    assert chunks == [b"aa", b"bb", b"cc"]


def test_stream_object_uses_default_chunk_size():
    s3 = MagicMock()
    body = MagicMock()
    body.iter_chunks.return_value = iter([b"x"])
    s3.get_object.return_value = {"Body": body}
    list(_storage(s3).stream_object(bucket="b", object_key="k"))
    body.iter_chunks.assert_called_once_with(DEFAULT_STREAM_CHUNK_SIZE)


def test_stream_object_releases_connection_on_error():
    s3 = MagicMock()
    body = MagicMock()
    body.iter_chunks.side_effect = RuntimeError("boom")
    s3.get_object.return_value = {"Body": body}
    with pytest.raises(RuntimeError):
        list(_storage(s3).stream_object(bucket="b", object_key="k"))
    body.close.assert_called_once()


def test_list_object_keys_pages_through_list_objects_v2():
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "a/1.png"}, {"Key": "a/2.png"}]},
        {"Contents": [{"Key": "a/3.png"}]},
        {},
    ]
    s3.get_paginator.return_value = paginator
    keys = list(_storage(s3).list_object_keys(bucket="b", prefix="a/"))
    s3.get_paginator.assert_called_once_with("list_objects_v2")
    paginator.paginate.assert_called_once_with(Bucket="b", Prefix="a/")
    assert keys == ["a/1.png", "a/2.png", "a/3.png"]


def test_list_object_keys_defaults_to_empty_prefix():
    s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = []
    s3.get_paginator.return_value = paginator
    assert list(_storage(s3).list_object_keys(bucket="b")) == []
    paginator.paginate.assert_called_once_with(Bucket="b", Prefix="")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_put_object_sends_a_single_put_with_an_explicit_length():
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"', "VersionId": "v"}
    result = _storage(s3).put_object(
        bucket="b", object_key="k", data=b"abc", content_type="image/webp"
    )
    s3.put_object.assert_called_once()
    _, kwargs = s3.put_object.call_args
    assert kwargs["Bucket"] == "b"
    assert kwargs["Key"] == "k"
    assert kwargs["ContentLength"] == 3
    assert kwargs["ContentType"] == "image/webp"
    assert kwargs["Body"].read() == b"abc"
    assert result.bucket_name == "b"
    assert result.object_name == "k"
    assert result.etag == "e"
    assert result.version_id == "v"


def test_put_object_stream_hands_the_handle_over_unbuffered():
    """The open handle is read through, never materialised first."""
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"'}
    handle = io.BytesIO(b"zip-bytes")
    _storage(s3).put_object_stream(
        bucket="b",
        object_key="k",
        data=handle,
        length=9,
        content_type="application/zip",
    )
    _, kwargs = s3.put_object.call_args
    assert kwargs["ContentLength"] == 9
    assert kwargs["ContentType"] == "application/zip"
    assert kwargs["Body"].read() == b"zip-bytes"


def test_put_object_stream_reads_from_the_current_position():
    """Delegation does not rewind: the caller owns the handle's position."""
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"'}
    handle = io.BytesIO(b"skipme-payload")
    handle.seek(6)
    _storage(s3).put_object_stream(
        bucket="b",
        object_key="k",
        data=handle,
        length=8,
        content_type="application/zip",
    )
    assert s3.put_object.call_args.kwargs["Body"].read() == b"-payload"


def test_put_object_stream_never_uploads_beyond_the_declared_length():
    """A handle holding more bytes than declared must not leak the surplus."""
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"'}
    _storage(s3).put_object_stream(
        bucket="b",
        object_key="k",
        data=io.BytesIO(b"declared-and-then-some"),
        length=8,
        content_type="application/zip",
    )
    assert s3.put_object.call_args.kwargs["Body"].read() == b"declared"


def test_single_put_buffers_a_non_seekable_handle():
    """A pipe cannot be rewound, so the bounded bytes are buffered to sign them."""
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"'}
    _storage(s3).put_object_stream(
        bucket="b",
        object_key="k",
        data=_WriteOnlyStream(b"declared-and-then-some"),
        length=8,
        content_type="application/zip",
    )
    body = s3.put_object.call_args.kwargs["Body"]
    assert isinstance(body, io.BytesIO)
    assert body.read() == b"declared"


def test_write_above_the_threshold_uploads_multipart():
    s3 = MagicMock()
    s3.create_multipart_upload.return_value = {"UploadId": "u1"}
    s3.upload_part.side_effect = [{"ETag": '"p1"'}, {"ETag": '"p2"'}]
    s3.complete_multipart_upload.return_value = {"ETag": '"final-2"'}
    payload = b"x" * (DEFAULT_MULTIPART_CHUNK_SIZE + 16)

    result = _storage(s3).put_object(
        bucket="b", object_key="k", data=payload, content_type="application/zip"
    )

    s3.create_multipart_upload.assert_called_once_with(
        Bucket="b", Key="k", ContentType="application/zip"
    )
    assert s3.put_object.call_count == 0
    assert [call.kwargs["PartNumber"] for call in s3.upload_part.call_args_list] == [
        1,
        2,
    ]
    sent = b"".join(call.kwargs["Body"] for call in s3.upload_part.call_args_list)
    assert sent == payload
    _, kwargs = s3.complete_multipart_upload.call_args
    assert kwargs["UploadId"] == "u1"
    assert kwargs["MultipartUpload"] == {
        "Parts": [
            {"ETag": '"p1"', "PartNumber": 1},
            {"ETag": '"p2"', "PartNumber": 2},
        ]
    }
    assert result.etag == "final-2"
    s3.abort_multipart_upload.assert_not_called()


def test_write_at_the_threshold_stays_single_part():
    s3 = MagicMock()
    s3.put_object.return_value = {"ETag": '"e"'}
    _storage(s3).put_object(
        bucket="b",
        object_key="k",
        data=b"x" * DEFAULT_MULTIPART_THRESHOLD,
        content_type="application/zip",
    )
    s3.put_object.assert_called_once()
    s3.create_multipart_upload.assert_not_called()


def test_failed_multipart_upload_is_aborted():
    """Orphaned parts occupy storage that no bucket listing would ever show."""
    s3 = MagicMock()
    s3.create_multipart_upload.return_value = {"UploadId": "u1"}
    s3.upload_part.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _storage(s3).put_object(
            bucket="b",
            object_key="k",
            data=b"x" * (DEFAULT_MULTIPART_CHUNK_SIZE + 16),
            content_type="application/zip",
        )

    s3.abort_multipart_upload.assert_called_once_with(
        Bucket="b", Key="k", UploadId="u1"
    )


def test_set_object_content_type_replaces_via_server_side_copy():
    s3 = MagicMock()
    s3.copy_object.return_value = {"CopyObjectResult": {"ETag": '"rewritten"'}}
    result = _storage(s3).set_object_content_type(
        bucket="public-media", object_key="k", content_type="image/png"
    )
    s3.copy_object.assert_called_once_with(
        Bucket="public-media",
        Key="k",
        CopySource={"Bucket": "public-media", "Key": "k"},
        MetadataDirective="REPLACE",
        ContentType="image/png",
    )
    assert result.etag == "rewritten"
    assert result.bucket_name == "public-media"


def test_copy_object_server_side_copies_across_buckets():
    s3 = MagicMock()
    s3.copy_object.return_value = {}
    result = _storage(s3).copy_object(
        src_bucket="private-media",
        src_object_key="k",
        dest_bucket="public-media",
        dest_object_key="k",
    )
    s3.copy_object.assert_called_once_with(
        Bucket="public-media",
        Key="k",
        CopySource={"Bucket": "private-media", "Key": "k"},
    )
    # No MetadataDirective: the stored Content-Type travels with the bytes.
    assert "MetadataDirective" not in s3.copy_object.call_args.kwargs
    assert result.etag == ""
    assert result.version_id is None


# ---------------------------------------------------------------------------
# Presigning
# ---------------------------------------------------------------------------


def test_post_upload_url_uses_path_style_http():
    url = _storage(MagicMock()).post_upload_url(bucket="private-media")
    assert url == "http://minio:9000/private-media"


def test_post_upload_url_uses_https_when_secure():
    url = _storage(MagicMock(), secure=True).post_upload_url(bucket="b")
    assert url == "https://minio:9000/b"


def test_presigned_post_object_constrains_size_and_content_type():
    s3 = MagicMock()
    s3.generate_presigned_post.return_value = {
        "url": "http://ignored/public-media",
        "fields": {"policy": "p", "x-amz-signature": "s", "key": "k"},
    }
    url, fields = _storage(s3, expire=900).presigned_post_object(
        bucket="public-media",
        object_key="k",
        content_type="image/png",
        max_size_bytes=4096,
        min_size_bytes=16,
    )
    _, kwargs = s3.generate_presigned_post.call_args
    assert kwargs["Bucket"] == "public-media"
    assert kwargs["Key"] == "k"
    assert kwargs["Fields"] == {"Content-Type": "image/png"}
    assert kwargs["Conditions"] == [
        {"key": "k"},
        {"Content-Type": "image/png"},
        ["content-length-range", 16, 4096],
    ]
    assert kwargs["ExpiresIn"] == 900
    assert fields["key"] == "k"
    assert fields["Content-Type"] == "image/png"
    assert fields["policy"] == "p"
    # The signed URL is discarded: a POST policy signs conditions, not the
    # host, so the browser posts to the public endpoint.
    assert url == "http://minio:9000/public-media"


def test_presigned_post_object_passes_a_fresh_conditions_list_each_call():
    """botocore appends its own bucket/key conditions to the list it is given."""
    s3 = MagicMock()
    s3.generate_presigned_post.return_value = {"url": "u", "fields": {}}
    storage = _storage(s3)
    for _ in range(2):
        storage.presigned_post_object(
            bucket="b", object_key="k", content_type="image/png", max_size_bytes=10
        )
    first, second = (
        call.kwargs["Conditions"] for call in s3.generate_presigned_post.call_args_list
    )
    assert first is not second
    assert first == second


def test_presigned_post_object_defaults_the_minimum_size_and_expiry():
    s3 = MagicMock()
    s3.generate_presigned_post.return_value = {"url": "u", "fields": {}}
    _storage(s3, expire=120).presigned_post_object(
        bucket="b", object_key="k", content_type="image/png", max_size_bytes=4096
    )
    _, kwargs = s3.generate_presigned_post.call_args
    assert kwargs["Conditions"][2] == ["content-length-range", 1, 4096]
    assert kwargs["ExpiresIn"] == 120


def test_presigned_get_object_uses_config_expiry():
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "http://signed"
    assert (
        _storage(s3, expire=900).presigned_get_object(bucket="b", object_key="k")
        == "http://signed"
    )
    s3.generate_presigned_url.assert_called_once_with(
        "get_object", Params={"Bucket": "b", "Key": "k"}, ExpiresIn=900
    )


def test_presigned_get_object_maps_response_overrides_to_boto_params():
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "http://signed"
    _storage(s3).presigned_get_object(
        bucket="b",
        object_key="k",
        expires_seconds=60,
        response_headers={
            "response-content-disposition": 'attachment; filename="f.pdf"',
            "Response-Content-Type": "application/pdf",
        },
    )
    s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={
            "Bucket": "b",
            "Key": "k",
            "ResponseContentDisposition": 'attachment; filename="f.pdf"',
            "ResponseContentType": "application/pdf",
        },
        ExpiresIn=60,
    )


def test_presigned_get_object_refuses_an_unknown_response_override():
    """A silently dropped response-content-disposition is stored XSS (S11)."""
    s3 = MagicMock()
    with pytest.raises(ValueError, match="unsupported response override"):
        _storage(s3).presigned_get_object(
            bucket="b", object_key="k", response_headers={"x-made-up": "v"}
        )
    s3.generate_presigned_url.assert_not_called()


# ---------------------------------------------------------------------------
# Dual-endpoint presigning
# ---------------------------------------------------------------------------


def test_config_public_endpoint_defaults_to_none():
    config = S3StorageConfig(
        endpoint="e", access_key="a", secret_key="s", secure=True, region="r"
    )
    assert config.public_endpoint is None
    assert config.public_secure is None


def test_no_public_endpoint_reuses_internal_client_for_presign():
    s3 = MagicMock()
    storage = _storage(s3)
    assert storage._presign_client is storage.client
    storage.presigned_get_object(bucket="b", object_key="k")
    s3.generate_presigned_url.assert_called_once()


def test_no_public_endpoint_post_url_uses_internal_endpoint():
    # Regression: behaviour byte-identical to today when no public endpoint set.
    url = _storage(MagicMock()).post_upload_url(bucket="private-media")
    assert url == "http://minio:9000/private-media"


def test_public_endpoint_post_url_uses_public_host_and_scheme():
    storage = ObjectStorage(
        _config(public_endpoint="storage.example.com"), client=MagicMock()
    )
    # public_secure unset → falls back to internal secure (False here).
    assert storage.post_upload_url(bucket="b") == "http://storage.example.com/b"


def test_public_secure_override_flips_scheme_independently():
    storage = ObjectStorage(
        _config(
            secure=False, public_endpoint="storage.example.com", public_secure=True
        ),
        client=MagicMock(),
    )
    assert storage.post_upload_url(bucket="b") == "https://storage.example.com/b"


def test_presigned_get_signed_by_client_bound_to_public_endpoint():
    internal = MagicMock()
    presign = MagicMock()
    with patch(
        "media_sdk_m8.storage.client.get_s3_client", return_value=presign
    ) as factory:
        storage = ObjectStorage(
            _config(public_endpoint="storage.example.com", public_secure=True),
            client=internal,
        )
    # The presign client was built for the public endpoint, not the internal one.
    factory.assert_called_once()
    (built_config,), _ = factory.call_args
    assert built_config.endpoint == "storage.example.com"
    assert built_config.secure is True
    assert storage._presign_client is presign

    storage.presigned_get_object(bucket="b", object_key="k")
    presign.generate_presigned_url.assert_called_once()
    internal.generate_presigned_url.assert_not_called()


def test_post_policy_is_signed_by_the_internal_client():
    """The POST policy signs conditions, not the host — no second client needed."""
    internal = MagicMock()
    internal.generate_presigned_post.return_value = {"url": "u", "fields": {}}
    presign = MagicMock()
    with patch("media_sdk_m8.storage.client.get_s3_client", return_value=presign):
        storage = ObjectStorage(
            _config(public_endpoint="storage.example.com"), client=internal
        )
    url, _ = storage.presigned_post_object(
        bucket="b", object_key="k", content_type="image/png", max_size_bytes=10
    )
    internal.generate_presigned_post.assert_called_once()
    presign.generate_presigned_post.assert_not_called()
    assert url == "http://storage.example.com/b"


# ---------------------------------------------------------------------------
# public_endpoint validation — malformed input rejection
# ---------------------------------------------------------------------------


def _base_config_kwargs() -> dict:
    return {
        "endpoint": "minio:9000",
        "access_key": "ak",
        "secret_key": "sk",
        "secure": False,
        "region": "us-east-1",
    }


def test_public_endpoint_with_scheme_rejected():
    """Rejects any value that contains '://'; catches accidental full-URL passthrough."""
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="https://storage.example.com"
        )


def test_public_endpoint_ftp_scheme_rejected():
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="ftp://storage.example.com"
        )


def test_public_endpoint_url_with_missing_host_rejected():
    """'https:///missing-host' — full-URL string, rejected on '://'."""
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="https:///missing-host"
        )


def test_public_endpoint_userinfo_in_netloc_rejected():
    """Rejects netloc that includes userinfo (@ sign) — would corrupt presigned URL host."""
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="user:pass@storage.example.com"
        )


def test_public_endpoint_fragment_rejected():
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="storage.example.com#section"
        )


def test_public_endpoint_query_string_rejected():
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(
            **_base_config_kwargs(), public_endpoint="storage.example.com?foo=bar"
        )


def test_public_endpoint_empty_string_rejected():
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(**_base_config_kwargs(), public_endpoint="")


def test_public_endpoint_whitespace_only_rejected():
    with pytest.raises(ValueError, match="public_endpoint"):
        S3StorageConfig(**_base_config_kwargs(), public_endpoint="   ")


def test_public_endpoint_bare_hostname_accepted():
    """Bare hostname (service standard port) is valid host:port format."""
    config = S3StorageConfig(
        **_base_config_kwargs(), public_endpoint="storage.example.com"
    )
    assert config.public_endpoint == "storage.example.com"


def test_public_endpoint_host_port_accepted():
    config = S3StorageConfig(
        **_base_config_kwargs(), public_endpoint="storage.example.com:443"
    )
    assert config.public_endpoint == "storage.example.com:443"


def test_public_endpoint_loopback_with_port_accepted():
    config = S3StorageConfig(**_base_config_kwargs(), public_endpoint="localhost:9000")
    assert config.public_endpoint == "localhost:9000"


def test_public_endpoint_loopback_ip_with_port_accepted():
    config = S3StorageConfig(**_base_config_kwargs(), public_endpoint="127.0.0.1:9000")
    assert config.public_endpoint == "127.0.0.1:9000"


# ---------------------------------------------------------------------------
# Presigned URL host/scheme normalization
# ---------------------------------------------------------------------------


def test_presigned_post_url_uses_public_host_and_http_scheme():
    """POST upload URL uses the public host and derives http from public_secure=False."""
    storage = ObjectStorage(
        _config(public_endpoint="storage.example.com", public_secure=False),
        client=MagicMock(),
    )
    assert storage.post_upload_url(bucket="media") == "http://storage.example.com/media"


def test_presigned_post_url_uses_public_host_and_https_scheme():
    """POST upload URL uses the public host and derives https from public_secure=True."""
    storage = ObjectStorage(
        _config(
            secure=False, public_endpoint="storage.example.com", public_secure=True
        ),
        client=MagicMock(),
    )
    assert (
        storage.post_upload_url(bucket="media") == "https://storage.example.com/media"
    )


def test_presigned_post_url_with_port_preserves_port():
    """Public host:port (e.g. loopback dev endpoint) is preserved in the POST URL."""
    storage = ObjectStorage(
        _config(public_endpoint="127.0.0.1:9000", public_secure=False),
        client=MagicMock(),
    )
    assert storage.post_upload_url(bucket="b") == "http://127.0.0.1:9000/b"


def test_presigned_get_uses_client_bound_to_public_host_and_secure_flag():
    """Presigned GET is signed by a client configured for the public endpoint."""
    internal = MagicMock()
    presign = MagicMock()
    with patch(
        "media_sdk_m8.storage.client.get_s3_client", return_value=presign
    ) as factory:
        storage = ObjectStorage(
            _config(public_endpoint="storage.example.com", public_secure=True),
            client=internal,
        )
    factory.assert_called_once()
    (built_config,), _ = factory.call_args
    assert built_config.endpoint == "storage.example.com"
    assert built_config.secure is True

    storage.presigned_get_object(bucket="b", object_key="k")
    presign.generate_presigned_url.assert_called_once()
    internal.generate_presigned_url.assert_not_called()
