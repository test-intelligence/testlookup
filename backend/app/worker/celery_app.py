"""Celery application configuration."""
from datetime import timedelta

from celery import Celery
from celery.signals import worker_process_init
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import settings

celery_app = Celery(
    "testlookup",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks", "app.worker.training_tasks"],
)

# ── Priority queues ────────────────────────────────────────────────────────────
# critical  (9)  — live test analysis, immediate user-facing results
# ingestion (7)  — test report parsing from MinIO
# ai_analysis(5) — offline pipeline (anomaly, root-cause, summary)
# default   (1)  — notifications, snapshots, housekeeping

_default_exchange = Exchange("default", type="direct")

# Phase 2.4 — declare every ingestion shard queue at boot so workers can
# subscribe without re-deriving the list. The legacy ``ingestion`` queue
# stays declared for non-shardable tasks (``ingest_test_run`` etc.) and
# for tests / deployments where ``LIVE_INGEST_SHARD_COUNT=0``. Build the
# tuple imperatively so the shard count is read from settings at startup,
# not frozen at module-import time.
def _build_task_queues() -> tuple[Queue, ...]:
    base = [
        Queue("critical",    _default_exchange, routing_key="critical",    queue_arguments={"x-max-priority": 10}),
        Queue("ingestion",   _default_exchange, routing_key="ingestion",   queue_arguments={"x-max-priority": 10}),
        Queue("ai_analysis", _default_exchange, routing_key="ai_analysis", queue_arguments={"x-max-priority": 10}),
        Queue("default",     _default_exchange, routing_key="default",     queue_arguments={"x-max-priority": 10}),
    ]
    # Lazy import to avoid the ``app.core.config`` → ``Celery`` import
    # cycle that bites if we put this at module top.
    from app.worker.ingestion_routing import all_shard_queues
    for q in all_shard_queues():
        base.append(Queue(q, _default_exchange, routing_key=q, queue_arguments={"x-max-priority": 10}))
    return tuple(base)


celery_app.conf.task_queues = _build_task_queues()

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_routes={
        "app.worker.tasks.run_live_test_analysis":          {"queue": "critical"},
        "app.worker.tasks.ingest_test_run":                 {"queue": "ingestion"},
        "app.worker.tasks.run_ai_analysis":                 {"queue": "ai_analysis"},
        "app.worker.tasks.run_agent_pipeline":              {"queue": "ai_analysis"},
        "app.worker.tasks.generate_run_compare_report":     {"queue": "ai_analysis"},
        "app.worker.tasks.precompute_suite_comparisons_for_run": {"queue": "ai_analysis"},
        "app.worker.tasks.generate_ai_test_cases_task":     {"queue": "ai_analysis"},
        "app.worker.tasks.create_ai_test_plan_task":         {"queue": "ai_analysis"},
        "app.worker.tasks.generate_ai_strategy_task":        {"queue": "ai_analysis"},
        "app.worker.tasks.run_fixer_run_task":               {"queue": "ai_analysis"},
        "app.worker.training_tasks.export_training_data":   {"queue": "default"},
        "app.worker.training_tasks.check_finetune_trigger": {"queue": "default"},
        "app.worker.training_tasks.run_finetune_pipeline":  {"queue": "default"},
        "app.worker.tasks.reindex_search":                    {"queue": "default"},
        "app.worker.tasks.run_retention_purges":            {"queue": "default"},
        "app.worker.tasks.*":                               {"queue": "default"},
    },
    beat_schedule={
        "daily-coverage-snapshot": {
            "task": "app.worker.tasks.take_coverage_snapshot",
            "schedule": crontab(hour=0, minute=5),
        },
        # PMF US-11.4: per-project retention purge. 02:00 UTC — the slot is
        # free (coverage snapshot 00:05, training export Sundays 02:00 is
        # queue-parallel, finetune check 03:00). Only projects whose
        # project_retention_policies row is enabled are touched; per-project
        # try/except inside the task so one failure can't stop the sweep;
        # each project gets a settings_audit_log purge record.
        "nightly-retention-purge": {
            "task": "app.worker.tasks.run_retention_purges",
            "schedule": crontab(hour=2, minute=0),
        },
        # Continuous learning pipeline
        "weekly-training-export": {
            "task": "app.worker.training_tasks.export_training_data",
            "schedule": crontab(hour=2, minute=0, day_of_week="sunday"),
        },
        "daily-finetune-trigger-check": {
            "task": "app.worker.training_tasks.check_finetune_trigger",
            "schedule": crontab(hour=3, minute=0),
        },
        "hourly-search-reindex": {
            "task": "app.worker.tasks.reindex_search",
            "schedule": crontab(minute=30),
        },
        # ENT-05: Scheduled digest delivery
        "daily-digest-dispatch": {
            "task": "app.worker.tasks.dispatch_scheduled_digests",
            "schedule": crontab(hour=7, minute=0),  # Daily at 07:00 UTC
        },
        # OPS-01: Integration health probes (every 15 minutes)
        "integration-health-probes": {
            "task": "app.worker.tasks.run_integration_health_probes",
            "schedule": crontab(minute="*/15"),
        },
        # PMF US-6.2: mirror Jira status onto linked OPEN defects (same
        # 15-minute cadence as the integration probes; offset by 5 minutes
        # so the two don't hit Jira in the same instant). Capped at ~50
        # issues per cycle inside the service — rate-limit respecting.
        "jira-defect-status-sync": {
            "task": "app.worker.tasks.sync_jira_defect_statuses",
            "schedule": crontab(minute="5-59/15"),
        },
        # Phase 3 — AI pipeline debouncer flush (every 2 minutes).
        # Drains the per-project SortedSet built by
        # services.ai_pipeline_debouncer.enqueue_pipeline_for_run,
        # applies the daily LLM cost-budget cap, then fans out
        # run_agent_pipeline tasks. See
        # docs/SCALABLE_INGESTION_DESIGN.md § Phase 3.
        "flush-ai-pipeline-queue": {
            "task": "app.worker.tasks.flush_ai_pipeline_queue",
            "schedule": crontab(minute="*/2"),
        },
        # RAG-6: Knowledge source freshness re-sync (every 4 hours)
        "knowledge-source-resync": {
            "task": "app.worker.tasks.resync_stale_knowledge_sources",
            "schedule": crontab(minute=0, hour="*/4"),
        },
        # FLK-P3: nightly retrain of the flaky-confidence model from human
        # quarantine decisions. No-op-safe until enough labeled decisions exist.
        "nightly-flaky-confidence-training": {
            "task": "app.worker.tasks.train_flaky_confidence_model",
            "schedule": crontab(hour=3, minute=30),
        },
        # Safety net for live sessions whose clients forgot to send
        # run_complete — without this the runs only show in Live Execution
        # and never propagate to Runs / Overview / Coverage / Failures /
        # Trends. The task is idempotent.
        #
        # 5-minute idle threshold (lowered from 10 on 2026-05-16 after a
        # homelab repro showed 5 ``running`` sessions in the API when
        # only 2 were emitting telemetry — the Live page now flags stale
        # rows immediately at the 60s mark via ``utils/liveSessionFreshness``
        # but the DB still owned the lie for up to 15 minutes). Sweep
        # every 2 minutes so worst-case staleness is ~7 minutes total
        # (5 min threshold + 2 min between sweeps).
        #
        # Tuning note: most test frameworks emit start/end events per
        # test, so legitimate inter-event gaps are well under a minute.
        # Workloads with single tests that take >5 min between events
        # (load tests, long e2e flows) should bump this back to 10 or
        # add SDK-side heartbeats. The Redis ``last_event_at`` hash is
        # the truth source for staleness — see the task body for the
        # NULL-last_event handling.
        "close-stale-live-sessions": {
            "task": "app.worker.tasks.close_stale_live_sessions",
            "schedule": crontab(minute="*/2"),
            "kwargs": {"idle_minutes": 5},
        },
        # Safety net for agent_pipeline_runs that got stuck in
        # status='running' — typically because a stage crashed mid-task
        # (OOM-kill, SIGKILL, asyncpg connection drop) before the outer
        # ``_mark_pipeline_done`` had a chance to record the failure.
        # The reaper marks any pipeline that has a failed stage OR has
        # been running past the task time_limit as ``failed``. The
        # /agents read-time derivation already shows the correct badge,
        # but this updates the DB so historical filters work cleanly.
        "reap-stuck-agent-pipelines": {
            "task": "app.worker.tasks.reap_stuck_agent_pipelines",
            "schedule": crontab(minute="*/10"),
        },
        # Tier 1 item 3: flaky-test quarantine maintenance (nightly at 04:00 UTC).
        # No-op until the ``flaky_auto_quarantine`` feature flag is enabled.
        "nightly-flaky-quarantine-maintenance": {
            "task": "app.worker.tasks.run_flaky_quarantine_maintenance",
            "schedule": crontab(hour=4, minute=0),
        },
        # Tier 2 item 10: per-test duration baseline refresh (nightly at
        # 04:30 UTC, after quarantine maintenance so its reclassifications
        # land first). No-op until ``perf_regression_detection`` flag is on.
        "nightly-perf-baseline-refresh": {
            "task": "app.worker.tasks.refresh_perf_baselines",
            "schedule": crontab(hour=4, minute=30),
        },
        # Tier 2 item 12: weekly auto-retro digest dispatch (Mondays
        # 07:00 UTC, same time as the existing daily digest pass). The
        # dispatcher inspects each subscription and only fires those
        # with ``schedule == WEEKLY_RETRO`` on the right day of week.
        "monday-weekly-retro-digests": {
            "task": "app.worker.tasks.dispatch_scheduled_digests",
            "schedule": crontab(hour=7, minute=5, day_of_week="monday"),
        },
        # Agentic plan AI-7: weekly flaky-debt review drafts to the US-7.3
        # team channels (Mondays 07:10 UTC, right after the digest passes).
        # Teams without a channel get their draft folded into the weekly
        # digest instead. Deterministic text only — no LLM calls.
        "monday-weekly-flaky-debt-reviews": {
            "task": "app.worker.tasks.dispatch_weekly_flaky_debt_reviews",
            "schedule": crontab(hour=7, minute=10, day_of_week="monday"),
        },
        # Agentic plan AI-2 (the Fixer): scheduled fixer runs. No-op unless a
        # project's fixer policy is enabled with the matching schedule. Daily
        # at 06:00 UTC; weekly on Mondays at 06:15 UTC.
        "daily-fixer-runs": {
            "task": "app.worker.tasks.dispatch_scheduled_fixer_runs",
            "schedule": crontab(hour=6, minute=0),
            "args": ("daily",),
        },
        "weekly-fixer-runs": {
            "task": "app.worker.tasks.dispatch_scheduled_fixer_runs",
            "schedule": crontab(hour=6, minute=15, day_of_week="monday"),
            "args": ("weekly",),
        },
        # AI-2 outcome loop: poll open fixer-created PR states every 30 min and
        # feed merged/closed outcomes back through record_fix_outcome (AI-5).
        "poll-fixer-pr-outcomes": {
            "task": "app.worker.tasks.poll_fixer_pr_outcomes",
            "schedule": crontab(minute="*/30"),
        },
        # P2-3 (DB audit 2026-05-16): nightly check for orphan TestSuite
        # rows left behind by finalize_run's per-step isolation. Emits a
        # structured WARNING + Prometheus counter per orphan so ops can
        # decide whether to reassign / delete; the task never mutates
        # data itself. Scheduled at 05:00 UTC, AFTER the perf-baseline
        # refresh at 04:30 — ingestion typically lands earlier in the
        # night and any suite_sync/canonical_sync split is well past
        # the 60-minute "still mid-ingest" cooldown by 05:00.
        "nightly-orphan-test-suite-flag": {
            "task": "app.worker.tasks.flag_orphan_test_suites",
            "schedule": crontab(hour=5, minute=0),
        },
        # Phase I follow-up: project-wide canonical-deletion reconcile.
        # finalize_run already calls the same service per-run; this beat
        # is the safety net for (a) ingest-time isolated-session failures
        # and (b) quiet projects with no new runs. 05:30 UTC keeps it
        # downstream of orphan-suite flagging so we don't race the
        # late-night ingestion tail.
        "nightly-canonical-deletion-reconcile": {
            "task": "app.worker.tasks.reconcile_canonical_deletions",
            "schedule": crontab(hour=5, minute=30),
        },
        # Backfill /my-failures inbox: any FAILED/BROKEN TestCase still
        # unassigned (project had no owner config at ingest time, or a
        # finalize_run step failed in isolation) gets re-resolved here.
        # The assignment service is idempotent — only NULL rows are
        # touched — so running every 15 minutes is safe and catches new
        # rows fast enough that QA leads aren't waiting for the next
        # ingest to see their queue populate.
        "backfill-unassigned-failures": {
            "task": "app.worker.tasks.backfill_unassigned_failures",
            "schedule": crontab(minute="*/15"),
        },
        # Phase 4.5: incremental drain of live-stream event buffers.
        # Without this, the 50K LTRIM cap on long-running sessions
        # silently drops the oldest per-test rows; HINCRBY aggregates
        # stay accurate so TestRun.total_tests reports a number the
        # test_cases table can't back up. The 30s cadence keeps the
        # window of at-risk events bounded; the per-run SET-NX lock
        # in the drainer makes overlapping ticks safe.
        # ``timedelta`` (not ``crontab``) — celery beat supports both,
        # and 30s isn't expressible with crontab granularity.
        "drain-active-live-sessions": {
            "task": "app.worker.tasks.drain_active_live_sessions",
            "schedule": timedelta(seconds=30),
        },
        # Retroactive placeholder synthesis for historical TestRuns
        # whose failure counters are populated but whose test_cases
        # table is empty (live-stream buffer was evicted or the SDK
        # never sent test_result events). Inserts the same placeholder
        # rows that ``persist_live_session`` now creates at write
        # time, so old runs flow into /my-failures + /test-management
        # alongside new ones. Hourly cadence — idempotent, and the
        # 15-min ``backfill-unassigned-failures`` beat then assigns
        # them within minutes. First run after deploy backfills the
        # entire history (capped at 500 runs/project).
        "backfill-placeholder-test-cases": {
            "task": "app.worker.tasks.backfill_placeholder_test_cases",
            "schedule": crontab(minute=10),  # once per hour at :10
        },
        # Auto-recovery for completed live_stream runs whose
        # close_session → persist_live_session handoff dropped on the
        # floor (silent apply_async failure, queue backpressure, worker
        # restart). Re-stages ``TestRun.event_archive`` into Redis and
        # re-queues the persist task so the REAL test names land in
        # ``test_cases`` within ~2 minutes — before the hourly
        # ``backfill-placeholder-test-cases`` task would mask them
        # with ``[ingestion gap …]`` rows.
        "auto-recover-completed-live-runs": {
            "task": "app.worker.tasks.auto_recover_completed_live_runs",
            "schedule": timedelta(minutes=2),
        },
        # Phase 4 — tiered duplicate authored-test-case detection. Sweeps every
        # project nightly at 06:00 UTC (downstream of the 05:30 canonical-
        # deletion reconcile so the catalog is settled first) and upserts
        # ``duplicate_test_case_candidates`` for the /test-management Duplicates
        # review queue. Offline-first + idempotent — the worker task owns the
        # per-project commit; the detection service stages only. Can also be
        # triggered ad-hoc per project from the router.
        "nightly-duplicate-detection": {
            "task": "app.worker.tasks.run_duplicate_detection",
            "schedule": crontab(hour=6, minute=0),
        },
    },
    # Prevent memory bloat from stale results
    result_expires=3600,
    # Worker reliability
    worker_prefetch_multiplier=1,   # fair dispatch — one task at a time per slot
    task_acks_late=True,            # ack only after task completes (safe retries on crash)
    worker_max_tasks_per_child=200, # recycle worker process after 200 tasks (prevent leaks)
    # Time limits: soft sends SIGTERM to task coroutine, hard sends SIGKILL
    task_soft_time_limit=1740,      # 29 min soft (pipeline tasks can run up to 30 min)
    task_time_limit=1860,           # 31 min hard
    # Connection resilience
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
)


# ── Post-fork DB pool isolation ──────────────────────────────────────────────
#
# Celery's default pool is prefork: ``--concurrency=N`` forks N children from
# this parent process. ``get_engine()`` / ``get_session_factory()`` are
# ``@lru_cache``'d, so if ANYTHING touches the database in the parent before the
# fork, every child inherits the same SQLAlchemy pool — and therefore the same
# open asyncpg sockets (same file descriptors). Two children driving one socket
# produces exactly:
#
#     asyncpg.exceptions._base.InterfaceError:
#     cannot perform operation: another operation is in progress
#
# observed on the ingestion worker during a 60-run bulk upload, where it made
# most first attempts fail and retry.
#
# ``_run_async`` in worker/tasks.py already handles the *event loop* half of this
# problem (BUG-003) — a pool bound to a loop that was since closed. It cannot
# help here: that is per-process bookkeeping, and this is one pool shared ACROSS
# processes by fork.
#
# The fix is to make each child build its own engine. Note it must CLEAR the
# caches, not dispose them: ``dispose()`` in a child would close sockets the
# parent and sibling children are still using. Clearing means the next
# ``get_engine()`` inside this child constructs a fresh engine, and the inherited
# one is simply never used again here.
@worker_process_init.connect
def _reset_db_pool_after_fork(**_kwargs: object) -> None:
    """Give every prefork child its own DB engine and connection pool."""
    from app.db.postgres import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()
