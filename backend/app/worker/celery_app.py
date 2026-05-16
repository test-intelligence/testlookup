"""Celery application configuration."""
from celery import Celery
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

celery_app.conf.task_queues = (
    Queue("critical",    _default_exchange, routing_key="critical",    queue_arguments={"x-max-priority": 10}),
    Queue("ingestion",   _default_exchange, routing_key="ingestion",   queue_arguments={"x-max-priority": 10}),
    Queue("ai_analysis", _default_exchange, routing_key="ai_analysis", queue_arguments={"x-max-priority": 10}),
    Queue("default",     _default_exchange, routing_key="default",     queue_arguments={"x-max-priority": 10}),
)

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
        "app.worker.training_tasks.export_training_data":   {"queue": "default"},
        "app.worker.training_tasks.check_finetune_trigger": {"queue": "default"},
        "app.worker.training_tasks.run_finetune_pipeline":  {"queue": "default"},
        "app.worker.tasks.reindex_search":                    {"queue": "default"},
        "app.worker.tasks.*":                               {"queue": "default"},
    },
    beat_schedule={
        "daily-coverage-snapshot": {
            "task": "app.worker.tasks.take_coverage_snapshot",
            "schedule": crontab(hour=0, minute=5),
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
        # RAG-6: Knowledge source freshness re-sync (every 4 hours)
        "knowledge-source-resync": {
            "task": "app.worker.tasks.resync_stale_knowledge_sources",
            "schedule": crontab(minute=0, hour="*/4"),
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
