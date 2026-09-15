# Conformance matrix

Recorded runs of `pytest -m conformance --backend=<name>` against each
candidate. A column exists once its backend has a driver in `backends.py`; a
`—` cell means the row could not be evaluated against that backend by design,
not that it was skipped by omission — the reason is stated in the notes below
each table.

`T4-run-garage` adds the Garage column, completing every candidate
`CANDIDATE_BACKENDS` pins.

Every cell below was measured **twice**: once with the `minio-py` client
(Wave 0) and once with the boto3/botocore client that replaced it
(`T6-boto3-storage-core`). Both readings agree, cell for cell — see
[Client re-measurement](#client-re-measurement).

## S3 surface (`OP-01`–`OP-13`)

Every row is `Severity.BLOCKER`: a backend failing one cannot host this stack.

| Case | Operation | MinIO `RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772` | SeaweedFS `4.45` | Garage `v2.3.0` |
|---|---|---|---|---|
| OP-01 | PostObject | ✅ pass | ✅ pass | ✅ pass |
| OP-02 | GetObject (presigned) | ✅ pass | ✅ pass | ✅ pass |
| OP-03 | GetObject (ranged) | ✅ pass | ✅ pass | ✅ pass |
| OP-04 | GetObject (streamed) | ✅ pass | ✅ pass | ✅ pass |
| OP-05 | HeadObject | ✅ pass | ✅ pass | ✅ pass |
| OP-06 | PutObject (bytes) | ✅ pass | ✅ pass | ✅ pass |
| OP-07 | PutObject (streamed) | ✅ pass | ✅ pass | ✅ pass |
| OP-08 | CopyObject (REPLACE) | ✅ pass | ✅ pass | ✅ pass |
| OP-09 | CopyObject (cross-bucket) | ✅ pass | ✅ pass | ✅ pass |
| OP-10 | DeleteObject | ✅ pass | ✅ pass | ✅ pass |
| OP-11 | ListObjectsV2 (paged, 1001 keys) | ✅ pass | ✅ pass | ✅ pass |
| OP-12 | HeadBucket | ✅ pass | ✅ pass | ✅ pass |
| OP-13 | Multipart upload | ✅ pass | ✅ pass | ✅ pass |

## Security invariants (`S2`, `S4`, `S5`, `S9`–`S12`)

The live rows the conformance suite can prove. `S1`, `S3`, `S6`–`S8`, `S13`–`S15`
are static-policy rows owned by other suites (`T10`, `T18`, `T19`, `T22`) and
are out of scope here.

| Case | Invariant | MinIO | SeaweedFS | Garage |
|---|---|---|---|---|
| S2 | Admin/master/filer/webdav/RPC unreachable from a sibling | — not evaluable (see note) | ✅ pass | ✅ pass |
| S4 | Scoped credential denied outside its 5 buckets | ✅ pass | ✅ pass | ✅ pass |
| S5 | Unsigned request refused on every bucket | ✅ pass | ✅ pass | ✅ pass |
| S9 | `content-length-range` enforced server-side | ✅ pass | ✅ pass | ✅ pass |
| S10 | `Content-Type` pinned + REPLACE rewrite | ✅ pass | ✅ pass | ✅ pass |
| S11 | `response-content-disposition` honoured | ✅ pass | ✅ pass | ✅ pass |
| S12 | Ranged GET returns 206 | ✅ pass | ✅ pass | ✅ pass |

**S2 / Garage — passed, one port to close instead of three.** Garage has a
single extra listener beyond the S3 gateway: RPC (cluster gossip and the CLI's
admin channel, port 3901). Pinning `rpc_bind_addr`/`rpc_public_addr` to
`127.0.0.1:3901` in the mounted config closes it to siblings while the
harness's own bootstrap — `docker exec … /garage <subcommand>` — keeps working,
because `docker exec` shares the container's network namespace and reaches
`127.0.0.1:3901` directly. The same fresh-`busybox`-on-the-backend's-network
probe used for SeaweedFS's `S2` (`sibling_port_reachable()`) confirms 3901
refused while 3900 (S3) answered.

**S2 / MinIO — not evaluable, not failed.** MinIO has one port and one
process: the S3 data path and the admin API are the same listener
(`minio:9000`), so "unreachable from a sibling while the S3 port stays
reachable" has no MinIO shape to assert. Today's posture instead comes from
Traefik's `!PathPrefix(/minio)` exclusion — a routing-layer control, not a
storage-layer one. `test_storage_admin_surface_unreachable_from_siblings`
calls `pytest.skip()` on `backend.admin_ports == ()` (true only for the MinIO
driver) rather than asserting or omitting silently; the MinIO run shows
`19 passed, 1 skipped`.

**S2 / SeaweedFS — passed, and proved from the right vantage point.** A fresh
`busybox` container on the backend's own docker network (never the host,
never through the SDK's own client) attempted a raw `nc -z` connect to
`master:9333`, `volume:8080`, `filer:8888` and `webdav:7333`; all four refused
while the same probe against `8333` succeeded, confirming the probe mechanism
itself is not just universally blind. This is the structural win over MinIO's
Traefik-only posture that §3 of the plan claims: the isolation lives in the
storage process itself, not in a reverse-proxy rule that a misconfigured route
could silently drop.

## Deviations from the MinIO baseline

| # | Finding | Severity | Detail | Feeds |
|---|---|---|---|---|
| D1 | The plan's literal boot command breaks all writes | **blocker in that literal form, closed here** | `weed server -dir=/data -filer -s3 -ip.bind=127.0.0.1 -s3.ip.bind=0.0.0.0` (the command named in `T3`'s own acceptance text) makes every write fail with a 500: `-ip.bind=127.0.0.1` binds the volume server to loopback, but `-ip` (the address components *advertise* to each other) is left at its container-IP default, so the filer's own chunk upload to the volume server dials the container's routable address and gets `connection refused` — observed directly in `docker logs` (`upload_content.go:335 … dial tcp 172.24.0.2:8080 … connection refused`). Adding `-ip=localhost` so bind and advertise agree fixes it with no other change; `backends.py`'s driver carries this as a code comment, not just here. | `T15-storage-service-block` — the hardened compose command must set `-ip` explicitly, not only `-ip.bind` |
| D2 | No distinct delete action | workaround | SeaweedFS's S3 IAM identities have `Read`/`Write`/`List`/`Tagging`/`Admin`; there is no `Delete` verb distinct from `Write`. MinIO's `media-rw` policy grants exactly `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` — four verbs. The SeaweedFS driver's `media-rw` identity is granted `Read`/`Write`/`List` per bucket, which is coarser (`Write` also authorizes delete) but not broader in bucket scope — `S4`'s negative case (denial on the unlisted bucket) still passes because the identity has no grant on that bucket at all. Recorded, not hidden: a coarser verb set is not the same claim as "identical to MinIO's ACL". | `T16-storage-bootstrap` — document the verb mapping when writing the real identity |
| D3 | CORS is not an env var | workaround | MinIO's CORS scoping is one process-wide env var (`MINIO_API_CORS_ALLOW_ORIGIN`). SeaweedFS 4.x implements `PutBucketCors`/`GetBucketCors` per bucket instead — confirmed present in the S3 API surface, not exercised by this step (the harness does not call it; `S3` is a static-policy row owned by `T18`, not this suite). The bootstrap step must issue one `PutBucketCors` call per bucket rather than setting one variable. | `T16-storage-bootstrap` |
| D4 | Two unauthenticated paths on the S3 port | cosmetic, input to `T17` | An unauthenticated `GET` on port 8333 returns `403 AccessDenied` XML for every path probed **except** `/healthz` and `/status`, both `200` with an empty body and no `Content-Type`. Neither leaks bucket names, object data, or any header beyond a bare 200 — a plain liveness probe, and the only two exceptions among `/`, `/public-media`, `/metrics`, `/favicon.ico`, `/.well-known/`, `/admin`, `/dir/status`, all of which are `403`. Recorded as the exhaustive enumeration `T3`'s acceptance criteria ask for; `T17` must decide whether Traefik forwards these two paths (harmless liveness) or denies them outright for a smaller advertised surface. | `T17-traefik-storage-route` |

No `S9`–`S12` deviation exists: every server-side security control MinIO
enforces, SeaweedFS 4.45 enforces identically, observed the same way (raw HTTP
status lines and response headers, never a client-side guard). `S11`'s version
floor (`response-content-disposition` only fixed in SeaweedFS ≥ 4.01) is
already asserted statically by `test_seaweedfs_candidate_is_at_least_4_01` in
`test_contract_spec.py`; `4.45` clears it with room.

**Garage `v2.3.0`.** No deviation from the MinIO baseline was found: every
surface row and every live invariant row this harness evaluates passed on the
first bootstrap, including `S2` (see above). The one documented Garage
limitation the plan's own §2.2 pre-analysis names — no distinct delete verb,
only Read/Write/Owner per key per bucket — is real (`garage bucket allow`'s
`--help` confirms the vocabulary) but never surfaces as a *test* deviation
here: `S4`'s negative case only asserts denial on a bucket outside the grant,
which Garage enforces exactly like MinIO and SeaweedFS. It is recorded as
input to a future Garage bootstrap step (mirroring `T3`'s D2 for SeaweedFS),
not as a matrix failure.

## Client re-measurement

`T6-boto3-storage-core` swapped the SDK's internals from `minio-py` to
boto3/botocore while the backends stayed exactly as pinned above. Because this
matrix is the evidence `.workspace/context/object-storage.md` cites, the whole
matrix was re-measured with the new client rather than assumed to carry over:

| Backend | `minio-py` (Wave 0) | boto3/botocore (`T6`) |
|---|---|---|
| MinIO `RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772` | 19 passed, 1 skipped (`S2`) | 19 passed, 1 skipped (`S2`) |
| SeaweedFS `4.45` | 20 passed | 20 passed |
| Garage `v2.3.0` | 20 passed | 20 passed |

No row changed verdict and no deviation above was added, removed or
re-classified. Two client-visible differences were absorbed by the harness, and
neither is a statement about a server:

* **`OP-12` now goes through `ObjectStorage.bucket_exists`** instead of
  reaching into `backend.storage.client` for `minio-py`'s method of the same
  name. The SDK owns the present/absent semantics the row asserts, so the row
  no longer depends on which library sits underneath.
* **`OP-10`'s client-side error code.** A `HEAD` response carries no body, so
  botocore reports `404` where a body-carrying error names the S3 code
  (`NoSuchKey`). The assertion accepts both. The row's server-side half — the
  presigned `GET` returning `404` off the wire — is unchanged and is what
  actually proves the deletion.

Two client-configuration choices were made to keep the wire format identical
across backends rather than AWS-shaped; both are asserted by the SDK's unit
suite:

* `request_checksum_calculation` / `response_checksum_validation` are set to
  `when_required`. Left at botocore's default, every upload carries an
  `x-amz-checksum-*` trailer that several S3-compatible servers reject; the
  stack has never depended on those trailers.
* Path-style addressing and `signature_version="s3v4"` are pinned explicitly —
  a self-hosted endpoint has no virtual-host DNS, and the contract is SigV4.

## Verdict

SeaweedFS `4.45` and Garage `v2.3.0` both pass every row this harness can
evaluate against them — SeaweedFS **20/20** (D1 fixed in the driver, not
merely noted), Garage **20/20** on the first bootstrap. Neither candidate is
disqualified by §4.2's regression checklist; the decision in §3 therefore
turns on the licence/patch-velocity/feature-ceiling trade the plan states, not
on either candidate failing a technical row. `T5-ratify-backend` records that
decision — SeaweedFS as the default, Garage as the validated fallback — in
`.workspace/context/object-storage.md`.
