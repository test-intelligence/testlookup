"""Celery application configuration."""
from datetime import timedelta
from pathlib import Path

from celery import Celery
from celery.signals import (
    celeryd_init,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_process_shutdown,
    worker_ready,
    worker_shutdown,
)
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import settings

celery_app = Celery(
    "testlookup",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks", "app.worker.training_tasks"],
)


@celery_app.task(bind=True, name="app.worker.celery_app.queue_delivery_probe")
def queue_delivery_probe(self, correlation_id: str) -> dict[str, str]:
    """Return transport attribution for the release queue-consumption proof."""
    delivery = self.request.delivery_info or {}
    return {
        "correlation_id": str(correlation_id),
        "routing_key": str(delivery.get("routing_key") or ""),
        "worker": str(self.request.hostname or ""),
    }

# ── Priority queues ────────────────────────────────────────────────────────────
# critical  (9)  — live test analysis, immediate user-facing results
# ingestion (7)  — test report parsing from MinIO
# ai_analysis(5) — parent offline/deep pipelines
# agent_children(3) — bounded cluster Investigator children (isolated workers)
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
        Queue("agent_children", _default_exchange, routing_key="agent_children", queue_arguments={"x-max-priority": 10}),
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
        "app.worker.tasks.run_agent_child_investigation":   {"queue": "agent_children"},
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
    # Redis keeps late-acked deliveries in ``unacked`` until this timeout.
    # Keep it above the 31-minute hard task limit so a healthy long-running
    # task is not duplicated, while still bounding recovery after a worker or
    # broker failure. Durable child outbox reconciliation is the faster
    # recovery path for cluster investigations.
    broker_transport_options={"visibility_timeout": 3600},
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
        # F-11: the only scheduled quality reading. Every other eval path is
        # event-driven (the gate fires on a prompt change and never otherwise),
        # so drift between changes was invisible. 04:00 UTC keeps it clear of
        # the 02:00 purge and the 03:00 finetune check.
        "daily-agent-eval": {
            "task": "app.worker.tasks.run_scheduled_agent_eval",
            "schedule": crontab(hour=4, minute=0),
            "options": {"queue": "default"},
        },
        # E9.6/G5: compare the two completed seven-day windows after the
        # nightly producer has populated Sunday's final observations.
        "weekly-agent-quality-drift": {
            "task": "app.worker.tasks.run_weekly_agent_quality_drift",
            "schedule": crontab(hour=6, minute=0, day_of_week="monday"),
            "options": {"queue": "default"},
        },
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
        # RAG-6: Knowledge source freshness re-sync (every 4 hours)
        "knowledge-source-resync": {
            "task": "app.worker.tasks.resync_stale_knowledge_sources",
            "schedule": crontab(minute=0, hour="*/4"),
        },
        # Migration 0150: prove the active-release invariant rather than
        # assume it. Hourly at :20 — frequent enough that a violation is
        # repaired long before it can misattribute a day's runs.
        #
        # The :20 offset does NOT avoid the nightly retention purge, and this
        # comment used to claim it did. `crontab(minute=20)` fires EVERY hour,
        # so it runs at 02:20 like every other hour; it is clear of the purge's
        # start MINUTE, not of its window. A sweep reading run→release
        # membership while retention deletes test_runs underneath it still
        # observes a moving target — the sweep is idempotent and re-runs
        # hourly, which is what actually makes that survivable.
        "reconcile-active-releases": {
            "task": "app.worker.tasks.reconcile_active_releases",
            "schedule": crontab(minute=20),
        },
        # Migration 0152: the denormalized primary_release_id has four writers
        # and no single owner, so drift is swept rather than assumed away.
        # :50 keeps it clear of the active-release sweep at :20 — the two read
        # overlapping rows and a repair racing a detection would log noise.
        "reconcile-primary-releases": {
            "task": "app.worker.tasks.reconcile_primary_releases",
            "schedule": crontab(minute=50),
        },
        # Migration 0151/0153 leave sort_key NULL rather than reimplementing
        # the encoder in SQL — see the task docstring. :35 keeps it clear of
        # the two sibling sweeps at :20 and :50.
        "reconcile-release-sort-keys": {
            "task": "app.worker.tasks.reconcile_release_sort_keys",
            "schedule": crontab(minute=35),
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
        "relay-agent-child-dispatch-outbox": {
            "task": "app.worker.tasks.relay_agent_child_dispatch_outbox",
            "schedule": crontab(minute="*"),
        },
        "process-decision-report-supersessions": {
            "task": "app.worker.tasks.process_decision_report_supersessions",
            "schedule": crontab(minute="*"),
        },
        "relay-agent-action-dispatch-outbox": {
            "task": "app.worker.tasks.relay_agent_action_dispatch_outbox",
            "schedule": crontab(minute="*"),
        },
        "relay-run-downstream-outbox": {
            "task": "app.worker.tasks.relay_run_downstream_outbox",
            "schedule": crontab(minute="*"),
        },
        "relay-queued-criteria-deletions": {
            "task": "app.worker.tasks.relay_queued_criteria_deletions",
            "schedule": crontab(minute="*"),
        },
        "recover-waiting-run-finalizations": {
            "task": "app.worker.tasks.recover_waiting_run_finalizations",
            "schedule": crontab(minute="*"),
        },
        "relay-pending-webhook-deliveries": {
            "task": "app.worker.tasks.relay_pending_webhook_deliveries",
            "schedule": crontab(minute="*"),
        },
        "relay-pending-notification-deliveries": {
            "task": "app.worker.tasks.relay_pending_notification_deliveries",
            "schedule": crontab(minute="*"),
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
        # Roadmap Phase 0: measure per-project flaky-classifier specificity.
        # Retrospective 90-day backtest per project, so it runs off-peak and
        # well after the nightly ingestion tail. Read-only as far as the
        # product is concerned — nothing consumes the result yet.
        "nightly-flaky-classifier-calibration": {
            "task": "app.worker.tasks.calibrate_flaky_classifiers",
            "schedule": crontab(hour=5, minute=45),
        },
        # Roadmap Phase 2: recompute the continuous flakiness score. Runs after
        # calibration so a project's measured classifier quality is fresh when
        # the score is read alongside it.
        "nightly-flaky-score-recompute": {
            "task": "app.worker.tasks.recompute_flaky_scores",
            "schedule": crontab(hour=6, minute=10),
        },
        # Roadmap Phase 3: rebuild systemic co-failure clusters. After
        # scoring so a cluster and its members' scores describe the same
        # window.
        "nightly-systemic-cluster-recompute": {
            "task": "app.worker.tasks.recompute_systemic_clusters",
            "schedule": crontab(hour=6, minute=40),
        },
        # Roadmap Phase 6, tier 1: screen new and directly-modified
        # fingerprints. Off the ingest path on purpose — screening buys nothing
        # by being synchronous, since this product ingests results rather than
        # executing tests, and a screening bug must not be able to cost an
        # ingestion.
        #
        # 30 minutes is the FLOOR of the per-project recommendation, not a
        # borrowed constant: beat schedules are global, so the fast tier runs at
        # the fastest interval any project could want and each project's own
        # cadence is reported by /metrics/detection-timing. Every run is a
        # no-op for projects with the flag off.
        "screen-new-test-fingerprints": {
            "task": "app.worker.tasks.screen_new_test_fingerprints",
            "schedule": crontab(minute="*/30"),
        },
        # Roadmap Phase 6, tier 2: the whole-corpus pass, for the
        # environment- and dependency-induced flakiness a diff cannot reach.
        # After the score recompute (06:10) and cluster rebuild (06:40) so a
        # fingerprint that just cleared the evidence floor has its latency
        # clock closed the same night. Nightly and deliberately not faster —
        # it is measured against the score's own 30-day window.
        "nightly-flaky-detection-sweep": {
            "task": "app.worker.tasks.sweep_flaky_detection",
            "schedule": crontab(hour=6, minute=55),
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
    # If the broker connection drops while a late-acked task is executing,
    # cancel the child process so Redis can redeliver the message instead of
    # leaving an in-flight task detached from the consumer.  Durable child
    # outbox rows remain the source of truth; this setting only bounds the
    # broker-side recovery path.
    worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_max_tasks_per_child=200, # recycle worker process after 200 tasks (prevent leaks)
    # Time limits: soft sends SIGTERM to task coroutine, hard sends SIGKILL
    task_soft_time_limit=1740,      # 29 min soft (pipeline tasks can run up to 30 min)
    task_time_limit=1860,           # 31 min hard
    # Connection resilience
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
)


# ── Beat must not subscribe to task results ──────────────────────────────────
#
# Celery beat is fire-and-forget: it dispatches periodic tasks and never reads
# what they return. But ``Celery.send_task`` does this (celery 5.6.3,
# ``app/base.py``):
#
#     ignore_result = options.pop('ignore_result', False)
#     ...
#     if not ignore_result:
#         self.backend.on_task_call(P, task_id)
#
# With the Redis result backend, ``on_task_call`` starts a **result consumer** --
# a pub/sub subscription per dispatched task -- inside the long-lived beat
# process. They accumulate until the backend connection cannot be re-established
# and beat dies with:
#
#     celery.beat.SchedulingError: Couldn't apply scheduled task <name>:
#     Retry limit exceeded while trying to reconnect to the Celery result store
#     backend. The Celery application must be restarted.
#
# After that beat logs "Sending due task" but the publish never lands, so EVERY
# periodic task silently stops while the container still reports healthy. Observed
# on the local stack 2026-09-19: the worker received nothing for 20+ minutes,
# every queue sat at depth 0, and 20 `run_downstream_outbox` rows stayed `pending`
# with `attempts=0` -- no AI pipeline, no webhooks, no notifications. It looks
# exactly like a broken relay, and it is not: invoking
# ``claim_downstream_dispatches`` directly claimed all 20 rows correctly.
#
# Note ``ignore_result`` is read from the per-call **options**, NOT from
# ``conf.task_ignore_result`` -- setting that would change nothing here. And beat
# reaches ``send_task`` rather than ``Task.apply_async`` because the task is not
# registered in the beat process, so the task-level ``ignore_result`` attribute
# is not consulted either. The option has to be on the schedule entry.
#
# Injected in a loop rather than typed into all 45 entries: a per-entry option is
# a rule the author of entry 46 has to remember, and this failure is invisible
# until periodic work silently stops.
for _entry in celery_app.conf.beat_schedule.values():
    _options = _entry.setdefault("options", {})
    _options.setdefault("ignore_result", True)
del _entry, _options


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
# ``worker/loop_runner.py`` handles the *event loop* half of this
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

    # A loop the parent built shares its selector with the parent; the child
    # must never use or close it (re-audit M1, worker/loop_runner.py).
    from app.worker.loop_runner import forget_inherited_loop

    forget_inherited_loop()

    # Each prefork child is a separate process, so the offline embedder guard
    # has to be installed in every one of them. Without this the child that
    # happens to run reindex_search would download the weights that
    # AI_OFFLINE_MODE is supposed to forbid — which is exactly how the
    # 1 GiB worker got OOM-killed in a restart loop.
    try:
        from app.services.local_embedder_guard import install_offline_embedder_guard

        install_offline_embedder_guard()
    except Exception:  # noqa: BLE001 — never block a worker child on the guard
        pass


# ── Prometheus: worker-side metrics ───────────────────────────────────────────
#
# TestLookupTaskLatencyHigh alerts on celery_task_runtime_seconds. Only the
# worker sees task execution, so the backend cannot emit it — the worker has to
# expose its own scrape target.
#
# Prefork means each child keeps a private registry, so the exporting process
# would otherwise publish only its own numbers. prometheus_client multiprocess
# mode solves it: children write metric files into PROMETHEUS_MULTIPROC_DIR and
# the exporter aggregates them. Without that directory set we deliberately do
# NOT start the server — publishing one child's view as if it were the whole
# worker is worse than publishing nothing.

_TASK_STARTED_AT: dict[str, float] = {}


@celeryd_init.connect
def _prepare_worker_metrics_directory(**_kwargs: object) -> None:
    """Remove metrics from an earlier worker parent before it forks children.

    Kubernetes ``emptyDir`` survives a container restart in the same pod, and
    Compose may restart a container without recreating its tmpfs mount. Keeping
    those files would make the replacement worker report the previous process
    group's counters again. ``celeryd_init`` runs once in the parent before the
    prefork pool starts, so it cannot race with live child observations.
    """
    import os

    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return
    try:
        for entry in os.scandir(multiproc_dir):
            if entry.is_file() and entry.name.endswith(".db"):
                os.unlink(entry.path)
    except OSError as exc:
        import structlog

        structlog.get_logger(__name__).error(
            "worker_metrics_directory_reset_failed",
            directory=multiproc_dir,
            error=str(exc),
        )


@task_prerun.connect
def _record_task_start(task_id=None, task=None, **_kwargs: object) -> None:
    import time

    if task_id:
        _TASK_STARTED_AT[task_id] = time.perf_counter()


@task_postrun.connect
def _record_task_runtime(task_id=None, task=None, **_kwargs: object) -> None:
    """Observe execution time. Never let instrumentation fail a task."""
    import time

    started = _TASK_STARTED_AT.pop(task_id, None) if task_id else None
    if started is None:
        return
    try:
        from app.core.metrics import celery_task_runtime_seconds

        queue = ""
        request = getattr(task, "request", None)
        if request is not None:
            queue = getattr(request, "delivery_info", {}).get("routing_key", "") or ""
        celery_task_runtime_seconds.labels(
            task_name=getattr(task, "name", "unknown"),
            queue_name=queue or "unknown",
        ).observe(time.perf_counter() - started)
    except Exception:  # noqa: BLE001 — metrics must never break the task
        pass


@task_postrun.connect
def _record_task_outcome(task_id=None, task=None, state=None, **_kwargs: object) -> None:
    """Count task outcomes so failure and retry are visible to Prometheus.

    ``testlookup_celery_tasks_total`` backs
    TestLookupFlakyQuarantineMaintenanceFailing and
    TestLookupPerfBaselineRefreshFailing. Nothing anywhere incremented it, so
    both alerts were structurally unable to fire — verified against the live
    deployment's /metrics, 0 samples. Same class as ``celery_queue_length``
    and ``celery_task_runtime_seconds`` before it.

    It is also the only signal that separates a task still retrying from one
    that has failed terminally — queue depth cannot, because a task waiting
    on a retry countdown is not on the queue at all.

    ``state`` is Celery's terminal state for this execution: SUCCESS, FAILURE
    or RETRY. Lower-cased verbatim rather than mapped through a table, so an
    unexpected state (REVOKED, REJECTED) reports itself instead of being
    folded into an "other" bucket that means nothing to the reader.
    """
    try:
        from app.core.metrics import celery_tasks_total

        celery_tasks_total.labels(
            task_name=getattr(task, "name", "unknown"),
            status=str(state or "unknown").lower(),
        ).inc()
    except Exception:  # noqa: BLE001 — metrics must never break the task
        pass


#: Readiness sentinel. Written once the worker has booted and connected to the
#: broker; removed on shutdown.
READY_SENTINEL = "/tmp/celery-worker-ready"


@worker_ready.connect
def _mark_worker_ready(**_kwargs: object) -> None:
    """Signal readiness with a file instead of a control-channel round-trip.

    The readiness probe used to run ``celery inspect ping --timeout=10`` inside
    a 15 s exec. That measures how *responsive* the worker is, not whether it
    is working — and under a saturated prefork pool (four children pinned at
    the CPU limit) the reply misses the deadline. Kubernetes then marks a
    perfectly healthy worker NotReady.

    For a queue consumer with no Service in front of it, readiness gates only
    the rollout, so that failure mode is worse than useless: it **deadlocks the
    deploy**. Observed on the homelab — the new ReplicaSet never reported
    Ready, so the Deployment could not scale down the old one, and an
    OOM-looping pod from the previous revision stayed alive serving the queue
    while the replacement sat NotReady beside it.

    ``worker_ready`` fires after the consumer has connected to the broker and
    begun consuming, which is exactly the condition readiness should express,
    and a file check costs nothing under load. Liveness stays on ``pgrep`` and
    remains the thing that catches a dead worker.
    """
    try:
        with open(READY_SENTINEL, "w", encoding="utf-8") as fh:
            fh.write("ready\n")
    except OSError:  # noqa: BLE001 — readiness must never stop a worker booting
        pass


@worker_shutdown.connect
def _clear_worker_ready(**_kwargs: object) -> None:
    """Drop the sentinel so a draining worker stops reporting Ready."""
    import os

    try:
        os.remove(READY_SENTINEL)
    except OSError:  # noqa: BLE001 — already gone, or never written
        pass


@worker_ready.connect
def _start_metrics_server(**_kwargs: object) -> None:
    """Expose /metrics from the worker parent, aggregating child processes."""
    import os

    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return
    try:
        from prometheus_client import CollectorRegistry, multiprocess, start_http_server

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        port = int(os.environ.get("WORKER_METRICS_PORT", "9100"))
        start_http_server(port, registry=registry)
        _start_metrics_file_guard()
    except Exception as exc:  # noqa: BLE001 — a metrics port must never stop a worker
        import structlog

        structlog.get_logger(__name__).error(
            "worker_metrics_server_start_failed",
            port=os.environ.get("WORKER_METRICS_PORT", "9100"),
            error=str(exc),
        )


def _start_metrics_file_guard() -> None:
    """Recycle the worker before dead-child metric files become unbounded.

    Counter and histogram files cannot be deleted when a prefork child exits:
    doing so would lose completed task observations. The orchestrator already
    restarts workers, so a bounded container lifecycle preserves observations
    until the final scrape and lets Prometheus handle the subsequent reset.
    """
    import os
    import signal
    import threading

    metric_dir = Path(os.environ["PROMETHEUS_MULTIPROC_DIR"])
    max_files = int(os.environ.get("WORKER_METRICS_MAX_FILES", "256"))
    interval = float(os.environ.get("WORKER_METRICS_FILE_CHECK_SECONDS", "60"))
    final_scrape_grace = float(
        os.environ.get("WORKER_METRICS_FINAL_SCRAPE_GRACE_SECONDS", "65")
    )

    def guard() -> None:
        import structlog

        logger = structlog.get_logger(__name__)
        while True:
            if len(list(metric_dir.glob("*.db"))) > max_files:
                logger.warning(
                    "worker_metrics_file_limit_reached",
                    directory=str(metric_dir),
                    max_files=max_files,
                )
                # Prometheus scrapes workers every 30 seconds in the shipped config.
                # Keep the complete registry available for two final scrapes
                # before asking Celery to shut down gracefully.
                threading.Event().wait(final_scrape_grace)
                os.kill(os.getpid(), signal.SIGTERM)
                return
            threading.Event().wait(interval)

    threading.Thread(
        target=guard,
        name="prometheus-multiproc-file-guard",
        daemon=True,
    ).start()


@worker_process_shutdown.connect
def _clear_dead_child_metrics(**_kwargs: object) -> None:
    """Tell prometheus_client this child is gone.

    ``--max-tasks-per-child`` recycles prefork children constantly (200 tasks
    here), so without this the multiprocess directory accumulates a file set per
    dead PID for the life of the pod.

    ``mark_process_dead`` clears the *gauge* files for the PID; counter and
    histogram files are deliberately left, because their observations already
    happened and dropping them would rewrite history — a task that ran is a task
    that ran, whether or not the worker that ran it still exists.
    """
    import os

    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(os.getpid())
    except Exception:  # noqa: BLE001 — never block worker shutdown
        pass


@worker_process_shutdown.connect
def _close_worker_loop(**_kwargs: object) -> None:
    """Drain this child's loop -- engine, Redis, httpx -- on the loop that owns them.

    Re-audit M1: tasks now share one event loop per child, so the drain that
    used to run after every task runs once, here.
    """
    try:
        from app.worker.loop_runner import shutdown_worker_loop

        shutdown_worker_loop()
    except Exception:  # noqa: BLE001 -- never block worker shutdown
        pass
