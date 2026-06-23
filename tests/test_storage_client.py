"""Tests for media_sdk_m8.storage.client.ObjectStorage."""

import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

from media_sdk_m8.storage.client import (
    DEFAULT_PRESIGNED_EXPIRE_SECONDS,
    DEFAULT_STREAM_CHUNK_SIZE,
    ObjectStorage,
    ObjectStorageConfig,
    get_minio_client,
)


def _config(
    *,
    secure: bool = False,
    expire: int = 120,
    public_endpoint: str | None = None,
    public_secure: bool | None = None,
) -> ObjectStorageConfig:
    return ObjectStorageConfig(
        endpoint="minio:9000",
        access_key="ak",
        secret_key="sk",
        secure=secure,
        region="us-east-1",
        presigned_expire_seconds=expire,
        public_endpoint=public_endpoint,
        public_secure=public_secure,
    )


def _storage(minio: MagicMock, **kw: object) -> ObjectStorage:
    return ObjectStorage(_config(**kw), client=minio)


def test_config_default_expiry():
    config = ObjectStorageConfig(
        endpoint="e", access_key="a", secret_key="s", secure=True, region="r"
    )
    assert config.presigned_expire_seconds == DEFAULT_PRESIGNED_EXPIRE_SECONDS


def test_stat_object_delegates():
    minio = MagicMock()
    _storage(minio).stat_object(bucket="b", object_key="k")
    minio.stat_object.assert_called_once_with("b", "k")


def test_remove_object_delegates():
    minio = MagicMock()
    _storage(minio).remove_object(bucket="b", object_key="k")
    minio.remove_object.assert_called_once_with("b", "k")


def test_put_object_streams_bytes_with_length():
    minio = MagicMock()
    _storage(minio).put_object(
        bucket="b", object_key="k", data=b"abc", content_type="image/webp"
    )
    minio.put_object.assert_called_once()
    args, kwargs = minio.put_object.call_args
    assert args[0] == "b"
    assert args[1] == "k"
    assert args[2].read() == b"abc"
    assert kwargs["length"] == 3
    assert kwargs["content_type"] == "image/webp"


def test_get_object_head_reads_partial_bytes():
    minio = MagicMock()
    response = MagicMock()
    response.read.return_value = b"\x89PNG\r\n\x1a\n"
    minio.get_object.return_value = response
    result = _storage(minio).get_object_head(bucket="b", object_key="k")
    minio.get_object.assert_called_once_with("b", "k", offset=0, length=512)
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
    assert result == b"\x89PNG\r\n\x1a\n"


def test_get_object_head_custom_length():
    minio = MagicMock()
    response = MagicMock()
    response.read.return_value = b"\xff\xd8"
    minio.get_object.return_value = response
    _storage(minio).get_object_head(bucket="b", object_key="k", length=128)
    minio.get_object.assert_called_once_with("b", "k", offset=0, length=128)


def test_get_object_reads_full_content():
    minio = MagicMock()
    response = MagicMock()
    response.read.return_value = b"full-bytes"
    minio.get_object.return_value = response
    result = _storage(minio).get_object(bucket="b", object_key="k")
    minio.get_object.assert_called_once_with("b", "k")
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
    assert result == b"full-bytes"


def test_stream_object_yields_chunks_and_releases():
    minio = MagicMock()
    response = MagicMock()
    response.stream.return_value = iter([b"aa", b"bb", b"cc"])
    minio.get_object.return_value = response
    chunks = list(
        _storage(minio).stream_object(bucket="b", object_key="k", chunk_size=2)
    )
    minio.get_object.assert_called_once_with("b", "k")
    response.stream.assert_called_once_with(2)
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
    assert chunks == [b"aa", b"bb", b"cc"]


def test_stream_object_uses_default_chunk_size():
    minio = MagicMock()
    response = MagicMock()
    response.stream.return_value = iter([b"x"])
    minio.get_object.return_value = response
    list(_storage(minio).stream_object(bucket="b", object_key="k"))
    response.stream.assert_called_once_with(DEFAULT_STREAM_CHUNK_SIZE)


def test_stream_object_releases_connection_on_error():
    minio = MagicMock()
    response = MagicMock()
    response.stream.side_effect = RuntimeError("boom")
    minio.get_object.return_value = response
    gen = _storage(minio).stream_object(bucket="b", object_key="k")
    try:
        list(gen)
    except RuntimeError:
        pass
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_list_object_keys_yields_recursive_keys():
    minio = MagicMock()
    minio.list_objects.return_value = [
        MagicMock(object_name="a/1.png"),
        MagicMock(object_name="a/2.png"),
    ]
    keys = list(_storage(minio).list_object_keys(bucket="b", prefix="a/"))
    minio.list_objects.assert_called_once_with("b", prefix="a/", recursive=True)
    assert keys == ["a/1.png", "a/2.png"]


def test_list_object_keys_defaults_to_empty_prefix():
    minio = MagicMock()
    minio.list_objects.return_value = []
    assert list(_storage(minio).list_object_keys(bucket="b")) == []
    minio.list_objects.assert_called_once_with("b", prefix="", recursive=True)


def test_set_object_content_type_replaces_via_server_side_copy():
    from minio.commonconfig import REPLACE

    minio = MagicMock()
    _storage(minio).set_object_content_type(
        bucket="public-media", object_key="k", content_type="image/png"
    )
    minio.copy_object.assert_called_once()
    args, kwargs = minio.copy_object.call_args
    assert args[0] == "public-media"
    assert args[1] == "k"
    assert args[2].bucket_name == "public-media"
    assert args[2].object_name == "k"
    assert kwargs["metadata"] == {"Content-Type": "image/png"}
    assert kwargs["metadata_directive"] == REPLACE


def test_copy_object_server_side_copies_across_buckets():
    minio = MagicMock()
    _storage(minio).copy_object(
        src_bucket="private-media",
        src_object_key="k",
        dest_bucket="public-media",
        dest_object_key="k",
    )
    minio.copy_object.assert_called_once()
    args, _ = minio.copy_object.call_args
    assert args[0] == "public-media"
    assert args[1] == "k"
    assert args[2].bucket_name == "private-media"
    assert args[2].object_name == "k"


def test_post_upload_url_uses_path_style_http():
    url = _storage(MagicMock()).post_upload_url(bucket="private-media")
    assert url == "http://minio:9000/private-media"


def test_post_upload_url_uses_https_when_secure():
    url = _storage(MagicMock(), secure=True).post_upload_url(bucket="b")
    assert url == "https://minio:9000/b"


def test_presigned_post_object_constrains_size_and_content_type():
    from minio.datatypes import PostPolicy

    minio = MagicMock()
    minio.presigned_post_policy.return_value = {"policy": "p", "x-amz-signature": "s"}
    url, fields = _storage(minio).presigned_post_object(
        bucket="public-media",
        object_key="k",
        content_type="image/png",
        max_size_bytes=4096,
    )
    minio.presigned_post_policy.assert_called_once()
    (policy,), _ = minio.presigned_post_policy.call_args
    assert isinstance(policy, PostPolicy)
    assert fields["key"] == "k"
    assert fields["Content-Type"] == "image/png"
    assert url == "http://minio:9000/public-media"


def test_presigned_get_object_uses_config_expiry():
    minio = MagicMock()
    _storage(minio, expire=900).presigned_get_object(bucket="b", object_key="k")
    minio.presigned_get_object.assert_called_once_with(
        "b", "k", expires=timedelta(seconds=900), response_headers=None
    )


def test_presigned_get_object_honors_override_and_headers():
    minio = MagicMock()
    headers = {"response-content-disposition": 'attachment; filename="f.pdf"'}
    _storage(minio).presigned_get_object(
        bucket="b", object_key="k", expires_seconds=60, response_headers=headers
    )
    minio.presigned_get_object.assert_called_once_with(
        "b", "k", expires=timedelta(seconds=60), response_headers=headers
    )


def test_config_public_endpoint_defaults_to_none():
    config = ObjectStorageConfig(
        endpoint="e", access_key="a", secret_key="s", secure=True, region="r"
    )
    assert config.public_endpoint is None
    assert config.public_secure is None


def test_no_public_endpoint_reuses_internal_client_for_presign():
    minio = MagicMock()
    storage = _storage(minio)
    assert storage._presign_client is storage.client
    storage.presigned_get_object(bucket="b", object_key="k")
    minio.presigned_get_object.assert_called_once()


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
        "media_sdk_m8.storage.client.get_minio_client", return_value=presign
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
    presign.presigned_get_object.assert_called_once()
    internal.presigned_get_object.assert_not_called()


def test_default_constructor_builds_minio_client():
    fake_minio = MagicMock()
    with patch(
        "media_sdk_m8.storage.client.get_minio_client", return_value=fake_minio
    ) as factory:
        storage = ObjectStorage(_config())
    factory.assert_called_once()
    assert storage.client is fake_minio


def test_get_minio_client_constructs_minio_instance():
    mock_minio_mod = MagicMock()
    with patch.dict(sys.modules, {"minio": mock_minio_mod}):
        get_minio_client(_config(secure=True))
    mock_minio_mod.Minio.assert_called_once_with(
        endpoint="minio:9000",
        access_key="ak",
        secret_key="sk",
        secure=True,
        region="us-east-1",
    )
