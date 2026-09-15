"""
Server-observation helpers for the conformance harness.

Every security invariant in the contract is a claim about what the **server**
does. Proving one therefore means looking at a raw HTTP status line or response
header, not at a client-side guard that happens to raise first. These helpers
speak HTTP directly with :mod:`urllib` so that a rejection observed here can
only have come from the backend:

* :func:`request` performs a plain request and returns the response even when
  the status is 4xx, so a refusal is data rather than an exception.
* :func:`post_multipart` submits an S3 POST-policy form exactly as a browser
  would, which is the only way to observe ``content-length-range`` and
  ``Content-Type`` enforcement (`S9`, `S10`).
* :func:`s3_error_code` reads the S3 error code out of whichever client library
  raised it, so the suite survives the ``minio-py`` → ``boto3`` swap in Wave 1.
"""

import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Final

#: Default socket timeout (seconds) for a harness HTTP call.
DEFAULT_TIMEOUT: Final = 30

#: Leading bytes of a PNG. Payloads start with these so that a content-type
#: sniffing gate downstream would agree with the declared ``image/png``.
PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A raw HTTP response, including refusals."""

    status: int
    #: Header names lower-cased; values verbatim.
    headers: dict[str, str]
    body: bytes

    def text(self, limit: int = 500) -> str:
        """Return a short, printable excerpt of the body for assertion messages."""
        return self.body[:limit].decode("utf-8", "replace")


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """
    Perform one HTTP request and return the response, 4xx and 5xx included.

    A refusal is the observation the security cases are after, so it must not
    surface as an exception that a test could pass by merely catching.
    """
    prepared = urllib.request.Request(  # noqa: S310 - fixed http scheme, test-local
        url, data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(prepared, timeout=timeout) as response:  # noqa: S310
            return _response(response)
    except urllib.error.HTTPError as error:
        with error:
            return _response(error)


def _response(raw: Any) -> HttpResponse:
    """Normalise an ``http.client.HTTPResponse``-alike into :class:`HttpResponse`."""
    return HttpResponse(
        status=raw.status,
        headers={key.lower(): value for key, value in raw.headers.items()},
        body=raw.read(),
    )


def post_multipart(
    url: str,
    fields: dict[str, str],
    *,
    content: bytes,
    content_type: str,
    filename: str = "upload.bin",
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """
    Submit an S3 POST-policy form the way a browser would.

    The signed *fields* are sent first and the ``file`` part last, as S3
    requires. Nothing here inspects the policy: an oversized or wrong-typed
    body is sent in full and the server decides, which is precisely what `S9`
    and `S10` must observe.
    """
    boundary = f"----media-conformance-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
            f"\r\n{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    return request(
        url,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(payload)),
        },
        body=payload,
        timeout=timeout,
    )


def s3_error_code(error: BaseException) -> str:
    """
    Return the S3 error code an exception carries, whatever client raised it.

    ``minio-py`` exposes ``S3Error.code``; ``botocore`` puts the same string in
    ``ClientError.response["Error"]["Code"]``. Reading both keeps the harness
    valid across the Wave 1 client swap, and keeps the assertions about the
    *server's* verdict rather than about a library's exception type.
    """
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        nested = response.get("Error", {})
        if isinstance(nested, dict):
            return str(nested.get("Code", ""))
    return ""


def payload(size: int) -> bytes:
    """Return *size* pseudo-random bytes whose prefix is a valid PNG signature."""
    if size < len(PNG_MAGIC):
        raise ValueError(f"payload size must be at least {len(PNG_MAGIC)} bytes")
    return PNG_MAGIC + os.urandom(size - len(PNG_MAGIC))
