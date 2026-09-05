"""
Docker-backed backend drivers for the S3 conformance harness.

A driver boots one **pinned** object-storage image, bootstraps it the way the
shipped stack does, and yields a :class:`BackendUnderTest` carrying two
``ObjectStorage`` handles: the scoped ``media-rw`` identity the application
uses, and the root identity that exists only inside the one-shot bootstrap.
Tests always drive the scoped handle — an invariant proved with root
credentials would prove nothing about the deployed system.

The image tags come from :data:`contract.CANDIDATE_BACKENDS`: an unpinned
backend is not a measured backend, so the harness never resolves a tag of its
own. Only the MinIO driver exists at this step; SeaweedFS and Garage are booted
by the plan steps that measure them, and asking for one before then fails with
a message naming that step rather than silently skipping.
"""

import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Final

from media_sdk_m8 import ObjectStorage, ObjectStorageConfig

from .contract import CANDIDATE_BACKENDS

#: Container engine binary. The workspace profile resolves an engine; the CLI
#: name is the portable spelling of it and is all the harness needs.
DOCKER: Final = "docker"

#: One-shot bootstrap images, pinned exactly like the backends themselves.
#: The MinIO entry is the same tag the shipped ``minio-init`` service uses, so
#: the harness bootstraps through the code path production bootstraps through.
BOOTSTRAP_IMAGES: Final[dict[str, str]] = {
    "minio": "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z",
}

#: Network alias the backend answers to from a sibling container.
STORAGE_ALIAS: Final = "storage"

#: The UI origin the shipped stacks scope CORS to. Never a wildcard (S3).
UI_ORIGIN: Final = "https://localhost:4430"

#: S3 region the media stacks sign for.
REGION: Final = "us-east-1"

#: Seconds to wait for a freshly started backend to answer.
READY_TIMEOUT: Final = 120

#: Backends the contract names that no driver boots yet, and the plan step
#: that must add one. Asking for one of these is a usage error, not a skip.
PENDING_DRIVERS: Final[dict[str, str]] = {
    "seaweedfs": "T3-run-seaweedfs",
    "garage": "T4-run-garage",
}


@dataclass(frozen=True, slots=True)
class MediaBuckets:
    """The five logical buckets every media stack provisions."""

    public: str = "public-media"
    private: str = "private-media"
    sensitive: str = "sensitive-media"
    temp: str = "temp-media"
    archive: str = "archive-media"

    def as_tuple(self) -> tuple[str, ...]:
        """Return the five bucket names in provisioning order."""
        return (self.public, self.private, self.sensitive, self.temp, self.archive)


#: A sixth bucket the scoped credential is deliberately **not** granted, so
#: S4 has something to be denied on.
UNLISTED_BUCKET: Final = "unlisted-media"


@dataclass(frozen=True, slots=True)
class BackendUnderTest:
    """A running, bootstrapped backend and the handles a case needs."""

    #: Contract key: ``minio``, ``seaweedfs`` or ``garage``.
    name: str
    #: The exact pinned image that produced this run — cited by the matrix.
    image: str
    #: ``host:port`` of the published S3 endpoint, as signed for.
    endpoint: str
    buckets: MediaBuckets
    #: Bucket outside the scoped credential's policy.
    unlisted_bucket: str
    #: CORS origin the backend was configured with.
    cors_origin: str
    #: Client holding the scoped ``media-rw`` identity — what the app uses.
    storage: ObjectStorage
    #: Client holding the root identity — bootstrap and negative control only.
    admin: ObjectStorage

    @property
    def base_url(self) -> str:
        """Return the S3 endpoint as a URL."""
        return f"http://{self.endpoint}"

    def object_url(self, bucket: str, object_key: str) -> str:
        """Return the unsigned, path-style URL of an object."""
        return f"{self.base_url}/{bucket}/{object_key}"


# -- docker plumbing --------------------------------------------------------


def docker_available() -> bool:
    """Return whether a usable container engine is present on this host."""
    if shutil.which(DOCKER) is None:
        return False
    probe = subprocess.run(
        [DOCKER, "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return probe.returncode == 0


def _docker(
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 300,
) -> str:
    """
    Run one ``docker`` command and return its stdout.

    Credentials are never passed as arguments: they reach a container through
    ``--env NAME`` reading *env*, so a failure message can quote the argv
    without disclosing a secret (SEC-NO-SECRET-DISCLOSURE).
    """
    completed = subprocess.run(
        [DOCKER, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"docker {' '.join(args)} failed ({completed.returncode}):\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _published_endpoint(container: str, port: int) -> str:
    """Return the ``host:port`` docker published *port* of *container* on."""
    mapping = _docker("port", container, f"{port}/tcp").splitlines()[0].strip()
    host, _, published = mapping.rpartition(":")
    if host in ("0.0.0.0", "::", "[::]", ""):
        host = "127.0.0.1"
    return f"{host}:{published}"


def _wait_for_http(url: str, *, timeout: int = READY_TIMEOUT) -> None:
    """Block until *url* answers with a 2xx, or fail with what it last did."""
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
                last = f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
        except OSError as error:
            last = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready within {timeout}s: {last}")


@contextmanager
def _network() -> Iterator[str]:
    """Create a throwaway docker network for the backend and its bootstrap."""
    name = f"media-conformance-{uuid.uuid4().hex[:10]}"
    _docker("network", "create", name)
    try:
        yield name
    finally:
        _docker("network", "rm", name, check=False)


@contextmanager
def _container(name: str, args: list[str], env: dict[str, str]) -> Iterator[str]:
    """Run a detached container and always remove it again."""
    _docker("run", "-d", "--name", name, *args, env=env)
    try:
        yield name
    finally:
        _docker("rm", "-f", name, check=False, timeout=120)


# -- MinIO driver -----------------------------------------------------------


def _media_rw_policy(buckets: MediaBuckets) -> str:
    """
    Return the scoped ``media-rw`` policy the shipped ``minio-init`` writes.

    Same four actions and the same five buckets as
    ``hardened_media_m8/docker-compose.yml``: reproducing it here is what makes
    S4 a statement about the deployed grant rather than about a policy invented
    for the test.
    """
    resources: list[str] = []
    for bucket in buckets.as_tuple():
        resources.append(f"arn:aws:s3:::{bucket}")
        resources.append(f"arn:aws:s3:::{bucket}/*")
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    "Resource": resources,
                }
            ],
        }
    )


#: Bootstrap script for the ``mc`` one-shot. Mirrors the shipped ``minio-init``
#: entrypoint; every value it needs arrives in the environment, never in argv.
_MINIO_BOOTSTRAP = "\n".join(
    (
        "set -e",
        'until mc alias set local "http://%(alias)s:9000" '
        '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do sleep 1; done',
        "for b in %(buckets)s; do",
        '  mc mb --ignore-existing "local/$b" >/dev/null',
        "done",
        "printf '%%s' \"$MEDIA_RW_POLICY\" > /tmp/media-rw.json",
        "mc admin policy create local media-rw /tmp/media-rw.json >/dev/null",
        'mc admin user add local "$MEDIA_ACCESS_KEY" "$MEDIA_SECRET_KEY" >/dev/null',
        'mc admin policy attach local media-rw --user "$MEDIA_ACCESS_KEY" >/dev/null',
        "echo bootstrap-ok",
    )
)


@contextmanager
def minio_backend() -> Iterator[BackendUnderTest]:
    """Boot the pinned MinIO baseline and bootstrap it like the shipped stack."""
    image = CANDIDATE_BACKENDS["minio"]
    buckets = MediaBuckets()
    env = {
        **os.environ,
        "MINIO_ROOT_USER": "conformance-root",
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
        "MINIO_API_CORS_ALLOW_ORIGIN": UI_ORIGIN,
        "MEDIA_ACCESS_KEY": "media-rw",
        "MEDIA_SECRET_KEY": secrets.token_urlsafe(24),
        "MEDIA_RW_POLICY": _media_rw_policy(buckets),
    }
    run_args_tail = [
        image,
        "server",
        "/data",
        "--console-address",
        ":9001",
    ]
    with _network() as network:
        name = f"media-conformance-storage-{uuid.uuid4().hex[:10]}"
        run_args = [
            "--network",
            network,
            "--network-alias",
            STORAGE_ALIAS,
            "--publish",
            "127.0.0.1::9000",
            "--env",
            "MINIO_ROOT_USER",
            "--env",
            "MINIO_ROOT_PASSWORD",
            "--env",
            "MINIO_API_CORS_ALLOW_ORIGIN",
            *run_args_tail,
        ]
        with _container(name, run_args, env) as container:
            endpoint = _published_endpoint(container, 9000)
            _wait_for_http(f"http://{endpoint}/minio/health/live")
            _minio_bootstrap(network, buckets, env)
            yield BackendUnderTest(
                name="minio",
                image=image,
                endpoint=endpoint,
                buckets=buckets,
                unlisted_bucket=UNLISTED_BUCKET,
                cors_origin=UI_ORIGIN,
                storage=_storage(
                    endpoint, env["MEDIA_ACCESS_KEY"], env["MEDIA_SECRET_KEY"]
                ),
                admin=_storage(
                    endpoint, env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"]
                ),
            )


def _minio_bootstrap(network: str, buckets: MediaBuckets, env: dict[str, str]) -> None:
    """Create the buckets, the scoped policy and the ``media-rw`` identity."""
    script = _MINIO_BOOTSTRAP % {
        "alias": STORAGE_ALIAS,
        "buckets": " ".join((*buckets.as_tuple(), UNLISTED_BUCKET)),
    }
    output = _docker(
        "run",
        "--rm",
        "--network",
        network,
        "--env",
        "MINIO_ROOT_USER",
        "--env",
        "MINIO_ROOT_PASSWORD",
        "--env",
        "MEDIA_ACCESS_KEY",
        "--env",
        "MEDIA_SECRET_KEY",
        "--env",
        "MEDIA_RW_POLICY",
        "--entrypoint",
        "/bin/sh",
        BOOTSTRAP_IMAGES["minio"],
        "-c",
        script,
        env=env,
    )
    if "bootstrap-ok" not in output:
        raise RuntimeError(f"minio bootstrap did not complete: {output!r}")


def _storage(endpoint: str, access_key: str, secret_key: str) -> ObjectStorage:
    """Build an ``ObjectStorage`` bound to *endpoint* with the given identity."""
    return ObjectStorage(
        ObjectStorageConfig(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
            region=REGION,
        )
    )


#: Backend name -> driver. Extended by the step that measures each candidate.
DRIVERS: Final[dict[str, Callable[[], AbstractContextManager[BackendUnderTest]]]] = {
    "minio": minio_backend,
}
