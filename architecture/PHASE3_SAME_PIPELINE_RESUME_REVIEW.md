# Phase 3 Same-Pipeline Resume Review

Status: implemented and deployed; default behavior remains unchanged.

## Scope

- Atomic terminal-pipeline claim under `SELECT ... FOR UPDATE`.
- Same pipeline UUID and immutable initial plan/cluster-child settings on resume.
- Frozen analysis-mode snapshot required before a retry can be claimed.
- Completed stage checkpoints require body checksum, runtime versions, attempt, and deterministic idempotency receipt.
- Incomplete stages reset to `pending` with an incremented attempt; pipeline-bound report/evidence stages are not imported from another pipeline.
- Non-retrying Celery entry point: `app.worker.tasks.resume_agent_pipeline`.

## Verification

- Focused tests: `66 passed` across checkpoint/resume, pipeline hardening, and agentic-runtime projection suites.
- Ruff: passed for workflow, agent export, worker task, and checkpoint tests.
- Python import smoke: workflow and worker task imports passed.
- `git diff --check`: passed for the scoped source/test/doc files.
- Multi-pass reports: `docs/reviews/phase3-same-pipeline-resume/00-action-plan.md` (READY WITH NITS; no Blocker/Major findings).

## Homelab evidence (2026-08-12 UTC)

- Build/deploy tag: `build-20260812-043323`.
- Backend migration head: `0121 (head)`.
- All 16 TestLookup pods reported Ready; backend and ingestion are two-replica deployments.
- Worker log contains `app.worker.tasks.resume_agent_pipeline` registration.
- Registry frontend manifest digest matched the Ready frontend pod image ID:
  `sha256:8831cf9f2ee008dc7a393693804132ffc2a718e63a4756497670606fed338343`.
- `http://testlookup.local/health/live` returned `status=alive`.
- Kustomize overlay placeholders were restored after deploy.

## Deferred operational gate

The local environment does not have Docker/PostgreSQL test infrastructure, so a live PostgreSQL concurrent-claim/rollback test remains open. The homelab database migration and runtime deployment smoke passed; this does not prove contention behavior under concurrent workers.
