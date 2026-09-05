# Conformance harness

`CONTRACT.md` states what the object-storage backend must do. This harness is
what actually asks a running backend whether it does it.

## Running it

```bash
pytest -m conformance --backend=minio           # the baseline
pytest -m conformance --backend=seaweedfs       # the candidate T3 measures
pytest -m conformance --backend=garage          # the fallback T4 measures
pytest -m conformance --backend=minio --no-cov  # same, without the coverage report
```

The suite needs a working container engine (`docker info` must succeed) and
pulls one pinned image plus its one-shot bootstrap image. A full run takes
roughly two minutes, most of it in `OP-11`, which uploads 1001 objects to force
a second `ListObjectsV2` page.

It is **deselected from the ordinary run** — `pyproject.toml` carries
`-m 'not conformance'` in `addopts` — so `pytest` on a machine with no
container engine still passes, and the 100 % coverage gate on `media_sdk_m8`
never sees a docker-backed case. `test_harness_spec.py` asserts that
deselection, so it cannot be dropped by accident.

`--backend` accepts the names pinned in `contract.py`'s `CANDIDATE_BACKENDS`.
`minio`, `seaweedfs` and `garage` all have drivers as of `T4-run-garage`;
`PENDING_DRIVERS` is empty. Asking for a name outside `CANDIDATE_BACKENDS`
raises a usage error rather than skipping — a backend that silently skipped
would be indistinguishable in the matrix from one that passed.

The SeaweedFS driver boots `weed server -dir=/data -filer -s3
-ip=localhost -ip.bind=127.0.0.1 -s3.ip.bind=0.0.0.0
-s3.config=/etc/seaweedfs/s3.json`. The `-ip=localhost` is not in the plan's
original command — without it, `-ip.bind=127.0.0.1` binds the volume server to
loopback while components still *advertise* the container's routable address
(`-ip`'s default), so the filer's own writes to the volume server fail with a
500. See `MATRIX.md`'s D1 for the observed failure. `-s3.config` is a static
identities file (`admin`: bootstrap-only, `media-rw`: scoped per bucket) —
SeaweedFS has no runtime user-creation API to mirror MinIO's
`mc admin user add`, so there is no separate bootstrap container for this
driver; the buckets are created directly through the admin handle once the
gateway answers.

The Garage driver mounts a single-node config (`replication_factor = 1`,
`rpc_bind_addr`/`rpc_public_addr` both pinned to `127.0.0.1:3901`) and
bootstraps entirely through `docker exec … /garage <subcommand>` against the
running node — Garage has neither MinIO's one-shot `mc` container nor
SeaweedFS's static identities file. A single-node cluster still needs an
explicit layout before it accepts writes: `garage node id -q` for the node's
own ID, `garage layout assign -z dc1 -c 1G <id>` then
`garage layout apply --version 1`. Buckets are `garage bucket create`; access
keys come from `garage key create <name>` (parsed from its human-readable
output — Garage's CLI has no `--format json` for this subcommand) and are
granted per bucket with `garage bucket allow --read --write [--owner] --key
<id> <bucket>`. `docker exec` shares the container's network namespace, so the
CLI reaches `127.0.0.1:3901` even though that port is never published and a
sibling container cannot reach it (S2).

## What the harness sets up

`backends.py` boots the backend on a throwaway docker network, publishes only
the S3 port on `127.0.0.1` with an ephemeral host port, and then bootstraps it
the way the shipped stack does: the five media buckets, and a `media-rw`
identity holding the *same* scoped policy JSON as `hardened_media_m8`'s
`minio-init` — `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`,
`s3:ListBucket` on those five buckets and nothing else. A sixth bucket,
`unlisted-media`, is created by root and left out of the policy so `S4` has
something real to be denied on.

Every test drives the **scoped** handle. `backend.admin` exists only for the
two places a case legitimately needs an operation the application never
performs: planting an object in the unlisted bucket, and asking whether a
non-existent bucket exists. An invariant proved with root credentials would
prove nothing about the deployed system.

Credentials are generated per session and passed to containers through
`--env NAME`, never in argv, so a failure message can quote the command
without disclosing them.

## What counts as proof

`S2` is a claim about what a **sibling container** can reach, not what the
host or the SDK's own client can reach — either of those would prove
something weaker. `sibling_port_reachable()` runs a fresh, pinned `busybox`
container on the backend's own docker network and asks it to `nc -z` each of
`backend.admin_ports` — SeaweedFS's master/volume/filer/webdav, or Garage's
single RPC port. MinIO's driver leaves that tuple empty (its admin API *is*
the S3 port, so there is nothing separate to probe) and the case skips with a
reason rather than asserting something MinIO cannot structurally do.

The security rows — `S9`, `S10`, `S11`, `S12` — are claims about what the
*server* does. Each is therefore observed over raw HTTP in `probe.py`: a status
line, a `Content-Disposition` header, a `Content-Range` header. No test may
pass because a client-side guard raised first; that is exactly the failure mode
a compatibility table hides.

Two habits keep the negative cases honest:

* **Positive controls.** `S9` sends an oversized body, an undersized body and a
  conforming one through the *same* signed conditions. Without the third, the
  two refusals could be any unrelated 4xx.
* **Absence checks.** A refusal is only meaningful if the object did not land.
  `_absent()` re-asks the server after each rejected POST.

`REFUSED` is `400..499` on purpose: a 5xx is a crashed backend, not an enforced
policy, and must not read as a pass.

## Adding a backend

Write a `@contextmanager` that boots the pinned image and yields a
`BackendUnderTest`, register it in `DRIVERS`, and remove its entry from
`PENDING_DRIVERS`. Nothing in the case modules should need to change — if it
does, the deviation belongs in `MATRIX.md` with a severity, which is what
`T3`/`T4` are for.
