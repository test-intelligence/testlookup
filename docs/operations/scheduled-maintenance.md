# Scheduled maintenance monitoring

Nightly quarantine maintenance runs at 04:00 UTC. Performance baseline refresh runs at 04:30 UTC. The alerts allow an hour for each enabled run to finish and wait another five minutes before firing.

Beat stamps its messages with the planned UTC slot when it publishes. The worker records the slot's attempt before doing the work and records completion after it succeeds. PostgreSQL stores both across worker restarts. A delayed message from an older slot cannot certify a newer one. Disabled features and a skipped baseline refresh do not record success. Interactive task calls without the Beat headers do not count as scheduled work.

The reader considers slots from the later of the schema installation time and the last off-to-on feature transition. This gives a newly monitored or re-enabled job time to reach its next scheduled slot. A description edit or a change between two positive rollout percentages leaves an overdue slot visible. Earlier history remains outside the new observation window.

| API sample | Meaning |
|---|---|
| `testlookup_nightly_job_enabled` | 1 when the job's feature is globally enabled with a positive rollout. |
| `testlookup_nightly_job_next_due_timestamp_seconds` | Unix timestamp of the next dispatch slot, or zero while disabled. |
| `testlookup_nightly_job_last_attempt_timestamp_seconds` | Start of the latest slot observed by a worker. |
| `testlookup_nightly_job_last_success_timestamp_seconds` | Completion of the latest successful slot. |
| `testlookup_nightly_job_overdue` | 1 when the latest eligible slot missed its completion deadline. |
| `testlookup_nightly_job_started_overdue_slot` | 1 when overdue and a worker began the slot or a later one. |

For `TestLookupFlakyQuarantineMaintenanceFailing` or `TestLookupPerfBaselineRefreshFailing`, inspect the latest attempt and success for the matching `schedule`. An overdue slot without an observed attempt points to Beat dispatch, queue delay or inability to persist the attempt. An observed attempt without success points to an incomplete, failed or skipped job. Check worker logs and database health before rerunning maintenance. The alert keeps its existing name for routing compatibility. It clears when a fresh scrape sees a successful slot that satisfies the deadline.

Verify collector health first with `testlookup_metric_collection_success{collector="nightly"}` and its last-success timestamp. A failed or stale scrape does not provide schedule truth. API replicas observe the same database, and Prometheus takes the maximum across fresh replicas for each schedule and installation.

Apply database migrations before starting the new Beat and workers so their observations can be stored. The threshold and recovery are covered by `infra/monitoring/tests/nightly-alerts.yml`. The worker/SQL regression covers never-run, disabled, failed, late and recovered slots. A deployment check should also exercise missed dispatch with an isolated broker and the intended alert receiver. [Celery periodic task options](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html#available-fields).
