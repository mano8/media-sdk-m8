"""
S2, S4, S5, S9, S10, S11, S12: the security invariants a live backend must prove.

These are the rows the contract pins to the conformance suite because no static
policy test can establish them — they are claims about what the *server* does
with a request. Each case therefore reaches the backend over raw HTTP (or, for
S2, over a sibling container's raw TCP) and reads the outcome back. A
client-side guard that refused first would make the case vacuous, which is the
exact failure mode the migration plan exists to prevent.

S2 (admin surface unreachable from a sibling) is delivered by
``T3-run-seaweedfs``: it asserts the loopback binding that replaces MinIO's
Traefik path exclusion. MinIO's own admin API is reachable on the S3 port by
design — there is no separate port to probe — so the baseline case skips
rather than failing; :data:`backends.BackendUnderTest.admin_ports` is empty for
that driver for exactly this reason.
"""

import pytest

from . import probe
from .backends import STORAGE_ALIAS, BackendUnderTest, sibling_port_reachable

pytestmark = pytest.mark.conformance

#: Statuses that count as a refusal. A 5xx does not: a crashed backend is not
#: an enforced policy.
REFUSED = range(400, 500)


def _absent(backend: BackendUnderTest, bucket: str, object_key: str) -> bool:
    """Return whether *object_key* is absent, as the server reports it."""
    url = backend.admin.presigned_get_object(bucket=bucket, object_key=object_key)
    return probe.request(url).status == 404


# -- S2 ---------------------------------------------------------------------


def test_storage_admin_surface_unreachable_from_siblings(
    backend: BackendUnderTest,
) -> None:
    """
    No master/volume/filer/webdav port answers a connection from a sibling.

    Reachability is asked from a fresh container on the backend's own docker
    network — the vantage point a compromised ``app_net`` sibling would
    actually have — never from the host and never through the backend's own
    client library, either of which would prove something weaker. MinIO folds
    its admin surface into the S3 port itself, so there is nothing separate to
    probe there; that is a documented "cannot pass" (see module docstring), not
    a silently skipped case.
    """
    if not backend.admin_ports:
        pytest.skip(
            f"{backend.name}: the admin surface shares the S3 port by design "
            "(plan §4.2 S2 — see this module's docstring); nothing to probe"
        )
    for port in backend.admin_ports:
        assert not sibling_port_reachable(backend.network, STORAGE_ALIAS, port), (
            f"{backend.name} port {port} answered a sibling container despite "
            "the loopback-bind posture this row requires"
        )


# -- S4 ---------------------------------------------------------------------


def test_scoped_credential_denies_unlisted_bucket(backend: BackendUnderTest) -> None:
    """
    The application credential reaches the five media buckets and nothing else.

    The bootstrap grants ``media-rw`` Get/Put/Delete/List on exactly those five
    buckets, so a sixth bucket that the root identity created must be refused
    on every one of those verbs. Root creating it is the point: the object is
    really there, and only the grant stops the application from touching it.
    """
    unlisted = backend.unlisted_bucket
    planted = "conformance/s4/planted.bin"
    backend.admin.put_object(
        bucket=unlisted,
        object_key=planted,
        data=probe.payload(64),
        content_type="application/octet-stream",
    )

    denials = {
        "PutObject": lambda: backend.storage.put_object(
            bucket=unlisted,
            object_key="conformance/s4/written.bin",
            data=b"x",
            content_type="application/octet-stream",
        ),
        "GetObject": lambda: backend.storage.get_object(
            bucket=unlisted, object_key=planted
        ),
        "ListBucket": lambda: list(backend.storage.list_object_keys(bucket=unlisted)),
        "DeleteObject": lambda: backend.storage.remove_object(
            bucket=unlisted, object_key=planted
        ),
    }
    for operation, call in denials.items():
        with pytest.raises(Exception) as raised:
            call()
        assert probe.s3_error_code(raised.value) == "AccessDenied", (
            f"{operation} on {unlisted} was not denied to the scoped credential"
        )

    assert backend.admin.get_object(bucket=unlisted, object_key=planted)

    for bucket in backend.buckets.as_tuple():
        key = "conformance/s4/allowed.bin"
        backend.storage.put_object(
            bucket=bucket,
            object_key=key,
            data=b"x",
            content_type="application/octet-stream",
        )
        backend.storage.remove_object(bucket=bucket, object_key=key)


# -- S5 ---------------------------------------------------------------------


def test_unsigned_object_request_is_denied(backend: BackendUnderTest) -> None:
    """
    Nothing is anonymously readable, ``public-media`` included.

    "Public" in this stack means *served through a presigned GET after a clean
    scan*, never *world-readable on the storage origin*. If an unsigned request
    ever succeeds, the scan gate has been bypassed at the storage layer.
    """
    for bucket in backend.buckets.as_tuple():
        key = f"conformance/s5/{bucket}.bin"
        backend.storage.put_object(
            bucket=bucket,
            object_key=key,
            data=probe.payload(128),
            content_type="image/png",
        )

        unsigned = probe.request(backend.object_url(bucket, key))
        assert unsigned.status in REFUSED, (
            f"{bucket}/{key} is anonymously readable (HTTP {unsigned.status})"
        )

        listing = probe.request(f"{backend.base_url}/{bucket}")
        assert listing.status in REFUSED, (
            f"{bucket} is anonymously listable (HTTP {listing.status})"
        )


# -- S9 ---------------------------------------------------------------------


def test_post_policy_oversized_body_rejected_by_server(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    ``content-length-range`` is enforced by the backend, in both directions.

    This is the only server-side cap on upload size in the whole stack: the
    browser POSTs straight to storage, so a backend that ignores the condition
    means unbounded upload and storage exhaustion. The bodies below are sent in
    full over raw HTTP — nothing client-side inspects the policy — so a refusal
    can only be the server's. The third case is the control: with the same
    conditions a conforming body must succeed, which is what makes the two
    refusals attributable to size rather than to some unrelated failure.
    """
    bucket = backend.buckets.temp
    minimum, maximum = 16, 1024

    def sign(suffix: str) -> tuple[str, dict[str, str], str]:
        key = f"{object_key}.{suffix}"
        url, fields = backend.storage.presigned_post_object(
            bucket=bucket,
            object_key=key,
            content_type="image/png",
            max_size_bytes=maximum,
            min_size_bytes=minimum,
        )
        return url, fields, key

    url, fields, oversized_key = sign("oversized")
    oversized = probe.post_multipart(
        url, fields, content=probe.payload(maximum * 4), content_type="image/png"
    )
    assert oversized.status in REFUSED, (
        f"a {maximum * 4}-byte body passed a {maximum}-byte cap "
        f"(HTTP {oversized.status})"
    )
    assert _absent(backend, bucket, oversized_key)

    url, fields, undersized_key = sign("undersized")
    undersized = probe.post_multipart(
        url, fields, content=b"tiny", content_type="image/png"
    )
    assert undersized.status in REFUSED, (
        f"a 4-byte body passed a {minimum}-byte floor (HTTP {undersized.status})"
    )
    assert _absent(backend, bucket, undersized_key)

    url, fields, conforming_key = sign("conforming")
    conforming = probe.post_multipart(
        url, fields, content=probe.payload(512), content_type="image/png"
    )
    assert conforming.status in (200, 201, 204), conforming.text()
    assert not _absent(backend, bucket, conforming_key)


# -- S10 --------------------------------------------------------------------


def test_post_policy_content_type_mismatch_rejected_by_server(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    The signed ``Content-Type`` is pinned server-side, and REPLACE overrides it.

    Two halves of one control. First: a POST that declares a different type
    from the one the policy was signed for must be refused, or a client picks
    the type its bytes are later served as. Second: the metadata-only self-copy
    must rewrite the stored type authoritatively, which is how the service
    forces the type its own validation determined.
    """
    bucket = backend.buckets.temp
    mismatch_key = f"{object_key}.mismatch"
    url, fields = backend.storage.presigned_post_object(
        bucket=bucket,
        object_key=mismatch_key,
        content_type="image/png",
        max_size_bytes=1_000_000,
        min_size_bytes=16,
    )
    tampered = dict(fields) | {"Content-Type": "text/html"}
    refused = probe.post_multipart(
        url, tampered, content=probe.payload(512), content_type="text/html"
    )
    assert refused.status in REFUSED, (
        f"a POST declaring text/html passed a policy signed for image/png "
        f"(HTTP {refused.status})"
    )
    assert _absent(backend, bucket, mismatch_key)

    replace_key = f"{object_key}.replace"
    backend.storage.put_object(
        bucket=bucket,
        object_key=replace_key,
        data=probe.payload(512),
        content_type="text/html",
    )
    backend.storage.set_object_content_type(
        bucket=bucket, object_key=replace_key, content_type="application/octet-stream"
    )
    served = probe.request(
        backend.storage.presigned_get_object(bucket=bucket, object_key=replace_key)
    )
    assert served.status == 200
    assert served.headers["content-type"] == "application/octet-stream", (
        "the REPLACE self-copy did not change the type the server serves"
    )


# -- S11 --------------------------------------------------------------------


def test_presigned_get_honours_response_content_disposition(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    ``response-content-disposition`` comes back verbatim on the response.

    This is the anti-stored-XSS control: user-supplied bytes are forced to
    download rather than render on the storage origin. A backend that signs the
    override but drops it from the response turns every uploaded HTML or SVG
    file into stored XSS, and does so silently — which is why the assertion is
    on the response header and not on the URL that was signed.
    """
    bucket = backend.buckets.public
    disposition = 'attachment; filename="user-upload.svg"'
    backend.storage.put_object(
        bucket=bucket,
        object_key=object_key,
        data=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        content_type="image/svg+xml",
    )

    url = backend.storage.presigned_get_object(
        bucket=bucket,
        object_key=object_key,
        response_headers={"response-content-disposition": disposition},
    )
    response = probe.request(url)

    assert response.status == 200, response.text()
    assert response.headers.get("content-disposition") == disposition, (
        "response-content-disposition was signed but not honoured: uploaded "
        "content renders inline on the storage origin"
    )


# -- S12 --------------------------------------------------------------------


def test_ranged_get_returns_partial_content(
    backend: BackendUnderTest, object_key: str
) -> None:
    """
    A ranged GET answers 206 with only the requested bytes.

    The magic-byte sniffing gate reads a 512-byte prefix before anything else
    is allowed to happen to an object. If the backend answered 200 with the
    whole body, the gate would silently pull every uploaded object into memory
    in full.
    """
    bucket = backend.buckets.private
    content = probe.payload(64 * 1024)
    backend.storage.put_object(
        bucket=bucket,
        object_key=object_key,
        data=content,
        content_type="application/octet-stream",
    )

    url = backend.storage.presigned_get_object(bucket=bucket, object_key=object_key)
    response = probe.request(url, headers={"Range": "bytes=0-511"})

    assert response.status == 206, (
        f"a ranged GET returned HTTP {response.status}, not 206 Partial Content"
    )
    assert response.headers["content-range"].startswith(f"bytes 0-511/{len(content)}")
    assert len(response.body) == 512
    assert response.body == content[:512]
    assert (
        backend.storage.get_object_head(bucket=bucket, object_key=object_key)
        == content[:512]
    )
