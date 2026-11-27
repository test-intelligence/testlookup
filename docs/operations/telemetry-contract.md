# Required telemetry and discovery failures

The monitoring profiles keep an explicit inventory separate from scrape
discovery. The Compose profile loads
`infra/monitoring/prometheus-rules/compose-telemetry-contract.yml` through the
existing rules volume. The Operator profile includes the separate
`k8s/monitoring/telemetry-contract.yaml` PrometheusRule. Both emit
`testlookup:telemetry_expected_role = 1` for each required role.

The default Compose roles are `backend`, `worker` and `worker-children`.
The default Kubernetes roles are `backend`, `worker-critical`,
`worker-ingestion`, `worker-ai`, `worker-default` and `worker-children`.
Beat does not expose a task metrics target. Optional node/PostgreSQL exporters
and Alertmanager are not requirements of the base inventory. Scheduled-work
and independent heartbeat checks cover different questions.

`TestLookupExpectedRoleUnavailable` fires after five minutes without **any
healthy target** for a declared role. It covers both `up = 0` and the complete
absence of `up`, including a deleted or mis-selected PodMonitor. An extra
rolling replica does not change the inventory; one healthy replica prevents a
role-loss alert. Existing down alerts still report individual scrape failures.
This is minimum role coverage, not exact replica-count or consumer-capacity
verification. `TestLookupTelemetryContractMissing` fires after five minutes
when the entire inventory is absent.

Pair role coverage with fresh broker-pending depth and worker progress to
detect a child backlog even when the metrics listener still answers.
[Celery queue monitoring](celery-queue-monitoring.md).

`TestLookupRequiredTelemetryMissing` checks each healthy API target for both
success and last-success series from the unconditional `queues`, `agents`,
`downstream` and `redis` collectors. API and worker targets must also expose
the preinitialized `testlookup_finalize_step_failures_total{step="suite_sync"}`
series as a registry sentinel. Zero values are healthy presence. Missing
series hold for five minutes; existing collection-failure/staleness rules
check values and freshness separately. Lazy task-labelled histograms, pool
metrics before pool initialization, optional collectors, and outbox/queue
values before a successful dependency read are excluded. This baseline does
not prove task execution or completeness of every application metric.

Before applying an Operator site overlay, configure its actual
`ruleSelector`, `ruleNamespaceSelector`, `podMonitorSelector` and
`podMonitorNamespaceSelector` and the permitted scrape ingress. Apply the
site's selection labels to **both** PrometheusRules and both PodMonitors,
using the monitoring kustomization's common labels. No private release,
cluster or receiver labels belong in tracked base files. If the application
namespace changes, patch the inventory rules' `labels.namespace` as well as
resource namespaces. Kustomize's namespace transformer does not rewrite
PromQL sample labels. If local scrape relabeling adds `cluster`, add the same
label to each inventory record. Prometheus external labels are not present
on ordinary local query results. Central/federated evaluation needs an
inventory for every selected cluster and namespace. Configure exactly one
inventory per profile; do not load the Compose inventory into the Operator
installation.

The reference contract assumes each evaluating Prometheus sees all required
application roles. A sharded installation needs central rule evaluation over
all shards, or an explicitly reviewed shard-specific inventory. Loading the
complete inventory into every shard that sees only a subset of targets would
create false role-loss alerts. Prometheus HA replicas that each scrape the
complete target set can evaluate this same contract independently.

If a role is intentionally disabled, remove its inventory entry in the same
reviewed site overlay. Do not infer applicability from the role's observed
targets: that would suppress the failure this inventory detects. New
mandatory collectors require an explicit applicability decision and fixture
update. Partial inventory edits are configuration changes; the missing
inventory alert cannot reconstruct a deleted individual requirement.

For triage, inspect `testlookup:telemetry_expected_role`, compare it with
`up` grouped by `namespace`, `job`, `telemetry_profile` and `telemetry_role`,
then inspect the target's discovery labels and last scrape error. Check
monitor selection, metrics port/path, credentials, dropped metric filters
and network policy before changing an application's workload. Verify the
required sample and current collection timestamp after recovery.

CI executes the semantic fixtures in `infra/monitoring/tests` with the pinned
promtool from the existing backend test job. The fixture covers complete
discovery loss, all targets down, a surviving rollout replica, missing series
on one healthy replica, zero activity, optional absence and recovery. These
are synthetic rule tests. They do not prove actual Operator selection,
network-policy reachability, notification delivery or outage recovery.
Disposable-profile monitor deletion, selector changes, blocked scrapes and
required-series removal remain runtime acceptance steps.

`TestLookupWatchdog` always fires and is routed only when the optional
[alerting profile](alert-routing.md) is configured. Its independently hosted
authenticated receiver must alert on a missing heartbeat. A rule inside
Prometheus cannot report the loss of that same Prometheus through its failed
notification path. The Alertmanager failure rules use actual optional scrape
series and do not make Alertmanager mandatory in the base profile. Receiver
delivery and independent synthetic journeys need separate runtime evidence.

The matching and empty-vector behavior follow the official
[PromQL set operators](https://prometheus.io/docs/prometheus/latest/querying/operators/#logicalset-binary-operators)
and [absent function](https://prometheus.io/docs/prometheus/latest/querying/functions/#absent).
The inventory uses ordinary
[recording rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/).
Target labels and monitor selection use the
[Prometheus Operator API](https://prometheus-operator.dev/docs/api-reference/api/).
