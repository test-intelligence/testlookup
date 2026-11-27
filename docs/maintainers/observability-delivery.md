# Observability delivery program

[Maintainer guide](README.md)

This program extends the existing Prometheus, Grafana and OpenTelemetry foundation. Its scope is eight epics, 43 stories and 129 implementation/verification tasks. Story identifiers remain stable across iterations. A completed code change does not establish production acceptance.

## Delivery and change tracking

Use the single `feat/production-observability` branch for this program. Keep changes in signed commits by iteration; validate locally and push a reviewed batch when it is ready. Do not create a branch per story, dispatch workflows for status checks, or remove required CI checks to save Actions usage. Each iteration updates this page with affected stories, behavior, checks, remaining acceptance and commit history.

Implementation baseline: `bc71694ca8fc91af2ab1fd72819e14ea3e66d540` on `main`. The earlier assessment baseline was `74fdb6b0c1c003c6d70350e8b4fa22a6686e1ac7`. Upstream authentication/MFA changes are included.

Status meanings: **Open** is planned; **In progress** has partial implementation; **Verification** has a candidate implementation with local checks and outstanding acceptance. **Complete** requires the whole story, its dependencies and evidence in the claimed deployment profiles. No epic is complete.

## Epic and story register

| Epic | Capability | Iteration sequence |
|---|---|---|
| OBS-E01 | Safe correlated application telemetry | 1: privacy, errors, worker log context; 3: worker tracing and clients |
| OBS-E02 | Reliable metric collection | 1: bounded reads, queue coverage and SQL outcomes; 2: producers, exporters and access |
| OBS-E03 | Alerting and scheduled detection | 1: low-volume webhook and new outcome rules; 3: schedules, discovery and routing |
| OBS-E04 | Durable log and trace platform | 2: profile contract; 3: collection, storage, offline and BYO profiles |
| OBS-E05 | Durable operational and audit evidence | 2: event outbox/replay; 3: audit archive and retention |
| OBS-E06 | Objectives and capacity | 2: SLIs and series budget; 4: measured objectives, burn rates and capacity |
| OBS-E07 | DevSecOps and operational rollout gates | 1: scan authorization and semantic rule tests; 3-4: full signal, security and rollout |
| OBS-E08 | Safe ingestion and worker lifecycle | 1: bounded publication, retention and readiness reset; 2: durable reconciliation |

| Story | Outcome | Status |
|---|---|---|
| OBS-E01-S01 | Redact every exported span surface | Verification |
| OBS-E01-S02 | Initialize tracing in every worker and scheduler role | Open |
| OBS-E01-S03 | Propagate task traces and reset task log context | In progress |
| OBS-E01-S04 | Transmit bounded browser telemetry using actual UTF-8 bytes | Verification |
| OBS-E01-S05 | Preserve backend correlation IDs in MCP and CLI errors | Open |
| OBS-E01-S06 | Instrument MCP operations without corrupting stdio | Open |
| OBS-E01-S07 | Preserve correlation and sanitized logs for every unexpected API error | Verification |
| OBS-E02-S01 | Bound dependency collection and expose freshness | Verification |
| OBS-E02-S02 | Monitor every routed queue including agent children | In progress |
| OBS-E02-S03 | Emit real SQLAlchemy pool saturation measurements | Verification |
| OBS-E02-S04 | Wire dependency and host exporters by deployment profile | In progress |
| OBS-E02-S05 | Make authenticated scrapes work in reference profiles | Verification |
| OBS-E02-S06 | Detect durable downstream failure independently of Celery success | In progress |
| OBS-E03-S01 | Make weekly digest alerts reflect actual due work and delivery | Verification |
| OBS-E03-S02 | Detect low-volume terminal webhook failures correctly | Verification |
| OBS-E03-S03 | Deliver routed firing and resolved notifications | Verification |
| OBS-E03-S04 | Observe durable expected-run and last-success state | Verification |
| OBS-E03-S05 | Detect missing targets and required telemetry | Verification |
| OBS-E03-S06 | Detect monitoring and notification path outages independently | In progress |
| OBS-E04-S01 | Define the production telemetry and ownership contract | Open |
| OBS-E04-S02 | Deploy bounded and secure log/trace collection | Open |
| OBS-E04-S03 | Provide central log search and working trace-to-log navigation | Open |
| OBS-E04-S04 | Persist incident traces with explicit small-install and HA options | Open |
| OBS-E04-S05 | Adapt and verify Kubernetes, K3s and OpenShift monitoring | Open |
| OBS-E04-S06 | Package offline telemetry and document cloud/BYO adapters | Open |
| OBS-E05-S01 | Persist failed pipeline event delivery with stable IDs | Open |
| OBS-E05-S02 | Replay events idempotently and expose fleet health | Open |
| OBS-E05-S03 | Protect operational audit history and archive integrity | Open |
| OBS-E05-S04 | Retain security events without deleting SCIM authority evidence | Open |
| OBS-E06-S01 | Define and emit durable journey success indicators | Open |
| OBS-E06-S02 | Add scoped error budgets, burn alerts and runbooks | Open |
| OBS-E06-S03 | Bound metric cardinality and installation scope | Open |
| OBS-E06-S04 | Measure storage, sampling and resource limits | Open |
| OBS-E07-S01 | Run semantic telemetry and mounted-configuration checks in CI | In progress |
| OBS-E07-S02 | Verify the full signal path against a disposable stack | Open |
| OBS-E07-S03 | Add useful application source security analysis | Open |
| OBS-E07-S04 | Validate infrastructure security policy in CI | Open |
| OBS-E07-S05 | Run authenticated dynamic security checks in isolation | Open |
| OBS-E07-S06 | Verify every deployed role and rehearse production handoff | Open |
| OBS-E07-S07 | Require same-SHA secret-scan success before publication and promotion | Verification |
| OBS-E08-S01 | Keep upload publication off the API event loop | Verification |
| OBS-E08-S02 | Preserve queued batch payloads after ambiguous publication | In progress |
| OBS-E08-S03 | Clear stale worker readiness before replacement startup | In progress |

## Iteration 1: critical correctness and signal foundations

Candidate changes on the shared branch:

| Stories | Change | Remaining acceptance |
|---|---|---|
| OBS-E01-S01 | Every configured trace exporter receives sanitized copies of attributes, events, exception/status text, names, resources, links and scope. Encoded JSON credentials are scrubbed; oversized text is omitted; unsafe spans fail closed. | Review candidate image sinks and configuration before expanding production tracing. |
| OBS-E01-S03 | Worker task hooks bind validated request/correlation headers and canonical task identity, clear every task outcome and preserve caller context during eager execution. | Full producer W3C propagation and prefork process lifecycle evidence remain open. |
| OBS-E01-S04 | Browser error batching uses serialized UTF-8 bytes and bounds messages, stacks, component data, URLs and user agents. | Browser transport acceptance with the candidate build. |
| OBS-E01-S07 | Unexpected core API errors emit one correlated error log and return X-Request-ID while preserving response bodies; pre-routing shared-link paths are sanitized. | Candidate HTTP smoke, with production log access policy. |
| OBS-E02-S01 | Concurrent collectors have finite foreground deadlines and one in-flight read per collector; failed/stale reads expose success, error and freshness signals. | Real dependency outage, cancellation and multiprocess fleet checks. |
| OBS-E02-S02 | Redis queue-depth collection includes agent_children and concurrent reads. | Authoritative routing inventory and queue age/throughput coverage. |
| OBS-E02-S06 | SQL outbox counts, age and status-specific overdue time expose business failures independently of Redis/Celery status. Unknown operation names use a bounded other bucket. Invalid lease state fails collection. | Real PostgreSQL/fleet evidence, historic-table query cost and authorized investigation/replay. |
| OBS-E03-S02 | Webhook ratio uses its actual nonzero attempt rate; namespace/cluster grouping preserves low-volume failures. | Receiver delivery and profile routing are separate stories. |
| OBS-E07-S01 | Semantic Prometheus fixtures cover changed rules; Kubernetes rules are generated from the same source. | Broader configuration, full-signal and CI acceptance remain open. |
| OBS-E07-S07 | Publication and reusable release authorization require successful trusted same-SHA main-push secret-scan evidence, with bounded waiting and failed/missing evidence denied. Main pushes also scan every tracked candidate blob, independently of added history. | Hosted Actions permission/event validation. |
| OBS-E08-S01/S02 | Upload publishing leaves the API event loop through a finite thread pool. Timeouts/cancellation retain capacity until the real publisher ends; ambiguous publication retains payload and polling identity, exposed in documented CORS-readable response headers. | Durable initial-upload reconciliation, orphan cleanup and HTTP retry idempotency remain open. Thread timeouts cannot terminate a running broker call. |
| OBS-E08-S03 | Readiness sentinel is removed at worker startup and shutdown so reused volumes cannot inherit a ready marker after a crash. | Container/prefork crash-and-restart acceptance. |

The new collection and outbox alerts retain target freshness and use maximum values for replicated shared SQL state instead of adding duplicate observations. Future retry deadlines do not alert as overdue. Completed rows are not overdue.

### Verification record

- Focused backend behavioral suites: 371 passed, covering upload admission, accepted-then-error retention, event-loop responsiveness, saturation, cancellation, authorization, worker context/readiness, core errors and metric failure/recovery.
- Trace serialization was exercised with Console JSON and OTLP encoding on Python 3.14.10 / OpenTelemetry SDK 1.45.2 in an isolated temporary process: 68 passed; one Kubernetes-manifest lookup was skipped because the temporary test tree contains no manifests. No live application code or configuration was replaced.
- Frontend error-reporting suite: 18 passed; TypeScript, production build and bundle budget checks passed. Full browser journeys and production deployment acceptance remain open.
- Release authorization uses mocked GitHub evidence tests; no workflow was dispatched or publication performed for this validation.
- Upload publication failure tests also passed in an isolated Python 3.14.10 / Celery 5.7.0 process: 14 passed against fake broker, storage and authorization boundaries. No live uploads were submitted.

The complete candidate-tree scan reads raw Git blobs in bounded batches and verifies their inventory, type, size and hashes before scanning a temporary snapshot. It scans tracked symlink targets as text and rejects unsupported gitlinks. Export attributes, checkout encodings and filters do not change the scanned bytes. Working-tree secrets and untracked files do not enter the snapshot. Existing synthetic credential fixtures use narrow, explained line annotations or exact path/value/detector exceptions; the former broad SDK-vector directory exemption is removed. Regression fixtures check that other credentials in those files still fail.

### Running semantic alert verification

The existing `backend-test` CI job downloads official Prometheus 3.16.0, verifies the reviewed archive SHA-256 before extraction, and runs the source rules and semantic fixtures. It adds no job or matrix. The version matches the reference Compose monitoring profile; update the version and reviewed official checksum together when that profile changes.

With the matching `promtool` installed, run from the repository root:

```sh
cd infra/monitoring/tests
promtool check rules ../prometheus-rules/testlookup-alerts.yml
promtool test rules operational-alerts.yml
```

Fixtures live in `infra/monitoring/tests/`, outside the production `rules/*.yml` mount. They exercise collection failure/freshness and recovery, same-target freshness filtering before shared-SQL `max`, replica/installation separation, retry deadlines and nonterminal states, strict thresholds, quiet/absent webhook traffic, low-volume failure, and recovery. They evaluate synthetic samples and send no notifications. This does not replace target discovery, collector integration or receiver-delivery acceptance. See [Prometheus rule testing](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/) and the [official 3.16.0 release](https://github.com/prometheus/prometheus/releases/tag/v3.16.0).

### Next iteration dependencies

1. Establish deployment-profile collection, selector, receiver, authentication and retention contracts. Reuse an existing monitoring stack where it meets the contract.
2. Wire missing dependency/pool instruments and scrape authentication; connect all required application targets/rules and exercise an authorized test receiver.
3. Add durable event/replay evidence and scheduled due-state detection. Reconcile retained initial uploads without duplicate publication.
4. Implement persistent logs/traces and fork-safe tracing, then measure journey SLIs, label budgets, capacity and burn-rate rules.
5. Complete all-role signal/security/rollback gates before declaring production readiness.

No production deployment or claim of enterprise readiness is represented by this iteration. Data retention, receivers, SLO targets and platform ownership must be explicit for each supported production profile.

## Iteration 2: reliable metric collection

This iteration builds on `30edfba5` and advances four stories in OBS-E02.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E02-S02 | The API scrape and ingest health use the Celery routing inventory and the configured broker connection. Counts sum all Redis transport priority lists for each logical queue. A failed list produces an unknown depth. | Measure queue age and processing throughput. Verify the backlog and recovery with a stopped child consumer in a disposable fleet. |
| OBS-E02-S03 | API and worker engines emit checked-out connections, pool capacity including overflow, in-flight acquisitions and acquisition outcomes. The alert and dashboard surface the most saturated live process. The Celery parent cleans up live gauges left by hard child exits. See [database pool metrics](../operations/db-pool-metrics.md). | Verify exact fleet counts and pressure against PostgreSQL in a deployment using the candidate image. |
| OBS-E02-S04 | The bounded API collector reads application and broker memory through their actual Redis/Valkey clients. Alerts and dashboards use fresh observations, distinct roles and a separate unlimited-memory signal. | Verify host and datastore exporters for each production profile and provider. See [dependency metrics](../operations/dependency-metrics.md). |
| OBS-E02-S05 | An optional Compose overlay mounts separate backend and Prometheus credential files. The Kubernetes component and PodMonitor use a namespace-local Secret. Rejected requests bypass dependency collection. | Exercise rotation and recovery in a deployed reference profile with the production selector, secret manager and network policy. See [Compose](../operations/deploy-compose.md#authenticate-the-api-scrape) and [Kubernetes](../operations/deploy-kubernetes.md#authenticate-the-api-scrape). |

Prometheus 3.16 scraped actual samples from an isolated candidate API, received 401 on a token mismatch and resumed scraping after rotation. The source and release Compose configurations and the K3s and OpenShift manifests rendered with the expected targets and mounts. The existing backend CI job checks the two mounted Prometheus configurations and runs all semantic rule fixtures using its checksum-verified binary.

The focused suite passed 354 tests with the repository-pinned metrics dependencies. Two PostgreSQL cases require a disposable database and run in the existing integration job. Promtool validated 39 rules and 19 semantic fixture groups. In a separate Linux process test, removing the gauge from a killed child preserved the live child's gauge and both counters.

## Iteration 3: alerting and scheduled detection

This iteration builds on `a84edf88`. It extends the existing profile monitoring and alert evaluation without adding a CI job.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E03-S01 | The digest claim records its unresolved slot, and the worker tracks generation, skipped work, provider success and ambiguous completion separately. The weekly alerts cover real subscription due dates and ignore inactive or paused subscriptions. | Apply migration 0262 and demonstrate failure, overdue detection and delivery recovery using isolated subscriptions. |
| OBS-E03-S03 | The optional Compose Alertmanager profile uses pinned images, private mounts, and explicitly configured HTTPS incident and watchdog receivers. An existing Kubernetes Alertmanager can reuse the contract. | Verify deployed mounts and receiver routing, finish rule ownership and runbook links, and perform an on-call handoff. See [alert routing](../operations/alert-routing.md). |
| OBS-E03-S04 | Beat stamps the UTC slot into nightly messages. Workers persist attempts and successful outcomes in a shared SQL table. Rules alert on missed enabled deadlines across worker replacements. | Apply migration 0261 and exercise missed dispatch and recovery in a deployment. See [scheduled maintenance](../operations/scheduled-maintenance.md). |
| OBS-E03-S05 | Compose and Kubernetes declare expected scrape roles independently of discovery. Rules detect missing roles and missing required series while accepting a surviving healthy rollout replica. | Confirm the site's Operator selectors, apply its inventory, and demonstrate loss and recovery in a disposable deployment. See [telemetry contract](../operations/telemetry-contract.md). |
| OBS-E03-S06 | The watchdog has a dedicated receiver route. Optional Alertmanager metrics expose delivery and notification path failures. | Configure an external service to page when the heartbeat stops, and verify it during Prometheus or Alertmanager outages. |

Prometheus 3.16 and Alertmanager 0.35.1 delivered firing and resolved payloads to a disposable TLS receiver. The test exercised grouped replicas, deduplication, silence, inhibition, watchdog cadence and provider retry. The rule fixtures cover missed schedules, receiver availability, disappearing roles and required series. Both migrations were rendered forward and backward for PostgreSQL. The disposable PostgreSQL concurrency case is part of the existing integration job.

The focused backend suite passed 406 tests and skipped the case that requires a disposable PostgreSQL instance. All 40 Prometheus semantic scenarios passed. Compose and Operator manifests were rendered, and no production receivers were contacted.

## Iteration 4: durable log and trace collection

This iteration builds on `be88915c`. The app keeps its normal stdout logs and Prometheus scrapes. An opt-in profile exports selected operational records and API traces.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E04-S01 | The [profile and ownership contract](../operations/durable-telemetry.md) defines installation boundaries, retention, capacity and fault drills across Compose and managed deployments. | Assign site owners and validate retention, storage sizing, residency and receiver routing with the deployment team. |
| OBS-E04-S02 | Collector ingress accepts the TestLookup service, rebuilds bounded log records and stores two finite, persistent exporter queues. Prometheus observes queue pressure and refusals. | Measure the actual full-stack failure window, pre-collector loss and capacity under site traffic. |
| OBS-E04-S03 | Persistent Loki indexes three resource labels. API, Celery and MCP use a structured projection with hashed request or task IDs. Grafana queries Loki and can open a known Jaeger trace. | Run an authorized access drill and measure retention expiry, disk use and incident readback in the deployed profile. |
| OBS-E04-S04 | Jaeger 2.23 uses synchronous Badger storage with a 72-hour TTL. Grafana can read traces by ID through the v3 API, and native Jaeger handles service and operation search. | Validate filesystem sizing, private UI access and the chosen HA storage path at each site. |
| OBS-E04-S05 | Kubernetes, K3s and OpenShift render optional mTLS client adapters for a site-operated gateway. | Verify workload admission, certificate rotation, monitoring selection and persistent readback in disposable platform-matched clusters. |
| OBS-E04-S06 | The image manifest and third-party scan track every durable reference image. The optional offline bundle records exact pins and aliases for all components and lists Grafana and Loki as operator-supplied prerequisites. | Exercise image import and the full stack with outbound internet blocked. |

Disposable native runs covered queue overflow, retry exhaustion and recovery after collector restart, plus forced Loki and Jaeger restarts. Grafana returned matching trace and log IDs using the pinned datasource versions. The Kubernetes adapters were rendered locally. The Compose stack's runtime networking, actual site gateways and disconnected operation still need deployment-level evidence.

## Iteration 5: durable operational and audit evidence

This iteration builds on `25fcc0c9`. Pipeline event health comes from shared SQL state, and the timeline includes accepted events while MongoDB delivery is pending.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E05-S01 | Migration 0263 adds a bounded project-scoped outbox. Committed stage, review, action and agent ledger changes can stage their audit intent in the source transaction. Rollback removes uncommitted intent, and the schema downgrade protects unresolved events. | Measure admission contention, throughput and full-queue behavior with the site's normal traffic and retention policy. |
| OBS-E05-S02 | A leased Celery relay materializes each stable ID once in MongoDB. Shared health exposes safe failed references and QA leads can request a project-scoped retry. Alerts cover backlog, capacity, failed delivery and admission refusal. History exports require delivered events in the selected pipelines. | Verify receipt recovery and notification delivery in a deployed fleet under controlled failure. See [pipeline event delivery](../operations/pipeline-event-delivery.md). |
| OBS-E05-S03 | An operator custody command seals and verifies archives with an independent key. The optional S3 publisher uses a conditional create and checks Object Lock, hold, encryption and checksum receipts. | Provision independent IAM and storage, test denied mutation and hold or deletion actions, and agree which source exports meet site evidence requirements. See [audit archive custody](../operations/audit-archive-custody.md). |
| OBS-E05-S04 | An admin-approved identity policy expires selected routine, successful events in bounded batches. Instance holds pause cleanup and SCIM authority rows remain available for supported authorization paths. | Confirm site defaults, approvals, window and retention behavior with the identity owner. See [identity event retention](../operations/identity-event-retention.md). |

Disposable PostgreSQL and MongoDB tests exercised competing producers and claims, the purge fence, and a retry following a lost receipt. The same event remained a single MongoDB document. Migration 0263 was applied and rolled back in a disposable schema. A downgrade with pending evidence was refused. A PostgreSQL identity test observed hold and purge transactions from independent sessions and verified the resulting audit receipts. Archive tests used synthetic records and provider fixtures.

## Iteration 6 — service objectives and telemetry capacity

This iteration builds on `58fe721e`. It introduces measurements and proposed objectives that installation owners can review before enabling alerts.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E06-S01 | Six core GET operations emit opt-in completion and latency counters. A bounded SQL collector includes late, failed, degraded and never-completed work in retained downstream, live materialization and AI cohorts. Migration 0264 indexes the acceptance timestamp on each source table. [Journey measurement contracts](../operations/journey-slis.md). | Run authenticated synthetic journeys through the ingress and measure database cost at retention scale. Complete durable denominators for upload acceptance, raw live events, scheduled history and verified restores require source evidence. |
| OBS-E06-S02 | An optional Compose and Operator profile records scoped error budgets, evaluates multiwindow burn and distinguishes quiet traffic from incomplete data. Adoption requires a declared owner and scope. [API objectives](../operations/api-objectives.md). | Approve site targets and routing, verify rule cost and full-window coverage, and exercise operational firing and resolution. |
| OBS-E06-S03 | Project and workflow detail remains in the application stores. Operational metrics use bounded case lifecycle, workflow stage, task and queue labels. Scoped queries preserve installation and deployment identity. [Metric cardinality](../operations/metric-cardinality.md). | Attach stable scrape identity before combining installations, update site dashboards that read retired families and measure rollout series churn. |
| OBS-E06-S04 | A disposable workload measures Collector admission and delivery, store readback, Prometheus head series, CPU, RSS and logical bytes. Compose supports an explicit parent-based trace sampling ratio. [Telemetry capacity](../operations/telemetry-capacity.md). | Run a sustained application workload with platform quotas, realistic report sizes and retention compaction. Verify disk, query and alert behavior at site traffic. |

The native reference stack delivered 4,096 logs with each sample policy. The 10% trace profile used 56% fewer OTLP bytes. Full destination outages filled both 32 MiB queues, returned bounded refusals and incremented per-signal failure counters. The disposable PostgreSQL workload verified a stable retry denominator, late recovery, and indexed plans against 20,000 older rows per source. Migration 0264 upgraded, repeated and downgraded in the disposable schema. Prometheus fixtures exercised role turnover, installation isolation, quiet and missing data, burn recovery and the 30-day coverage boundary. Source and release Compose configurations rendered with objectives, authenticated scrapes, alerting and durable telemetry together.

## Iteration 7 — delivery security and rollout evidence

This iteration builds on `849323c9` and uses the existing backend and release verification workflows.

| Story | Change | Remaining acceptance |
|---|---|---|
| OBS-E07-S01 | The backend job checks the complete mounted Prometheus inventory alongside the native rule engine. Fixtures reject missing rule files, missing groups and invalid expressions. | Observe the hosted check on the new candidate. |
| OBS-E07-S02 | The disposable signal gate uses registered queue-delivery receipts, exact counter deltas, durable downstream completion and correlated API and worker telemetry. The monitored profile binds its evidence to immutable images and explicit installation identity. [Release signal gate](../operations/release-signal-gate.md). | Run the complete gate against verified candidate images, then confirm firing and resolved delivery through the site's authorized receiver. |
| OBS-E07-S03 | The hosted repository already runs CodeQL for Python and JavaScript/TypeScript. The existing required backend job adds a pinned, SHA-bound source policy for TLS verification and safe YAML loading. | Review any separately proposed repository rule for CodeQL alert severity. [Security control acceptance](../operations/security-control-acceptance.md). |
| OBS-E07-S04 | The rendered infrastructure gate exercises 27 reference profiles. It rejects seeded operational exposure and privilege violations in every render. [Infrastructure policy](../operations/infrastructure-policy.md). | Verify site overlays, admission policy, actual network enforcement and any provider-specific policy. |
| OBS-E07-S05 | The isolated release campaign checks authenticated identity, benign viewer denial, authorized access and browser response policy. Its output includes bounded control results tied to the candidate. | Exercise the case against a verified deployed candidate. |
| OBS-E07-S06 | The development workflow pins all three app images, observes every application deployment and resolves runtime image identity from the candidate's OCI descriptors. K3s also embeds build provenance in its MCP image. [Release assurance](../operations/release-assurance.md). | Rehearse candidate rollout, scheduled work, alert delivery, privacy, capacity, rollback and isolated restore in the selected deployment. |
| OBS-E07-S07 | The published main SHA passed the existing independent secret-scan verifier. Active main rules require the Gitleaks check, and local fixtures cover absent, pending, failed and unrelated evidence. | Verify the hosted check and release authorization on the new candidate. |

Two native workers consumed the existing delivery probe through a disposable Valkey broker. With the locked Celery and OpenTelemetry versions, both emitted a span with the supplied parent and an operational log with the matching trace, span and hashed task ID. Native Prometheus and Alertmanager delivered firing and resolved notifications to a separate disposable TLS receiver. The homelab role inventory was read-only.
