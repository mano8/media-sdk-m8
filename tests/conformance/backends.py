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
own. MinIO, SeaweedFS and Garage drivers exist as of ``T4-run-garage`` — every
backend the contract pins can be booted; :data:`PENDING_DRIVERS` is empty.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
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
    #: Sibling-reachability probe for S2 — a minimal image carrying `nc`, never
    #: the backend under test, so a probe failure cannot be the backend's own
    #: client library.
    "probe": "busybox:1.37.0",
}

#: SeaweedFS components other than the S3 gateway, and the port each binds.
#: `-ip.bind=127.0.0.1` scopes every one of these to the container's own
#: loopback interface, so a sibling container on the same docker network must
#: see a closed port even though it shares that network with the S3 gateway.
SEAWEEDFS_ADMIN_PORTS: Final[dict[str, int]] = {
    "master": 9333,
    "volume": 8080,
    "filer": 8888,
    "webdav": 7333,
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
#: Empty as of ``T4-run-garage`` — every candidate has a driver.
PENDING_DRIVERS: Final[dict[str, str]] = {}

#: Garage's RPC port. Never published; siblings must find it closed (S2).
GARAGE_RPC_PORT: Final = 3901


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
    #: Docker network the backend runs on. Empty only for a backend that never
    #: needed a sibling-reachability probe registered against it.
    network: str = ""
    #: Ports S2 must prove unreachable from a sibling container. Empty means
    #: the invariant cannot be evaluated against this backend by design (MinIO:
    #: the admin API *is* the S3 port), which the S2 case must skip, not fail.
    admin_ports: tuple[int, ...] = field(default_factory=tuple)

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


def _wait_for_any_response(url: str, *, timeout: int = READY_TIMEOUT) -> None:
    """
    Block until *url* answers at all, an auth refusal included.

    Unlike :func:`_wait_for_http`, a 4xx counts as ready here: an IAM-scoped
    S3 gateway correctly refuses an anonymous request once it is up, and that
    refusal is itself the liveness signal — only a connection failure means
    "not up yet".
    """
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):  # noqa: S310
                return
        except urllib.error.HTTPError:
            return
        except OSError as error:
            last = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready within {timeout}s: {last}")


def sibling_port_reachable(
    network: str, host: str, port: int, *, timeout: int = 5
) -> bool:
    """
    Return whether *host:port* accepts a TCP connect from a fresh sibling container.

    Proves S2 from the vantage point that matters — another container on the
    same docker network, the same position a compromised ``app_net`` sibling
    would have — never from the host, and never through the backend's own
    client library, which would only prove the client is well-behaved.
    """
    result = subprocess.run(
        [
            DOCKER,
            "run",
            "--rm",
            "--network",
            network,
            BOOTSTRAP_IMAGES["probe"],
            "nc",
            "-z",
            "-w",
            str(timeout),
            host,
            str(port),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout + 30,
    )
    return result.returncode == 0


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


# -- SeaweedFS driver --------------------------------------------------------


def _seaweedfs_iam_config(
    buckets: MediaBuckets,
    *,
    admin_access_key: str,
    admin_secret_key: str,
    media_access_key: str,
    media_secret_key: str,
) -> str:
    """
    Return the static S3 IAM identities config ``weed server -s3.config`` boots from.

    SeaweedFS has no bootstrap-time user-creation API to mirror MinIO's
    ``mc admin user add`` — identities are declared up front in this file and
    loaded at startup instead. The ``admin`` identity exists only to create
    buckets and to plant the ``unlisted-media`` object S4 is denied on, the
    same two root-only uses the MinIO driver reserves for its root identity.
    ``media-rw`` gets ``Read``/``Write``/``List`` scoped to the five media
    buckets and nothing else — SeaweedFS's action vocabulary has no separate
    delete verb, so ``Write`` is where MinIO's ``s3:DeleteObject`` grant lands
    here; that coarsening is recorded in MATRIX.md, not hidden.
    """
    media_actions = [
        f"{verb}:{bucket}"
        for bucket in buckets.as_tuple()
        for verb in ("Read", "Write", "List")
    ]
    return json.dumps(
        {
            "identities": [
                {
                    "name": "admin",
                    "credentials": [
                        {"accessKey": admin_access_key, "secretKey": admin_secret_key}
                    ],
                    "actions": ["Admin", "Read", "Write", "List"],
                },
                {
                    "name": "media-rw",
                    "credentials": [
                        {"accessKey": media_access_key, "secretKey": media_secret_key}
                    ],
                    "actions": media_actions,
                },
            ]
        }
    )


@contextmanager
def seaweedfs_backend() -> Iterator[BackendUnderTest]:
    """
    Boot the pinned SeaweedFS candidate with every non-S3 port loopback-bound.

    ``-ip.bind=127.0.0.1`` scopes master/volume/filer to the container's own
    loopback interface; ``-s3.ip.bind=0.0.0.0`` is what actually needs to be
    reachable, and only that port is published to the host, and only on
    ``127.0.0.1`` — nothing here is exposed the way MinIO's driver is not
    either. Identities are static (``-s3.config``), so there is no separate
    bootstrap container: the buckets are created directly through the admin
    handle once the gateway answers.
    """
    image = CANDIDATE_BACKENDS["seaweedfs"]
    buckets = MediaBuckets()
    admin_access_key = "conformance-admin"
    admin_secret_key = secrets.token_urlsafe(24)
    media_access_key = "media-rw"
    media_secret_key = secrets.token_urlsafe(24)
    iam_config = _seaweedfs_iam_config(
        buckets,
        admin_access_key=admin_access_key,
        admin_secret_key=admin_secret_key,
        media_access_key=media_access_key,
        media_secret_key=media_secret_key,
    )
    with tempfile.TemporaryDirectory(prefix="media-conformance-seaweedfs-") as tmp:
        config_path = Path(tmp) / "s3.json"
        config_path.write_text(iam_config, encoding="utf-8")
        mount = f"{str(config_path).replace(chr(92), '/')}:/etc/seaweedfs/s3.json:ro"
        with _network() as network:
            name = f"media-conformance-storage-{uuid.uuid4().hex[:10]}"
            run_args = [
                "--network",
                network,
                "--network-alias",
                STORAGE_ALIAS,
                "--publish",
                "127.0.0.1::8333",
                "--volume",
                mount,
                image,
                "server",
                "-dir=/data",
                "-filer",
                "-s3",
                # `-ip` is the address components advertise to *each other*
                # (master<->volume<->filer heartbeats and uploads), separate
                # from `-ip.bind`. The plan's own command names only
                # `-ip.bind=127.0.0.1`, which leaves `-ip` at its container-IP
                # default: the volume server then binds loopback while the
                # filer tries to reach it on the container's routable address,
                # and every write fails with a 500 (upload_content.go dial
                # tcp ... connect: connection refused). Pinning `-ip` to the
                # same loopback address is what makes bind and advertise agree
                # — recorded in MATRIX.md as a blocker in the plan's literal
                # command, closed by this one extra flag.
                "-ip=localhost",
                "-ip.bind=127.0.0.1",
                "-s3.ip.bind=0.0.0.0",
                "-s3.config=/etc/seaweedfs/s3.json",
            ]
            with _container(name, run_args, env={}) as container:
                endpoint = _published_endpoint(container, 8333)
                _wait_for_any_response(f"http://{endpoint}/")
                admin = _storage(endpoint, admin_access_key, admin_secret_key)
                for bucket in (*buckets.as_tuple(), UNLISTED_BUCKET):
                    admin.client.create_bucket(Bucket=bucket)
                yield BackendUnderTest(
                    name="seaweedfs",
                    image=image,
                    endpoint=endpoint,
                    buckets=buckets,
                    unlisted_bucket=UNLISTED_BUCKET,
                    cors_origin=UI_ORIGIN,
                    storage=_storage(endpoint, media_access_key, media_secret_key),
                    admin=admin,
                    network=network,
                    admin_ports=tuple(SEAWEEDFS_ADMIN_PORTS.values()),
                )


# -- Garage driver ------------------------------------------------------


def _garage_config(rpc_secret: str) -> str:
    """
    Return the single-node Garage config the ``T4-run-garage`` container boots from.

    ``replication_factor = 1`` is the single-node setting — nothing here claims
    the durability story a real multi-node Garage cluster would have; the
    conformance suite only measures the S3 surface and security invariants a
    *node* enforces. ``rpc_bind_addr``/``rpc_public_addr`` are both pinned to
    the container's own loopback interface: unlike SeaweedFS's master/volume/
    filer, Garage has exactly one extra port (RPC, cluster gossip and the CLI's
    admin channel) to close for S2, and closing it costs nothing here because
    the CLI bootstrap below runs *inside* the container via ``docker exec``,
    which shares its network namespace and can still reach ``127.0.0.1:3901``.
    """
    return "\n".join(
        (
            'metadata_dir = "/data/meta"',
            'data_dir = "/data/data"',
            'db_engine = "sqlite"',
            "",
            "replication_factor = 1",
            "",
            'rpc_bind_addr = "127.0.0.1:3901"',
            'rpc_public_addr = "127.0.0.1:3901"',
            f'rpc_secret = "{rpc_secret}"',
            "",
            "[s3_api]",
            f's3_region = "{REGION}"',
            'api_bind_addr = "0.0.0.0:3900"',
            'root_domain = ".s3.garage.localhost"',
        )
    )


def _garage_cli(container: str, *args: str) -> str:
    """Run one ``garage`` CLI subcommand inside *container* and return stdout."""
    return _docker("exec", container, "/garage", *args)


def _garage_key_create(container: str, name: str) -> tuple[str, str]:
    """Create a named Garage access key and return its ``(key_id, secret_key)``."""
    output = _garage_cli(container, "key", "create", name)
    key_id = re.search(r"Key ID:\s+(\S+)", output)
    secret_key = re.search(r"Secret key:\s+(\S+)", output)
    if not key_id or not secret_key:
        raise RuntimeError(
            f"garage key create {name!r} returned no credentials: {output!r}"
        )
    return key_id.group(1), secret_key.group(1)


@contextmanager
def garage_backend() -> Iterator[BackendUnderTest]:
    """
    Boot the pinned Garage candidate and bootstrap it through its own CLI.

    Garage has no ``mc``-style bootstrap image and no static identities file
    like SeaweedFS's ``-s3.config`` — access keys and per-bucket grants are
    created at runtime through ``garage key create`` / ``garage bucket allow``,
    issued here over ``docker exec`` against the running node. A single-node
    cluster still needs an explicit layout (``garage layout assign`` +
    ``garage layout apply``) before it will accept writes; that is Garage's
    equivalent of MinIO's instant readiness and SeaweedFS's static config.

    Garage's permission model is Read/Write/Owner per key per bucket — there is
    no separate delete verb, so ``media-rw``'s ``--write`` grant is where
    MinIO's ``s3:DeleteObject`` lands here, the same coarsening ``T3`` recorded
    for SeaweedFS (D2). ``admin`` additionally gets ``--owner`` on every bucket,
    including ``unlisted-media``, so it can plant the object S4 is denied on.
    """
    image = CANDIDATE_BACKENDS["garage"]
    buckets = MediaBuckets()
    rpc_secret = secrets.token_hex(32)
    config = _garage_config(rpc_secret)
    with tempfile.TemporaryDirectory(prefix="media-conformance-garage-") as tmp:
        config_path = Path(tmp) / "garage.toml"
        config_path.write_text(config, encoding="utf-8")
        mount = f"{str(config_path).replace(chr(92), '/')}:/etc/garage.toml:ro"
        with _network() as network:
            name = f"media-conformance-storage-{uuid.uuid4().hex[:10]}"
            run_args = [
                "--network",
                network,
                "--network-alias",
                STORAGE_ALIAS,
                "--publish",
                "127.0.0.1::3900",
                "--volume",
                mount,
                image,
            ]
            with _container(name, run_args, env={}) as container:
                endpoint = _published_endpoint(container, 3900)
                _wait_for_any_response(f"http://{endpoint}/")

                node_id = _garage_cli(container, "node", "id", "-q").split("@", 1)[0]
                _garage_cli(
                    container, "layout", "assign", "-z", "dc1", "-c", "1G", node_id
                )
                _garage_cli(container, "layout", "apply", "--version", "1")

                for bucket in (*buckets.as_tuple(), UNLISTED_BUCKET):
                    _garage_cli(container, "bucket", "create", bucket)

                admin_access_key, admin_secret_key = _garage_key_create(
                    container, "admin"
                )
                media_access_key, media_secret_key = _garage_key_create(
                    container, "media-rw"
                )

                for bucket in buckets.as_tuple():
                    _garage_cli(
                        container,
                        "bucket",
                        "allow",
                        "--read",
                        "--write",
                        "--key",
                        media_access_key,
                        bucket,
                    )
                for bucket in (*buckets.as_tuple(), UNLISTED_BUCKET):
                    _garage_cli(
                        container,
                        "bucket",
                        "allow",
                        "--read",
                        "--write",
                        "--owner",
                        "--key",
                        admin_access_key,
                        bucket,
                    )

                yield BackendUnderTest(
                    name="garage",
                    image=image,
                    endpoint=endpoint,
                    buckets=buckets,
                    unlisted_bucket=UNLISTED_BUCKET,
                    cors_origin=UI_ORIGIN,
                    storage=_storage(endpoint, media_access_key, media_secret_key),
                    admin=_storage(endpoint, admin_access_key, admin_secret_key),
                    network=network,
                    admin_ports=(GARAGE_RPC_PORT,),
                )


#: Backend name -> driver. Extended by the step that measures each candidate.
DRIVERS: Final[dict[str, Callable[[], AbstractContextManager[BackendUnderTest]]]] = {
    "minio": minio_backend,
    "seaweedfs": seaweedfs_backend,
    "garage": garage_backend,
}
