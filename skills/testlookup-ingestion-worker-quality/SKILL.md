---
name: testlookup-ingestion-worker-quality
description: Review and improve TestLookup ingestion and Celery worker tests. Use for batch or report ingestion, parsers, queue routing, scheduled jobs, delivery retries, idempotency, and recovery.
---

# TestLookup Ingestion and Worker Quality

## Trace acceptance to completion

- Read `docs/pipelines/ingestion.md` and `docs/reference/worker-operations.md`. Locate the input path, parser, task and queue in `backend/app/routers`, `backend/app/services/ingestion_pipeline.py` and `backend/app/worker/celery_app.py`. Identify which worker in the target profile consumes that queue and whether beat or the downstream outbox is required.
- Confirm the documented meaning of the acceptance response, task state, terminal data and asynchronous consumers. Compare expected counts and names from independently generated producer fixtures, including skipped/broken cases, and verify the persisted run and totals for the intended project/source identity.
- Check source identity across repeated submissions and attempts, retry caps and terminal failures. Inspect `backend/app/services/run_downstream_outbox.py` for post-commit delivery. Verify whether a recovery attempt resumes durable work or reprocesses it and check for duplicate downstream results.

## Select verification

- Run focused parser, normalization or routing pytest cases first. For real persistence use `backend/tests/integration/test_ingestion_identity_postgres.py` or the relevant disposable integration fixture. For changes to queue routing, compare `backend/tests/test_perf_celery_routes_match_topology.py` with broker delivery in `backend/tests/integration/test_compose_queue_delivery_redis.py`. Verify the consuming worker in the selected profile. Route live session persistence changes through the WebSocket skill as well.
- For a producer-to-release claim, use the format-specific runner cases under `frontend/tests/release` or `e2e-runner/live`, the current `TL-INGEST-*` catalogue and an isolated candidate. Verify receipt or outcome, worker completion, readback and cleanup. Add bounded synthetic failure and duplicate controls that match the documented input contract.
- Record producer and format, candidate, queue and workers, acceptance versus terminal result, durable reconciliation and release case coverage. Keep gated high-volume results distinct from focused tests.
