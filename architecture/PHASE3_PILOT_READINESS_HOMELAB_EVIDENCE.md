# Phase 3 pilot-readiness and homelab evidence

This record is intentionally kept under `architecture/` so it is included in
the source tree; the repository-wide `docs/` ignore rule is for local working
notes and deployment scratch artifacts.

## Current implementation gate

The fail-closed pilot-readiness contract requires two consecutive passing
evaluation cycles over the same corpus hash and qualified-user utility of at
least 80%. The QA-lead endpoint and evaluation dashboard expose explicit
`ready`/`not_ready` status and blocking reasons. Default-on specialist fan-out
and external mutation remain disabled until governance and pilot evidence are
approved.

## Regression evidence

- Phase 3 cluster-child/runtime/evidence/budget/migration suite: **173 passed**.
- Routing/provenance/transaction-boundary regression set after the final
  routing and agent-subclass changes: **94 passed, 1 skipped**.
- Architectural quality gate: **22/22 guards passed**.
- Pilot/report-version backend suite: **26 passed**.
- Frontend report/readiness suite: **35 passed**.
- Frontend production TypeScript/Vite build: **passed**.
- Deterministic Phase 4 specialist/report corpus (Contract Agent, Log
  Intelligence, RegressionWatchman, and report quality): **6 passed**.
- Ruff: **passed**.
- `git diff --check`: **passed**.
- Alembic: **0128 (head)**.

## Homelab rollout

Verified against the configured K3s homelab via the control node on the current
source revision:

- Build tag: `build-20260813-033346`.
- Secret-free build-context SHA-256: `ebcaa5a39732ca96b872842c4606504a07c981903d925ef102a0ec4f65c26baa`.
- Backend: `sha256:04b93d29eac54ba4c3a555886c08e7cadf72c238c95f9cc348f05d5ae4b42076`.
- Frontend: `sha256:d25aa538d0d3e3da52986bc3672418e51c0263a67687cf3b2d5d7619022217c3`.
- MCP: `sha256:b0ca5b695aa7b5763b966b2373b8b1b5f0f498270b0130cb6c6d67b3403a6383`.
- All application deployments rolled out and resulting pods reached Ready.
- Backend `/health/details` reported `healthy`; PostgreSQL, MongoDB, Redis,
  MinIO, Ollama, and ChromaDB checks reported `ok`.
- Deployed build provenance reported revision `worktree-32734d4f`.
- The ingress VIP was `192.168.0.201` (the current Traefik LoadBalancer address).
- Alembic in the deployed backend reported `0128 (head)`.
- Ingress `/health/live` with Host `testlookup.local` returned HTTP 200;
  frontend ingress returned HTTP 200.
- The protected pilot-readiness endpoint returned HTTP 401 without credentials.
- Temporary Kaniko jobs, namespace, source archive, and extracted build context
  were removed after the rollout.

## Live recheck (2026-08-13 04:11 UTC)

Using the bundled Kubernetes client, the deployed namespace was rechecked
without changing cluster state:

- All 18 pods were `Running` and Ready across 15 deployments.
- Both backend replicas resolved to the recorded backend digest.
- In-pod `/health/live` returned HTTP 200 with `status=alive`.
- In-pod `/health/details` returned `healthy`; PostgreSQL, MongoDB, Redis,
  MinIO, Ollama, and ChromaDB checks were healthy.
- The deployed backend reported Alembic `0128 (head)`.
- `AI_OFFLINE_MODE=true`; cluster-child and async supersession flags remained
  unset, preserving their default-off behavior.
- The protected PostgreSQL integration set was copied into an ephemeral `/tmp`
  test directory in the backend pod and ran against the homelab database with
  the repository's pytest configuration: **16 passed**. The run covered action
  ledger, memory lifecycle, change/ownership, cluster cancellation/outbox/
  retention, evidence authority, Investigator budget/claim/resume, pipeline
  resume, and report-evaluation persistence.

This is deployment and operational verification, not a pilot approval. Named
owners/personas, an approved representative corpus, qualified-user feedback,
two passing cycles, and provider/privacy signoff remain external gates.

## Live recheck (2026-08-13 04:39 UTC)

A follow-up read-only check confirmed the same deployed state after the final
local regression run:

- All 18 pods remained `Running` and Ready; the deployed image tag remained
  `build-20260813-033346`.
- In-pod `/health/live` returned `{"status":"alive"}` and `/health/details`
  remained `healthy` with all six dependency checks `ok`.
- The deployed backend still reported Alembic `0128 (head)`.
- The local Phase 3 runtime/cluster/report/CI-contract regression selection
  completed with **113 passed**; the governance/evaluation selection completed
  with **43 passed, 2 skipped** and Ruff passed.

No cluster mutation or redeploy was needed because the intervening changes were
CI, test, and evidence-documentation changes; the deployed runtime image was
unchanged.

## Notification history regression and hotfix (2026-08-13)

The first post-Phase-3 rollout exposed a schema-projection regression on
`GET /api/v1/notifications/history`: report-binding fields intended only for
`chat_sessions` had also been mapped onto `NotificationLog`, while migration
0122 did not create those columns on `notification_logs`. PostgreSQL therefore
returned `UndefinedColumn` and the endpoint surfaced HTTP 500.

The fix removes the unmigrated mappings, adds a projection regression test, and
was verified with the real homelab database before rollout. The corrected
backend image `fix-notification-history-20260813` rolled out to backend, beat,
and worker deployments; all 18 pods are Ready, `/health/details` is healthy,
Alembic remains at `0128 (head)`, and the notification-history service query
returns successfully. Unauthenticated HTTP requests correctly return 401.
The regression is also explicitly included in the protected PostgreSQL/Mongo
CI job alongside the other clean-database integration checks.
