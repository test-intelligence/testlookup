# M26 preflight checkpoint

**Agent and time:** GPT-6 Astra, 2026-09-16T13:23:48Z.
**Source:** `34110eace555423a4326b15e3ba502ac3ac2e4d4` on
`codex/exploratory-quality-release`; execution-package documentation was
uncommitted at this checkpoint.
**Target:** Kubernetes context `default`, namespace `testlookup`, ingress
`testlookup-traefik-ingress` at `192.168.0.201` for host
`testlookup.local`.

Read-only inspection found all 17 deployment pods ready. The stable application
tag was `build-20260912-012628`; observed application digest prefixes were
backend/workers `sha256:4f9d5d5f`, frontend `sha256:ba72e8ec`, and MCP
`sha256:fe3c1744`. `/health/live`, `/health/ready`, and `/health/details`
passed. PostgreSQL, MongoDB, Redis, MinIO, and ChromaDB reported healthy;
Ollama was skipped because the deployment uses hosted OpenRouter.

The running backend reports version `0.0.1` but reports both build revision and
build date as `unknown`. Its database is at Alembic head `0172`; the checked-out
source has one head, `0189`. This stable deployment can support exploratory
discovery, but it cannot prove candidate behavior. A reviewed commit and full
candidate deployment are required before candidate verification.

The backup CronJob is scheduled daily at `17 2 * * *`; the three latest
observed jobs completed successfully, including the 2026-09-16 job. PVCs were
bound. No restore or rollback was attempted because the namespace is shared and
no disposable restore target has been established.

**Result:** RUNNING. Preflight and stable-deployment inventory passed. Candidate
build/deploy, migration to `0189`, per-container digest verification, live
mission matrix, rollback rehearsal, and restored-candidate proof remain.

## Candidate checkpoint — 2026-09-16T15:56:50Z

Exact commit `e40fbddb564d6139c9ac2ad0c899773d0da4608c` was built as
`build-20260916-154849` and deployed at Alembic `0189 (head)`. The supported
deploy script verified every application workload's image digest and the
serving backend revision before continuing. `/health/version` reported the
same commit and build time `2026-09-16T15:49:08Z`; ready and detailed health
were green.

Observed manifest digests were backend/workers
`sha256:f85e1d2a440c7630f520f260a43688ab2e3725dc7c918dfb6ef1f213204dcaab`,
frontend
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`,
and MCP
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.

The deployment also reproduced EXP-BUG-007: a later broad backend label wait
includes retained completed migration pods, delays for 300 seconds, and skips
admin creation even while the backend is `1/1 Running`. The final live health
check still passed. M26 remains RUNNING for that fix, rollback rehearsal, and
restored-candidate proof.

## Candidate checkpoint — 2026-09-16T16:32:35Z

Exact runtime commit `230abe7d4caaaf827861f37c7ac6fc02b35f5604` was
built as `build-20260916-161635` and deployed before its green validation.
`/health/version` reported the same revision and build time
`2026-09-16T16:16:54Z`; ready and detailed health were green; all nine
application Deployments were `1/1`; Alembic remained `0189 (head)`.

Observed manifest digests were backend/workers
`sha256:31def477b1d38584bf4f6fb99983744d93522265ad943b14fa924a31d287a49a`,
frontend
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`,
and MCP
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
The strict workload/digest and serving-revision authority check passed again.
EXP-BUG-007 again added 300 seconds of false waiting and skipped initial-admin
creation despite the healthy backend; no runtime health failure occurred.

## Candidate checkpoint — 2026-09-16T17:02:47Z

Exact runtime commit `5297fea9b6dd9d538a58190b0c5168f91209e592` was
built as `build-20260916-164412` and deployed before the final M01 validation.
`/health/version` reported the same revision and build time
`2026-09-16T16:44:32Z`; ready and detailed health were green; the serving
backend, frontend, and MCP Deployments were `1/1`; Alembic remained
`0189 (head)`.

Observed manifest digests were backend/workers
`sha256:10946d67ddfe6d2d0689b8382922b2cd97e3f35e3e66b9f1d2fd47ecd7529e8f`,
frontend
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`,
and MCP
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
The strict workload/digest and serving-revision authority check passed.
EXP-BUG-007 again caused the broad-selector false waits; final health and
revision authority were unaffected.

## Candidate checkpoint — 2026-09-16T17:33:50Z

Exact runtime commit `7b38d58af5f8a86f25155fe2c858dc2d1564aaee` was built
as `build-20260916-171741` and deployed before final M01 validation.
`/health/version` reported that revision and build time
`2026-09-16T17:17:42Z`; ready and detailed health were green; all nine
application Deployments were ready; Alembic remained `0189 (head)`.

Observed manifest digests were backend/workers
`sha256:8bc943186f12ad839c3861c518f683731d7778a10d4b4a021064b4a96b5c7bac9`,
frontend
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`,
and MCP
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
The strict workload/digest and serving-revision authority check passed.
EXP-BUG-007 reproduced with the expected 180-second and 120-second false waits;
the serving backend stayed `1/1 Running` and final ingress health passed.
