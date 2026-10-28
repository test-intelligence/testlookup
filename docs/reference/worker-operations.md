# Worker tasks, queues and schedules

[Documentation home](../README.md) · [Regeneration](../maintainers/maintenance.md)

Generated from tracked source by `scripts/generate_reference.py`. Do not edit by hand. What the checks cover is recorded in [verification](../maintainers/verification.md).

Static declarations, not observations of running consumers. Deployment profiles override shard count and subscriptions; see [deployment](../operations/deployment.md). Every task below includes its decorator options and arguments; side effects and error handling remain in the linked implementation.

## task_routes

```python
{'app.worker.tasks.run_live_test_analysis': {'queue': 'critical'}, 'app.worker.tasks.ingest_test_run': {'queue': 'ingestion'}, 'app.worker.tasks.run_ai_analysis': {'queue': 'ai_analysis'}, 'app.worker.tasks.run_agent_pipeline': {'queue': 'ai_analysis'}, 'app.worker.tasks.run_agent_child_investigation': {'queue': 'agent_children'}, 'app.worker.tasks.generate_run_compare_report': {'queue': 'ai_analysis'}, 'app.worker.tasks.precompute_suite_comparisons_for_run': {'queue': 'ai_analysis'}, 'app.worker.tasks.generate_ai_test_cases_task': {'queue': 'ai_analysis'}, 'app.worker.tasks.create_ai_test_plan_task': {'queue': 'ai_analysis'}, 'app.worker.tasks.generate_ai_strategy_task': {'queue': 'ai_analysis'}, 'app.worker.tasks.run_fixer_run_task': {'queue': 'ai_analysis'}, 'app.worker.training_tasks.export_training_data': {'queue': 'default'}, 'app.worker.training_tasks.check_finetune_trigger': {'queue': 'default'}, 'app.worker.training_tasks.run_finetune_pipeline': {'queue': 'default'}, 'app.worker.tasks.reindex_search': {'queue': 'default'}, 'app.worker.tasks.rebuild_embedding_generation': {'queue': 'ai_analysis'}, 'app.worker.tasks.run_retention_purges': {'queue': 'default'}, 'app.worker.tasks.rebuild_run_snapshot': {'queue': 'default'}, 'app.worker.tasks.*': {'queue': 'default'}}
```

## broker_transport_options

```python
{'visibility_timeout': 3600}
```

## beat_schedule

```python
{'daily-coverage-snapshot': {'task': 'app.worker.tasks.take_coverage_snapshot', 'schedule': crontab(hour=0, minute=5)}, 'daily-agent-eval': {'task': 'app.worker.tasks.run_scheduled_agent_eval', 'schedule': crontab(hour=4, minute=0), 'options': {'queue': 'default'}}, 'weekly-agent-quality-drift': {'task': 'app.worker.tasks.run_weekly_agent_quality_drift', 'schedule': crontab(hour=6, minute=0, day_of_week='monday'), 'options': {'queue': 'default'}}, 'nightly-retention-purge': {'task': 'app.worker.tasks.run_retention_purges', 'schedule': crontab(hour=2, minute=0)}, 'nightly-search-usage-purge': {'task': 'app.worker.tasks.purge_search_usage_events', 'schedule': crontab(hour=2, minute=40), 'options': {'queue': 'default'}}, 'expire-unlabelled-shadow-pairs': {'task': 'app.worker.tasks.expire_unlabelled_shadow_pairs', 'schedule': crontab(hour=2, minute=50), 'options': {'queue': 'default'}}, 'golden-match-shadow-pairs': {'task': 'app.worker.tasks.golden_match_shadow_pairs', 'schedule': crontab(hour=3, minute=50), 'options': {'queue': 'default'}}, 'reconcile-stale-eval-runs': {'task': 'app.worker.tasks.reconcile_stale_eval_runs', 'schedule': crontab(minute='*/10'), 'options': {'queue': 'default'}}, 'nightly-report-export-sweep': {'task': 'app.worker.tasks.sweep_report_exports', 'schedule': crontab(hour=1, minute=15)}, 'weekly-training-export': {'task': 'app.worker.training_tasks.export_training_data', 'schedule': crontab(hour=2, minute=0, day_of_week='sunday')}, 'daily-finetune-trigger-check': {'task': 'app.worker.training_tasks.check_finetune_trigger', 'schedule': crontab(hour=3, minute=0)}, 'nightly-ml-classifier-training': {'task': 'app.worker.tasks.train_ml_classifier', 'schedule': crontab(hour=3, minute=10), 'kwargs': {'triggered_by': 'beat'}}, 'hourly-search-reindex': {'task': 'app.worker.tasks.reindex_search', 'schedule': crontab(minute=30)}, 'daily-digest-dispatch': {'task': 'app.worker.tasks.dispatch_scheduled_digests', 'schedule': crontab(hour=7, minute=0)}, 'integration-health-probes': {'task': 'app.worker.tasks.run_integration_health_probes', 'schedule': crontab(minute='*/15')}, 'jira-defect-status-sync': {'task': 'app.worker.tasks.sync_jira_defect_statuses', 'schedule': crontab(minute='5-59/15')}, 'knowledge-source-resync': {'task': 'app.worker.tasks.resync_stale_knowledge_sources', 'schedule': crontab(minute=0, hour='*/4')}, 'reconcile-active-releases': {'task': 'app.worker.tasks.reconcile_active_releases', 'schedule': crontab(minute=20)}, 'reconcile-primary-releases': {'task': 'app.worker.tasks.reconcile_primary_releases', 'schedule': crontab(minute=50)}, 'reconcile-release-sort-keys': {'task': 'app.worker.tasks.reconcile_release_sort_keys', 'schedule': crontab(minute=35)}, 'nightly-flaky-confidence-training': {'task': 'app.worker.tasks.train_flaky_confidence_model', 'schedule': crontab(hour=3, minute=30)}, 'close-stale-live-sessions': {'task': 'app.worker.tasks.close_stale_live_sessions', 'schedule': crontab(minute='*/2'), 'kwargs': {'idle_minutes': 5}}, 'reap-stuck-agent-pipelines': {'task': 'app.worker.tasks.reap_stuck_agent_pipelines', 'schedule': crontab(minute='*/10')}, 'relay-agent-child-dispatch-outbox': {'task': 'app.worker.tasks.relay_agent_child_dispatch_outbox', 'schedule': crontab(minute='*')}, 'process-decision-report-supersessions': {'task': 'app.worker.tasks.process_decision_report_supersessions', 'schedule': crontab(minute='*')}, 'relay-agent-action-dispatch-outbox': {'task': 'app.worker.tasks.relay_agent_action_dispatch_outbox', 'schedule': crontab(minute='*')}, 'reap-stuck-agent-actions': {'task': 'app.worker.tasks.reap_stuck_agent_actions', 'schedule': crontab(minute='*/5')}, 'relay-run-downstream-outbox': {'task': 'app.worker.tasks.relay_run_downstream_outbox', 'schedule': crontab(minute='*')}, 'relay-queued-criteria-deletions': {'task': 'app.worker.tasks.relay_queued_criteria_deletions', 'schedule': crontab(minute='*')}, 'recover-waiting-run-finalizations': {'task': 'app.worker.tasks.recover_waiting_run_finalizations', 'schedule': crontab(minute='*')}, 'relay-pending-webhook-deliveries': {'task': 'app.worker.tasks.relay_pending_webhook_deliveries', 'schedule': crontab(minute='*')}, 'relay-pending-notification-deliveries': {'task': 'app.worker.tasks.relay_pending_notification_deliveries', 'schedule': crontab(minute='*')}, 'nightly-flaky-quarantine-maintenance': {'task': 'app.worker.tasks.run_flaky_quarantine_maintenance', 'schedule': crontab(hour=4, minute=0)}, 'nightly-perf-baseline-refresh': {'task': 'app.worker.tasks.refresh_perf_baselines', 'schedule': crontab(hour=4, minute=30)}, 'monday-weekly-retro-digests': {'task': 'app.worker.tasks.dispatch_scheduled_digests', 'schedule': crontab(hour=7, minute=5, day_of_week='monday')}, 'monday-weekly-flaky-debt-reviews': {'task': 'app.worker.tasks.dispatch_weekly_flaky_debt_reviews', 'schedule': crontab(hour=7, minute=10, day_of_week='monday')}, 'daily-fixer-runs': {'task': 'app.worker.tasks.dispatch_scheduled_fixer_runs', 'schedule': crontab(hour=6, minute=0), 'args': ('daily',)}, 'weekly-fixer-runs': {'task': 'app.worker.tasks.dispatch_scheduled_fixer_runs', 'schedule': crontab(hour=6, minute=15, day_of_week='monday'), 'args': ('weekly',)}, 'poll-fixer-pr-outcomes': {'task': 'app.worker.tasks.poll_fixer_pr_outcomes', 'schedule': crontab(minute='*/30')}, 'nightly-orphan-test-suite-flag': {'task': 'app.worker.tasks.flag_orphan_test_suites', 'schedule': crontab(hour=5, minute=0)}, 'nightly-canonical-deletion-reconcile': {'task': 'app.worker.tasks.reconcile_canonical_deletions', 'schedule': crontab(hour=5, minute=30)}, 'nightly-flaky-classifier-calibration': {'task': 'app.worker.tasks.calibrate_flaky_classifiers', 'schedule': crontab(hour=5, minute=45)}, 'nightly-flaky-score-recompute': {'task': 'app.worker.tasks.recompute_flaky_scores', 'schedule': crontab(hour=6, minute=10)}, 'nightly-systemic-cluster-recompute': {'task': 'app.worker.tasks.recompute_systemic_clusters', 'schedule': crontab(hour=6, minute=40)}, 'screen-new-test-fingerprints': {'task': 'app.worker.tasks.screen_new_test_fingerprints', 'schedule': crontab(minute='*/30')}, 'nightly-flaky-detection-sweep': {'task': 'app.worker.tasks.sweep_flaky_detection', 'schedule': crontab(hour=6, minute=55)}, 'backfill-unassigned-failures': {'task': 'app.worker.tasks.backfill_unassigned_failures', 'schedule': crontab(minute='*/15')}, 'drain-active-live-sessions': {'task': 'app.worker.tasks.drain_active_live_sessions', 'schedule': timedelta(seconds=30)}, 'backfill-placeholder-test-cases': {'task': 'app.worker.tasks.backfill_placeholder_test_cases', 'schedule': crontab(minute=10)}, 'backfill-suite-executions': {'task': 'app.worker.tasks.backfill_suite_executions', 'schedule': crontab(minute='*/10')}, 'backfill-ai-run-versions': {'task': 'app.worker.tasks.backfill_ai_run_versions', 'schedule': crontab(minute='5,15,25,35,45,55')}, 'auto-recover-completed-live-runs': {'task': 'app.worker.tasks.auto_recover_completed_live_runs', 'schedule': timedelta(minutes=2)}, 'nightly-duplicate-detection': {'task': 'app.worker.tasks.run_duplicate_detection', 'schedule': crontab(hour=6, minute=0)}}
```

## worker_prefetch_multiplier

```python
1
```

## task_acks_late

```python
True
```

## task_soft_time_limit

```python
1740
```

## task_time_limit

```python
1860
```

## queue_delivery_probe

[backend/app/worker/celery_app.py:29](../../backend/app/worker/celery_app.py#L29)

Return transport attribution for the release queue-consumption proof.

```python
celery_app.task(bind=True, name='app.worker.celery_app.queue_delivery_probe')
queue_delivery_probe(self, correlation_id: str)
```

## dispatch_run_completed_webhook

[backend/app/worker/tasks.py:588](../../backend/app/worker/tasks.py#L588)

Persist and publish an idempotent run.completed webhook emission.

```python
celery_app.task(name='app.worker.tasks.dispatch_run_completed_webhook', base=DownstreamTrackedTask, bind=True, max_retries=2, default_retry_delay=15, queue='default')
dispatch_run_completed_webhook(self, project_id: str, payload: dict)
```

## persist_live_session

[backend/app/worker/tasks.py:629](../../backend/app/worker/tasks.py#L629)

Persist a completed live execution session to PostgreSQL.

Drains the per-run durable evidence Stream (with legacy LIST migration)
and creates:
  - One TestRun row (with aggregated counts from final_state / Redis data)
  - One TestCase row per event

Called by DELETE /api/v1/stream/sessions/{session_id} after run_complete.
Deduplicates by run_id so retries are safe.

```python
celery_app.task(name='app.worker.tasks.persist_live_session', base=DownstreamTrackedTask, bind=True, max_retries=3, queue='ingestion')
persist_live_session(self, run_id: str, project_id: str, build_number: str, client_name: str='', framework: str='', branch: str='', commit_hash: str='', final_state: dict | None=None, suite_name: str | None=None, completed_at: str | None=None)
```

## ingest_test_run

[backend/app/worker/tasks.py:1126](../../backend/app/worker/tasks.py#L1126)

Background task: parse Allure JSON + TestNG XML from MinIO and
upsert structured data into PostgreSQL + MongoDB.
Deduplicates by minio_prefix so concurrent webhooks don't double-ingest.

The MinIO webhook queues the sentinel's object KEY, and this task reads the
sentinel (services/minio_sentinel.py): off the API process, and inside this
task's retries for a storage hiccup (code review of re-audit N10). A
sentinel that is definitively unusable -- missing, oversized, not a JSON
object, invalid -- is refused without a retry. ``sentinel_dict`` is the
shape an API from before that change queued; it is still accepted.

```python
celery_app.task(name='app.worker.tasks.ingest_test_run', bind=True, max_retries=3, queue='ingestion')
ingest_test_run(self, sentinel_dict: dict | None=None, minio_prefix: str='', sentinel_key: str | None=None)
```

## ingest_uploaded_results

[backend/app/worker/tasks.py:1223](../../backend/app/worker/tasks.py#L1223)

Process a JSON batch of test results from POST /api/v1/ingest.
Creates a TestRun, upserts test cases, runs post-ingestion pipeline.

```python
celery_app.task(name='app.worker.tasks.ingest_uploaded_results', bind=True, max_retries=3, queue='ingestion')
ingest_uploaded_results(self, run_id: str, payload: dict=None, user_id: str=None, payload_storage_key: str=None)
```

## ingest_uploaded_file

[backend/app/worker/tasks.py:1321](../../backend/app/worker/tasks.py#L1321)

Parse an uploaded test result file and ingest.
Supports JUnit XML, TestNG XML, and Allure JSON.

```python
celery_app.task(name='app.worker.tasks.ingest_uploaded_file', bind=True, max_retries=3, queue='ingestion')
ingest_uploaded_file(self, run_id: str, file_name: str, file_format: str, project_id: str, build_number: str, file_storage_key: str=None, file_content: str=None, branch: str=None, commit_hash: str=None, release_name: str=None, user_id: str=None, disabled_formats: list=None, run_ai: bool=True, ci_provider: str=None, ci_repo: str=None, pr_number: int=None, ci_actor: str=None, ci_run_url: str=None, jenkins_job: str=None, environment: str=None, executed_at: str=None, commit_range=None)
```

## run_live_test_analysis

[backend/app/worker/tasks.py:1881](../../backend/app/worker/tasks.py#L1881)

Immediate root-cause analysis for a single test that failed during live execution.
Runs on the critical queue (priority=9) so results appear in the dashboard fast.
Provider failures degrade through the endpoint-scoped breaker in llm_factory.

```python
celery_app.task(name='app.worker.tasks.run_live_test_analysis', bind=True, max_retries=2, queue='critical', time_limit=120, priority=9)
run_live_test_analysis(self, test_case_id: str, test_name: str, run_id: str, project_id: str)
```

## run_ai_analysis

[backend/app/worker/tasks.py:1974](../../backend/app/worker/tasks.py#L1974)

Background task: run the LangChain ReAct agent for a single test case.
Used by the offline auto-analyzer. The LLM factory owns endpoint breaker state.

```python
celery_app.task(name='app.worker.tasks.run_ai_analysis', bind=True, max_retries=2, queue='ai_analysis', time_limit=180)
run_ai_analysis(self, test_case_id: str, test_name: str, **kwargs)
```

## dispatch_run_notifications

[backend/app/worker/tasks.py:2021](../../backend/app/worker/tasks.py#L2021)

Background task: fan-out run-completion notifications to all subscribed users.

When ``dashboard_url`` is empty, builds an absolute link from
``settings.public_base_url`` so notifications rendered in Slack/Teams/email
contain a clickable link regardless of where the cluster is deployed.

```python
celery_app.task(name='app.worker.tasks.dispatch_run_notifications', base=DownstreamTrackedTask, bind=True, max_retries=2, default_retry_delay=15, queue='default')
dispatch_run_notifications(self, project_id: str, run_id: str, build_number: str, pass_rate: float, total_tests: int, failed_tests: int, project_name: str, dashboard_url: str='')
```

## dispatch_transition_notifications

[backend/app/worker/tasks.py:2077](../../backend/app/worker/tasks.py#L2077)

Background task: evaluate transition events (PMF US-7.1/US-7.2) for a
finalized run and send the batched, cluster-deduped notification.

Idempotent per run — the state store stamps each fingerprint with the
run id, so a retry (or a re-finalized run) advances nothing and sends
nothing.

```python
celery_app.task(name='app.worker.tasks.dispatch_transition_notifications', base=DownstreamTrackedTask, bind=True, max_retries=2, default_retry_delay=15, queue='default')
dispatch_transition_notifications(self, run_id: str)
```

## run_agent_pipeline

[backend/app/worker/tasks.py:2219](../../backend/app/worker/tasks.py#L2219)

Background task: run the full multi-agent LangGraph pipeline for a completed test run.
Stages: ingestion → anomaly detection → root-cause analysis → summary → triage
Deduplicates by test_run_id so multiple triggers for the same run don't stack up.
Moves to DLQ after max retries.

```python
celery_app.task(name='app.worker.tasks.run_agent_pipeline', base=DownstreamTrackedTask, bind=True, max_retries=0, queue='ai_analysis', time_limit=1800)
run_agent_pipeline(self, test_run_id: str, project_id: str, build_number: str, workflow_type: str='offline', workflow_id: str | None=None, workflow_version: int | None=None, rerun_of: str | None=None, requested_by: str | None=None, bulk_job_id: str | None=None, run_overrides: dict | None=None)
```

## rebuild_run_snapshot

[backend/app/worker/tasks.py:2564](../../backend/app/worker/tasks.py#L2564)

Rebuild a run's intelligence snapshot after its pipeline invalidated it (W07 FAIL-04).

Queued by ``run_agent_pipeline`` right after its invalidation. Idempotent:
a run that already has a servable snapshot (someone opened its Analysis tab
first, or this task ran twice) is left alone. Otherwise
``rebuild_snapshot`` computes and saves it on this task's own session; the
defect-candidate build the read schedules runs on THIS task's loop, and the
rebuild waits for it (``SNAPSHOT_REBUILD_BUILD_WAIT_SECONDS``, inside the
soft time limit), so the pipeline's slot never waits and the build is not
cancelled when the pipeline's task ends. Never raises.

```python
celery_app.task(name='app.worker.tasks.rebuild_run_snapshot', bind=True, max_retries=0, queue='default', soft_time_limit=360, time_limit=420)
rebuild_run_snapshot(self, run_id: str)
```

## resume_agent_pipeline

[backend/app/worker/tasks.py:2732](../../backend/app/worker/tasks.py#L2732)

Resume a failed / retry_wait / degraded pipeline under its existing id.

The workflow atomically claims the row, preserves the immutable initial
plan, and replays only checksum-authorized completed checkpoints.
Duplicate deliveries return ``pipeline_not_resumable`` without spending
another model call.

``expected_attempt`` (E7.2) is set by the scheduled retry: the claim
succeeds only if the row's next attempt number is exactly this one, so a
stale scheduled resume (the run was cancelled, or a manual retry already
ran) exits without side effects instead of resurrecting the run.

```python
celery_app.task(name='app.worker.tasks.resume_agent_pipeline', bind=True, max_retries=0, queue='ai_analysis', time_limit=1800)
resume_agent_pipeline(self, pipeline_run_id: str, build_number: str='resume', expected_attempt: int | None=None)
```

## run_agent_investigation

[backend/app/worker/tasks.py:2771](../../backend/app/worker/tasks.py#L2771)

Background task: execute one hypothesis-loop investigation (AI-1).

Dispatched by the manual endpoint and the auto-trigger hooks
(``test.newly_failing`` transitions / gate NO_GO). No retries by design:
the workflow's own error path marks the investigation row ``failed``
and writes the AgentRun ledger entry, and a blind retry could double-run
LLM budgets. Duplicate protection is the one-active-per-run partial
unique index at trigger time plus the runner's queued-status check.

```python
celery_app.task(name='app.worker.tasks.run_agent_investigation', bind=True, max_retries=0, queue='ai_analysis', time_limit=1800)
run_agent_investigation(self, investigation_id: str)
```

## run_agent_child_investigation

[backend/app/worker/tasks.py:2815](../../backend/app/worker/tasks.py#L2815)

Execute one ID-only, cluster-scoped child on the isolated queue.

```python
celery_app.task(name='app.worker.tasks.run_agent_child_investigation', bind=True, max_retries=0, queue='agent_children', time_limit=900)
run_agent_child_investigation(self, investigation_id: str)
```

## resume_agent_child_investigation

[backend/app/worker/tasks.py:2850](../../backend/app/worker/tasks.py#L2850)

Resume a failed cluster child under its stable investigation identity.

```python
celery_app.task(name='app.worker.tasks.resume_agent_child_investigation', bind=True, max_retries=0, queue='agent_children', time_limit=900)
resume_agent_child_investigation(self, investigation_id: str)
```

## relay_agent_child_dispatch_outbox

[backend/app/worker/tasks.py:2875](../../backend/app/worker/tasks.py#L2875)

Recover pending/stale cluster-child dispatches from the PG outbox.

```python
celery_app.task(name='app.worker.tasks.relay_agent_child_dispatch_outbox', bind=True, max_retries=0, queue='default', time_limit=120)
relay_agent_child_dispatch_outbox(self)
```

## process_decision_report_supersessions

[backend/app/worker/tasks.py:2898](../../backend/app/worker/tasks.py#L2898)

Publish terminal child-enriched report versions from durable requests.

```python
celery_app.task(name='app.worker.tasks.process_decision_report_supersessions', bind=True, max_retries=0, queue='default', time_limit=120)
process_decision_report_supersessions(self)
```

## relay_agent_action_dispatch_outbox

[backend/app/worker/tasks.py:2924](../../backend/app/worker/tasks.py#L2924)

Publish approved action IDs to the guarded action executor.

```python
celery_app.task(name='app.worker.tasks.relay_agent_action_dispatch_outbox', bind=True, max_retries=0, queue='default', time_limit=120)
relay_agent_action_dispatch_outbox(self)
```

## relay_run_downstream_outbox

[backend/app/worker/tasks.py:2948](../../backend/app/worker/tasks.py#L2948)

Publish durable post-ingestion intents whose retry time has arrived.

```python
celery_app.task(name='app.worker.tasks.relay_run_downstream_outbox', bind=True, max_retries=0, queue='default', time_limit=120)
relay_run_downstream_outbox(self)
```

## relay_queued_criteria_deletions

[backend/app/worker/tasks.py:2972](../../backend/app/worker/tasks.py#L2972)

Recover criteria deletions committed before broker publication.

```python
celery_app.task(name='app.worker.tasks.relay_queued_criteria_deletions', bind=True, max_retries=0, queue='default', time_limit=120)
relay_queued_criteria_deletions(self)
```

## recover_waiting_run_finalizations

[backend/app/worker/tasks.py:2987](../../backend/app/worker/tasks.py#L2987)

Resume finalization after a worker died before opening its child gate.

```python
celery_app.task(name='app.worker.tasks.recover_waiting_run_finalizations', bind=True, max_retries=0, queue='default', time_limit=1860)
recover_waiting_run_finalizations(self)
```

## relay_pending_webhook_deliveries

[backend/app/worker/tasks.py:3011](../../backend/app/worker/tasks.py#L3011)

Republish webhook rows stranded before broker acceptance.

```python
celery_app.task(name='app.worker.tasks.relay_pending_webhook_deliveries', bind=True, max_retries=0, queue='default', time_limit=120)
relay_pending_webhook_deliveries(self)
```

## relay_pending_notification_deliveries

[backend/app/worker/tasks.py:3035](../../backend/app/worker/tasks.py#L3035)

Deliver and retry durable per-channel notification children.

```python
celery_app.task(name='app.worker.tasks.relay_pending_notification_deliveries', bind=True, max_retries=0, queue='default', time_limit=180)
relay_pending_notification_deliveries(self)
```

## execute_agent_action

[backend/app/worker/tasks.py:3061](../../backend/app/worker/tasks.py#L3061)

Resolve and consume one action; unregistered effects fail closed.

```python
celery_app.task(name='app.worker.tasks.execute_agent_action', bind=True, max_retries=0, queue='default', time_limit=120)
execute_agent_action(self, project_id: str, action_id: str)
```

## reap_stuck_agent_actions

[backend/app/worker/tasks.py:3090](../../backend/app/worker/tasks.py#L3090)

Settle agent actions whose executor died after its claim (W15 AGT-03, D-13).

``execute_agent_action`` commits ``approved → executing`` BEFORE an effect;
a worker killed after that leaves the row ``executing``. Past the lease
(``EXECUTION_LEASE_SECONDS``, 10 min; the executor's time limit is 2) the
row becomes ``failed`` / ``outcome_unknown`` -- never a retry, since the
effect may have happened. The sweep is stage-only; this task owns its one
commit.

```python
celery_app.task(name='app.worker.tasks.reap_stuck_agent_actions', bind=True, max_retries=0, queue='default', time_limit=120)
reap_stuck_agent_actions(self)
```

## run_fixer_run_task

[backend/app/worker/tasks.py:3131](../../backend/app/worker/tasks.py#L3131)

Background task: execute one budgeted Fixer run (AI-2).

Dispatched by the manual endpoint and the scheduler beats. No retries by
design: a blind retry could double-run validation budgets / re-open PRs.
The workflow writes the ``agent_runs`` ledger entry on completion.

```python
celery_app.task(name='app.worker.tasks.run_fixer_run_task', bind=True, max_retries=0, queue='ai_analysis', time_limit=3600)
run_fixer_run_task(self, project_id: str, fixer_run_id: str, triggered_by: str='scheduled')
```

## dispatch_scheduled_fixer_runs

[backend/app/worker/tasks.py:3167](../../backend/app/worker/tasks.py#L3167)

Beat: enqueue a Fixer run for every project whose fixer policy is
enabled with ``schedule == <schedule>`` (daily|weekly). Each run re-checks
its own gate; already-running projects are skipped. The gate also takes
the cross-process dispatch lock (released in workflow._finalize), closing
the race with a concurrent manual POST.

```python
celery_app.task(name='app.worker.tasks.dispatch_scheduled_fixer_runs', bind=True, max_retries=0, queue='default', time_limit=300)
dispatch_scheduled_fixer_runs(self, schedule: str)
```

## poll_fixer_pr_outcomes

[backend/app/worker/tasks.py:3236](../../backend/app/worker/tasks.py#L3236)

Beat: poll open fixer-created PRs; merged → record_fix_outcome(fixed),
closed-unmerged → not_fixed (AI-5 feedback loop). No-op offline.

The sweep owns its sessions/commits internally (bounded batches; no DB
session held across the GitHub GETs) — see pipeline.poll_open_fixer_prs.

```python
celery_app.task(name='app.worker.tasks.poll_fixer_pr_outcomes', bind=True, max_retries=0, queue='default', time_limit=600)
poll_fixer_pr_outcomes(self)
```

## generate_run_compare_report

[backend/app/worker/tasks.py:3259](../../backend/app/worker/tasks.py#L3259)

Generate and cache the AI report for a deterministic run comparison.

```python
celery_app.task(name='app.worker.tasks.generate_run_compare_report', bind=True, max_retries=1, queue='ai_analysis', time_limit=900)
generate_run_compare_report(self, project_id: str, left_run_id: str, right_run_id: str, suite_name: str | None=None)
```

## precompute_suite_comparisons_for_run

[backend/app/worker/tasks.py:3358](../../backend/app/worker/tasks.py#L3358)

Precompute default latest-vs-previous suite comparison reports after nightly runs.

```python
celery_app.task(name='app.worker.tasks.precompute_suite_comparisons_for_run', base=DownstreamTrackedTask, bind=True, max_retries=1, queue='ai_analysis', time_limit=1800)
precompute_suite_comparisons_for_run(self, test_run_id: str, project_id: str)
```

## dispatch_ai_summary_email

[backend/app/worker/tasks.py:3547](../../backend/app/worker/tasks.py#L3547)

EM-1: Send AI executive-summary email after the pipeline completes.

Loads the run summary (MongoDB or fallback) and dispatches to users
subscribed to AI_ANALYSIS_COMPLETE notifications. Durable child delivery
keys deduplicate retries per run.

```python
celery_app.task(name='app.worker.tasks.dispatch_ai_summary_email', base=DownstreamTrackedTask, bind=True, max_retries=2, default_retry_delay=30, queue='default')
dispatch_ai_summary_email(self, test_run_id: str, project_id: str, build_number: str, pipeline_run_id: str | None=None, evidence_bundle_sha256: str | None=None, source_executive_summary: str | None=None, source_executive_panel: dict | None=None, source_summary_is_ai: bool | None=None)
```

## notify_release_decision_no_go

[backend/app/worker/tasks.py:3861](../../backend/app/worker/tasks.py#L3861)

W09 NTF-01: stage "Quality gate failed" for a run the deep pipeline
published as NO_GO.

Enqueued by the decision-report critic after it publishes
(``decision_report_critic_agent._persist``), so the reads and the staging
run here on ``default`` -- never inside the pipeline task, never on
``ai_analysis``. ``gate_notifications.stage_agent_decision_no_go`` decides
(no decision, a pipeline mismatch, a human override, a withheld value and
anything but NO_GO stage nothing; one delivery per run however often the
pipeline re-publishes) and this task owns the commit.

No retry: the relay of staged rows is the durable half. A lost enqueue or
a failure here costs this one notification, as it does for the
``release.decided`` webhook; it is logged and never raised.

```python
celery_app.task(name='app.worker.tasks.notify_release_decision_no_go', bind=True, max_retries=0, queue='default', time_limit=60)
notify_release_decision_no_go(self, run_id: str, pipeline_run_id: str | None=None, evidence_bundle_sha256: str | None=None)
```

## generate_ai_test_cases_task

[backend/app/worker/tasks.py:3909](../../backend/app/worker/tasks.py#L3909)

Background task: run LLM test-case generation and persist results to DB.
Enqueued by POST /cases/ai-generate/async — fires immediately and returns,
so the HTTP request never times out.

```python
celery_app.task(name='app.worker.tasks.generate_ai_test_cases_task', bind=True, max_retries=1, queue='ai_analysis', time_limit=300)
generate_ai_test_cases_task(self, requirements: str, project_id: str, author_id: str)
```

## create_ai_test_plan_task

[backend/app/worker/tasks.py:4006](../../backend/app/worker/tasks.py#L4006)

Background task: run LLM plan optimisation and persist the test plan to DB.

```python
celery_app.task(name='app.worker.tasks.create_ai_test_plan_task', bind=True, max_retries=1, queue='ai_analysis', time_limit=300)
create_ai_test_plan_task(self, project_id: str, author_id: str, plan_name: str | None=None, constraints: str | None=None)
```

## generate_ai_strategy_task

[backend/app/worker/tasks.py:4099](../../backend/app/worker/tasks.py#L4099)

Background task: run LLM strategy generation and persist to DB.

```python
celery_app.task(name='app.worker.tasks.generate_ai_strategy_task', bind=True, max_retries=1, queue='ai_analysis', time_limit=300)
generate_ai_strategy_task(self, project_id: str, author_id: str, project_context: str, strategy_name: str | None=None)
```

## take_coverage_snapshot

[backend/app/worker/tasks.py:4166](../../backend/app/worker/tasks.py#L4166)

Scheduled task (beat 00:05 UTC): one coverage snapshot a project a UTC day (W08 TRND-07).

For every active project: yesterday and every MISSING day of the last 30,
computed from live data and UPSERTED (``coverage_snapshot_service``), so a
second run rewrites a day and never duplicates it; today's partial day is
never stored. The project ids are read in one session, closed; each
project then has ITS OWN session and one commit, and a failure is logged
and skipped so one project cannot stop the sweep. No analytics epoch bump:
the history read is at most its cache TTL stale.

```python
celery_app.task(name='app.worker.tasks.take_coverage_snapshot', queue='default', soft_time_limit=1500, time_limit=1800)
take_coverage_snapshot()
```

## reindex_search

[backend/app/worker/tasks.py:4220](../../backend/app/worker/tasks.py#L4220)

Background task: reindex test cases into ChromaDB for semantic search.
Uses incremental indexing by default; pass full=True for complete rebuild.

```python
celery_app.task(name='app.worker.tasks.reindex_search', bind=True, max_retries=2, soft_time_limit=540, time_limit=600)
reindex_search(self, project_id: str | None=None, full: bool=False)
```

## rebuild_embedding_generation

[backend/app/worker/tasks.py:4524](../../backend/app/worker/tasks.py#L4524)

Build the TARGET embedding generation, one ≤ 480 s slice per invocation.

Routed to ``ai_analysis``. Re-enqueues itself (same tag, same fence) until
the build finishes, then switches the active generation and schedules
``retire_embedding_generations``. A stale fence (the setting changed
again) answers ``superseded``; a failure leaves the active generation in
use and records why (``status: failed``). No Celery retry.

```python
celery_app.task(name='app.worker.tasks.rebuild_embedding_generation', bind=True, soft_time_limit=600, time_limit=660)
rebuild_embedding_generation(self, generation_tag: str, fence: int)
```

## retire_embedding_generations

[backend/app/worker/tasks.py:4649](../../backend/app/worker/tasks.py#L4649)

Drop the collections of generations retired ≥ RETIRE_GRACE_SECONDS ago.

Default queue, no beat entry: scheduled by a switch (and by a settings
change that replaced a target). A copy family's writes that landed in the
old generation are copied to the active one first. A Chroma fault keeps
the generation listed and re-enqueues this task.

```python
celery_app.task(name='app.worker.tasks.retire_embedding_generations', bind=True)
retire_embedding_generations(self)
```

## purge_search_usage_events

[backend/app/worker/tasks.py:4689](../../backend/app/worker/tasks.py#L4689)

Nightly: delete ``search_query_events`` older than the fixed 30-day window.

Deployment-wide (NULL-project rows and projects without a retention policy
included), in batches of ``USAGE_PURGE_BATCH``, each committed, at most
100 batches a run, until a batch deletes nothing.

```python
celery_app.task(name='app.worker.tasks.purge_search_usage_events', bind=True)
purge_search_usage_events(self)
```

## sync_knowledge_source

[backend/app/worker/tasks.py:4705](../../backend/app/worker/tasks.py#L4705)

Fetch content for a KnowledgeSource, chunk, index in ChromaDB, and update sync state.

```python
celery_app.task(queue='ai_analysis', bind=True, max_retries=3)
sync_knowledge_source(self, source_id: str, trigger: str='manual')
```

## resync_stale_knowledge_sources

[backend/app/worker/tasks.py:4734](../../backend/app/worker/tasks.py#L4734)

Periodic task: find stale/failed sources and enqueue individual sync tasks.

Reaps sources stranded in SYNCING first. ``run_sync`` commits SYNCING
before a fetch+chunk+embed that can outlive the process, and every terminal
write sits in an ``except`` block that a kill never runs -- so an OOM,
eviction or Celery hard time limit strands the row. ``list_stale_sources``
excludes SYNCING to avoid concurrent syncs, so nothing would ever pick it
up again. Reaping flips it to FAILED, which that same sweep already
selects, so recovery happens in this very run.

```python
celery_app.task(queue='default', bind=True, max_retries=0)
resync_stale_knowledge_sources(self)
```

## refresh_perf_baselines

[backend/app/worker/tasks.py:4790](../../backend/app/worker/tasks.py#L4790)

Nightly sweep that extends each per-test duration baseline with
the newest observations from the TestCase table.

No-op until the ``perf_regression_detection`` feature flag is on.

```python
celery_app.task(name='app.worker.tasks.refresh_perf_baselines', queue='default', bind=True, max_retries=0)
refresh_perf_baselines(self)
```

## deliver_webhook

[backend/app/worker/tasks.py:4822](../../backend/app/worker/tasks.py#L4822)

Deliver a single webhook subscription event.

Delegates the actual HTTP work to ``webhook_service.deliver`` which
holds the DB row as the authoritative outcome. When that function
signals a retryable failure, we schedule an exponential retry via
``self.retry`` so Celery's own backoff policy drives the cadence.

```python
celery_app.task(name='app.worker.tasks.deliver_webhook', queue='default', bind=True, max_retries=5, default_retry_delay=30)
deliver_webhook(self, delivery_id: str, dispatch_token: str | None=None)
```

## run_flaky_quarantine_maintenance

[backend/app/worker/tasks.py:4882](../../backend/app/worker/tasks.py#L4882)

Nightly housekeeping for the flaky auto-quarantine workflow.

Runs four passes in order:

  1. ``expire_stale_proposals`` — PROPOSED rows older than 7 days
     flip to EXPIRED so the UI stays readable.
  2. ``schedule_pending_rechecks`` — windowed quarantines whose
     ``recheck_at`` has passed move to RECHECK_SCHEDULED. Covers both
     QUARANTINED (the first window) and RE_QUARANTINED (every window
     after that); missing the latter stranded re-quarantined tests in
     an active state permanently.
  3. ``run_recheck_cycle`` — evaluates RECHECK_SCHEDULED rows against
     recent TestCase history and either releases or re-quarantines
     the test.
  4. ``mark_stale_quarantines`` (PMF US-5.4) — active quarantines past
     their SLA window get a once-per-entry ``test.quarantine_stale``
     notification (anchored on ``stale_notified_at``).

Every pass is a no-op when the ``flaky_auto_quarantine`` feature flag
is off, so enabling this beat entry is safe on existing deployments.

```python
celery_app.task(name='app.worker.tasks.run_flaky_quarantine_maintenance', queue='default', bind=True, max_retries=0)
run_flaky_quarantine_maintenance(self)
```

## train_flaky_confidence_model

[backend/app/worker/tasks.py:4952](../../backend/app/worker/tasks.py#L4952)

Nightly retrain of the FLK-P3 flaky-confidence model from human
quarantine approve/reject decisions.

A no-op-safe degrade chain: returns ``insufficient_data`` /
``insufficient_class_diversity`` / ``error`` status strings (never raises)
when scikit-learn is absent or there aren't yet enough labeled decisions,
so enabling this beat entry is safe on a fresh deployment. The model is a
LOCAL scikit-learn artifact — no outbound calls — so it is unaffected by
``AI_OFFLINE_MODE``.

```python
celery_app.task(name='app.worker.tasks.train_flaky_confidence_model', queue='default', bind=True, max_retries=0)
train_flaky_confidence_model(self)
```

## train_ml_classifier

[backend/app/worker/tasks.py:4994](../../backend/app/worker/tasks.py#L4994)

Train the failure classifier once (``services/ml/trainer.train_classifier``).

Nightly at 03:10 UTC from beat (``triggered_by="beat"``, gated by
``ML_RETRAIN_ENABLED``) and on "Train now" (``POST
/api/v1/settings/ai/ml/retrain``, ``triggered_by="user:<name>"``, which
took ``ml:retrain:lock`` under this task's id before queueing it). The job
never raises: below the sample floor, below the accuracy threshold or
without scikit-learn it records the outcome in ``ml:retrain:last`` and
keeps the previous model. A trained model is published to the object store
so every pod picks it up, and audited here as ``ml_classifier / trained``
(the task owns the commit; the job service never commits). CPU-bound and
local: no outbound call, so ``AI_OFFLINE_MODE`` does not apply.

```python
celery_app.task(name='app.worker.tasks.train_ml_classifier', queue='default', bind=True, max_retries=0, time_limit=1800, soft_time_limit=1500)
train_ml_classifier(self, triggered_by: str='beat')
```

## run_integration_health_probes

[backend/app/worker/tasks.py:5106](../../backend/app/worker/tasks.py#L5106)

Periodic task: probe all configured integrations and record health status.

W10 INT-04: the deployment-wide probes, then every enabled per-project
GitHub / GitLab integration (read on one short session, probed over HTTP
with no session open, at most 5 in flight), persisted together; this full
sweep also prunes the rows of integrations that were removed or disabled.

```python
celery_app.task(name='app.worker.tasks.run_integration_health_probes', bind=True, max_retries=0, queue='default')
run_integration_health_probes(self)
```

## enrich_run_ocp_metadata

[backend/app/worker/tasks.py:5148](../../backend/app/worker/tasks.py#L5148)

Fetch the OpenShift pod metadata of a run the sentinel ingest committed.

Enqueued by ``ingestion.process_sentinel`` after its commit, for a run
that names a pod and a namespace. ``ocp_enrichment.pod_metadata_for_run``
reads on a short session that it closes, then makes the pod and events
calls with no session open; this task then stages the write on its own
short session and commits it (the worker owns the transaction). Best
effort: every failure is logged (structlog kwargs, no token) and
swallowed, and nothing is retried (the run is complete without it).

```python
celery_app.task(name='app.worker.tasks.enrich_run_ocp_metadata', bind=True, max_retries=0, queue='ingestion')
enrich_run_ocp_metadata(self, run_id: str)
```

## sync_jira_defect_statuses

[backend/app/worker/tasks.py:5197](../../backend/app/worker/tasks.py#L5197)

Periodic task: mirror Jira issue status onto linked OPEN defects.

The task owns the transaction (no request session exists here); the
service stages the ``jira_status`` / ``external_status_at`` /
``external_status_conflict`` mutations and this commit makes them
durable. Capped inside the service (~50 issues/cycle) so a big backlog
never hammers Jira — the 15-minute beat catches up over cycles.

```python
celery_app.task(name='app.worker.tasks.sync_jira_defect_statuses', bind=True, max_retries=0, queue='default')
sync_jira_defect_statuses(self)
```

## dispatch_scheduled_digests

[backend/app/worker/tasks.py:5230](../../backend/app/worker/tasks.py#L5230)

Periodic task: find all digest subscriptions due for delivery and dispatch.

Runs daily at 07:00 UTC. For each active, non-paused subscription whose
next_delivery_at <= now, generate digest content and deliver via the
configured channel.

```python
celery_app.task(name='app.worker.tasks.dispatch_scheduled_digests', bind=True, max_retries=1, default_retry_delay=60, queue='default')
dispatch_scheduled_digests(self)
```

## dispatch_weekly_flaky_debt_reviews

[backend/app/worker/tasks.py:5657](../../backend/app/worker/tasks.py#L5657)

Weekly beat (Mondays 07:10 UTC): send each channel-mapped team its
flaky-debt review draft through its team notification channel. Teams
WITHOUT a channel are deliberately not handled here — their
drafts fold into the project's weekly digest as a section instead
(``digest_content_service.generate_digest``).

All content is deterministic TEXT built from the quarantine lifecycle
rows — no LLM calls. Per-project failures are logged and skipped inside
the service (fail-open); delivery audit rows are written via
``notification_routing.record_team_delivery_logs``.

```python
celery_app.task(name='app.worker.tasks.dispatch_weekly_flaky_debt_reviews', bind=True, max_retries=1, default_retry_delay=60, queue='default')
dispatch_weekly_flaky_debt_reviews(self)
```

## close_stale_live_sessions

[backend/app/worker/tasks.py:5693](../../backend/app/worker/tasks.py#L5693)

Periodic safety net for live sessions whose clients forget to send a
``run_complete`` event.

Symptom this fixes: clients (especially raw curl/Postman users) post
test results without a closing ``run_complete``. The LiveSession row
stays ``status='active'`` forever, ``upsert_test_run`` never runs, and
the run only ever appears in Live Execution — Runs / Overview / Coverage
/ Failures / Trends all read from ``test_runs`` so they show 0.

Heuristic: any active LiveSession whose Redis state hash either no
longer exists (24h Redis TTL has expired = definitely orphaned) or
whose ``last_event_at`` is older than ``idle_minutes`` is closed via
the normal ``stream_service.close_session`` path. That path is
idempotent (it short-circuits if status='completed'), so a session
that was closed legitimately between the LIST and the per-row close
doesn't double-fire.

```python
celery_app.task(name='app.worker.tasks.close_stale_live_sessions', bind=True, queue='default', time_limit=300)
close_stale_live_sessions(self, idle_minutes: int=15)
```

## reap_stuck_agent_pipelines

[backend/app/worker/tasks.py:5812](../../backend/app/worker/tasks.py#L5812)

Reclaim pipelines whose worker stopped renewing its lease (E7.3).

Before leases this task declared a run dead by a fixed 30-minute age, and
the /agents router applied the same guess at read time. That is wrong both
ways: a legitimately long deep run was reported failed while it was still
working, and a worker that died in its first minute kept its row
``running`` for half an hour.

Now a holder renews ``lease_expires_at`` every 30 seconds from inside the
stage it is running. A row whose lease has lapsed has no live holder, so:

* the fencing token is ROTATED, which makes every subsequent write from
  the old holder fail (``LeaseLost``) instead of landing underneath the
  next attempt;
* the row moves to ``retry_wait`` when attempts remain -- the lapse counts
  as a failed attempt and the run is rescheduled under its own id -- or to
  ``failed`` at the ceiling.

``stale_minutes`` is retained for callers and for rows that predate the
lease columns (no ``lease_expires_at``), which still fall back to age.

```python
celery_app.task(name='app.worker.tasks.reap_stuck_agent_pipelines', bind=True, queue='default', time_limit=300)
reap_stuck_agent_pipelines(self, stale_minutes: int=30)
```

## flag_orphan_test_suites

[backend/app/worker/tasks.py:5973](../../backend/app/worker/tasks.py#L5973)

Detect and structured-log orphan ``TestSuite`` rows for ops review.

The ingestion pipeline's ``finalize_run`` commits each step in its own
session via ``_run_isolated`` (resilience pattern: a failing canonical
sync shouldn't roll back the suite sync that already succeeded). The
trade-off is that suite_sync may create a TestSuite row, then
canonical_sync fails before linking any CanonicalTestCase rows to it
— leaving an empty suite dangling.

This task runs nightly, finds non-default TestSuite rows that:

* Have no ``CanonicalTestCase`` children, AND
* Are older than ``min_age_minutes`` (default 60 — recent suites are
  still mid-ingest and not yet orphaned).

For each orphan it emits a structured WARNING (greppable by
``event=orphan_test_suite``) and bumps the ``orphan_test_suites_total``
Prometheus counter. The suite row is NOT deleted automatically — an
operator decides whether to reassign / delete / wait for the next
ingest to repopulate it.

```python
celery_app.task(name='app.worker.tasks.flag_orphan_test_suites', bind=True, queue='default', time_limit=300)
flag_orphan_test_suites(self, min_age_minutes: int=60)
```

## reconcile_canonical_deletions

[backend/app/worker/tasks.py:6072](../../backend/app/worker/tasks.py#L6072)

Nightly safety net for canonical-deletion detection (Phase I follow-up).

``finalize_run`` already calls ``test_suite_service.reconcile_canonical_deletions``
in an isolated session for every completed run, which is the primary
write path. This beat task exists for two failure modes that primary
path can't catch:

  1. A run finalizes but the isolated reconcile step itself raises
     (transient DB blip, lock conflict). Without this safety net the
     canonical stays ``active`` until the next run for that project.
  2. A project that's gone quiet — no new runs for days — needs
     its catalog kept honest. Otherwise stale ``active`` rows
     persist indefinitely after the underlying tests were removed.

Iterates every project and runs the same service function. Per-project
failures are logged but never abort the sweep so one bad project
doesn't starve the rest.

```python
celery_app.task(name='app.worker.tasks.reconcile_canonical_deletions', bind=True, queue='default', time_limit=600)
reconcile_canonical_deletions(self)
```

## drain_active_live_sessions

[backend/app/worker/tasks.py:6155](../../backend/app/worker/tasks.py#L6155)

Phase 4.5 — drain every active live session's Redis event buffer
into Postgres ``test_cases`` rows.

Runs on a 30-second beat schedule (``drain-active-live-sessions``)
so a long-running session that exceeds the ``LTRIM`` cap doesn't
lose its oldest per-test rows. The drain task is idempotent
(per-run SET-NX lock + LRANGE/LTRIM atomicity under append-only
writers) so overlapping ticks degrade gracefully.

The terminal ``persist_live_session`` + ``finalize_run`` chain at
``close_session`` time is unchanged — this task only writes per-
test rows progressively so close-time has less to do.

```python
celery_app.task(name='app.worker.tasks.drain_active_live_sessions', bind=True, queue='default', time_limit=120)
drain_active_live_sessions(self)
```

## backfill_placeholder_test_cases

[backend/app/worker/tasks.py:6186](../../backend/app/worker/tasks.py#L6186)

Retroactively synthesize placeholder TestCase rows.

For every TestRun where ``failed_tests + broken_tests > 0`` but
no ``test_cases`` rows exist (the live-stream-buffer-eviction or
SDK-no-test_result-events scenario), this task inserts the same
placeholder rows that ``persist_live_session`` now creates at
write time for new runs. The follow-on
``backfill_unassigned_failures`` beat task (every 15 min) then
picks them up via ``failed_test_assignment_service`` so the
placeholders appear on ``/my-failures``.

Idempotent — the candidate query filters to runs with zero
test_cases, so a second tick after the first one's commit
produces zero new rows.

```python
celery_app.task(name='app.worker.tasks.backfill_placeholder_test_cases', bind=True, queue='default', time_limit=600)
backfill_placeholder_test_cases(self, max_runs_per_project: int=500)
```

## backfill_suite_executions

[backend/app/worker/tasks.py:6242](../../backend/app/worker/tasks.py#L6242)

EXEC-02: time every run that predates ``suite_executions`` -- one-shot, chunked, re-entrant.

A chunk is ``chunk_size`` runs in ``(created_at, id)`` order after the Redis
cursor ``suite_exec.backfill_cursor``, written by
``suite_execution_service.backfill_chunk`` and committed on its own; then
the cursor advances. A run that already has a ``timing_source`` (a finalize
timed it) is skipped, so a pass never overwrites report timing and
re-running from any cursor is idempotent (the rows upsert on
``(test_run_id, suite_key)``). A pass stops after ``max_chunks`` chunks,
``time_budget_s`` seconds, or once its own RSS grew by
``BACKFILL_RSS_GROWTH_CAP_BYTES`` (bulk work in one process OOMed the API
before, so this runs in the worker and bounds itself); the beat entry
``backfill-suite-executions`` resumes it. A short chunk marks the cursor
``done``, after which every tick returns at once. Deleting the Redis key
runs it again from the oldest run.

```python
celery_app.task(name='app.worker.tasks.backfill_suite_executions', bind=True, queue='default', max_retries=0, soft_time_limit=540, time_limit=600)
backfill_suite_executions(self, chunk_size: int=500, max_chunks: int=40, time_budget_s: float=300.0)
```

## backfill_ai_run_versions

[backend/app/worker/tasks.py:6426](../../backend/app/worker/tasks.py#L6426)

AIH-01: give every run analysed before AI history existed its first versions -- one-shot,
chunked, re-entrant.

A chunk is ``chunk_size`` runs in ``(created_at, id)`` order after the Redis
cursor ``ai_history.backfill_cursor``: ``ai_history_service.backfill_chunk``
records a ``run_summary`` version from ``run_summaries`` and an
``analysis_digest`` version from the run's current analysis, each only
when the run has none of that kind yet (a run a live pipeline already
versioned is skipped, so re-running from any cursor is idempotent). Each
chunk runs on its own session, committed on its own; then the cursor
advances. A pass stops after ``max_chunks`` chunks, ``time_budget_s``
seconds, or once its own RSS grew by 256 MiB; the beat entry
``backfill-ai-run-versions`` resumes it. A short chunk marks the cursor
``done``, after which every tick is one Redis read. Deleting the Redis key
runs it again from the oldest run. ``ai_analysis_versions`` needs no
backfill here: migration 0205 wrote it.

```python
celery_app.task(name='app.worker.tasks.backfill_ai_run_versions', bind=True, queue='default', max_retries=0, soft_time_limit=540, time_limit=600)
backfill_ai_run_versions(self, chunk_size: int=100, max_chunks: int=40, time_budget_s: float=300.0)
```

## auto_recover_completed_live_runs

[backend/app/worker/tasks.py:6463](../../backend/app/worker/tasks.py#L6463)

Recover REAL per-test rows from ``TestRun.event_archive`` for
completed live_stream runs whose ``test_cases`` table is empty.

Defensive net for the close_session → persist_live_session handoff.
When the worker dispatch is dropped (silent apply_async failure,
queue backpressure, worker restart) the run shows correct aggregates
on /runs but per-test detail is missing on /test-management,
/coverage/suite, and the run-detail page. The hourly
``backfill_placeholder_test_cases`` task eventually inserts marker
rows but loses the real test names the SDK shipped. We capture
those names in ``TestRun.event_archive`` at close-time (15-day TTL)
so this task can materialise them when the regular handoff
misfired. Runs on its own cadence (every 2 minutes) so users see
real per-test detail within ~2 minutes of close_session, well
before the placeholder backfill fires.

```python
celery_app.task(name='app.worker.tasks.auto_recover_completed_live_runs', bind=True, queue='default', time_limit=120)
auto_recover_completed_live_runs(self, lookback_hours: int=24, max_runs: int=100)
```

## backfill_unassigned_failures

[backend/app/worker/tasks.py:6534](../../backend/app/worker/tasks.py#L6534)

Retroactively assign FAILED/BROKEN TestCases left unassigned.

Drives two related backfills:

* ``default_qa_lead_service.backfill_default_qa_lead_for_all_projects``
  to provision the synthetic QA-lead user on projects created before
  this feature shipped.
* ``failed_test_assignment_service.backfill_unassigned_failures`` for
  every project so already-ingested failures pick up the new owner.

Both resolvers are idempotent (default-lead provisioning is a no-op
when the FK is already set; per-run assignment only touches NULL
rows), so this can run on a tight cadence without risking write
storms. One project is processed per session so a stuck project
doesn't starve the others.

```python
celery_app.task(name='app.worker.tasks.backfill_unassigned_failures', bind=True, queue='default', time_limit=600)
backfill_unassigned_failures(self, max_runs_per_project: int=200)
```

## notify_test_suite_owner

[backend/app/worker/tasks.py:6658](../../backend/app/worker/tasks.py#L6658)

Dispatch the "test is failing repeatedly" notification email.

Called from ``POST /api/v1/analytics/notify-owner`` after the caller has
already resolved the recipient. Kept idempotent-ish via a short dedup
window so a double-click doesn't fan out two emails.

```python
celery_app.task(name='app.worker.tasks.notify_test_suite_owner', bind=True, max_retries=3, queue='default')
notify_test_suite_owner(self, *, to_email: str, owner_name: str, test_name: str, suite_name: str | None, fail_count: int | None, days: int, project_id: str, project_name: str | None, latest_run_id: str | None, latest_run_build: str | None, is_fallback_owner: bool, triggered_by: str | None=None)
```

## run_duplicate_detection

[backend/app/worker/tasks.py:6757](../../backend/app/worker/tasks.py#L6757)

Phase 4 — tiered duplicate authored-test-case detection per project.

Two modes:

* **Sweep (``project_id`` is None — the nightly beat entry).** Loads every
  project id and FANS OUT one ``run_duplicate_detection.delay(project_id=…)``
  sub-task per project, so each project gets its OWN 900s time budget and a
  single slow/large project cannot starve the tail of the project list under
  the task's hard ``time_limit`` (previously the whole sweep ran in ONE task
  and a global timeout SIGKILLed the worker mid-loop, silently skipping every
  project after the kill point — always the same later-listed projects).
  Semantic is left OFF for the sweep (cheaper + deterministic); it is run on
  demand from the router instead.
* **Single project (``project_id`` given — the fan-out sub-task or an ad-hoc
  call).** Runs ``detect_duplicates_for_project`` for that one project. This
  worker task is the COMMIT OWNER — the detection service never commits.

Idempotent: candidates are upserted on ``(project_id, case_a_id, case_b_id)``
and dismissed pairs are suppressed, so repeated runs converge.

```python
celery_app.task(name='app.worker.tasks.run_duplicate_detection', bind=True, queue='default', time_limit=900)
run_duplicate_detection(self, project_id: str | None=None, enable_semantic: bool | None=None)
```

## persist_ai_eval_shadow_pair

[backend/app/worker/tasks.py:7034](../../backend/app/worker/tasks.py#L7034)

Persist one sampled live pair as a pending labelling candidate.

```python
celery_app.task(name='app.worker.tasks.persist_ai_eval_shadow_pair', queue='default', bind=True, max_retries=0)
persist_ai_eval_shadow_pair(self, *, project_id: str, agent_id: str, sample_key: str, incumbent_tier: str, candidate_tier: str, incumbent_output: dict, candidate_output: dict, incumbent_tokens: int, candidate_tokens: int, daily_token_budget: int)
```

## run_scheduled_agent_eval

[backend/app/worker/tasks.py:7079](../../backend/app/worker/tasks.py#L7079)

Evaluate the agent stack against the golden datasets, on a schedule (F-11).

Every other quality signal in this system is event-driven: the eval gate
fires when a prompt changes and never otherwise, so a model swap, a routing
change or a slow drift in output quality is invisible until someone opens
an API route by hand. This is the scheduled reading.

It seeds the golden datasets first, because without them the gate returns
``FAIL: no evaluation dataset found`` -- which reads as "quality regressed"
when it means "nothing was measured". Seeding is idempotent, so the cost
after the first run is four SELECTs.

``insufficient_samples`` is already a blocking verdict upstream and is deliberately
left that way: a gate with nothing to compare against has not passed.

```python
celery_app.task(name='app.worker.tasks.run_scheduled_agent_eval', queue='default', bind=True, max_retries=0)
run_scheduled_agent_eval(self, change_id: str | None=None)
```

## run_weekly_agent_quality_drift

[backend/app/worker/tasks.py:7170](../../backend/app/worker/tasks.py#L7170)

Run G5 and commit capability review pins once each Monday.

```python
celery_app.task(name='app.worker.tasks.run_weekly_agent_quality_drift', queue='default', bind=True, max_retries=0)
run_weekly_agent_quality_drift(self)
```

## run_candidate_evaluation

[backend/app/worker/tasks.py:7264](../../backend/app/worker/tasks.py#L7264)

Replay one queued candidate evaluation (W16 AGT-09 b).

Dispatched by ``POST /ai-eval/runs/evaluate/{dataset_id}`` AFTER its commit.
Never retried and never re-dispatched: a worker that dies leaves the run
``running`` and ``reconcile_stale_eval_runs`` settles it ``partial``. A
run-level failure marks the run ``failed`` (its stored items kept).

```python
celery_app.task(name='app.worker.tasks.run_candidate_evaluation', queue='ai_analysis', bind=True, max_retries=0)
run_candidate_evaluation(self, run_id: str)
```

## reconcile_stale_eval_runs

[backend/app/worker/tasks.py:7304](../../backend/app/worker/tasks.py#L7304)

Every 10 min: a replay silent for 15 min -> ``partial`` / ``worker_lost``;
a ``queued`` one never delivered -> ``failed``. Never re-dispatched.

```python
celery_app.task(name='app.worker.tasks.reconcile_stale_eval_runs', queue='default', bind=True, max_retries=0)
reconcile_stale_eval_runs(self)
```

## golden_match_shadow_pairs

[backend/app/worker/tasks.py:7337](../../backend/app/worker/tasks.py#L7337)

Nightly (03:50 UTC, after the 03:10 ML training): label pending
``root_cause:`` shadow pairs from the test case's OWN human feedback
(``label.source = "feedback"``). Summary pairs are never auto-labelled.

```python
celery_app.task(name='app.worker.tasks.golden_match_shadow_pairs', queue='default', bind=True, max_retries=0)
golden_match_shadow_pairs(self)
```

## expire_unlabelled_shadow_pairs

[backend/app/worker/tasks.py:7370](../../backend/app/worker/tasks.py#L7370)

Nightly (02:50 UTC, after the 02:40 search-usage purge): delete PENDING
shadow pairs older than 30 days, deployment-wide, in committed batches
(≤ 100 a night). Labelled pairs are G2 evidence and are kept.

```python
celery_app.task(name='app.worker.tasks.expire_unlabelled_shadow_pairs', queue='default', bind=True, max_retries=0)
expire_unlabelled_shadow_pairs(self)
```

## generate_report_export

[backend/app/worker/tasks.py:7388](../../backend/app/worker/tasks.py#L7388)

VIZ-607: render one background report export and store the file.

One argument, the ``report_exports`` id: everything else is read from the
row, so a redelivered or retried message carries nothing stale. The
service claims the row with a guarded UPDATE (a duplicate delivery is a
no-op), re-checks the requester's access, renders, stores, and records the
outcome fenced by attempt number. No Celery retry: a failure is recorded
on the row and the reader retries it from the page.

```python
celery_app.task(name='app.worker.tasks.generate_report_export', queue='default', max_retries=0)
generate_report_export(export_id: str)
```

## sweep_report_exports

[backend/app/worker/tasks.py:7404](../../backend/app/worker/tasks.py#L7404)

VIZ-607: delete report export files and rows past their 7 days.

Object storage has no lifecycle rules, so nothing else ever deletes them.

```python
celery_app.task(name='app.worker.tasks.sweep_report_exports', queue='default', max_retries=0)
sweep_report_exports()
```

## run_retention_purges

[backend/app/worker/tasks.py:7424](../../backend/app/worker/tasks.py#L7424)

Nightly retention purge sweep (02:00 UTC beat), or a single-project
execute-mode purge when enqueued from the router with ``project_id``.

The beat path only touches projects whose policy row is ``enabled``;
the explicit-project path was already gated by the router (ADMIN +
typed-name confirmation + 409-when-disabled).

```python
celery_app.task(name='app.worker.tasks.run_retention_purges', queue='default', bind=True, max_retries=0)
run_retention_purges(self, project_id: str | None=None)
```

## calibrate_flaky_classifiers

[backend/app/worker/tasks.py:7448](../../backend/app/worker/tasks.py#L7448)

Measure how well the flaky classifier actually works, per project.

Roadmap Phase 0 (P0-2). The backtest is retrospective and reads a 90-day
window of failures per project, so it runs off-peak on a beat rather than
inline on any request path.

Nothing consumes the result yet — Phase 0 measures, a later phase gates on
it. A per-project try/except keeps one bad project from stopping the sweep,
matching the retention-purge sweep's shape.

```python
celery_app.task(bind=True, name='app.worker.tasks.calibrate_flaky_classifiers')
calibrate_flaky_classifiers(self, project_id: str | None=None)
```

## recompute_flaky_scores

[backend/app/worker/tasks.py:7522](../../backend/app/worker/tasks.py#L7522)

Recompute the continuous flakiness score per project (roadmap Phase 2).

Reads a 30-day window per project, so it runs off-peak on a beat rather
than on any request path. Per-project try/except keeps one bad project from
stopping the sweep, matching the retention-purge and calibration sweeps.

Fingerprints below the evidence floor are skipped rather than stored as
0.0 — a stored zero would read as "measured, and clean".

```python
celery_app.task(bind=True, name='app.worker.tasks.recompute_flaky_scores')
recompute_flaky_scores(self, project_id: str | None=None)
```

## recompute_systemic_clusters

[backend/app/worker/tasks.py:7587](../../backend/app/worker/tasks.py#L7587)

Rebuild systemic co-failure clusters per project (roadmap Phase 3).

Reads a 60-day window of failures per project, so it runs off-peak on a
beat. Per-project try/except keeps one bad project from stopping the sweep.

Most projects legitimately produce ZERO clusters — reported as
``projects_without_clusters`` rather than treated as a failure.

```python
celery_app.task(bind=True, name='app.worker.tasks.recompute_systemic_clusters')
recompute_systemic_clusters(self, project_id: str | None=None)
```

## screen_new_test_fingerprints

[backend/app/worker/tasks.py:7668](../../backend/app/worker/tasks.py#L7668)

Tier 1 of roadmap Phase 6: screen the new and directly-modified.

Runs on a short beat rather than in ``finalize_run``. Screening buys nothing
by being synchronous — TestLookup ingests results, it does not execute
tests, so there is no re-run to trigger the moment a suspect appears — and
keeping it off the ingest path means a screening bug cannot cost an
ingestion.

**Gated per project** on the ``flaky_detection_timing`` feature flag, which
is off until a project opts in. Nothing here changes a verdict, but it does
write state, and a project that has not asked for the tier should not
accumulate it.

```python
celery_app.task(bind=True, name='app.worker.tasks.screen_new_test_fingerprints')
screen_new_test_fingerprints(self, project_id: str | None=None)
```

## sweep_flaky_detection

[backend/app/worker/tasks.py:7751](../../backend/app/worker/tasks.py#L7751)

Tier 2 of roadmap Phase 6: the continuous whole-corpus pass.

Where tier 1 screens a diff, this reaches everything else — which is where
environment- and dependency-induced flakiness lives, and that is the part
of the corpus screening cannot see by construction.

Nightly, and deliberately not faster: it is measured against the flakiness
score's own 30-day window, which does not move enough inside a day to
justify re-reading the corpus. It runs after the score recompute so a
fingerprint that just cleared the evidence floor has its latency clock
closed on the same night it happened, not a day later.

Gated per project on the same flag as tier 1.

```python
celery_app.task(bind=True, name='app.worker.tasks.sweep_flaky_detection')
sweep_flaky_detection(self, project_id: str | None=None)
```

## delete_run_everywhere

[backend/app/worker/tasks.py:7832](../../backend/app/worker/tasks.py#L7832)

Delete ONE run across all five stores. Irreversible.

Queued by ``DELETE /api/v1/runs/{run_id}``, which has already refused
in-flight runs, cited runs, and prefixes outside the project scope. This
performs the deletion the route promised with its 202.

**Reuses ``execute_candidates``.** The Mongo -> MinIO -> Postgres ordering
is the hardest part of a cross-store delete — the Postgres CASCADE destroys
the only mapping from a run to its documents and keys, so those stores must
be visited while the mapping still exists. A second copy of that sequence
is exactly what S2a existed to prevent.

**The tombstone lands in the same commit as the deletion.** Five code paths
re-create a ``TestRun`` from a caller-supplied id on a SELECT miss.
Committing the tombstone first blocks live ingestion into a run that still
exists; committing it after leaves a window in which the run is gone and
those paths are free to bring it back. One transaction has neither problem.

``queue="critical"`` because an operator is waiting on the 202 and polling
the job; behind a long ingestion backlog it would look hung.

```python
celery_app.task(name='app.worker.tasks.delete_run_everywhere', bind=True, max_retries=2, queue='critical')
delete_run_everywhere(self, run_id: str, job_id: str | None=None, reason: str='', requested_by_id: str | None=None)
```

## execute_criteria_deletion_task

[backend/app/worker/tasks.py:7988](../../backend/app/worker/tasks.py#L7988)

Replay a frozen candidate set, one run at a time.

Per-run rather than one big transaction, deliberately. A criteria job can
cover thousands of runs across five stores; a single transaction holding
all of it would sit on locks for minutes and lose everything to one bad
row. Per-run means a failure costs one run, and the others still go.

That is also why ``partial`` exists as an outcome. The nightly purge
self-heals within 24h because it re-runs; nothing retries this
(``max_retries=0`` — a retry would replay deletions already done and
report them as failures). "Some of them went" is therefore a real, final
state, and reporting it as either success or failure would be a lie.

```python
celery_app.task(name='app.worker.tasks.execute_criteria_deletion_task', bind=True, max_retries=0, queue='critical')
execute_criteria_deletion_task(self, job_id: str, project_id: str, requested_by_id: str | None=None)
```

## reconcile_active_releases

[backend/app/worker/tasks.py:8163](../../backend/app/worker/tasks.py#L8163)

Sweep for projects with no active release, repair them, and REPORT.

Migration 0150. Every other enforcement point — project creation, rotation,
deletion, reset, the defensive resolve at ingest — is supposed to keep the
invariant true. This task exists because "supposed to" is not a guarantee,
and because SQL cannot express "every project has a row over there".

The reporting half is not incidental. A reconciliation task that silently
repairs what it finds makes the invariant look perfect precisely *because*
something keeps fixing it, and the defect that caused the violation is
never seen. So every detection increments a counter and is written to the
audit log with ``record_attempt`` — which commits on its own session and
therefore survives a repair that fails.

Two counters, deliberately: ``sweeps_total`` proves the sweep ran at all,
because a violations counter sitting at 0 reads identically whether nothing
is broken or nothing is checking. Alert on violations only against a
non-zero, increasing sweep count.

```python
celery_app.task(name='app.worker.tasks.reconcile_active_releases', bind=True, queue='default', time_limit=300)
reconcile_active_releases(self)
```

## reconcile_primary_releases

[backend/app/worker/tasks.py:8255](../../backend/app/worker/tasks.py#L8255)

Repair drift between ``test_runs.primary_release_id`` and the link table.

Migration 0152 denormalized the primary release onto ``test_runs`` so a
release filter is one indexed predicate rather than a join. Four live paths
change which link is primary and each must call ``sync_primary_release``;
this sweep exists because "must" is not "does".

Drift is not cosmetic. Release-scoped analytics read the denormalized
column while ``/runs`` reads the link table, so a stale value makes two
surfaces disagree about which release a run belongs to — with no error
anywhere. The counters make a missed call site measurable instead of
invisible.

```python
celery_app.task(name='app.worker.tasks.reconcile_primary_releases', bind=True, queue='default', time_limit=300)
reconcile_primary_releases(self)
```

## reconcile_release_sort_keys

[backend/app/worker/tasks.py:8342](../../backend/app/worker/tasks.py#L8342)

Fill in ``releases.sort_key`` for rows that have none.

Migration 0151 adds the column and 0153 indexes it, but neither computes
it. An earlier draft reimplemented the encoder in SQL and got pre-releases
wrong — every ``2.4.0-rc1`` landed in the text band, sorting after its own
GA instead of before it, which is the precise inversion the encoder exists
to prevent. Two implementations of one encoding is a drift this codebase
has paid for before, and SQL is the copy that cannot be unit-tested, so it
was deleted rather than patched.

This sweep is the single writer's reach into rows the writers missed:
releases created before 0151, and any future path that forgets. Safe to
run repeatedly — it only touches NULLs.

```python
celery_app.task(name='app.worker.tasks.reconcile_release_sort_keys', bind=True, queue='default', time_limit=300)
reconcile_release_sort_keys(self)
```

## run_agent_invocation

[backend/app/worker/tasks.py:8411](../../backend/app/worker/tasks.py#L8411)

Run one agent invoked through ``POST /api/v1/agents/{agent_id}/invoke`` (E1.2).

The invocation IS a pipeline run -- same leases, fencing, row-owned retries,
cancellation and Finalize (review staging) -- created under the id the API
minted, with a frozen plan that selects only the agent and its declared
dependencies. A failed attempt goes through the pipeline retry scheduler,
whose resume keeps that frozen plan.

```python
celery_app.task(name='app.worker.tasks.run_agent_invocation', bind=True, max_retries=0, queue='ai_analysis', time_limit=1800)
run_agent_invocation(self, invocation_id: str)
```

## export_training_data

[backend/app/worker/training_tasks.py:51](../../backend/app/worker/training_tasks.py#L51)

Weekly task: export verified training examples to MinIO for all three tracks.
After export, checks if any track has crossed its fine-tuning trigger threshold.

```python
celery_app.task(name='app.worker.training_tasks.export_training_data', bind=True, max_retries=2, queue='default', time_limit=600)
export_training_data(self)
```

## check_finetune_trigger

[backend/app/worker/training_tasks.py:78](../../backend/app/worker/training_tasks.py#L78)

Daily task: check if enough new verified feedback has accumulated
to trigger an incremental fine-tuning run.

```python
celery_app.task(name='app.worker.training_tasks.check_finetune_trigger', queue='default', time_limit=60)
check_finetune_trigger()
```

## run_finetune_pipeline

[backend/app/worker/training_tasks.py:93](../../backend/app/worker/training_tasks.py#L93)

Run the complete fine-tuning pipeline for one track:
  1. Find the latest training JSONL in MinIO
  2. Submit fine-tuning job to provider
  3. Poll until complete (with timeout)
  4. Evaluate candidate model against holdout set
  5. Promote if evaluation passes, retire old version
  6. Persist ModelVersion record

```python
celery_app.task(name='app.worker.training_tasks.run_finetune_pipeline', bind=True, max_retries=1, queue='default', time_limit=7200)
run_finetune_pipeline(self, track: str)
```
