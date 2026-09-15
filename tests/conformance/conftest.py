"""
Fixtures for the docker-backed conformance harness.

The suite is deselected by default (``-m 'not conformance'`` in
``pyproject.toml``) so the ordinary run — and the 100% coverage gate it
carries — never needs a container engine. Run it explicitly:

.. code-block:: console

   pytest -m conformance --backend=minio

A missing container engine or an unimplemented backend raises a usage error
rather than skipping: a case that quietly does not run is indistinguishable
from one that passed, and this suite exists precisely to stop that.
"""

import uuid
from collections.abc import Iterator

import pytest

from .backends import DRIVERS, PENDING_DRIVERS, BackendUnderTest, docker_available
from .contract import CANDIDATE_BACKENDS
from .probe import payload

#: Size of the shared multipart fixture object. Comfortably above the 5 MiB
#: S3 minimum part size, so the client is forced onto the multipart path.
MULTIPART_SIZE = 12 * 1024 * 1024


@pytest.fixture(scope="session")
def backend_name(request: pytest.FixtureRequest) -> str:
    """Return the backend name selected with ``--backend``."""
    name = str(request.config.getoption("--backend"))
    if name not in CANDIDATE_BACKENDS:
        raise pytest.UsageError(
            f"--backend={name} is not a candidate backend; "
            f"the contract pins {sorted(CANDIDATE_BACKENDS)}"
        )
    return name


@pytest.fixture(scope="session")
def backend(backend_name: str) -> Iterator[BackendUnderTest]:
    """Boot the selected backend once per session and yield it bootstrapped."""
    if backend_name in PENDING_DRIVERS:
        raise pytest.UsageError(
            f"--backend={backend_name} has no driver yet; "
            f"{PENDING_DRIVERS[backend_name]} adds one"
        )
    if backend_name not in DRIVERS:
        raise pytest.UsageError(f"--backend={backend_name} has no driver")
    if not docker_available():
        raise pytest.UsageError(
            "the conformance harness needs a working container engine; "
            "`docker info` did not succeed"
        )
    with DRIVERS[backend_name]() as running:
        yield running


@pytest.fixture
def object_key(request: pytest.FixtureRequest) -> str:
    """Return a fresh object key, namespaced by the test that asked for it."""
    stem = request.node.name.removeprefix("test_")[:48]
    return f"conformance/{stem}/{uuid.uuid4().hex}.bin"


@pytest.fixture(scope="session")
def multipart_object(backend: BackendUnderTest) -> tuple[str, bytes]:
    """
    Upload one object large enough to force a multipart PUT, and return it.

    Shared by OP-05 and OP-13: the opaque-etag invariant is only meaningful on
    an object the server stored in parts, because a single-part etag legitimately
    equals the payload's MD5 on several backends.
    """
    key = f"conformance/multipart/{uuid.uuid4().hex}.bin"
    content = payload(MULTIPART_SIZE)
    backend.storage.put_object(
        bucket=backend.buckets.archive,
        object_key=key,
        data=content,
        content_type="application/octet-stream",
    )
    return key, content
