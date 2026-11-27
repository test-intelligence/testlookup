# Operational metric dimensions and migration

The E06 cardinality boundary keeps project/workflow identities in the authorized
SQL-backed APIs and pipeline evidence, and uses finite operational dimensions in
Prometheus. It covers the twelve families in
[the machine-readable budget](../../infra/monitoring/metric-cardinality-budget.json).
Measure the remaining registry and production capacity separately.

## Producer contracts

| Signal | Operator question and boundary |
| --- | --- |
| `testlookup_test_case_transitions_total` | Committed governance event volume, by stored `from`/`to` states and actor role. Ten state values and six role values include `other`. Project identity stays in SQL. |
| `testlookup_test_case_promotions_total`, `testlookup_test_case_deprecations_without_reason_total` | Committed governance event volume, with no application labels. The request owner emits only after commit. Rollback discards staged events. Telemetry failures remain best effort. |
| `testlookup_pipeline_stage_*`, `testlookup_pipeline_fallback_total` | Stage execution, time, tokens, cost, calls and failure/fallback categories by the static agent capability. Stage duration is seconds, cost is USD, and tokens and calls are counts. Authored step identities stay in SQL and events. |
| `testlookup_celery_tasks_total` | Execution outcomes by a frozen deployed task registry, capped at 128 names plus `other`, and eleven known states including `unknown`/`other`. SUCCESS, FAILURE, RETRY, REVOKED and REJECTED retain their meanings. |
| `celery_task_runtime_seconds` | Execution duration in seconds by the same task registry and eight queue families. All numeric `ingestion.shard.N` routing keys map to `ingestion_shard`. |

[The shared inventories](../../backend/app/core/metric_dimensions.py) contain 27
stage values, seven fallback reasons and nine error categories, each including
an overflow value. Unrecognized free text maps to `other`. Truncating or hashing
free text still permits unbounded series. Full runtime step identity and
original fallback/error detail remain in SQL and pipeline events, subject to
their existing privacy and retention boundaries. Queue routing, exact per-shard
queue-depth gauges and the ingestion health response are unchanged.

The task inventory freezes at first observation in each process. Production
workers load their task modules before execution. Late/dynamic registrations
and names beyond the sorted 128-name budget use `other`. Restart after an
intentional task-registry change. The source regression test loads the production
registry and rejects growth above the budget so that increasing it requires an
explicit capacity review. A received routing key or task name cannot extend
the vocabulary. This follows [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/)
to keep dimensions bounded and move detailed high-cardinality analysis to an
appropriate data system.

## Authoritative tenant analytics

Three process-local snapshots are retired:
`testlookup_test_cases_by_state`, `testlookup_automation_cases_orphaned` and
`testlookup_workflow_eval_coverage`. Stored definitions are a growing set, and
deleted IDs remained in these gauges indefinitely. Neither a request-local
snapshot nor an unlabeled last-workflow gauge represents shared current state.

Authored cases and immutable lifecycle snapshots remain in SQL. The existing
orphan endpoint still returns authorized, project-filtered rows and the total,
including zero and pagination, using its two authoritative queries. Workflow
evaluation still
writes coverage/verdict/gate evidence to `WorkflowDefinition` and `AIEvalGateRun`.
[The workflow serializer](../../backend/app/services/workflow_definition_service.py)
continues returning `project_id`, `workflow_id`, `eval_coverage` and
`eval_gate_run_id`. There is no data migration or loss of stored analytics.

## Scope and replica changes

The changed stage and Celery panels in
[the overview dashboard](../../infra/monitoring/grafana/dashboards/testlookup-overview.json)
preserve `installation`, `environment`, `cluster` and `namespace` when present.
Counter increases sum across distinct producer replicas within that scope.
Histogram buckets aggregate before `histogram_quantile`. Replica count changes
alter event volume while the application's dimensions stay bounded. Never sum
replica quantiles or duplicate-scrape one multiprocess producer directory.

The default Compose profile deliberately uses a dedicated local Prometheus and
has no cluster/namespace labels. These queries continue to work for that
single-installation boundary. Missing scope labels remain a separate unlabelled
group. The default legend may show unavailable scope placeholders. The numerical
query remains usable.

Before federation, remote read or scraping multiple installations, operators
must attach a unique stable installation/environment identity to each source,
and cluster/namespace where applicable. Configure scrape relabeling or the site's
Operator selectors/target labels and prove the resulting **sample labels**.
Prometheus `external_labels` alone do not label locally evaluated raw samples.
Align target and application label ownership to avoid conflicts that cause
Prometheus to rename application labels to `exported_*`. The optional
objective profile has a separate application-owned identity contract. Coordinate
with [API objectives](api-objectives.md). Two entirely unlabelled installations
in the same query source cannot be separated retrospectively. Their multi-site
acceptance remains open until source identities are verified.

## Budget and rollout

The conservative sum for the twelve selected families is **21,358 series per
process** if every permitted combination is observed. It includes Counter
`_created` samples and Histogram `+Inf`, count, sum and created samples. Some
multiprocess modes omit created samples. For example, stage duration allows
`27 × 2 × 12 = 648` series, and task runtime allows
`129 × 8 × 15 = 15,480`. These ceilings assume all combinations are observed.
Series appear on actual observations. Applicable roles emit subsets of this
combined budget. Scale by active exporter targets and the union of vocabularies actually
present in a multiprocess directory. One exporter merges identical child labels.

Account separately for scrape labels, concurrent build versions, process/replica
churn, the remainder of the registry and historical TSDB retention. A newly
deployed task inventory may have different names, so its retained union can
exceed one version's 128 names. Feature flag keys, configured model names and
endpoint digests are explicit residual dimensions listed in the budget. Budget
those remaining dimensions separately. Inspect measured
`scrape_samples_post_metric_relabeling`, head series and TSDB growth before
setting capacity or changing retention.

Roll out API and worker producers together. Existing governance metric names
now have fewer labels. Update external queries that filter by `project` and
replace retired snapshot queries with the existing APIs. During overlap, old
processes can still export tenant/custom-step series. Do not use an unfiltered
mixed-version sum as proof of the new schema. To inspect new transition samples
during overlap, `project=""` excludes old nonempty-project labels. Drain old
processes and apply the existing private multiprocess-directory startup reset
before evaluating the current-export budget. The
[Python client's multiprocess contract](https://prometheus.github.io/client_python/multiprocess/)
requires this startup directory lifecycle and a separate collector registry.
Keep historical Prometheus data until its normal retention expiry.

## Verification and remaining deployment acceptance

Source tests exercise 2,000 project identity changes without growing counter
series, 200 workflow/project evaluation identities while retaining API evidence,
unknown stage/category/state/task input, the deployed task cap, custom-step SQL
identity, native multiprocess replacement with cumulative counts, and configured
shard-family normalization. The synthetic PromQL fixture
checks default single-installation data, labelled installation/environment and
namespace isolation, and replica addition/removal:

```powershell
python -m pytest backend/tests/regression/test_operational_metric_dimensions.py backend/tests/services/test_test_management_metrics_service.py backend/tests/services/test_workflow_evaluation_service.py -q
promtool test rules infra/monitoring/tests/metric-scope.yml
```

The fixture is outside the rule-loading directory and runs in the existing
backend CI job. These tests use local/fake storage boundaries and synthetic
time series. Deployment acceptance still requires actual target identity,
exporter deduplication, production series counts, private-directory lifecycle
and API/worker load capacity. Verify those with a known workload in the intended
disposable profile, including create/delete/recreate and a rolling replica change, before
closing the corresponding rollout and capacity acceptance.
