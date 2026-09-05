# Conformance harness

`CONTRACT.md` states what the object-storage backend must do. This harness is
what actually asks a running backend whether it does it.

## Running it

```bash
pytest -m conformance --backend=minio          # the baseline
pytest -m conformance --backend=minio --no-cov # same, without the coverage report
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
Only `minio` has a driver at this step; `seaweedfs` and `garage` raise a usage
error naming the plan step that adds theirs (`T3-run-seaweedfs`,
`T4-run-garage`). That is deliberate — a backend that silently skipped would be
indistinguishable in the matrix from one that passed.

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
