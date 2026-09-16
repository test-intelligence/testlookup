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
