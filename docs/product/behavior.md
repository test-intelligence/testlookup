# Behavior and error expectations

[Documentation home](../README.md)

| Trigger or condition | Expected behavior | Client/operator action | Source |
|---|---|---|---|
| Accepted upload | 202 with IDs; work continues in workers | Poll upload and run separately | [ingest](../../backend/app/routers/ingest.py) |
| Empty upload / unsupported explicit format | 400 before useful processing | Fix input/format | [ingest](../../backend/app/routers/ingest.py) |
| File larger than 50 MiB | 413 during bounded read | Split/reduce report or use supported batching | [upload reader](../../backend/app/routers/ingest.py) |
| Invalid typed field/status/bounds | 422 validation response | Read `detail` and correct payload | [schemas](../../backend/app/models/schemas.py) |
| Wrong project/key scope | Refusal, typically 403; entity guards can conceal inaccessible records | Use authorized project and credential | [dependencies](../../backend/app/core/deps.py) |
| Admission bucket exhausted | 429 and `Retry-After` | Back off, retain identity | [rate limit](../../backend/app/services/ingestion_rate_limit.py) |
| Redis memory above configured threshold | 503 and `Retry-After: 5` | Reduce producer load, inspect Redis/queues | [backpressure](../../backend/app/services/ingestion_backpressure.py) |
| Admission Redis probe unavailable | Those admission checks can fail open | Do not infer healthy capacity from acceptance | [backpressure](../../backend/app/services/ingestion_backpressure.py) |
| Duplicate source identity | Reuse canonical run identity; duplicate test rows collapse with worst outcome | Send specific CI identity to distinguish different jobs | [pipeline](../../backend/app/services/ingestion_pipeline.py) |
| Finalized run has no executed tests | `STOPPED` | Treat as no usable validation, inspect skipped/parse failures | [status](../../backend/app/services/run_status.py) |
| Unknown outcomes without known failures | `STOPPED`; known failed/broken cases still make run `FAILED` | Inspect unsupported result mapping | [status](../../backend/app/services/run_status.py) |
| Missing optional evidence or deadline-limited stage | Explicit skip/degraded metadata | Read stage quality and reasons | [workflow](../../backend/app/agents/workflow.py) |
| Pipeline transient error | Bounded retry using durable attempt state | Inspect next retry/attempt count | [retry policy](../../backend/app/services/retry_policy.py) |
| Manual retry after authority/config changes | New linked rerun when resume authority is incompatible | Compare provenance; do not expect checkpoint reuse | [retry config](../../backend/app/services/pipeline_retry_config.py) |
| Rejected/superseded AI report | Cannot be distributed as a draft when enforcement is active | Produce/review a valid replacement | [distribution policy](../../backend/app/services/report_distribution_policy.py) |
| Review enforcement disabled | Would-refuse audit path preserves rollout visibility | Distinguish observation from blocking | [distribution policy](../../backend/app/services/report_distribution_policy.py) |
| Missing optional model/vector service | Availability-dependent fallback or feature-specific failure | Inspect actual routing provenance | [analysis router](../../backend/app/services/analysis_router.py) |
| Stale frontend chunk after deployment | One reload recovery before error boundary | Refresh/reopen; inspect assets if failure persists | [App](../../frontend/src/App.tsx) |

## State vocabularies must stay distinct

| Object | Vocabulary / meaning |
|---|---|
| Test execution | `PASSED`, `FAILED`, `SKIPPED`, `BROKEN`, `UNKNOWN` in persistence; JSON batch input accepts the first four |
| Test run | `IN_PROGRESS`, `PASSED`, `FAILED`, `STOPPED` |
| File-upload task | `pending → parsing → ingesting → succeeded/failed`; Redis status expires after 24 hours |
| Agent pipeline internal | `pending`, `running`, `retry_wait`, `completed`, `passed`, `failed` |
| Agent pipeline public | Internal pending/running/retry_wait become `in_progress`; completed/passed/failed retain those names |
| AI review | Carries its own pending/accepted/rejected/superseded semantics; `completed` pipeline does not imply human approval |
| Release-level decision | `GO`, `CONDITIONAL_GO`, `NO_GO`, `NOT_EVALUATED`; distribution projection may add pending-review signaling |
| Authored test lifecycle | `draft`, `review_requested`, `under_review`, `approved`, `active`, `rejected`, `needs_update`, `deprecated`, `archived` |

For exact transition legality, see [workflow state](../../backend/app/services/workflow_run_state.py), [test lifecycle](../../backend/app/services/test_case_lifecycle_service.py) and [Python contracts](../reference/python-contracts.md). A completed-but-degraded pipeline is represented by metadata, not a new public “partial” status.
