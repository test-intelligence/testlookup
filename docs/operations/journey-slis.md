# Journey measurement contracts

These measurements describe observed response and durable business state. Their thresholds are **proposed**, pending owner adoption after workload and deployment review. Enterprise availability commitments require that review. Existing Instrumentator HTTP histograms, performance diagnostics, outbox state/lease alerts, nightly observations and digest delivery alerts remain independently useful.

Set an explicit `OBSERVABILITY_INSTALLATION` on the API before enabling objective inputs. It accepts 1–63 ASCII letters, digits, dots, underscores or hyphens and must start with a letter or digit. An empty value disables all new journey inputs and avoids a database read for this collector. The process never infers a site from a hostname, URL, project or task. Every new series carries that installation and the validated `APP_ENV` as `environment`. Configure one stable value per independently operated installation. Changing it starts a new measurement scope and budget history.

Scrape target labels must also distinguish the actual cluster/namespace. Keep installations, environments and worker/API target populations separate. Preserve application scope labels and reject conflicts. Treat absent/unconfigured inputs as unknown. A measured zero denominator means no eligible activity. The [Google SRE SLO workbook](https://sre.google/workbook/implementing-slos/) recommends defining eligible events and good events around the service users receive. The contracts below identify the available evidence and each remaining complete-journey gap.

## Core API response completion

Owner: backend/SRE. Applicable scope: API processes with metrics enabled and explicit installation identity. `JourneySLIMiddleware` is installed around application middleware and handlers, beneath Starlette's outer server-error machinery. It neither changes responses nor replaces standard HTTP diagnostics.

`testlookup_api_journey_events_total{installation,environment,objective,outcome}` has exactly two objective names, `api_availability` and `api_latency`, and two outcomes, `good` and `bad`. Both outcomes start at zero for each configured target. `testlookup_api_sli_configured{installation,environment,objective} = 1` establishes that initialization. Owner adoption is a separate operator decision.

Only these existing GET operation templates are eligible:

| Operation | Template | Proposed latency threshold |
|---|---|---:|
| Project list | `/api/v1/projects` | 200 ms |
| Run list | `/api/v1/runs` | 250 ms |
| Run detail | `/api/v1/runs/{run_id}` | 250 ms |
| Run intelligence | `/api/v1/runs/{run_id}/intelligence` | 800 ms |
| Keyword search | `/api/v1/search` | 300 ms |
| Flaky coach | `/api/v1/projects/{project_id}/flaky-coach` | 600 ms |

The thresholds come directly from `LATENCY_BUDGETS`. They are existing p95 diagnostic budgets. A proposed latency objective of 95% within these thresholds is consistent with that basis. A different adopted fraction needs a measured baseline and owner decision. The aggregate is weighted by the traffic mix of these six operations and can hide an individual operation's poor latency. Keep per-operation HTTP diagnostics alongside it. Freeze or deliberately version threshold policy when adopting an objective so a mixed-version rollout remains distinguishable.

Eligibility includes completed 2xx/3xx responses, server quota/backpressure 429, server errors and unhandled/cancelled eligible handlers. Ordinary 4xx client/auth/scope/validation refusals are excluded. A resolved route supplies the template. Before the router runs, an internal matcher recognizes only the same six fixed method/path patterns so an early guard's 429/500 can be observed. It follows Starlette's `root_path` boundary handling for mounted applications and reverse proxies. Metric labels contain only the bounded objective/outcome and configured scope. Guards outside this middleware remain outside its coverage. These are supported core-route **attempts**. An early server error can precede successful authentication.

Availability is good for a fully sent 2xx/3xx response and bad otherwise. Latency is good only when availability is good and the final response body is sent within the operation's threshold. A failed response is bad for both objectives. Each eligible attempt contributes once per objective. Post-response background work preserves the already measured response outcome. Asynchronous uploads, external notifications and AI results require their own completion evidence.

These process counters reset with a fresh process metric epoch. Sum counter rates across actual API processes before computing a ratio. Requests that never finish or raise remain censored until they terminate. A process killed before recording its outcome, scrape loss around a restart, a proxy rejecting a request before the app, and client-to-proxy network failures are outside this denominator. Independent synthetic journeys, availability checks and a durable accepted-request ledger remain required for complete accepted-to-completed availability.

## Matured SQL-intent cohorts

Owner: backend/AI platform. Applicable scope: retained SQL intents observed by an authorized API scrape. The `journeys` bounded collector reads each of its two tables once in a read-only PostgreSQL repeatable-read snapshot. A 750 ms per-statement limit and the existing one-second foreground collection deadline bound the attempt. It publishes only after both aggregate queries succeed. Query failure preserves the previous observation time.

Each serving API child encodes the entire cohort, source-support indicators and observation timestamp into one immutable Prometheus bundle. The authorized scrape appends this bundle only after successful collection. This keeps one child's changing outcomes together when Gunicorn uses multiple workers. The three cohort metric families remain outside native multiprocess Gauge aggregation.

A 30-second local cache reuses a complete bundle for the same installation, environment and AI deadline. Explicit `now` arguments bypass this cache for deterministic tests. A cache hit preserves the database observation timestamp. The collector's successful cached response measures scrape availability. Consumers must also check the actual cohort timestamp against the 120-second freshness contract. The reference 15-second scrape interval fits this contract. Review scrape cadences above 60 seconds, missed-scrape tolerance and the freshness threshold together.

Migration `0264` creates concurrent `created_at` indexes for both source tables with matching ORM definitions. It safely retries an invalid concurrent build and drops only these indexes on downgrade. There is no row backfill. The indexes, two aggregate reads and cache reduce repeated scan work. Production acceptance still requires measuring retained 30-day row counts, query CPU/latency and replica count under the chosen scrape cadence. Statement timeouts provide a bound rather than a capacity result.

`testlookup_journey_cohort_events{installation,environment,journey,window,outcome}` is an **absolute gauge**, not a counter. The bounded windows are `1h`, `6h`, `24h` and `30d`. For observation time `t`, deadline `d` and window `w`, the acceptance interval is `(t-d-w, t-d]`. Work younger than its deadline is not yet eligible. Every retained intent in this matured cohort contributes once, including an intent that never started or completed. A terminal failure enters the cohort when its deadline matures, rather than artificially advancing the failure's acceptance time.

| Journey | Durable acceptance grain | Good completion evidence | Proposed deadline |
|---|---|---|---|
| `downstream` | One committed `run_downstream_outbox` row, uniquely keyed by run/operation/input_version | Supported operation with explicit `completed` status and valid `completed_at` | 4200 s: existing finalization recovery grace plus one execution lease |
| `live_materialization` | The subset whose operation is `persist_live_session` | Explicit completion of that persisted intent | Same 4200 s reference bound |
| `ai_completion` | One committed `agent_pipeline_runs` identity | `completed` or `passed` with valid `completed_at`; reported degradation is allowed | Current positive `AI_PIPELINE_DEADLINE_SECONDS`, default 1500 s |
| `ai_reported_full_quality` | The same pipeline identity, with a stricter numerator | Timely completion with explicit `fallback_used = false` and no degraded/unknown stage-quality marker | Same configured reference bound |

The downstream bound provides a conservative diagnostic reference. Workflow-specific product deadlines require separate adoption. The AI bound uses the current configured reference criterion. Historical workflows can have different frozen execution deadlines. A zero/nonpositive AI bound makes both AI source-support indicators zero and omits their cohort samples. The independently valid downstream cohorts remain available.

The six disjoint outcomes are:

| Outcome | Meaning |
|---|---|
| `good` | Positive durable completion within the reference deadline; full-quality journey additionally requires its explicit quality evidence. |
| `late` | Valid durable completion after that deadline. Recovery changes overdue to late, preserving a bad timely-completion denominator. |
| `failed` | Explicit durable terminal failure. |
| `overdue` | Mature work still waiting/pending/sending/published/processing, or AI pending/running/retry_wait. |
| `degraded` | Timely completed AI work lacks explicit full-quality evidence or reports fallback/degradation; used only by the stricter quality journey. |
| `unknown` | Unknown operation/status, missing successful completion timestamp, or completion timestamp before acceptance or after observation. |

Outbox states retain their actual meanings. `waiting` awaits finalization/recovery. `pending` awaits dispatch. `sending` holds a publication lease. `published` has been sent to the broker. `processing` holds an execution lease. `completed` is explicit tracked-operation completion. `failed` exhausted or explicitly failed its durable lifecycle. A Celery `SUCCESS`, deferred publication, empty broker queue or incremented retry counter provides task/dispatch diagnostics only. Dispatch/execution retries retain the same intent denominator. An operation can validly finish with no configured receiver. Actual notification delivery needs its own delivery receipt.

Select one freshest valid API target for each installation/environment/cluster/namespace tuple. Join that same target identity to **all** outcomes and support indicators before optionally removing replica labels. Equal observation timestamps may choose either single target. Independently taking the maximum of each outcome can combine an old overdue count with a new late count and invent a denominator. Summing replicas has the same duplication risk. These absolute gauges support current-state ratios, with counter operations reserved for actual counters.

A reader or API restart rederives the same SQL state without a local counting checkpoint. Require fresh `testlookup_journey_cohort_observed_at_seconds` **and** successful/fresh `testlookup_metric_collection_*{collector="journeys"}` before displaying any ratio. `testlookup_journey_source_supported` states source capability. A current successful read is established separately by observation freshness.

These are current retained cohorts. Authorized retention and run cascades remove rows. Same-ID AI resumes can clear/rewrite completion and quality. The current deadline criterion can change. An older never-completed intent eventually leaves the rolling horizon while remaining unresolved. Existing all-retained-state outbox failure/overdue alerts continue to expose that old work. An immutable acceptance/outcome ledger and declared retention/measurement epoch remain required for historical business burn rates and a complete long-term SLO.

## Explicit unsupported boundaries

`testlookup_journey_source_supported{installation,environment,journey}` is zero for the following complete journeys. Their contracts and corrective boundaries are explicit. Missing sources remain visible as unsupported measurements.

| Journey | Available evidence and gap | Required completion contract / owner |
|---|---|---|
| `api_durable` | Process response counters lack durable accepted requests and never-returning outcomes. | Durable/independent attempted-request accounting, declared request timeout, and customer-facing response deadline; backend/SRE. |
| `upload_acceptance` | JSON/file 202 precedes durable normalized TestRun. Advisory Redis status expires after 24 h and has no acceptance/completion timestamps; task SUCCESS cannot repair that denominator. | One durable accepted upload identity/receipt and terminal persisted usable result, with measured size/workflow deadlines; backend. |
| `live_event_acceptance` | Redis stream acceptance/checkpoints and replay protocols exist. SQL attempt/event receipts begin at projection, so counting them alone is success-biased. | Complete accepted event/batch denominator plus durable projection and visibility time, replay deduplication and explicit freshness/recovery bounds; backend. |
| `scheduled_history` | Nightly slot observations and digest delivery state detect current due/never-delivered work, but retain latest observations rather than every expected slot. | Immutable enabled expected-slot history, deduplicated generation versus confirmed delivery, existing nightly 1 h / weekly-dispatch 25 h grace respected, and owner-adopted schedule objectives; backend/SRE. |
| `backup_verified_restore` | CronJob success is process completion, not proof of a restorable archive or a successful restore. | Durable expected backup/archive verification receipts, declared RPO/RTO and periodic successful isolated restore of all required stores/artifacts; SRE. |

This stage adds indexes and derives existing records. Complete accepted-upload/live-event, schedule-history and verified-restore objectives remain open until their durable boundaries and safe lifecycle changes have been implemented and tested.

## Known-workload verification

From `backend`:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q -p pytest_asyncio.plugin tests/test_journey_sli.py
```

The fixture verifies actual ASGI completion/error behavior, early fixed-template guards, final-body timing, excluded refusals, disabled/invalid installation scope, all seven outbox states, retry deduplication, good/failed/degraded/late/never-completed work, exclusive cohort boundaries, late recovery, repeated independent reads and empty-source zeros. No worker, provider or live application is exercised by this unit fixture.

`tests/integration/test_journey_sli_postgres.py` requires an explicitly configured disposable `TESTLOOKUP_POSTGRES_TEST_DSN` and is wired into the existing CI PostgreSQL command. It creates isolated `LIKE` copies, inserts only synthetic intents/pipelines, executes the actual read-only collector, compares fresh readers and retry/recovery outcomes, and drops its schema. It also verifies migration upgrade/retry/downgrade, ORM index parity and selective query plans with 20,000 older rows in each table. Run it only against a disposable fixture database. Native PostgreSQL 16.15 verification uses synthetic ORM-DDL source tables without account foreign keys. That proof covers SQL derivation, transactions and index eligibility. Full migrated account-schema behavior and production load capacity need separate acceptance evidence.
