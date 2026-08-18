# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### 2026-08-17 — Fix: feature-flag toggles agree across replicas

The live deployment runs multiple backend and worker processes. Each process
kept its own 30-second feature-flag cache, so deleting the shared Redis entry
after a committed toggle did not evict stale values held by other replicas.

Redis is now the single shared cache tier for feature-flag decisions. Every
gate reads the shared cache before falling back to Postgres, making committed
invalidation immediately visible across API replicas and prefork workers.
Regression coverage prevents a process-local feature-flag cache from returning.

### 2026-08-17 — Fix: feature-flag caches invalidate after commit

Feature-flag create, update, and delete operations invalidated the in-process
and Redis caches inside the service transaction. Because the database commit
occurred only after the handler returned, a concurrent reader could repopulate
both caches from the old committed row and keep serving the stale gate for the
30-second TTL.

Feature-flag mutation services now only stage database and audit writes. Their
admin CRUD handlers explicitly commit first and invalidate second, matching the
existing AI-settings flag path. Regression guards cover the event order for all
three mutations and prevent services from reintroducing pre-commit invalidation.

### 2026-08-17 — Fix: natural build ordering for runs and flake transitions

Sharded ingestion can persist CI runs out of order, while lexical sorting puts
identifiers such as `ui-10` before `ui-2`. Run listings, per-suite `Run #N`
ordinals, and the transition sequence used by the flaky-test counter were all
ordered primarily by persistence time, so the UI could assign misleading run
numbers and the flake detector could count transitions in the wrong sequence.

These paths now use the numeric chunks of `build_number` as a PostgreSQL
`bigint[]` natural-sort key, followed by the raw build number, persistence time,
and row id. Values without digits sort after numbered builds in chronological
order (and before them when the listing is reversed). Build identifiers remain
an imperfect cross-branch/cross-scheme clock, but this is deterministic and
closer to CI execution order than asynchronous commit time.

### 2026-08-17 — Fix: the readiness probe deadlocked a rollout under load

Found while deploying the worker memory fix. The probe ran
`celery -A app.worker.celery_app inspect ping --timeout=10` inside a 15 s exec — a
control-channel round-trip, so it measures how *responsive* a worker is, not whether it is
healthy. With four prefork children pinned at the CPU limit the reply missed the deadline:

```
Warning  Unhealthy  28s (x13 over 5m32s)  kubelet
  Readiness probe failed: command timed out:
  "celery -A app.worker.celery_app inspect ping --timeout=10"
```

The worker's own log showed it consuming tasks throughout, `restarts=0`, no OOM. It was
busy, not broken.

For a queue consumer with **no Service in front of it**, readiness gates only the rollout,
so marking a busy worker NotReady is worse than useless — it stops the deploy finishing:

```
rs …-5f546fc8b7   desired=2  ready=1    <- new revision, 4Gi
rs …-6bb47455bc   desired=1  ready=0    <- old revision, 2Gi, 6 restarts, OOM-looping
```

The Deployment could not scale the old ReplicaSet down, so the pod that was OOM-looping at
2 GiB stayed alive serving the queue while its fixed replacement sat NotReady beside it.
The deploy script's DEGRADED verdict was the same cause.

`worker_ready` now writes `/tmp/celery-worker-ready` and `worker_shutdown` removes it; the
probe checks for the file. That signal fires once the consumer has connected to the broker
and started consuming — which is what readiness should mean here — and a file check costs
nothing under load. Liveness stays on `pgrep` and remains what catches a dead worker.

The manifests already carried a comment recording that `inspect ping` "can exceed the
timeout under load and kill a worker mid-task" — that lesson had been applied to liveness
and not to readiness.


### 2026-08-17 — Fix: worker-default's per-child memory budget was half of any peer

Follow-up to the OOM fix earlier today, which was **half right**. Verified on the live
homelab by reproducing the ingest that caused the crash loop:

```
cache redirect   WORKS   167M in /var/cache/testlookup/chroma-onnx (model.onnx + archive),
                         survived two container restarts — the refetch loop is gone
OOM              NOT FIXED   restarts 1 -> 2, OOMKilled, on a run with the weights
                             ALREADY on disk
```

So the download was never the dominant cost. Raising 1 GiB → 2 GiB by analogy with
`worker-ingestion` was reasoning from the wrong number; the per-child budget is what
matters:

| worker | concurrency | limit | per child |
| --- | --- | --- | --- |
| worker-ai | 2 | 4Gi | 2048Mi |
| worker-children | 1 | 2Gi | 2048Mi |
| worker-critical | 2 | 2Gi | 1024Mi |
| worker-ingestion | 4 | 2Gi | **512Mi** |
| worker-default | 4 | 2Gi | **512Mi** |

A child holding the all-MiniLM model plus onnxruntime does not fit in 512Mi. `worker-default`
goes to 4 GiB (1024Mi per child, matching `worker-critical`) and gains
`--max-memory-per-child=900000`, which recycles a bloated child *between tasks* instead of
letting the kubelet kill the container mid-task.

`worker-ingestion` had the identical 512Mi ratio and runs the very ingestion path that
drove `worker-default` into the loop — same ChromaDB indexing, same model per child. It
had not been *seen* failing, which is not the same as having headroom, so it gets the same
treatment: 4 GiB and `--max-memory-per-child`. No worker is now below 1024Mi per child.


### 2026-08-17 — Fix: worker-default OOM-killed in a loop re-downloading embedding weights

Found on the live homelab while verifying an unrelated deploy: `worker-default` had **31
restarts**, exit 137, and ordinary test-report ingestion reproduced it on demand.

```
Last State: Terminated   Reason: OOMKilled   Exit Code: 137
GET https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz  200 OK
  onnx.tar.gz: 100%|##########| 79.3M/79.3M
```

Two independent causes, both fixed here.

**The archive was re-downloaded on every container restart.** ChromaDB caches the weights
under `$HOME/.cache/chroma`, and `HOME` is `/tmp` in these pods — the container's writable
layer, which a restart discards. So each OOM-kill came back to an empty cache, re-fetched
79 MB, and that fetch fed the next OOM.

`CHROMA_ONNX_MODEL_DIR` looked like the fix, and `local_embedder_guard`'s own error
message recommends it as the air-gap side-load hatch. **It did neither.** The setting fed
only that module's `_model_dir()` check while ChromaDB kept reading its own
`DOWNLOAD_PATH`, so weights placed there made `model_present()` true, the guard stood
aside, and ChromaDB looked elsewhere and downloaded anyway. `install_offline_embedder_guard`
now repoints `ONNXMiniLM_L6_V2.DOWNLOAD_PATH` at the configured directory, which makes the
documented hatch real and lets the cache live on a mounted volume.

Every backend-image workload now mounts an `embedder-model-cache` emptyDir at
`/var/cache/testlookup/chroma-onnx` with `CHROMA_ONNX_MODEL_DIR` pointing at it. An
emptyDir outlives a *container* restart while the pod stands — which is exactly the case
that was hurting — so the weights are fetched at most once per pod instead of once per
crash.

**The steady state did not fit either.** `worker-default` was the only `--concurrency=4`
worker at a 1 GiB limit; `worker-ingestion`, also 4, has had 2 GiB. Four prefork children
each loading an ONNX model do not fit in 1 GiB even with no download. Raised to 2 GiB.

Note the scope of the earlier `local_embedder_guard` work: it closed the **egress** half of
this defect (offline mode must cover weight acquisition, not just inference) and is
unchanged in that respect. The memory half was still open, and is reachable on any
deployment where `AI_OFFLINE_MODE=false` — which is every deployment using a cloud LLM.

Baking the model into the image remains the better end state for air-gapped installs; this
change makes that work too, since a baked-in model at the configured path is simply found.


### 2026-08-17 — Fix: feature-flag caches were invalidated before the commit

Caught on the live homelab while verifying #653, not by review. Flipping the Knowledge-RAG
switch through the API returned 200 and landed in Postgres, and every cached reader kept
serving the old value:

```
PUT /settings/ai {knowledge_rag_enabled: true}   ->  200
GET /feature-flags/knowledge_rag/status          ->  true    (queries Postgres directly)
GET /settings/ai                                 ->  false   (via is_enabled -> cache)
GET /knowledge-sources/<id>/freshness            ->  503     (via is_enabled -> cache)
```

`update_flag` does invalidate, but it runs **inside the caller's transaction**, and
`get_db` commits only after the handler returns. Between that invalidate and the commit,
any reader re-reads the *old* committed row and re-populates the in-process and Redis
caches with it — where it then stands for the full 30 s TTL. Invalidating mid-transaction
is the same as not invalidating.

`invalidate_flag_cache()` is now public and `PUT /settings/ai` calls it after
`db.commit()`, before re-resolving the value it reports.

Known remaining limit, not addressed here: the in-process cache is per-process and there
are two backend replicas, so a replica that did not serve the write can still answer from
its own cache until the 30 s TTL expires. Closing that needs a cross-process invalidation
signal (Redis pub/sub), which is a larger change.

`PATCH /api/v1/feature-flags/{key}` — the route the Feature Flags page uses — has the same
pre-commit ordering and is left for its own PR.


### 2026-08-16 — Fix: Knowledge RAG had three gates and the UI toggled the wrong one

Found during the UI sweep of the live homelab, where all three disagreed:

```
GET /feature-flags/knowledge_rag/status  ->  {"enabled": false}
GET /settings/ai                          ->  knowledge_rag_enabled: true
/settings/feature-flags rendered              knowledge_rag   OFF
/settings/ai rendered                         "Enable Knowledge RAG   Active"
GET /knowledge-sources/<id>/freshness     ->  503 "Knowledge RAG feature is not
                                              enabled. Enable it from
                                              Settings > AI Configuration."
```

The 503 sent the operator to the page that already said **Active**.

`services/knowledge_source_service.py` held three resolvers over three stores:

| resolver | store | who used it |
| --- | --- | --- |
| `_is_rag_enabled_from_db` | `ai_config.knowledge_rag_enabled` AppSetting — **the switch the UI wrote** | the RAG evaluator, and nothing else |
| `require_rag_enabled_async` | `feature_flags` row | all four knowledge-source endpoints |
| `require_rag_enabled` | env var only | **nothing — zero callers** |

So flipping the switch the error message named changed whether the RAG evaluator
ran, and nothing else.

There is now one gate. `feature_flags.is_enabled` is the survivor because the flag
service is the declared successor: `LEGACY_ENV_VAR_MAP` in `services/feature_flags.py`
exists "during the one-release cutover from hand-rolled RAG flag to the new service" —
the AppSetting *was* the hand-rolled flag. `PUT /settings/ai` now writes that flag row,
`GET /settings/ai` reports it, and the `config:knowledge_rag_enabled` Redis mirror (a
third copy of the same bit) is gone.

The write goes through `feature_flags.update_flag` rather than
`feature_flag_service.set_flag` — only the former invalidates the in-process and Redis
caches `is_enabled` reads, so without it the switch would appear dead for 30 seconds
after being flipped. It also earns the flag change an audit entry.

`get_rag_status` claimed in its docstring to mirror the gate and did not; it read the
Redis key and the AppSetting the gate never consulted. It calls the same resolver now,
and reports `feature_flag: "knowledge_rag"` instead of the env var's name.

**Behaviour change:** a workspace whose AppSetting said true while the flag said false —
this homelab — now shows the switch **off**, which is what was actually in force the
whole time. Re-enabling it there now takes effect.

Three existing tests covered `require_rag_enabled`, the resolver with no callers. Passing
tests over dead code are part of how three gates survived; they now drive the real one.


### 2026-08-16 — Fix: /trends and /overview disagreed about the same pass rate

Found during the UI sweep of the live homelab, against the hand-computed fixture
(60 executions on one day: 31 passed, 17 failed, 6 broken, 6 skipped).

```
/overview        57%     pass rate · 14d
GET /metrics/trends → pass_rate: 57.4
/trends          51.7%   pass rate · 14d      <- same project, same window
```

`TrendsPage` computed its own rate as `passed / (passed + failed + skipped + broken)`.
Skips belong in neither half of that ratio — a skipped test was never evaluated — and
`backend/app/services/metrics_service.py` says so explicitly, having already been fixed
for this exact class once (a trend line reading 83.9% beside an 81.0% headline).

The right number was in the payload the page had already fetched: `TrendPoint.pass_rate`
is on the contract, is typed in `types/metrics.ts`, and had **no consumer**. Same for the
per-suite `pass_rate` from `/analytics/coverage`, which the suite rows recomputed instead
of reading. The page now uses one denominator — passed + failed + broken — everywhere,
and takes the API's rate where the API publishes one.

A third denominator lived in the same function: `passRatePerDay` used
`passed / (passed + failed)`, dropping broken, which fed the variance-stability score.

Test executions were also labelled "runs" throughout, so a 6-run window read
`31 / 60 runs` and `failed 23 of 60 runs` — the wording defect #643 fixed on the
dashboard, reintroduced here. The model fields are renamed `*Executions` so the label
and the quantity can't drift apart again.
### 2026-08-16 — Fix: every date on /trends was a day early west of Greenwich

Found during the UI sweep of the live homelab, in `America/Chicago`. Six runs were
ingested on Aug 16 and the API bucketed them as `2026-08-16`. The page rendered:

```
Aug 2   Aug 5   Aug 9   Aug 12   Aug 15 (today)      <- today was Aug 16
13-day silence between Aug 2 and Aug 14              <- the gap was Aug 3–15
the scheduler appears paused since Aug 2             <- nothing ran on Aug 2
```

`shortDate()` did `new Date('2026-08-16').toLocaleDateString()`. A bare `YYYY-MM-DD`
parses as UTC **midnight**, which is the previous evening anywhere west of Greenwich, so
every label lost a day — the run-cadence heatmap, the daily-breakdown axis, the gap
narrative, and the axis tick that claims to be today. The endpoints bucket by
`DATE_TRUNC('day', created_at)`, so these strings are calendar-day labels, not instants;
they are now formatted from their own parts and never routed through a local `Date`.

`relativeAgo()` had the same input and reported hours from it, so a run ingested minutes
earlier read "23h ago". It now answers at the granularity a calendar day carries:
today / yesterday / N days ago.

New `utils/calendarDay.ts` holds the day arithmetic; `/coverage` and `/failures` built
their windows from a local clock date and stamped the cells with `toISOString()`, and
now use the same helpers. That mix turns out to produce the same day sequence as staying
in UTC — DST transitions included — so this part is consolidation, not a second fix, and
`calendarDay.test.ts` pins the equivalence so it stays that way.
### 2026-08-16 — Fix: /defects reported a Jira connection it had never checked

Found during the UI sweep of the live homelab, whose Jira has never been configured.
The bridge card rendered:

```
Jira bridge · jira                        Connected
Last sync             just now
Webhook v2 · auto-link enabled
```

`GET /api/v1/settings/integrations` on that same workspace returns
`jira_enabled: false`, `jira_domain: null`, `jira_token_set: false`. None of the card was
wired to anything: the green badge, the host, the sync time and the webhook line were all
literals, and "Bridge settings →" toasted "coming in Phase 2". A user reading it would
believe their defects were syncing somewhere.

The card now reads the config and reports one of three states. **Unknown** is a state in
its own right — that endpoint needs QA_LEAD, so a developer gets a 403 and the page has
not been told anything; it must not fall back to either claim. "Enabled" alone is not
"connected" either: the toggle can be on with no host and no credential.

Removed rather than reworded: `Sync latency p95` (no source), `Last sync` (no clock),
`Webhook v2 · auto-link enabled` (nothing reads it), `Auto-link rule misses … in last 7d`
(it rendered the all-time unlinked count), and the header's `synced just now`.
"Bridge settings →" now goes to /settings/integrations.
### 2026-08-16 — Fix: Overview KPI captions contradicted the values above them

Found during the UI sweep of the live homelab, on a project whose six runs all landed on
one day:

```
NEW FAILURES · 24H    23      14d · no failures recorded
TOTAL EXECUTIONS      60      14d · awaiting runs
AVG PASS RATE        57%      14d · need ≥ 2 runs        <- six runs existed
```

The caption fills the slot the sparkline would have used and appears whenever the series
has fewer than two points. It was worded as a claim about the *metric*, so it read as a
denial of the number printed directly above it. The shortfall is also days of history,
not runs — six runs on one day is one point.

Captions now explain the missing trend line and name what they have: "1 of 14 days has
data · no trend line", or "14d · no executions recorded" when the window really is empty.
The execution-trend chart said "Need ≥ 2 timed runs over 14 days" for the same reason and
now says the same thing about days.
### 2026-08-16 — Fix: chat run cards promised an AI analysis nobody had queued

Found during the UI sweep of the live homelab. Six runs were ingested with `run_ai` set
on one of them. The five others each rendered:

```
ui-5   AI PENDING   just now
  Build ui-5 completed — 3 tests failed. Pass rate: 66.7% (6/9 executed, 1 skipped).
  AI analysis is being generated and will appear shortly.
```

Nothing was being generated, and nothing ever would be. `finalize_run` queues the agent
pipeline only when `run_ai` is set; otherwise it logs `agent_pipeline_skipped` and
returns. That decision is not persisted anywhere, so the stub builder — which fires for
every run with no summary in Mongo — cannot distinguish "queued" from "never asked for"
from "failed permanently". It asserted the most optimistic of the three unconditionally.

The card now states there is no analysis instead of predicting one, and the badge reads
NO AI ANALYSIS without the pulsing spinner that implied work in progress. The counts
beside it were already right and are pinned by the new test.


### 2026-08-16 — Fix: deleted projects were still voting in the dashboard verdict

Found during the UI sweep of the live homelab, against a hand-computed fixture.

`DELETE /api/v1/projects/{id}` is a **soft** delete — it flips `is_active` and leaves every
run, test case and history row in place. Two unscoped dashboard aggregates never excluded
them:

```
new_failures_24h   reported 301   truth 23    (93 soft-deleted projects supplied the rest)
flaky_test_count   reported  27   truth 24
```

`new_failures_24h` is not decorative: it feeds the `max_new_failures_24h` hard cap, so the
dashboard read **No-Go — ship blocked** on the strength of failures belonging to projects
the user had already deleted, and no amount of fixing live tests could clear it.

This is the third instance of the class in one file. `_period_stats` was fixed earlier and
its comment records the same shape (44,315 executions where only 192 belonged to live
projects) plus the reason the filter is unconditional: putting it behind `if project_id:`
guards only the branch that cannot over-count. Both new filters follow that rule.

### 2026-08-16 — Fix: the Overview called test executions "runs"

`total_executions_7d` counts **test executions**. The Overview page rendered it as a run
count in five places, including the release verdict's stated sample size:

```
SAMPLE SIZE  702 runs / 30 days     <- 104 runs actually existed
SAMPLE SIZE   60 runs / 30 days     <- a 6-run project with 60 executions
```

A reader weighing a No-Go verdict was told the evidence base was ~7x larger than it was.
The coverage micro-strip compounded it by printing the **relative** trend as an absolute:
`702 runs · +680 this period`, where +680 means the count grew by 680%. That is the same
defect `deltaFromMetric` already carries a comment about ("▲ +400" beside a value of 150);
the strip was a fourth site that pass missed.

Labels now say "test executions" and the strip renders `+680%`.

Regression: `backend/tests/regression/test_deleted_projects_do_not_vote.py` (5 tests) and
three new cases in `frontend/src/pages/OverviewPage.test.tsx`; **6/6 mutations killed**.
Two pre-existing Overview assertions were scoped (`findByText` → `findAllByText`, and the
sample-size guard bound to its own card) because the fix made a second element carry the
same text — the singular queries were failing for the opposite of the reason they exist.


### 2026-08-16 — Fix: the same failure no longer has two different confidences

Found while measuring AI accuracy on the homelab. One test case, one body of evidence,
two numbers:

```
pipeline-stored : confidence=50  evidence=0  requires_human_review=True   gate=below_threshold
POST /analyze   : confidence=95  evidence=0  requires_human_review=False  gate=above_threshold
stored AFTER    : confidence=95  evidence=0  requires_human_review=False  gate=above_threshold
```

The third line is the damage. `POST /api/v1/analyze` upserts the same `AIAnalysis` row, so
**opening a finding in the UI rewrote the persisted figure**, cleared its human-review flag
and moved it above the confidence gate — with no new evidence and nothing that could have
earned the higher number. `requires_human_review` is the field analytics counts as
"needs review", so the act of looking at a finding removed it from the queue.

Cause: the confidence policy — no evidence references caps at 50, an UNKNOWN category
cannot be confident, an analysis carrying an error cannot claim confidence — lived as the
private `AnalysisAgent._validate_confidence`, reachable only from the batch pipeline. The
endpoint called the same `analysis_router.classify_test` and persisted the engine's raw
self-assessment. This is the recurring class in this codebase: a validation stage that
exists but that one of its callers cannot reach. The `confidence_validated` marker the
pipeline had been setting since US-15.2 was read by nothing but tests.

- The rules move to `services/confidence_validation.validate_confidence()`, where every
  caller can reach them; `AnalysisAgent` delegates rather than keeping a second copy that
  would drift.
- `POST /api/v1/analyze` applies them before persisting or responding.
- Validation is now idempotent — `evidence_multiplier_bonus` adds +5, so two callers
  validating the same dict would have inflated a score nothing re-earned.

**Behaviour change:** `POST /api/v1/analyze` now returns and stores the *validated*
confidence. Findings whose engine score was high but evidence-free will read lower than
before (50 rather than 95 in the case above) and will correctly show as needing review.
Nothing was recomputed for existing rows — no backfill.

Regression: `backend/tests/regression/test_confidence_validated_on_every_path.py`
(9 tests, 6/6 mutations killed), including a behavioural router test asserting the row
written for an evidence-free 95 carries 50, and that an evidenced, tool-backed claim is
*not* capped so the fix does not become a blanket ceiling.


### 2026-08-16 — Feature: AI output now names the suite, the tests, and the reasons (FR-001, FR-002)

Reported by the owner from `/agents` and `/chat`: a suite is assigned to the pipeline but
appears nowhere in the LLM output or the generated reports, and summaries give failure and
skip *counts* while never saying which tests failed or why — "text ... which lack credible
details".

That was an accurate description of the model's **input**, not of the model:

- `ConversationAgent._fetch_run_context` selected run-level aggregates only — build, branch,
  status, counts, pass rate. **No per-test rows and no suite.** The model was being asked to
  describe failures it had never been shown.
- `SummaryAgent._build_context` built its bullets as `[CATEGORY] conf=N%: <summary>`, binding
  the test id to `_tc_id` and **discarding it**.
- `AnalysisAgent` already had the name and suite in `test_meta`, used them to prioritise and
  to build the classifier payload, then stored only the classifier's result — so nothing
  downstream could attribute a category to a test.

New `failure_detail_service` assembles the named failures for a run and is consumed by both
surfaces. Reports and chat answers can now state the suite, the failing test names, the
error text, the classification and its confidence.

Deliberate choices, each guarding a failure mode this codebase has already produced:

- **Suite reads from both places.** Live-stream runs carry it only on the run
  (`primary_suite_name`), newer ingests on the case — reading one leaves it blank for half
  the corpus. Same rule as `_effective_suite_sql`.
- **The cap is disclosed.** A 400-failure run cannot go in a prompt; the payload carries
  `returned` / `total` / `truncated` and the rendered text says so. A silently truncated
  list is a partial view that reads as complete.
- **An unanalysed failure says "not analysed"** rather than `UNKNOWN` — the latter is
  indistinguishable from a classifier that ran and could not decide.
- **Confidence travels with the category**, because `INFRASTRUCTURE` at 95 and at 30 are
  different claims.
- **The 50% confidence floor now discloses what it removed.** A run whose analyses were all
  low-confidence previously rendered "No analyses available", which reads as "nothing was
  analysed" rather than "nothing was confident".
- The prompt tells the model to name only what it was given and to say when something was
  not recorded — supplying the rows is what prevents invention; an instruction alone would
  have invited it.

Seven mutations verified, including restoring the exact reported state (aggregates with no
names, no suite, no reasons). One initially SURVIVED: a guard asserting the phrase
"confidence floor" appeared in the source matched its own explanatory comment. It is now
behavioural — it builds a context from two low-confidence analyses and asserts the
disclosure is present.


### 2026-08-16 — Fix: the deep pipeline ran every stage after `summary` twice

The deep pipeline fanned three branches into `summary` at unequal path lengths:

```
anomaly_detection                          -> summary    1 hop from ingestion
root_cause_analysis                        -> summary    1 hop
failure_clustering -> dispatch -> join     -> summary    3 hops
```

LangGraph runs a node once per superstep in which *any* predecessor completed. Two
branches landed in the first superstep and clustering in a later one, so `summary`
fired twice — and took the entire specialist chain with it.

Measured in a single live run:

- `route_after_summary_deep` (summary's own outgoing edge) logged **twice**;
- every skipped specialist stage recorded **twice** — 12 entries for 6 stages;
- `decision_report_verification` logged **both `passed` and `failed`**, 464 ms apart.
  The failing pass won, so the report was withheld after an earlier execution said it
  passed independent checks. Either an unverified report would have shipped, or a good
  one was suppressed.

It stayed invisible because `completed_stages` uses a de-duplicating reducer while
`skipped_stages` does not — the repetition was hidden on the list people read and
visible only on the one they don't.

Clustering is now sequenced after `summary` instead of fanning in. Nothing is lost:
`SummaryAgent` never read the cluster output — it passes a literal `cluster_count=0` —
and every stage that *does* consume clusters runs later in the chain. The cost is that
clustering no longer overlaps analysis, which is worth paying to run each stage once.

The same defect had a second, unobserved instance: on an **all-green** run the
conditional routed `ingestion -> summary` directly while `root_cause_analysis -> summary`
fires unconditionally, so `summary` would run twice there too. Green runs now route
through `root_cause_analysis`, which early-returns when there is nothing to analyse.

Guarded by a **topology** test rather than a behavioural one: nothing about the graph
fails, every stage completes, and a test asserting "the pipeline succeeds" passes
happily while every node runs twice. The guard asserts that no node is reachable at two
different path lengths within a fixed set of conditional choices, plus that every deep
stage stays reachable — a graph with no duplicate depths is otherwise trivially
achievable by deleting edges.

Four mutations verified, the decisive one being a restoration of the exact original
edges. The guard's first version was itself wrong in the opposite direction — it treated
mutually-exclusive conditional branches as simultaneously active and failed on the
*fixed* graph.


### 2026-08-16 — Fix: the chat table made the model derive the pass-rate denominator

After the reconciliation fix, the chat agent answered a question with known ground
truth (4 passed / 4 failed / 1 broken / 1 skipped of 10) as:

> "4 tests failed out of **10 executed**, resulting in a 44.4% pass rate."

The figures now reconciled, and the failure count and rate were both right — but
*executed* is 9, not 10. The table stated a total and a skipped count and left the
model to subtract. It didn't.

`Executed` is now its own column. Publishing a rate means publishing its denominator,
not the ingredients for computing one.

**The guards for this were initially worthless, and mutation testing is what said so.**
Two mutations survived: dropping the `Executed` column, and computing it without
excluding skips. Both survived for the same reason — the test rebuilt the row-formatting
expression itself and asserted on its own copy, so changes to the real builder never
reached it. A third failure mode compounded it: the source-level check
`assert "Executed" in src` matched the *comment* explaining the column.

The guards now call `ConversationAgent._fetch_run_context` directly against a fake
session and parse what it actually renders. 11/11 mutations killed.


### 2026-08-16 — Fix: the faithfulness evaluator label named a code path, not the judge

Scored cases recorded `faithfulness_evaluator = "ollama"`, and the refusal message read
`(evaluator: ollama)`. But `"ollama"` is the name of a *strategy* — a single-prompt
yes/no check — and that strategy calls `get_llm()`, which resolves to whatever provider
the deployment has configured. On a deployment running OpenRouter, a case judged by
mistral-nemo was recorded, and shown to the user, as judged by Ollama.

Same class as the settings badge that reported a missing API key as "Model Missing": a
label naming the wrong thing. It matters more here because it is provenance on a
**gating decision** — "which model refused my test case" is the first question an
operator asks.

The label now names what actually judged: the resolved provider for the LLM strategy,
`ragas` for the Ragas strategy.

A second honesty problem surfaced while fixing it. `_evaluate_via_ollama` short-circuits
when there are no citations and returns 0.0 **without calling any model** — but that
result was still labelled with a backend, implying a model had rejected the case. That
decision is a rule, not a judgement, so it moved up into `evaluate()` where it belongs
and is labelled `no-citations`. Replacing one misleading label with a different
misleading label would not have been a fix.

Four mutations verified for this change specifically, including reverting the label to
the strategy name and making the rule-based refusal claim a model judged it. A fifth
initially SURVIVED because it mutated a redundant truncation rather than the line that
owns the column-width invariant — retargeted at `apply_evaluation`, where the guarantee
actually lives.


### 2026-08-16 — Feature: the RAG faithfulness gate is now reachable

`rag_faithfulness_service` implemented a complete feature — `evaluate`, `gate_accept`,
`persist_evaluation`, `list_needs_review`, an Ollama and a RAGAS backend, a feature flag,
a score parser, four columns and migration 0069 — and **nothing in `app/` called any of
it**. The only reference outside the module was a comment. Its own tests passed, because
they called it directly: a green suite proved the code worked, not that anything ran it.

Wired at the three points its docstrings already named:

- **generation** (`rag_generation_service._persist_cases`) scores each generated case
  against the chunks it was grounded in — the only place holding both;
- **accept** (`rag_review_service.accept_case`) consults the gate before flipping a case
  to draft, and refuses with **409** when the score is below threshold, leaving the case
  untouched with the reason attached;
- **`GET /api/v1/test-management/cases/needs-review`** exposes the held-back queue,
  lowest score first.

**BEHAVIOUR CHANGE, opt-in only.** The `rag_faithfulness_gate` flag defaults **off** (a
missing flag row resolves to `False`), so every existing deployment sees no extra LLM
calls and no new refusals until someone turns it on. With it on, accepting a
low-faithfulness case now fails where it previously succeeded — that is the point of the
feature, and it is stated here because it is the kind of change that should never be a
surprise.

Two traps handled rather than discovered later:

- `persist_evaluation` and `gate_accept` open their **own** session and commit. Called
  from inside a request they would violate the staging discipline, and worse, generation
  has only `flush()`ed the case — a second session cannot see it, so the evaluator would
  silently score nothing while looking wired up. Added `apply_evaluation` and
  `check_accept` as the in-request forms; the session-owning pair now delegate to them,
  so the threshold wording lives in one place.
- `/cases/needs-review` is a literal path segment competing with `/cases/{case_id}`.
  `bootstrap.py` already registers `rag_generation.router` first for exactly this reason
  (the comment there cites `/cases/stale`), and a test now pins that ordering.

The primary guard is **reachability**, not behaviour: every public entry point must have
a caller in `app/` that is not the module itself. A unit test cannot catch "nothing calls
this" by construction, which is precisely why this survived.

Seven mutations verified — including reverting to the unreachable state, swapping the
in-request call for the own-session one, and flipping the router registration order.


### 2026-08-16 — Fix: five more places published figures that could not all be true

The run summary's non-reconciling figures turned out to be one instance of a class,
not a one-off. The same shape was live in five places, each publishing a `total`, a
`failed` and a `pass_rate` while omitting the skipped and broken buckets. Pass rate is
`passed / executed`, so a reader combining the printed numbers gets a different answer
than the printed rate.

Found by asking the chat agent a question with known ground truth (4 passed / 4 failed
/ 1 broken / 1 skipped). It replied:

> "In the most recent run (s2-6), 4 tests failed out of 10, resulting in a 44.4% pass rate."

Faithful to its input and wrong to a reader — 10 − 4 = 6 passed reads as 60%. The chat
context table was feeding it `| 10 | 4 | 44.4% |`. That is a product defect, not a model
one: the same table would mislead a person.

Fixed:

- **chat run-context table** — now selects and shows every bucket, and states that the
  rate excludes skipped tests.
- **`chat_service` run stubs** — derived `passed = total - failed`, counting broken and
  skipped tests as passes; for the run above it produced **6** where the stored count is
  **4**, contradicting both the real value and the rate beside it. Now reads the stored
  count, shows the fraction over executed tests, and counts a broken test as a failure
  (it previously called such a run "completed with no failures").
- **GitHub check-run title** — `4/10 passed (44.4%)` invited 40%. Now over executed
  tests, matching the rate's own denominator. The body already broke out every bucket.
- **GitLab commit-status description** — same shape, same fix.
- **summary agent** — already fixed; now guarded across bucket mixes rather than only
  the one reported shape.

Nine mutations verified across all five surfaces. The rule this class keeps violating:
**never derive a count by subtraction, and never print a rate without the terms it was
computed from.**


### 2026-08-16 — Fix: `/analyze` could never classify anything

The user-facing "Analyze" action returned the same thing for every test, always:

```
failure_category   UNKNOWN
confidence_score   30
root_cause_summary "Could not determine failure cause from available data."
```

The failure text was stored correctly, and the engine classifies it without
difficulty — checked directly in the running backend:

```
RulesEngine.classify_test('AssertionError: expected total 100 but was 97')
    -> PRODUCT_BUG, confidence 55
RulesEngine.classify_test('java.net.ConnectException: Connection refused: ...')
    -> INFRASTRUCTURE, confidence 70
```

`routers/analyze.py` built its `test_case` payload from ids, names, a timestamp and
pod metadata, and never included `error_message` or `stack_trace` — the two keys
`analysis_router.classify_test` reads, and which its own docstring names.
`worker.tasks.run_live_test_analysis` had the same omission, so live-stream analysis
was permanently UNKNOWN as well. `agents/analysis_agent.py` *did* pass the text,
which is why the feature worked in the offline pipeline and the endpoint was never
suspected.

It stayed invisible because `classify_test` degrades to UNKNOWN rather than failing.
"Could not determine failure cause from available data" was true in a useless sense:
there was no data, because the caller never sent any. No exception, no log, no red
test — three callers silently disagreeing about one contract.

Guarded structurally rather than behaviourally: an AST check requires every call site
that builds a literal `test_case` to name the failure text. A behavioural test would
only have covered whichever caller it exercised, and the whole defect was callers
diverging from each other.

Four mutations verified. The most important is the fourth: reverting a payload to a
`**splat` turns the contract check into a *skip*, disabling the guard with nothing
going red. An explicit anti-vacuity floor now pins which callers must stay enforced —
the first version of this fix used a splat and was caught by exactly that.

### 2026-08-16 — Fix: the dashboard trend badge was a percentage with no percent sign

The Overview KPI read:

```
TOTAL EXECUTIONS   150   ▲ +400
```

`MetricCard.trend` is a **relative percentage change vs the previous period** —
`((cur - prev) / prev) * 100` in `metrics_service` — for every metric on that row.
So `+400` meant the count quadrupled, not that four hundred more runs happened. Next
to a value of `150`, the absolute reading is the natural one, and it is wrong by a
factor of nothing in particular.

The badge now renders `+400%` / `-2.2%` / `0%` and carries the comparison basis in a
title, because a percentage with no baseline is still ambiguous — 400% of what, since
when?

Same class as the run summary that stated figures which could not all be true at once:
**a published number whose unit is not stated is one the reader assigns the wrong unit
to.** Four mutations verified. The fourth — a flat trend losing its unit — survived the
first three guards and exposed a real hole in them, which is the entire point of
mutating rather than trusting a green suite.

### 2026-08-16 — Fix: a working OpenRouter deployment reported itself as broken

Three surfaces told the operator the AI stack was down while it was demonstrably serving
traffic — the LLM-generated summaries were verified against OpenRouter's own billing counter
at the same time the UI was reporting failure.

- **The global banner read "Degraded: ollama unreachable" permanently.** `/health/details`
  reports `ollama: {"status": "skipped"}` when `AI_OFFLINE_MODE=false`, because a cloud-LLM
  deployment has no local Ollama worth probing — and returns an overall `"status": "healthy"`
  in the same payload. `useSystemHealth` counted every check whose status was not exactly
  `ok` as unavailable, so a deliberate non-check rendered as a failure and the SPA
  contradicted the backend's own verdict.
- **The AI settings page reported `Unknown LLM provider 'openrouter'`** and marked the LLM
  tier unavailable. `model_status_service` kept its own hand-maintained `_CLOUD_PROVIDERS`
  tuple; #616 taught `llm_factory` and `llm_policy_service` about OpenRouter and this second
  copy was never updated. It now derives from `llm_policy_service`, the single source of
  truth, and an unmapped API key reports honestly instead of raising `KeyError` and 500ing
  the page.

- **The analysis-mode badge called every cloud failure "Model Missing".** It derived the label
  from `ollama_reachable`, re-deriving a cause the backend had already determined — so an
  unset OpenRouter API key told the operator to go install a model. The fallback-chain entry
  now carries a machine-readable `reason_code` (`no_api_key`, `offline_blocked`,
  `unreachable`, `model_missing`, `unknown_provider`, `unverifiable`) and the badge branches
  on it. An unrecognised code renders a neutral "Unavailable" rather than inventing a specific
  claim, and a backend too old to send one still gets the previous behaviour.

All three are the same class this codebase keeps producing — **a consumer rendering from an
older copy of a producer's vocabulary, or re-deriving something the producer already knew** —
following the settings page that hardcoded six of seven providers (#617). The guards are therefore cross-language and generic rather than spot-checks
for the string `openrouter`: no provider the policy service permits may be described as
unknown, every remote provider must have a key source, and every status the health probes can
emit must be explicitly classified as benign or failing on the frontend. An unrecognised
status still counts as a problem — a false alarm is safer than silence for a health banner —
but a new one on either side now fails a test instead of silently defaulting. The same shape
guards the badge: every `reason_code` the service can emit must have a UI label.

Fifteen mutations verified, including reverting each original bug.

### 2026-08-16 — Fix: the run summary stated figures that did not add up

The summary described a run as `6 tests, 2 failures, 60.0% pass rate`, which cannot be read
consistently: 6 − 2 = 4 passed would be 66.7%. The missing term was the skipped test — pass
rate is `passed / executed`, and the line neither mentioned skips nor said what the rate was
over.

Both consumers of that line were harmed by it. Handed figures that do not reconcile, the LLM
invented a count to make the arithmetic work — a live run produced *"Pass rate was 60.0% with
1 failure out of 6 tests"* against ground truth of 3 passed / 1 failure / 1 error / 1 skipped.
The deterministic fallback narrative built the same contradiction from the same fields and
showed it to the user with no model involved at all.

One formatter now serves both call sites. Counts are stated only when the run actually carries
them and are never derived by subtraction — inferring `passed` from the others would publish a
number nobody measured, which is the failure mode the line already had. The basis of the rate
is stated explicitly rather than left to be inferred.

The guard parses the numbers back out of the rendered line and checks that they sum to the
total and that the stated rate is recoverable from them, so it pins the property that broke
rather than a fixed sentence. Both call sites are covered, because fixing only the prompt
would have left the fallback — the path that runs precisely when the LLM is unavailable —
still printing the contradiction.

### 2026-08-16 — Fix: a routine startup condition was logged as four errors per pod

`LiveEventStreamConsumer._ensure_group` called `XGROUP CREATE` unconditionally and swallowed
the resulting `BUSYGROUP`. The consumer group lives in Redis and survives pod restarts, so on
every deploy after the first this fired once per uvicorn worker.

The application handled it correctly, but the OpenTelemetry Redis instrumentation records the
exception on the span *before* this code catches it, so each one was exported as an
ERROR-level record with a full stack trace. A condition the code considers entirely normal
therefore reached operators as errors: a log scan of a completely healthy deployment reported
four failures per backend pod. Error logs that are routinely wrong train people to stop
reading them.

The expected path no longer raises at all — `EXISTS` then `XINFO GROUPS`, creating only when
the group is genuinely absent. `BUSYGROUP` is still tolerated, because check-then-create is
not atomic and two workers starting at once is a real race, but it is now the exceptional
case rather than the guaranteed one. A genuine creation failure is still reported.

The guard asserts on the Redis calls actually issued rather than on log output: the exception
never reached our logger even when the bug was live, so a test watching `caplog` would have
passed throughout. Eight mutations verified across this and the summary fix.

### 2026-08-16 — Fix: the homelab's OpenRouter settings did not survive a deploy

OpenRouter was enabled with a live `kubectl patch` on `testlookup-config`, verified end to
end, and then **turned itself back off** at the next deploy. The deploy re-applies the overlay
manifest on every run, and that manifest still pinned `AI_OFFLINE_MODE: "true"` — so the
patched values silently reverted and the integration stopped working with no error to point
at. `ai_offline_mode` simply read `true` again.

The settings now live in `k8s/overlays/homelab/kustomization.yaml`, which is the file the
deploy actually applies. `k8s/base/configmap.yaml` still defaults to `AI_OFFLINE_MODE: "true"`
so every other install stays offline unless it opts out — enabling egress remains a
deliberate, per-deployment decision.

`ollama` is deliberately kept in both the provider allowlist and the allowed base URLs, so
reverting to on-box inference is a one-line `LLM_PROVIDER` change rather than another
allowlist edit made under pressure.

Guarded by `backend/tests/regression/test_llm_config_is_self_consistent.py`. Enabling a hosted
provider takes five settings that must agree, and two of them are easy to forget precisely
because the other three are the obvious ones:

- a configured `LLM_PROVIDER` must appear in `AI_LLM_PROVIDER_ALLOWLIST`, and a hosted one
  must have its origin in `AI_LLM_ALLOWED_BASE_URLS` — miss either and every call raises
  `LLMPolicyViolation`, which reads like a broken integration rather than a deliberate
  refusal;
- `AI_OFFLINE_MODE: "false"` alone only opens the ceiling: without a provider and model pinned
  beside it the deployment inherits the base's local provider, which is the one it cannot run;
- the shipped default must stay offline;
- the local provider must stay permitted.

Five mutations verified, each reproducing one of those misconfigurations.


### 2026-08-16 — Feature: in-app user documentation

Requested by the owner (backlog B-2): "how to use the application for a new project, how to
use AI reports, explain how the AI agents work, explain how flaky tests are determined,
explain how the go / no go decision is made… the user should understand core features using
the documentation."

A new **Documentation** page under Dashboard (`/docs`) covers exactly those five topics.

**Every number in it was read out of the implementation**, and the page is guarded from both
sides so it cannot drift:

- `frontend/src/pages/DocsPage.test.tsx` pins that the page renders the constants;
- `backend/tests/regression/test_docs_match_the_engine.py` pins that those constants still
  match Python. Change a flakiness weight, the observation floor, the hard-floor factor or
  add a risk dimension, and the suite fails until the documentation is updated too. All four
  mutations verified.

What it documents, faithfully rather than aspirationally:

- **Flakiness** — the four weighted signals (0.45 result volatility, 0.25 retry rate, 0.20
  environment instability, 0.10 duration variance), the five-observation floor, and the
  confidence bands. It states plainly that below five observations there is *no* score, not a
  zero, because reporting 0.0 would read as evidence of stability nobody measured.
- **GO / NO-GO** — the seven risk dimensions, and the five decision rules **in the order the
  engine evaluates them**, including that the two NO-GO rules come first so nothing below can
  soften them. It works the 70%-of-bar hard floor through with a concrete example, and states
  that pass-rate bands can only tighten a verdict, never unblock one.
- **AI reports and agents** — the four summary layers, the rules/ML/LLM routing with
  fallback, the per-stage decision trail, and prompt redaction.

It also documents what the product does **not** claim: AI output is advisory rather than
authoritative and carries confidence and provenance; and a summary may be deterministic
rather than model-written, in which case it says so and `fallback_used` is true.

That last part is the point. Documentation that overstates the product is the same defect
class this codebase keeps producing — a value published to a reader that nothing in the
system actually produces. Prose is harder to notice, not less wrong.
### 2026-08-16 — Fix: the AI settings page had no API-key field for Anthropic or OpenRouter

Selecting a provider you cannot give a key to is a dead end. The settings page offered inputs
for OpenAI and Google only, while the backend accepted keys for Anthropic — and now
OpenRouter — so those providers could only be configured by writing to the database by hand.

Worse, `anthropic_key_set` was **declared on the read schema with nothing assigning it**. It
defaulted to `False` and stayed there, so the "(set)" indicator beside the field could never
light up even with a key configured. That is the third instance this session of a value
published to consumers that no code path produces — after the summary agent's
`_fallback_used` and live-stream's `events_received`.

Wired end-to-end: the secret registry (so keys land in encrypted `secret_refs` rather than
plain `app_settings`), the update and read schemas, **both** router response sites, the
frontend types, and the inputs themselves.

The vocabulary guard grew to cover secret plumbing: every provider whose policy profile says
it `requires_secret` must have a registered secret field, an update-schema field, a
`*_key_set` flag, and a UI input. Two corrections were needed while writing it, both mine:

- the key field is **not** uniformly `<provider>_api_key` — `gemini` is configured with
  `google_api_key`, named for the vendor. The mapping is now explicit, with its own test that
  the map still matches the schema, rather than a guessed convention;
- the first version only required each `*_key_set` to be assigned *somewhere*. The router
  builds that response at two sites, so a mutation removing one still passed — leaving one
  endpoint reporting a permanent `False`. It now requires assignment at **every** construction
  site. Five mutations verified against the final version.


### 2026-08-16 — Feature: OpenRouter as an LLM provider

Requested by the owner (backlog B-4). A deployment that cannot host a model had no way to
exercise the AI paths at all: the homelab's Ollama has zero models and cannot reach
`registry.ollama.ai`, so every agent ran on its deterministic fallback. OpenRouter fronts
~400 hosted models behind one OpenAI-compatible endpoint and one key, which makes it the
cheapest way to put real LLM calls through the pipeline.

- `openrouter` joins the `LLM_PROVIDER` vocabulary, classified **remote** — so
  `AI_OFFLINE_MODE` refuses it exactly like the other hosted providers. That flag remains the
  non-bypassable egress ceiling; enabling OpenRouter is a deliberate posture change on the
  deployments that want it, not a default.
- The factory drives it through `ChatOpenAI` against `https://openrouter.ai/api/v1`, sending
  the `HTTP-Referer` / `X-Title` attribution headers so spend is traceable to this app rather
  than arriving as one anonymous lump.
- The key resolves from `OPENROUTER_API_KEY` or an encrypted secret ref, the same path the
  other providers use. A missing key raises a named error instead of calling with `None`.

**Pricing entries are mandatory, not decorative.** An unpriced remote provider meters $0.00,
which does not read as a missing number in a report — it makes the per-project USD cap
untrippable, the exact state the backend was in before `llm_pricing.py` existed. Rates for
the models this product defaults to were taken from the live
`https://openrouter.ai/api/v1/models` feed on 2026-08-16, and a catch-all row covers the rest
at a **deliberately un-cheap** $3/$15 per Mtok: an unrecognised OpenRouter model is more
likely a frontier model than a budget one, and over-estimating a bill is recoverable where
under-estimating it is not. Override per deployment with `LLM_PRICE_OVERRIDES`.

Default model suggestion is `inclusionai/ling-2.6-flash` — $0.01/$0.03 per Mtok, 262k context,
and it advertises structured-output support, which matters because the summary agent needs
three JSON layers. `mistralai/mistral-nemo` at $0.019/$0.03 is the sturdier alternative and a
one-variable switch.

**Fixed in passing: the AI settings page had been missing `anthropic`.** It hardcoded six
providers while the backend accepted seven, so anthropic could only be selected by editing
the database by hand — this repo's vocabulary-subset defect, where a producer grows a value
and a consumer keeps rendering the old set. Adding OpenRouter without noticing would have
made it eight versus six.

Guarded by `backend/tests/regression/test_llm_provider_vocabulary.py`, which enumerates the
vocabulary from `config.py` — its single source — and asserts every consumer covers it: a
policy profile exists for each (a remote provider wrongly classed local would slip past the
offline ceiling), no remote provider reports a *confident* $0.00, and the settings page offers
exactly what the backend accepts, in both directions. Five mutations verified, including one
that reclassifies OpenRouter as local and is caught as the egress bypass it would be.

To enable on a deployment:

```bash
kubectl -n testlookup patch secret testlookup-secrets   -p '{"stringData":{"OPENROUTER_API_KEY":"sk-or-v1-..."}}'
# then set on backend AND workers:
#   AI_OFFLINE_MODE=false, LLM_PROVIDER=openrouter, LLM_MODEL=inclusionai/ling-2.6-flash
```


### 2026-08-16 — Fix: first-run guide's ingest `curl` pointed at `localhost:8000` on every self-host

The empty-dashboard first-run guide shows a copy-paste `curl` for CI runners that ingest
straight to the API. It hardcoded `http://localhost:8000/api/v1/ingest/file` — but the whole
bundle is deliberately deploy-target-agnostic: `services/api.ts` talks to the backend
same-origin (the k8s ingress routes both `/api/` and `/` to the same host), so an operator
viewing the dashboard at `https://testlookup.example.com` was handed a command aimed at a
backend that only exists on the maintainer's laptop — unreachable, wrong scheme, wrong host.

The guide now resolves the ingest endpoint through a new `backendUrl(path)` helper in
`services/api.ts` that mirrors the axios client's own resolution (`VITE_API_BASE_URL` when
set, otherwise same-origin as the page). The copy-paste command therefore targets exactly the
backend the running UI already reaches. `localhost:8000` remains only as the pure-function
default for local dev, where it is correct. Same class as the earlier ingest-command work: a
snippet must be runnable in the environment it is shown in.

### 2026-08-15 — Change: /my-failures opens on the team inbox for leads

Requested by the owner (backlog B-3). `/my-failures` defaulted to the "Mine" scope, which
opened almost empty for a real lead or admin: auto-assignment routes failures to the synthetic
per-project QA-Lead user, so the human viewing the page owns very few of them. The page looked
broken, and the fix was a toggle most people never found.

Team is now the default **for anyone allowed to see it**. Lower roles are unaffected — they
never see the toggle, and the client no longer even asks for `scope=team` on their behalf.

The default is derived on every render from an explicit "hasn't chosen yet" state rather than
seeded into `useState`. `isQaLead` reads from the auth store, which reports `VIEWER` until
`user` hydrates, so a `useState` initialiser would latch that early `false` and strand a lead
on "Mine" depending on load timing — an intermittent bug that would have been miserable to
diagnose. Deriving it means the default follows the permission as soon as it arrives, while an
explicit click still sticks.

The subtitle now says whose failures are on screen ("Every unresolved failure across …" vs
"Failures assigned to you across …"). The page is titled *My Failures* while opening on the
team inbox, and it should not claim the rows belong to the viewer.

The existing scope tests were **updated, not deleted** — they previously pinned `scope=mine`
as the default. Added alongside them: a non-lead is still forced to `mine`, the default
follows a permission that arrives after the first render, and the subtitle names the scope.
All four mutations verified, including one that reintroduces the `useState` latch and is
caught by the late-hydration test.


### 2026-08-15 — Fix: the deploy could break the cluster and still report success

A homelab deploy printed **"Health check passed"** and **"Deployment Complete"** and exited
**0**, while the backend it had just built was in CrashLoopBackOff for its entire life:

```
testlookup-backend-5c49cc97b9-mgztv   0/1   CrashLoopBackOff   7
testlookup-backend-6fd7db796c-zxjfl   1/1   Running            (previous ReplicaSet)

asyncpg.exceptions.InvalidPasswordError:
  password authentication failed for user "testlookup_user"     (+30 in worker-default)
```

Three weaknesses combined, and the script caused the failure it then failed to notice:

1. **Step 4 regenerates every credential whenever `testlookup-secrets` is absent** — but
   Postgres keeps a persistent PVC and the role password it was *first* initialised with. So
   `POSTGRES_PASSWORD` described a password the database did not have, and every new backend
   and worker pod died on it. Now reconciled: once Postgres is ready the deploy runs an
   idempotent `ALTER USER` to make the database agree with the secret.
2. **A stalled rollout only warned.** `rollout status … || warn` let the run continue to the
   success banner. It now marks the deploy degraded.
3. **The health check is answered by whichever backend pod is Ready** — including one from the
   ReplicaSet being replaced. A check the old pod can satisfy says nothing about the new one.
   Pod state is now inspected first, and a passing HTTP response is explicitly *not* reported
   as success while pods are unhealthy.

A degraded run now prints a red banner naming the likely cause and **exits 1**. Reporting 0
while the new image never started is exactly how this went unnoticed.

Fixed in the same pass: the MCP provisioning step read `$BACKEND_POD`, a variable assigned
only inside the *admin* step's success branch. The admin step had been skipped (backend
unhealthy), so provisioning silently did nothing and left every authenticated MCP tool
returning 401. It now resolves — and waits for — a backend pod of its own, and marks the
deploy degraded if it cannot.

A trap worth recording for whoever debugs this next: `pg_hba.conf` in that image is `trust`
for `local` and `127.0.0.1/32`, and `scram-sha-256` only for remote hosts. A `psql` check run
from inside the pod therefore **accepts any password** — it accepted
`definitely-not-the-password-xyz`. The mismatch was only visible over the remote path the
application actually uses.

Guarded by `backend/tests/regression/test_deploy_reports_failure_honestly.py`, whose classes
are: a degraded outcome must reach the exit code; pod state must be consulted, not only an
HTTP endpoint anything can answer; credentials the script regenerates must be reconciled with
the stateful service that already holds them; and no step may depend on a variable another
step may never have set. Five mutations verified — **two guards were vacuous on the first
pass** because they asserted on a phrase that also appears in a comment or in failure-help
text (`get pods --no-headers`, `ALTER USER`), and both now match the executable construct.


### 2026-08-15 — Fix: the MCP service account was never created, so every authenticated tool 401'd

Found immediately after the NetworkPolicy fix let the MCP server reach the backend at all.
With connectivity restored, tool calls failed differently:

```
tools/call health_check -> "Backend unreachable at http://testlookup-backend:8000:
                            Client error '401 Unauthorized' for .../auth/login"

MCP pod env: TESTLOOKUP_USERNAME=mcp_service   (secretKeyRef MCP_USERNAME)
psql: select count(*) from users where username='mcp_service'  ->  0
```

`deploy-homelab.sh` generates `MCP_PASS`, writes `MCP_USERNAME`/`MCP_PASSWORD` into
`testlookup-secrets`, prints them under a **"SAVE THESE CREDENTIALS — SHOWN ONLY ONCE"**
banner and saves them to `.homelab-credentials` — but never created the account. The deploy
handed the operator working-looking credentials for a user that did not exist.
`scripts/deploy-k8s.sh` and the OpenShift installer had the same gap.

**Creating it was not sufficient.** Non-admin users only see projects they are a member of, so
a fresh QA_LEAD service account authenticated and then answered "No active projects found."
for every listing tool while five projects existed, and `get_quarantine_stats` reported all
zeros. And enrolling it once is not enough either — any project created afterwards would be
invisible to it for the life of the deployment.

Fixed in four parts:

- **migration 0136** adds `users.is_service_account` (with a partial index), so the backend
  can identify machine accounts without hardcoding a username or being handed MCP credentials;
- **`scripts/createServiceAccount.py`** converges the account from the secret — creates it if
  missing, and re-syncs password, role and flag if present, because for a machine account the
  secret is the source of truth. A human admin's password is deliberately never overwritten
  that way. It refuses to run without an explicit password: a defaulted one on an account that
  can quarantine tests and trigger analysis is worse than no account;
- **deploy Step 10b** reads the credentials back out of the secret rather than from
  `$MCP_PASS`. Step 4 is skipped whenever the secret already exists, so that variable is unset
  on every re-run — precisely when the account still needs converging;
- **project creation enrols every active service account**, staged in the same transaction as
  the project itself, with each account's membership cache invalidated after the commit. A
  stale "no projects" cache is indistinguishable from never having been enrolled.

The role is the operator's choice (`MCP_SERVICE_ROLE`, default `QA_LEAD`). Service accounts
deliberately do **not** get ADMIN: they see projects through the same membership mechanism as
everyone else, so per-project authorisation still applies to the one account that talks to
every project.

Guarded by `backend/tests/regression/test_service_account_provisioning.py`, whose classes are
**a credential a deploy publishes must correspond to an account that deploy creates** and
**a service account must be able to see projects created after it**. Five mutations verified —
two of the first-cut guards were vacuous and were tightened: one asserted the deploy merely
*contained* the script's filename, which still passed with the invocation replaced by `true`
because the name also appears in the failure-help text; the other used `x or y` and matched
the converge branch while the create branch's flag was deleted.
### 2026-08-15 — Fix: `events_received` was published to API clients and never counted

Found by driving a controlled live-stream session against the homelab — eight `test_result`
events plus `run_start` and `run_complete`, all accepted:

```
POST /api/v1/stream/events/batch   -> 202  {"accepted": 10, ...}
GET  /api/v1/stream/sessions/{id}  -> {"events_received": 0, ...}
psql: select events_received from live_sessions -> 0
```

`events_received` exists in migration 0008, on the ORM model, in
`architecture/DATABASE_SCHEMA.md`, in the API response and in
`frontend/src/types/live-stream.ts`. **Nothing in the product ever incremented it.**

What kept it hidden: `seed_dev_data.py` wrote a fabricated `sum(tests) * 3`, so seeded
sessions were the only ones with a non-zero value. The field looked alive in every demo while
every real session reported 0 — invisible in the data you demo with, wrong in the data you
run with.

Same class as the summary agent's `_fallback_used`: a value reported to consumers that no
code path produces.

The counter now lives on the Redis state hash the ingest path already writes, so it costs one
extra pipeline op rather than a database round trip per batch. `get_session` prefers the live
value, because the column is only written at close and reading it alone reports 0 for the
entire duration of the run it describes — precisely when a client watching ingest progress
would ask. `close_session` copies it onto the row so it outlives the hash's 1-hour
post-completion TTL. Seed data now uses `sum(tests) + 2` — one result per test plus
`run_start`/`run_complete`, which is what the real path counts.

Keepalives are excluded. `live_heartbeat` exists only to bump `last_event_at` for the idle
reaper, and a standing test pins that a heartbeat-only batch touches no counter at all. The
first cut of this fix incremented on every accepted event and broke that contract — the full
suite caught it — which would also have inflated the figure with idle noise on any run with
long gaps between tests.

Guarded by `backend/tests/regression/test_events_received_is_counted.py`: five mechanics
tests, each mutation-verified, plus a class ratchet requiring every field in the live-session
response to be written by something in `app/` — with a self-test proving the parser still
sees the response, and a guard stopping seed data from re-inventing the value.


### 2026-08-15 — Fix: the MCP server could not reach the backend, so every tool call failed

An entire advertised surface — the README and CLAUDE.md list UI, REST API, CLI and **MCP
server** — was non-functional on any NetworkPolicy-enforcing cluster.

The full MCP handshake over SSE worked and `tools/list` returned ~45 tools. Every
`tools/call` then failed:

```
Error executing tool list_projects: All connection attempts failed
```

From inside the MCP pod, DNS resolved but the backend refused on the service IP *and* both
pod IPs, while the service itself was configured correctly (selector matching, two healthy
endpoints on :8000).

Under `default-deny-all` a connection needs **both** halves, and the two disagreed:
`allow-mcp` granted MCP egress to `app=testlookup-backend:8000`, but `allow-backend`'s
ingress permitted only the `ingress-nginx` namespace, `app=testlookup-frontend` and
`app.kubernetes.io/component=worker`. The MCP pod carries `app: testlookup-mcp` and no
`component` label, so it matched none of them.

Proven rather than inferred, with two throwaway pods from the same image differing only by
one label: unlabelled → connection refused; `component=worker` → 200. That also ruled out
"NetworkPolicy isn't enforced here", which the Traefik-vs-`ingress-nginx` selector mismatch
had made a genuine alternative explanation.

Nothing caught it because the MCP probes are `tcpSocket` on its own port — the right choice
for an SSE server, since an HTTP GET on `/sse` would hang — and because the handshake and
`tools/list` are both served without ever calling the backend. The pod looked healthy and
the tool catalogue looked complete while every tool was dead.

`allow-workers` in the same file already records this exact class: before 2026-05-15 only
`worker` was matched, so the beat pod's `wait-for-redis` initContainer hung forever and
`close_stale_live_sessions` silently stopped running. Two omissions, same shape, four months
apart — so this lands with a ratchet rather than a third comment.

Guarded by `backend/tests/regression/test_networkpolicy_halves_agree.py`: **every egress rule
naming a pod selector must have a matching ingress grant at the destination.** A rule
permitting traffic the other end drops is not a tighter policy, it is a broken one. The check
carries positive and negative self-tests so a parse change cannot make it vacuously green.


### 2026-08-15 — Fix: the AI-degradation flag `fallback_used` was permanently False

Found on the homelab immediately after the deterministic summary fallback was made
reachable. The very first run to use it stored:

```
summary_provenance.fallback_used = True      <- the truth
fallback_used                    = False     <- what the API reports
executive_summary = "…This summary was generated from stored pipeline evidence
                     because the configured LLM was unavailable."
```

`SummaryAgent._store_summary` read:

```python
fallback_used = structured.get("_fallback_used", False)
```

**`_fallback_used` is written nowhere.** Grepping `backend/app` and `backend/tests` returned
that single line as the only occurrence in the codebase; the value `run()` actually records
is `structured["_provenance"]["fallback_used"]`. The expression was a constant `False`
dressed up as a lookup — the wrong-name/dead-branch class again, the fourth instance found
this session.

Across all 57 stored summaries at the time, `fallback_used == True` appeared **zero** times,
while `summary_provenance.fallback_used == True` appeared once — the single summary generated
since the fallback became reachable at all.

Not a cosmetic boolean: `fallback_used` is the flag a consumer checks to learn that AI output
is degraded. It propagates to `provenance.fallback_used` on the run-intelligence contract and
to the `run_intelligence_snapshots.fallback_used` column. A summary whose own prose says the
LLM was unavailable, reporting itself as undegraded, is precisely the failure the AI-trust
work exists to prevent.

Guarded by `backend/tests/regression/test_fallback_used_flag_is_honest.py`: the flag must
agree with the provenance it ships beside, a summary may not say it was degraded while
reporting otherwise, and a ratchet pins the dead key out of the tree. The ratchet matches the
exact key literal rather than the bare substring — the first version also flagged a log event
name and an f-string template, and a ratchet that cries wolf gets deleted. It carries its own
test proving it still fires on the real pattern. Both mutations verified in both directions.
### 2026-08-15 — Fix: `/intelligence/refresh` returned 500, and no run with an AI summary could cache

Found while re-verifying the summary fix on the homelab.
`POST /api/v1/runs/{run_id}/intelligence/refresh` returned **500**:

```
TypeError: Object of type datetime is not JSON serializable
  [SQL: INSERT INTO run_intelligence_snapshots (..., payload, ...)]
...then...
sqlalchemy.exc.PendingRollbackError: This Session's transaction has been rolled back
  due to a previous exception during flush.
```

Two defects stacked, and the second is what turned the first into an outage:

1. **The payload was not JSON-safe.** `run_intelligence_snapshots.payload` is a JSON column,
   but the intelligence payload is assembled partly from MongoDB, which returns BSON dates as
   real `datetime` objects. Walking a live payload found exactly two —
   `structured_summary.generated_at` and `provenance.generated_at`.

2. **The failure was not contained.** The refresh handler wrapped the cache write in
   `except Exception: pass` on the **injected request session**. Catching the exception does
   not undo the failed flush: the session is left rolled back, so the next use of it raises
   `PendingRollbackError` and the endpoint 500s. A best-effort write on a shared session
   cannot be made best-effort by catching it — only by not sharing the session. The GET
   handler already used a dedicated write session and carried a comment explaining exactly
   this; refresh had drifted from it.

Measured blast radius: **all 56 stored run summaries carried `generated_at`**, so every run
with an AI summary both failed to cache its snapshot — silently recomputing the whole
payload on every read, with the failure visible only as a `logger.warning` — and returned
500 on refresh. The reported run had zero rows in `run_intelligence_snapshots`.

Fixed by encoding the payload with `jsonable_encoder` at the persistence boundary (covering
every field that reaches the column, including ones added later) and giving the refresh
handler its own write session.

Guarded by `backend/tests/regression/test_intelligence_snapshot_json_safe.py`, including a
test that the encoding reaches arbitrarily deep — a fix that special-cased the two known keys
would otherwise pass and break on the next Mongo-sourced field. Each guard was
mutation-verified.

### 2026-08-15 — Fix: a cached Mongo client outlived its event loop, so every task after the first failed

Found by scanning the homelab's worker pods for `Traceback|ERROR/|CRITICAL/|"level":"error"`.
Two hits in a three-hour window, both the same cause:

```
worker-ai       RuntimeError: Event loop is closed
                  at app/agents/summary_agent.py:892 in _store_summary
worker-default  [AI Email] Failed for run 704cca9a…: Event loop is closed
```

`worker/tasks.py::_run_async` builds a **fresh event loop for every task** and closes it
afterwards. `AsyncIOMotorClient` binds to the loop that is running when it is constructed,
and `app/db/mongo.py` caches it in a module global. So task #1 built the client on its own
loop, that loop closed, and every task after it on the same worker child got a client wired
to a dead loop.

The wrapper already understood this failure mode — it reset Redis for exactly this reason,
and disposed the Postgres engine *on the loop that owns it*, both with comments explaining
why. **Mongo was simply never added to the list.** And the second wrapper,
`worker/training_tasks.py::_run_async`, reset nothing at all.

This was not only a log line. When `_store_summary` raises, the summary agent's outer handler
returns `structured_summary=None` — so the run's intelligence page renders empty, the *same*
user-visible symptom as the LLM-unavailable bug fixed above, arriving by a completely
different route.

Fixed with a single `reset_loop_bound_clients()` in `app/db/loop_bound.py` that **both**
wrappers call. Two independently maintained lists drifting apart is what caused this, so
there is now one list. Postgres stays deliberately exempt and records why: its pool holds
live asyncpg connections, so it is disposed on its owning loop rather than dropped.

Guarded by `backend/tests/regression/test_loop_bound_clients_reset.py`: a real
two-sequential-task reproduction, both wrappers pinned, and a ratchet that fails when a new
cached client is added under `app/db/` without being wired into the reset or explicitly
exempted. The ratchet carries its own test proving it can fail — a grep-based check that
always returned true would have been vacuous. Each guard was mutation-verified.

### 2026-08-15 — Fix: the deterministic summary fallback could never run when the LLM was down

Reported from the homelab: on `/runs/{id}/intelligence`, "the data will not be present for
many of the fields". The stored summary for that run read:

```
layer1_executive_summary: "Executive summary generation failed.
                           Please refer to the detailed breakdown below."
layer2_incident_view:     {}
layer3_evidence_pack:     {"citations": []}
layer4_action_plan:       {}
```

The report directs the reader to a detailed breakdown that is three empty objects.

The operational cause was local: `ollama list` returned zero models while the deployment
was configured for `qwen2.5:7b`, so every `ainvoke` raised `model "qwen2.5:7b" not found
(404)`. (That host cannot reach `registry.ollama.ai` — the pull fails with `connection
refused` — so the model cannot be fetched there at all.)

**But the product already had the right answer for this, and could not reach it.**
`_build_fallback_structured_report` assembles all four layers from stored pipeline
evidence — pass rate, dominant failure category, real stack traces, an action plan — and
names the remedy. It has exactly one caller: the `except Exception` around
`_generate_structured_report`.

And `_generate_structured_report` could not raise. Layer 1 caught `Exception` and
substituted the placeholder above; layers 2–4 went through `_call_json_layer`, documented
as *"Returns parsed dict or error stub"*, which returns `{}`. Every failure was absorbed
one layer at a time, so **the branch written for exactly this scenario was dead in exactly
this scenario** — the repo's dead-branch class (cf. `PerformanceBaseline`/`PerfBaseline`,
`oc_namespace`/`ocp_namespace`), here as a working feature that could never execute.

Fixed by giving the generator a way to say the LLM produced nothing:

- an invoke-level failure on layer 1 (model missing, provider unreachable, bad credentials)
  now raises `SummaryLLMUnavailable` **immediately** — it will fail identically for layers
  2–4, so the run no longer burns three more doomed calls, each up to the layer timeout;
- a layer-1 *timeout* still lets layers 2–4 try, but if none of them produced content the
  same signal is raised rather than shipping four blank layers.

Either way the existing degraded path now runs, and the run gets a summary stating its real
measured numbers plus why it is degraded. The surrounding machinery was already correct and
already wired — `fallback_used=true`, confidence 70, decision reason
`deterministic_summary_fallback`, and the progress message "Summary generated in fallback
mode because the configured LLM was unavailable". None of it had ever been reachable.

Guarded by `backend/tests/regression/test_summary_fallback_is_reachable.py`, whose class is
**a degraded-mode path must be reachable from the failure it degrades for**, plus the rule
that no summary may defer to a breakdown it left empty. Each guard was mutation-verified.

Note: runs summarised before this fix keep their stored placeholder; re-running analysis on
a run regenerates it.

### 2026-08-15 — Fix: infrastructure failures went unclassified when named by exception class

Found by ingesting five failures with distinguishable causes and reading what the analysis
pipeline made of them. Four were right. One was not:

```
test_infra_dns  |  java.net.UnknownHostException: payments.internal
  -> failure_category = UNKNOWN, confidence 30
  -> "Could not determine failure cause from available data"
```

…for a failure whose cause is fully determined by its first line. Its sibling,
`java.net.ConnectException: Connection refused`, classified correctly — but only because
that message happens to *also* contain the prose "connection refused".

**The rules matched human prose but not exception class names.** `"unknown host"` (with a
space) never matches `UnknownHostException`. Class names are how these failures actually
appear in a Java, Python or .NET stack trace — which is this product's entire corpus:
JUnit, TestNG, pytest, NUnit, TRX.

Probing eight common signatures found **six** falling through to UNKNOWN:

| signature | before | after |
|---|---|---|
| `java.net.UnknownHostException` | UNKNOWN | INFRASTRUCTURE (75) |
| `java.net.ConnectException` | UNKNOWN | INFRASTRUCTURE (70) |
| `java.net.NoRouteToHostException` | UNKNOWN | INFRASTRUCTURE (75) |
| `requests.exceptions.ConnectionError` | UNKNOWN | INFRASTRUCTURE (70) |
| `socket.gaierror: Name or service not known` | UNKNOWN | INFRASTRUCTURE (75) |
| `.NET SocketException: No such host is known` | UNKNOWN | INFRASTRUCTURE (75) |

This is not a tuning question. An unclassified infrastructure failure loses its cause, its
confidence drops to the "could not determine" floor, and it stops feeding the co-failure
clustering that would otherwise group an outage into a single finding. That the codebase
already knew better elsewhere — `systemic_cluster_service` lists `"unknownhost"` in its
cause-family matcher — is what makes this an oversight rather than a scope decision.

**The other direction is guarded too.** Widening keywords is exactly how a classifier
starts calling everything infrastructure, so assertions, NPEs and locator failures are
pinned as *not* infrastructure — including a test named `test_connection_pool_sizing`
failing an assertion, which must stay a product bug. Matching the bare word "connection"
would have been the lazy fix and is now a failing test.

27 tests across three runtimes; five mutations verified in both directions.

One of those tests was wrong first time: the routing signature ended in "no route to
host", so the prose rule matched and the class-name path it claimed to test was never
exercised — it passed with the class name deleted. The signature now carries no prose
keyword.


### 2026-08-15 — Onboarding: first-run guide shows the raw ingest-API curl for CI

The empty-dashboard first-run guide's second step reads "Point your CI at the
ingest API … or upload a file from the CLI", but it only ever showed the CLI
command. CI runners that `curl` the endpoint directly — rather than installing
the CLI — had nothing to copy and had to leave the app to find the request
shape.

The step now carries a second copy-paste command: the `POST /api/v1/ingest/file`
multipart curl, mirroring the documented contract (`file`, the required
`project_id` and `build_number`, `format=auto`). The project id is spliced in
when the guide is scoped to a concrete project — exactly like the CLI command —
while `$TL_TOKEN` and the `<build>` label stay placeholders (bearer token and
per-run build label are caller-only).

Frontend-only, presentational. Regression tests cover the new row rendering,
the id splicing, the retained placeholders, verbatim clipboard copy, and the
`ingestApiCommand` helper across undefined/empty/whitespace/concrete ids.
### 2026-08-15 — Ingest: reject an empty file upload with a clear 400

`POST /api/v1/ingest/file` accepted a zero-byte upload with a 202: an empty
file auto-detects to `junit` (the format-sniffer's default) and then silently
parses to zero results in the worker. A self-hoster curling the endpoint with a
wrong or empty path (`-F file=@results.xml` where `results.xml` is empty) saw
success and never learned nothing had been ingested.

The endpoint now fails fast with `400 Uploaded file is empty` immediately after
reading the upload, before any format detection or task enqueue — mirroring the
existing `project_id` and `format` up-front guards. Non-empty uploads are
unaffected.

Regression tests cover the new `_require_nonempty_upload` guard rejecting empty
content and passing non-empty content through.
### 2026-08-15 — Fix: native TestNG reports parsed to zero tests, silently

Found by uploading one representative file per advertised ingestion format to the live
deployment and checking the counts against hand-computed ground truth. **Nine of ten
matched exactly.** TestNG produced no run at all — and the upload still answered:

```
HTTP 202  {"status": "accepted", "run_id": "…", "total_results": 0}
```

TestNG emits **two** different files. Under Maven, surefire writes JUnit-shaped
`TEST-*.xml` (`<testsuite>/<testcase>`) — which the parser handled. TestNG itself writes
`testng-results.xml`, a completely different document:

```xml
<testng-results><suite><test><class>
  <test-method status="PASS|FAIL|SKIP" name="…" duration-ms="…">
    <exception class="…"><message/><full-stacktrace/></exception>
```

`_detect_format` explicitly matches `<testng-results` and routes it to that parser, so the
product **claimed** the format — but `root.findall("testsuite")` matches nothing in such a
document, so a valid six-test report became zero results. No error, no warning: a
silent data-loss path on one of the eight formats the product advertises, and the
uploader was told it worked.

Native `testng-results.xml` is now parsed properly — status, suite, class, package,
duration, exception message and full stack trace. Two details that are easy to get wrong
and are pinned by tests:

- **Configuration methods are not tests.** `@BeforeMethod`/`@AfterSuite` arrive as
  `<test-method is-config="true">`; counting them would inflate every total a TestNG user
  sees.
- **An unrecognised status is `unknown`, not `passed`.** A status the parser does not
  know is not evidence a test passed — that would turn a parser gap into a green build.

The guard is the **class**, not TestNG: every format the detector can return must parse a
representative file to a non-zero result, *and* each fixture must be detected as the
format whose parser handles it. Detection and parsing agreeing separately is not enough —
they have to agree with each other.

28 tests, four mutations verified. Surefire parsing is separately pinned, since both
shapes now share one entry point and adding the native branch could have shadowed the path
that already worked.

#### The other nine formats

JUnit, pytest, Robot, NUnit, TRX, xUnit, Cypress, Playwright and Cucumber each parsed a
6-test fixture (3 passed / 1 failed / 1 error / 1 skipped) to exactly the expected counts.
JUnit, pytest, NUnit and Playwright additionally preserved the FAILED-vs-BROKEN
distinction their formats carry; Robot, TRX, xUnit, Cypress and Cucumber map both to
FAILED, which is a limitation of those formats rather than of the parsers.


### 2026-08-15 — Fix: ingest returned a run_id that did not exist

Found by exploratory testing: ingesting a controlled payload, then re-ingesting the same
build number and following the id the API handed back.

```
POST /api/v1/ingest  ->  202 {"run_id": "3168f853-…", "total_results": 10}
GET  /api/v1/runs/3168f853-…  ->  404 {"detail":"Test run not found"}
select count(*) from test_runs where id='3168f853-…'  ->  0
```

The row never existed. Ingest is asynchronous — 202 plus a Celery task — so the router
minted a fresh `uuid4()`, handed it to the worker and returned it. The pipeline then
deduped on `(project_id, build_number)`, **reused** the existing run, and discarded the
minted id.

**The deduplication itself is correct and stays.** Re-ingesting did not double a single
count and the project still held exactly one run — verified field by field. Only the
response was wrong.

It matters because of *when* duplicate build numbers occur: **CI retries**. The callers
most likely to hit this are the automated ones that POST results and then poll or link
`/runs/{run_id}` — and they were handed a dead id behind an HTTP 202 saying everything
had succeeded.

Both ingest paths — JSON batch and file upload — now resolve the id they will actually
land on before returning it. One indexed lookup, skipped entirely when no build number is
supplied, and scoped to the project because `build-1` exists in plenty of them and
matching across tenants would be far worse than a 404.

A narrow race remains and is written into the code rather than claimed away: two
concurrent ingests of the same *new* build number can both find nothing and mint
different ids, and the pipeline keeps one. That is rarer, self-correcting, and not worth
a lock on the ingest hot path.

Ten regression tests; four mutations verified — reverting either path to a blind
`uuid4()`, making the resolver ignore the existing run, and dropping the project scope
are each caught.

#### What else the controlled ingest checked out clean

A payload of 10 tests across two suites (4 passed / 1 failed / 1 skipped in Alpha;
2 passed / 1 failed / 1 broken in Beta) reported **every** derived number correctly:
totals, per-status counts, suite attribution and dominant suite, run status, per-test
rows, and `pass_rate` **66.67 = 6/9** — the denominator correctly excluding the skipped
test, which is the number this codebase has had wrong before. Zero errors across the
backend and all three worker pods during processing.


### 2026-08-15 — Fix: run pickers read "Unknown suite" for runs whose tests name a suite

The agents page's two run pickers labelled every option `Unknown suite · Run #12`.

`TestRun.primary_suite_name` / `suite_names` are a **denormalisation** of
`test_cases.suite_name`, maintained by `_update_run_aggregates`. Runs that never went
through that path — seeded and legacy rows — carried NULL while their test cases named
suites perfectly well. Measured on the reference deployment:

```
1241 runs   both columns populated       (the ingest path works)
 297 runs   neither populated
   4 runs   list but no primary
 298 runs   NULL primary WHILE their own test cases name a suite
```

The data was there. Only the copy was missing — one run's tests named
`AuthenticationSuite`, `AuthorizationSuite` and `PasswordSuite` between them while the run
itself claimed none.

This is the **read half of the effective-suite rule** the repo already states: any query
reading a suite must consider *both* `tc.suite_name` and `tr.primary_suite_name`. The
write half already works, so deriving at read time fixes all 298 existing rows with no
migration and no backfill sweep.

`fetch_run_suites_map` groups distinct suites for a whole page in **one** query — a picker
showing 25 runs must not fire 25 — and empty input fires none. Both read paths use it, the
list and the single-run fetch, because they feed different pages and fixing one would have
left the other reading "Unknown suite".

**A recorded label always wins.** It is the user's chosen run name — the testng.xml
`<suite name>` value — and overwriting it with a dominant test-class name is a bug this
repo has already had once. The fallback only fills what is missing, and a run whose tests
genuinely carry no suite still reports none, because "Unknown suite" is honest there.

Twelve tests. Four mutations verified: overwriting a recorded name, losing the sort that
makes the derived label deterministic, letting blank suite names through, and querying on
an empty page are each caught.

One of those tests was wrong first time and is worth recording: the determinism check ran
through the enrichment helper, which only takes `derived[0]` and therefore **could not
observe the sort at all** — it passed with the sort deleted. It now asserts against
`fetch_run_suites_map`, where the ordering is actually decided.

Three existing tests needed the new helper patched alongside the ones they already stub;
they mock `db.execute` with a fixed-length `side_effect`, so an added query exhausted it.


### 2026-08-15 — Default look-back window raised to 30 days

Two reports, one cause:

* *"the entire AI intelligence page is broken — no records for all or most projects"*
* *"the pipeline runs drop down and test suite & build drop down have no options"*

Four projects' most recent runs were 8–10 days old. The **runs endpoints defaulted to six
days** and the UI store to seven, so every caller that omitted `days` got an empty list —
including the agents page's suite+build picker, which then had no run to select, which
left the pipeline dropdown beneath it empty too. Nothing had failed. The window was just
shorter than the gap since the last run, and a product that empties out after a quiet week
reads as an outage.

The exact call that page makes, before and after:

```
GET /api/v1/runs?project_id=…&page=1&size=25            -> 0 items
GET /api/v1/runs?project_id=…&page=1&size=25&days=30    -> 11 items
```

Raised in all four places the number lives, because they have to agree:

| | was | now |
|---|---|---|
| `GET /api/v1/runs` | 6 | **30** |
| `GET /api/v1/runs/failed-ids` | 6 | **30** |
| `DEFAULT_TIME_WINDOW_DAYS` (store) | 7 | **30** |
| `/intelligence` URL-param default | 7d | **30d** |

30 is present in every page's allowed option set (`[1,7,14,30,90]`, `[1,7,14,30]`,
`[1,7,30]`), so `snapToAllowed` returns it exactly rather than rounding to a neighbour and
leaving one page on a different window from the rest.

**Existing sessions move with it.** Raising the constant alone would have fixed nothing
for anyone who had already used the app — their `7` is in localStorage. The persist
version goes to 3 and re-seeds any value equal to a *superseded default*, now kept as data
(`[7, 1]`) so a future change cannot forget the one it replaces. Honest limitation,
recorded in the code: a stored `7` from someone who deliberately chose 7 is
byte-identical to one nobody touched, so this moves both — the store records a number, not
an intent. Re-seeding is the lesser harm; the cost of moving a deliberate choice is one
click, the cost of a stale implicit default is a product that looks empty.

`0` (all time) and the 365-day ceiling are unchanged, and tests pin both — a default is
not a licence to scan forever, and someone auditing an old release still needs the escape
hatch.

Eight backend tests and eleven frontend tests, four mutations verified: reverting the API
default, reverting the store, dropping `7` from the superseded list, and letting the
intelligence page drift from the store are each caught.

One existing test needed updating and is worth noting: `MyFailuresPage` clicked `30d` as
"a window different from the default" — that assertion **became vacuous** the moment 30d
became the default. It now derives a non-default option from the constant, so the next
change to the default cannot quietly hollow it out again.


### 2026-08-15 — Fix: an empty window was reported as "All clear"

Reported as *"the entire AI intelligence page is broken — no records for all or most
projects."* The page was empty, and the reason it was empty was not the worst part.

**What was happening.** `/intelligence` lists runs through the global time window, which
defaults to 7 days. The four real projects' most recent runs are 8–10 days old, so the
window genuinely contained nothing:

```
days=7  -> 0 runs   (all four projects)
days=30 -> 11 / 5 / 11 / 11
```

Only the throwaway probe project, whose runs are 4–5 days old, still fell inside the
window — which is exactly the "all or **most** projects" in the report.

**The serious part.** With zero runs the verdict state machine had no member for "nothing
analysed", so it fell through to its initial value:

```ts
let state: VerdictState = 'all-clear'
if (failed > 0) state = 'at-risk'          // failed === 0 when total === 0
```

A project whose most recent run had **three failing tests** was therefore told *"All clear
— nothing needs investigation right now"*, beside a composite health score of **0/100**
rendered as though it had been measured. One reassures falsely; the other fabricates a
measurement. Both from an empty window, which says nothing about the code at all.

Three fixes:

- **`no-data` is now a first-class verdict state**, checked before the others, so an empty
  window can never fall through to a reassuring one. The style map is
  `Record<VerdictState, …>`, so the new member could not be added without deciding how it
  reads — the same vocabulary-subset guard used for attribution verdicts.
- **`composite` is `number | null`** and renders `—` with no `/100` when nothing was
  analysed. "Not measured" and "measured, and terrible" are different claims.
- **The empty state names the gap**: *"No runs in this window — the most recent one is 10
  days ago … There is history here, just outside the selected range."* It fetches the
  most recent run **only when the window is empty**, through a hook gated on that
  condition, so the normal path costs nothing.

Five regression tests; three mutations verified — restoring the fall-through, restoring
the fabricated `0`, and dropping the how-old message are each caught.

**Not a code regression.** The runs list, the time-window store and the page were
untouched by the recent work; the only edits to `useRuns.ts` / `runsService.ts` were
additive (a new attribution hook and service method). The four projects crossed the 7-day
boundary on 2026-08-12 and 2026-08-14 as the calendar moved. What the recent work *did*
do is make the failure visible, and the false all-clear is a real defect that had been
latent for as long as the state machine has existed.


### 2026-08-15 — Fix: the verdict's evidence was unreachable in the only place it shipped

Found by opening the running app and looking at a run.

Phase 4's verdict badge renders in run detail's test table — a dense table cell, which is
the `compact` path. `compact` meant *"badge only, no disclosure"*. So on the live
deployment the five composed signals and the advisory-policy line — the parts this
component exists to argue for — **could not be reached at all**. Only a hover tooltip
carrying the rationale survived.

Every claim made for that panel was true of code nobody could get to:

> *"When a verdict disagrees with an engineer, the useful question is which input was
> wrong. A badge alone cannot answer that, so the five signals are one click away."*

They were not one click away. They were zero clicks and unreachable.

**Nothing caught it, and the reason is familiar.** The component tests exercised the
expanded panel directly; the page test only asserted a badge appeared. Both passed. The
integration — *is the evidence reachable from the page as shipped?* — was never asserted.
That is the same shape as the flaky-score defect earlier in this cycle, where thorough
tests of the pure functions never executed the query path.

`compact` now means what it means for the sibling `KindBadgeWithEvidence` sitting beside
it in the same cell: **a denser badge whose evidence opens in a popover**. Same
information in both modes, different chrome. The popover stops click propagation, because
the row navigates on click and without that the evidence is unreadable exactly where it
ships.

Two guards, both mutation-verified:

- **Component**: the five signals and the policy line are asserted **in compact mode**,
  parameterised per signal, plus one proving the popover does not navigate its row.
  Reverting `compact` to badge-only fails 8 tests.
- **Page**: run detail renders a failing test with an attribution, clicks the badge, and
  asserts all five signals and the policy are on screen — reachable *from the page*, not
  from the component in isolation. Wiring a bare label instead of the badge fails it.


### 2026-08-15 — Fix: two endpoints were unreachable, one of them a webhook

Found by sweeping every parameterless GET on the reference deployment and reading the
failures. FastAPI matches routes in **registration order**, so a literal path segment
declared after a same-shape parameter route is dead — the parameter route wins, the
literal is handed to the parameter's validator, and the caller gets a 422 naming a field
they never sent.

Both confirmed by calling them:

```
POST /api/v1/feedback/jira-webhook
  {"loc":["path","analysis_id"],"msg":"Input should be a valid UUID,
   invalid character: found `j` at 1","input":"jira-webhook"}

GET /api/v1/test-management/cases/stale
  {"loc":["path","case_id"],"msg":"Input should be a valid UUID,
   invalid character: found `s` at 1","input":"stale"}
```

The first is a **webhook an external system posts to**. Jira had been receiving a 422 for
every delivery, and nothing in the product would ever have reported it — from the
application's point of view, nothing failed.

They arose two different ways, which is why the guard is structural rather than a
convention about one file. `jira-webhook` was shadowed **within a single router**
(declared at line 268, below its parameter route at line 127). `cases/stale` was shadowed
**across routers**, by registration order in `bootstrap.py` — `rag_generation` owns the
literal, `test_management_cases` owns `/cases/{case_id}`. A reviewer reading either file
alone would see nothing wrong.

`tests/test_architectural_route_shadowing.py` walks the **assembled application's own
routing table**, so it asks what FastAPI will actually match rather than what the source
looks like. It also confirmed the `bootstrap.py` reorder introduced no new shadow, which
was the real risk in moving a whole router.

Eleven tests: the regression itself, the two endpoints by name, eight parameterised cases
proving the detector is not vacuous, and one asserting the route table is populated at all
— because a check that silently matches nothing is exactly how the original bug survived.
Both fixes mutation-verified by reverting each and watching the test fail.


### 2026-08-14 — Fix: semantic indexing degrades where the embedder actually runs

**A correction to the previous entry.** It claimed the air-gapped path "degrades cleanly",
on the strength of `index_test_cases` and `index_incremental` both wrapping
`_get_or_create_collection()` in `try/except`. Running it on the deployment showed that
was wrong:

```
File ".../chromadb/utils/embedding_functions/onnx_mini_lm_l6_v2.py", line 199, in __call__
  self._download_model_if_not_exists()
...
OfflineModelUnavailable: AI_OFFLINE_MODE is on and no local embedding model is present … in upsert.
```

ChromaDB computes embeddings at **`upsert`**, not at collection creation. The offline
ceiling therefore raises from `collection.upsert` — *outside* the guarded block — and the
whole `reindex_search` task failed and retried instead of degrading. The ceiling worked;
the fallback around it did not.

Both indexing paths now wrap the upsert too, and return `0`.

**The cursor is deliberately not advanced on that path.** Degrading to zero while moving
the cursor forward would mean those rows are never re-examined — the corpus would end up
permanently missing exactly the records that were pending when the model went away.
Leaving the cursor put means they are indexed on a later run, once embeddings are
available again.

Six regression tests, parameterised over both indexing functions: it degrades, the cursor
does not move on failure, and the cursor *does* still move on success. Verified by
mutation — re-raising instead of degrading, and advancing the cursor on failure, are both
caught.


### 2026-08-14 — Fix: `AI_OFFLINE_MODE` now covers model weights, not just inference

`AI_OFFLINE_MODE` is documented as a hard egress ceiling. It was not one.

Observed live on the reference deployment, with `AI_OFFLINE_MODE=true` set inside the
container:

```
HTTP Request: GET https://chroma-onnx-models.s3.amazonaws.com/
  all-MiniLM-L6-v2/onnx.tar.gz "HTTP/1.1 200 OK"
```

A successful 79.3 MB egress — not a blocked attempt. In those pods `HOME` is `/tmp`, so
the cache did not survive a restart and the fetch recurred every time; the download plus
ONNX load, across several concurrent Celery forks, exceeded the worker's 1 GiB limit and
**OOM-killed it in a restart loop — 56 restarts in 18 hours**.

**The reasoning error.** Two modules argued, in comments, that because they pass no
`embedding_function` ChromaDB uses its *"bundled LOCAL"* model, never a cloud API, and
therefore **no `AI_OFFLINE_MODE` gate is required**. The first half is true — there is no
cloud *inference* on that path. The second half does not follow: the model is not
bundled, and its **weights** are fetched on first use. An offline ceiling that covers
inference but not acquisition is not a ceiling, and a comment asserting otherwise is
worse than no comment. All three occurrences are corrected — a test finds them, which is
how the third one surfaced.

**One chokepoint, not nine call sites.** Nine modules create ChromaDB collections and
every one would trigger the same download; gating each is nine chances to miss the tenth.
ChromaDB funnels them all through `_download_model_if_not_exists`, so the ceiling lives
there. Every one of those call sites already wraps ChromaDB in `try/except` and degrades,
so a clear typed error at the chokepoint produces the right fallback everywhere at once —
confirmed: both indexing paths return `0`, and `reindex_search` reports
`indexed_count: 0` rather than retrying.

The guard is installed in the API lifespan **and in every Celery prefork child** — the
child running `reindex_search` is the process that actually downloaded, so installing
only in the parent would have missed it entirely.

**This does not disable semantic features.** A model already present — baked into an
image, or side-loaded into the new `CHROMA_ONNX_MODEL_DIR` — is never blocked. Sealing a
deployment must not mean disabling what it can already serve.

Two questions from the finding, now answered with evidence rather than left open:

- **Does the air-gapped path degrade or hard-fail?** It degrades. `index_test_cases` and
  `index_incremental` both catch and return `0`, and `max_retries=2` bounds the worst
  case regardless.
- **What was the crashloop losing?** Nothing. `task_acks_late=True` means an OOM-killed
  task is redelivered — but that is also *why* the loop sustained itself: the poison task
  came straight back and killed the worker again.

22 regression tests. Seven guards verified by mutation, including one that catches the
guard silently never installing on a wrong ChromaDB module path — the same fail-open
shape as a check that cannot look and reports OK.


### 2026-08-14 — Fix: the continuous flakiness score was computing nothing

Found by running the product on the reference deployment rather than by reading it.
`recompute_flaky_scores` reported `errors=5` across **every project** and scored
nothing. The cause:

```
ImportError: cannot import name 'PerformanceBaseline' from 'app.models.postgres'
```

The class is `PerfBaseline`. Phase 2's headline feature had therefore produced **zero
scores on every project for as long as it had been deployed** — while the sweep
reported success.

Three things had to line up for that to stay invisible, and all three are ordinary
good practice:

1. The import sat **inside the function**, so nothing raised at module import — no
   linter, no `tsc`-equivalent, no type-check pass had anything to flag.
2. The only caller wraps each project in `except Exception`, deliberately, so one bad
   project cannot stop a nightly sweep. That turned a hard `ImportError` into one
   warning line in a log nobody reads.
3. The tests exercised the pure scoring functions thoroughly and **never executed the
   query path**, so the import statement never ran in CI.

Two guards, because the defect has two shapes:

- **`backend.model-imports-resolve`** (new, 23rd quality gate) — every
  `from app.models.postgres import X` names something the module actually defines,
  across all 359 such imports in `backend/app`. It **parses** the model module rather
  than importing it: importing builds the SQLAlchemy engine, which needs a
  `DATABASE_URL` the gate does not have, so an import-based check would report OK
  because it could not look. My first version did exactly that and passed against the
  known-broken file — a guard that fails open is worse than no guard. It also matches
  relative imports, so a later style change cannot open a hole in it.
- **A test that runs `score_project` against a stub session**, so the query path — and
  every function-local import in it — executes at least once in CI. Plus one that
  names the exact `PerfBaseline` / `PerformanceBaseline` confusion, so renaming either
  side breaks loudly.

Both guards were verified by restoring the misspelling and confirming each fails.


### 2026-08-14 — Feat: detection timing (roadmap Phase 6 — the last phase)

Two-tier flaky detection, and — more usefully — a measurement of what actually
limits it.

**The justification this does NOT rest on.** The plan originally leaned on "75% of
flaky tests are already flaky at their introducing commit". That claim was **refuted
1–2** in the evidence review and justifies nothing here. What survives is weaker and
heavily qualified: **85/15 among order- and implementation-dependent flaky tests in
55 Java OSS projects** — 245 flaky tests found by two detectors, skewed to order- and
implementation-dependent flakiness with async-wait, concurrency and network flakiness
under-sampled, and the authors state the results may not generalize. That is enough to
justify the *shape* (look at the new and the directly-modified first) and not enough to
justify a constant, so nothing here hard-codes 85, 15 or 150. A regression test fails if
the refuted claim is ever cited without its refutation.

**Tier 1** screens fingerprints that are new or **directly modified** — a same-subject
filename match, not a fuzzy same-directory one, which would have quietly turned a
screening tier into "most of the corpus". It runs on a beat rather than in
`finalize_run`: screening buys nothing by being synchronous, because this product
ingests results rather than executing tests, and a screening bug must not be able to
cost an ingestion. It produces a **population and a reason, never a verdict** — a test
observed once supports no conclusion, and saying so promptly is the deliverable.

**Tier 2** is the nightly whole-corpus pass, and it never stops at what tier 1 reached.
The 15% tail is flakiness introduced by changes elsewhere, which is precisely the
environment- and dependency-induced kind this product sees most.

**Cadence is time-based, and that is a measurement, not a preference.** A commit-count
cadence assumes a commit range on most runs and enough of them to count; the Phase 0
census measured **zero of four** genuine projects on the reference deployment clearing
that. Such a cadence would simply never fire here. Screening cadence is instead derived
per project from its own measured arrival rate; the whole-corpus sweep stays nightly
because it is judged against the score's own 30-day window.

**The finding worth reading is `bottleneck`.** On a thin corpus, detection is limited by
how often tests *run*, not by how often they are screened: a score needs
`MIN_OBSERVATIONS` before it is defensible, and at a run a day that is days away no
matter how fast the beat is. The endpoint computes both terms from measured numbers and
names which dominates — so cranking the cadence, which would look like progress and
deliver none, is visibly the wrong lever.

Latency is reported **only** over fingerprints whose first appearance was actually
observed. Everything predating screening is counted and excluded rather than backfilled
to a zero that would make rollout day look like instant detection forever. The payload
also discloses that a retention purge can delete the older runs which marked a
fingerprint as pre-existing, making latency look better than it is.

Observations count **distinct runs**, not per-test rows — a retry writes several rows for
one execution, and counting those would push a fingerprint past the evidence floor with
no new evidence behind it.

`GET /api/v1/metrics/detection-timing` (project-scoped, bounded read that says when it
sampled). Migration `0135`. Both beats gated per project on the `flaky_detection_timing`
flag, off until a project opts in. 51 regression tests; five guards verified by
deliberately mutating the property each protects.


### 2026-08-14 — Feat: the attribution verdict reaches the UI

Phase 4 shipped the verdict as an API and nothing else, so the flagship was invisible in the
product. Run detail now leads each failing test with what the failure appears to *be* — a raw
failure list is mostly noise, since roughly 84% of pass→fail transitions involve a flaky test.

The badge is deliberately an **annotation, not a filter**: it decorates a row that is already
displayed, and nothing is hidden, reordered or greyed out on the strength of a verdict. The page
renders identically with attribution absent, which the existing run-detail tests now prove by
mocking the hook to return nothing.

Expanding a verdict shows **all five composed signals** — the transition, the flakiness score with
its confidence, co-failure cluster membership with its cause, overlap with the changed files (named
individually), and this project's measured classifier calibration. When a verdict disagrees with an
engineer, the useful question is *which input was wrong*, and a badge alone cannot answer it.

Two properties are enforced structurally rather than by convention:

- **Every verdict renders, including `UNCERTAIN`.** The colour map is typed
  `Record<AttributionVerdict, string>`, so omitting a member is a **compile error**, not a blank
  badge — verified by deleting `UNCERTAIN` and watching `tsc` reject it. `UNCERTAIN` is a real
  answer (the backend emits it when signals disagree) and is styled muted rather than invisible.
- **The advisory policy is rendered, not buried in a tooltip.** A reader must not be able to take
  `LIKELY_FLAKY` as permission to stop looking — roughly 1 in 6 newly-flaky tests reflected a real
  production bug — and a test asserts the surface never says a failure is safe to ignore.

The verdicts hook does not poll: a verdict is composed from a nightly score, a nightly cluster and
the run's own commit range, none of which move while someone reads the page.

12 component tests.


### 2026-08-14 — Feat: test-intelligence Phase 5 — distribution and the closed loop

Phase 5 of `architecture/TEST_INTELLIGENCE_PLAN.md`, migration `0134`.

**P5-A — the verdict arrives where people already are.** The PR-comment plumbing existed; what it
delivered was a raw count (*"Failed: 7"*), which is the signal that makes people stop reading —
roughly 84% of pass→fail transitions involve a flaky test, and the surveyed adoption gap says the
verdict has to reach a reviewer *before* they are paged, not in a dashboard they must remember to
open. The sticky comment now carries one line saying what the failures appear to be:

> **Attribution:** 7 failures: 2 attributable to this change, 4 likely infrastructure (cluster
> sfc_001 — networking), 1 uncertain

The infrastructure clause names the cluster and its cause, because a reviewer can go look at one
shared dependency but cannot check the word "infra". It is **additive only** — nothing is hidden,
reordered, or marked green because the failures looked flaky, and the full failure list still
follows. If attribution fails for any reason the comment posts without the line: a comment that says
less beats one that never posts.

**P5-B — quarantine stays a loop rather than becoming a landfill.** The lifecycle already had an
SLA, auto-created defects, auto-promotion and continued execution of quarantined tests. What it
lacked was anything that stops the pile growing quietly, which is how quarantine becomes "delay with
documentation" and the tests are eventually deleted along with the feature they covered:

- **A cap with a visible warning** (`max_active_quarantined`, default 8 and per-project
  configurable — Fowler's number is a rule of thumb, not a law). It **warns and never blocks**:
  refusing to quarantine a genuinely broken test would just push the noise back into the build.
  `0` means unlimited, for teams who want the lifecycle without the ceiling.
- **An unmasking safeguard.** Quarantine can mask a real race condition, and nobody watches
  quarantined tests by construction — so a quarantined test whose failure *signature* changes is
  flagged. Compared on the normalised signature rather than the raw message, so a shifted line
  number is not mistaken for a new fault, and missing data reports "no change" rather than crying
  wolf.
- **A visible population and SLA breach count**, so growth is a number someone sees rather than
  something discovered a year later.

Nothing in either half suppresses, releases or blocks anything — both services only describe.

23 regression tests, with three properties verified by deliberate mutation: dropping a verdict
clause so failures vanish from the summary, reading a `0` cap as "none allowed" rather than
unlimited, and discarding an unrecognised verdict instead of counting it.


### 2026-08-14 — Feat: first-run guide links to the in-app setup checklist

The empty-dashboard `FirstRunGuide` now offers a **Setup checklist** action that routes to the
in-app `/getting-started` onboarding flow (the progress-tracked activation checklist:
create project → upload run → connect Jira/telemetry → view intelligence). Previously the only
"help me set up" link pointed at the external `GETTING_STARTED.md` on GitHub — dead for
air-gapped self-hosters and pinned to the upstream repo — leaving no in-app path from the
first-run guide to the guided checklist that already lives in the sidebar. Presentational,
additive change; covered by a `FirstRunGuide` regression test asserting the internal route.
### 2026-08-14 — Feat: test-intelligence Phase 4 — the attribution verdict

Phase 4 of `architecture/TEST_INTELLIGENCE_PLAN.md`, migration `0133`. The flagship, and the item
the rest of the roadmap existed to reach.

At Google roughly **84% of pass→fail transitions involve a flaky test**. A raw transition is
therefore weak evidence of a real regression, and a product that presents every new red as "new
failure" produces a false-positive flood that trains engineers to dismiss the real ones. Everything
needed to do better already existed here and had never been composed: flaky scores (Phase 2),
systemic co-failure clusters (Phase 3), commit-range attribution, a last-green baseline, and
measured per-project classifier calibration (Phase 0).

`GET /api/v1/runs/{run_id}/attribution` now returns one verdict per failing test —
`LIKELY_YOUR_CHANGE`, `LIKELY_FLAKY`, `LIKELY_INFRA` or `UNCERTAIN` — with **all five inputs and the
votes behind them**. When a verdict disagrees with an engineer the useful question is *which input
was wrong*, and that is unanswerable from a bare label.

The composition is a vote count, not a cascade of conditionals: exactly one supported verdict wins,
two or more means `UNCERTAIN`, none means `UNCERTAIN`. **Disagreement cannot be resolved to whichever
signal scored highest** — that would manufacture precisely the false confidence the verdict exists to
remove. Calibration *caps* rather than hides: where a project's classifier measures weak, or was
never measured, the verdict degrades to `UNCERTAIN` while still reporting every input.

Rationales name the specific evidence — the changed files, the cluster and its cause, the score and
its confidence — because practitioners reject generic factor-level explanations.

**Nothing suppresses anything.** Roughly 1 in 6 newly-flaky tests reflected a real production bug,
so `LIKELY_FLAKY` never reads as "safe to ignore"; the schema has no suppression column, the service
offers no such field, and tests assert both. Confidence exists to rank what a human sees, never to
hide it.

One defect found by this phase's multi-pass review and fixed before merge: the `LIKELY_YOUR_CHANGE`
signal read `run.changed_files`, a column `TestRun` does not have — the changed files live in
`run_commit_ranges.commits[].files`. `getattr` with a default swallowed it, so the overlap signal
would never have voted and that verdict could never have been emitted, silently. **This is the
second instance of this class in the roadmap** (Phase 0 read `oc_namespace` where the column is
`ocp_namespace`), so a new test now asserts every ORM attribute the composer reads exists on the
real model — guarding the class rather than the instance.

26 regression tests, with the three load-bearing properties verified by deliberate mutation.


### 2026-08-14 — Fix: the denominators behind the headline numbers (F-080, F-067)

Two long-standing findings, resolved together because they share one question: *what population is
this percentage over?* Both were recorded as decisions rather than bugs; the roadmap's attribution
verdict is built on these numbers, and a verdict resting on figures users already distrust inherits
that distrust.

**F-080 — the dashboard's suite selector was inert.** `/metrics/summary` and `/metrics/trends`
returned identical numbers for every suite, equal to the unscoped figure: 60 executions for `api`,
`regression` and `smoke` alike where the truth was 25/20/15, with the same pass rate for each. The
selector looked like it worked — the API documented the parameter and returned 200 — while
answering a different question than the one asked.

The cause was that the suite branch selected runs which *touch* the suite and then summed
**whole-run aggregate columns** (`TestRun.passed_tests`, `total_tests`, …). Those are run totals and
cannot be suite-scoped, so a run containing three suites reported all three under each of them.

It was not careless: the prior implementation INNER-JOINed `test_cases`, and live-stream runs
persist run aggregates *before* their per-test rows, so a populated suite briefly returned 0 and
blanked the dashboard. That fix traded a wrong-zero for a wrong-total. Both are avoidable — the
suite branch now counts per-test rows bucketed by **effective suite** (the house rule: run label
wins for live-stream, per-case label otherwise) and falls back to run-level aggregates **only for
runs with no per-test rows at all**, which is exactly the mid-ingest case the earlier fix was
protecting. Run count and duration stay run-level facts; there is nothing to apportion there.

**F-067 — two different pass rates, neither labelled.** The dashboard reported 81.0% while the
Summary Report reported 83.3% for the same window. Both were correct: the first counts every test
**execution**, the second counts each **unique test** once. Neither said so, leaving a user to
conclude one screen was lying.

Neither number was deleted, and neither basis was forced on the other. The unique-test basis is
what makes the Summary Report's counts agree with Coverage; the execution basis is what "how did CI
behave in this window" means. What was wrong was shipping them unlabelled — so both surfaces now
publish `basis` plus a human `basis_label`, drawn from one shared vocabulary in `metrics_service`
so the two cannot drift into differently-worded descriptions of the same thing.

Three existing tests were retargeted rather than deleted, because they pinned the *old* contract:
two asserted the suite branch reads whole-run aggregates (the F-080 bug itself), and one probed for
a `sum_total` label the rewrite no longer emits. Each now pins the guarantee instead of the
mechanism — including the property the old implementation existed to protect, that a mid-ingest
live-stream run must not vanish from a suite's numbers.

10 new regression tests, each verified to fail under deliberate mutation.


### 2026-08-14 — Feat: test-intelligence Phase 3 — systemic co-failure clusters

Phase 3 of `architecture/TEST_INTELLIGENCE_PLAN.md`, migration `0132`. Finds tests that fail
**together across runs** and names the shared cause.

Roughly 75% of flaky tests fail in co-occurring clusters rather than in isolation, with root causes
skewing to networking and unstable external dependencies. That makes per-test triage a mismatch for
the dominant failure mode: *"these 14 tests flip together and it smells like an external
dependency"* is one investigation where 14 individual flaky flags are 14. It also reframes what an
automated verdict can honestly claim — a cluster-level environmental cause is checkable, where a
per-test code-defect explanation usually is not.

**This is deliberately not `failure_clusters`.** That table is keyed `test_run_id` (a grouping
inside one run, which dies with it) and built by semantic embedding of error *messages*. Systemic
flakiness is neither: it groups **across runs** by **literal co-failure**. Two tests can co-fail on
every network blip while emitting completely different errors, so message similarity would never
join them, and a single run's grouping cannot express a pattern that only exists over time. New
entities `systemic_flake_cluster` + `systemic_flake_cluster_member`; a regression test guards the
boundary so the two cannot later be collapsed.

Method is the published one rather than something invented here: each test is the set of run IDs it
failed in, distance is Jaccard over those sets, agglomerative average linkage merges while the
closest pair is under the ceiling, and a cluster is reported only at mean silhouette ≥ 0.6.
Everything else is discarded — a weak grouping shown as a cluster sends an engineer hunting a
pattern that is not there, which is strictly worse than saying nothing. Implemented in plain Python
(no scipy/sklearn in this dependency set), so the metric, linkage, silhouette and cause
classification are exhaustively testable without a database.

**An empty result is the common correct answer.** In the source study only 10 of 22 projects
containing flaky tests contained any cluster at all, so the endpoint states that explicitly rather
than letting an empty list read as a bug, and `cause_family` may legitimately be `unknown` — a
cluster is still actionable without a named cause, and inventing one would be worse than admitting
we cannot tell.

New: `GET /api/v1/analytics/systemic-clusters` and a nightly `recompute_systemic_clusters` beat at
06:40 UTC, sequenced after scoring so a cluster and its members' scores describe the same window.

One defect found by this phase's multi-pass review and fixed before merge: `build_clusters` was
unbounded, and its worst case is exactly the scenario the feature targets. Average linkage
recomputes every pairwise cluster distance per merge, so cost grows roughly cubically once merges
are admissible — measured at 0.37s for 200 co-failing fingerprints, 6.35s for 400 and **85s for
800**. A wide outage makes hundreds of tests co-fail, every pair becomes mergeable, and the nightly
sweep would have stalled on the project that most needed the answer. Now capped at 400, keeping the
most-failing tests, with truncation logged rather than silently clustering a subset as whole; 3000
fingerprints now completes in about six seconds.

27 regression tests, with the two anti-fabrication properties verified by deliberate mutation.


### 2026-08-14 — Feat: test-intelligence Phase 2 — signal quality

Phase 2 of `architecture/TEST_INTELLIGENCE_PLAN.md`, migration `0131`. Two halves.

**P2-B — a continuous, decomposable flakiness score, without the Bayesian layer.** A binary
flaky/not-flaky label answers "is it?", which is the wrong question — all real tests are flaky to
some degree, so the useful question is *how* flaky and *on what evidence*. `flaky_score` stores a
bounded 0–1 composite fused from four signals (result volatility, retry rate, duration variance,
environment instability) **together with every component and the weights used**, so the number can
be decomposed and recomputed. A score nobody can audit is one users are asked to trust on faith.

Three of the four signals already existed; duration variance reuses the Welford statistics
`perf_baselines` maintains rather than becoming a second source of truth, and uses a coefficient of
variation so a 10-second test varying by a second is not ranked alongside a 100ms test varying by
a second.

**The posterior was descoped by the Phase 0 gate, not skipped.** That census measured every genuine
project at 12–15 fingerprints with a median of 5–12 runs, far under what a moving-window posterior
needs — over that much data it mostly reports its prior back. The four signals degrade honestly
instead. Below five observations **no score is emitted at all** (not a small score, not a hedged
one), and `confidence` is a separate band derived from evidence volume alone: a test seen 5 times
and one seen 500 can both produce 0.5, and collapsing that is how a thin-history guess starts
looking like a measurement.

**P2-A — per-project calibration decides how much authority a verdict carries.** Measured
classifier specificity swings from 100% to no-better-than-random across projects, so
`flaky_suppression_gate` reads the Phase 0 calibration and returns `hint` (weak *or unmeasured* —
an unknown classifier is never defaulted to trusted) or `advisory`.

**It never returns "may act".** Per decision D2, `ALLOW_SUPPRESSION` is a module constant pinned by
a test rather than a setting, and `may_suppress` is double-guarded so flipping that constant alone
still cannot unlock suppression. The evidence: roughly 1 in 6 newly-flaky tests reflected a real
production bug. A wrongly-shown verdict costs a minute; a wrongly-suppressed failure ships the bug.

New: `GET /api/v1/analytics/flaky-scores` (scores + components + the project's suppression
decision) and a nightly `recompute_flaky_scores` beat at 06:10 UTC, after calibration.

Three defects found by this phase's multi-pass review and fixed before merge: `score_project`
loaded a project's whole 30-day window into memory before applying its limit (now capped at 200k
rows, read newest-first so the cap drops the oldest, with truncation logged rather than silently
scoring a partial window as whole); `store_scores` never persisted `window_days`, so a 90-day score
would have sat in the table claiming the 30-day default — a provenance lie about the number beside
it; and `test_name` was never populated.

38 regression tests, each verified to fail under deliberate mutation of the property it guards.

### 2026-08-14 — Fix: the commit-range collector now says why it collected nothing

A flaky test in the product built to detect flaky tests. `test_commit_cap_keeps_newest_hundred_
oldest_first` failed on PR #583 for both the `[sdk]` and `[cli]` parametrisations with
`assert None is not None`, then passed on a plain re-run of the same job with no code change —
costing a manual CI re-run on an unrelated PR and yielding no information when it did.

It yielded no information because the collector could not produce any. Every git invocation in
`commit_range.py` funnelled into a bare `Optional[str]`, so **"git says that ref does not exist"
and "git never answered" arrived at the caller identically**. `_resolve_ref` then reported a
transient git failure as *the user's base is unresolvable*, `collect_commit_range` took the
deliberate do-not-guess branch, and the cause was gone. Three distinct states were being collapsed
into one silent `None`.

The fix separates them. `_run_git` now returns `GIT_OK` / `GIT_REFUSED` / `GIT_UNAVAILABLE` with
git's own exit code and stderr tail attached — where exit 1 is git's conventional "no", a higher
code is a `fatal:` it could not complete, and a negative code is a process killed before it could
answer (the OOM-killer case). A new `diagnose_commit_range()` returns the range **and** why it is
what it is; `collect_commit_range()` is now a one-line wrapper over it and is unchanged for callers.
A git invocation that stops answering *after* git has demonstrably worked is logged at WARNING
rather than DEBUG — that combination is an anomaly, not a normal empty result — while genuinely
empty runs stay quiet, because a client SDK must not shout about a run with nothing to report.

Not a retry, and not a proven root cause: 300 full runs of the CI command and 400 collector
iterations under 4× CPU oversubscription on Linux failed to reproduce the original failure, so the
specific git call that broke on that runner is still unknown. What is fixed is that it can no
longer break *invisibly* — the next occurrence names itself in the assertion message.

The fixture that feeds the test was also rebuilt. It span 211 git processes (`git commit
--allow-empty` × 105, each followed by a `rev-parse`) to produce a history longer than
`MAX_COMMITS`, which was most of the module's runtime and a lot of environment exposure for one cap
assertion; a single `git fast-import` builds the identical 106-commit history 65× faster in two
processes. Its timestamps are now fixed and strictly increasing, which also retires a latent
hazard: `git log` walks a date-ordered queue, and a test asserting the exact identity of the newest
100 commits should not depend on 106 commits landing in a readable order inside the same second.

### 2026-08-14 — Docs: Phase 0 gate reading — the Bayesian scorer is descoped

Ran the Phase 0 readiness census against the live homelab database and recorded the verdict in
`architecture/TEST_INTELLIGENCE_PLAN.md`. **No project has the history to support a windowed
posterior, so Phase 2's Bayesian layer is deferred** and the scorer descopes to the signals that
work at low volume (result volatility, retry rate, duration variance, environment consistency).

Synthetic projects were excluded: 58 projects exist but 51 are `ZZ …` load-test and probe artefacts
whose run counts are bulk-ingest artefacts rather than CI cadence. Of the four genuine projects,
**none** clears the bar, and not narrowly — the thresholds want ≥25 fingerprints with ≥20 runs each,
while these have 12–15 fingerprints in total and a median of 5–12 runs per fingerprint. A posterior
computed there would be dominated by its prior: a number that looks like a measurement and is
mostly an assumption.

This answers the research's open question #4 — *do hyperscale magnitudes hold for small self-hosted
teams?* — with data rather than assumption, which is the whole reason Phase 0 ran first. The
limits are recorded with it: one deployment, and a developer's homelab rather than a production
tenant, so it is evidence about the plausible low end and not proof about every user. The gate is a
measurement, not a permanent ruling — re-running the census on a real high-cadence corpus can
reopen it.


### 2026-08-14 — Feat: test-intelligence Phase 1 — surfaces that show evidence instead of erasing it

Phase 1 of `architecture/TEST_INTELLIGENCE_PLAN.md`. No migration; three surfaces that depend on
nothing above them in the roadmap.

**P1-A — per-test history timeline.** `CanonicalDetailPage` had a paginated table of run rows; a
table of ticks is not the "bigger picture" practitioners asked for. The page now leads with a
timeline: one cell per run, oldest to newest, annotated with build, branch, environment, duration
and retry evidence, plus flip/outcome/environment counts. The table stays below for per-run detail
a strip cannot carry. The API reversal lives in the component — newest-first is right for every
other consumer, but a flip pattern is only legible if time runs one way.

The endpoint had to grow the run context to make this possible: `list_runs_for_canonical` already
joined `TestRun` purely to order by its timestamp and then discarded it, so environment/branch/build
were one query away and never exposed. Environment is *resolved*, not read raw — a run that never
recorded one reports `null` with `environment_source: "unknown"` rather than being folded into a
synthetic default group.

**P1-B — retry transparency.** `retry_count` and `is_flaky_run` have been persisted at ingest and
shown nowhere, so a test that only went green on its third attempt rendered identically to one that
passed first time. Run detail now marks those rows "attempt N". A retry is evidence about
stability, not a way to make the build green. The attempt calculation lives in
`utils/retryEvidence.ts` so every surface agrees what "attempt" means, and it floors at 2 when a
producer sets only the flaky flag without a count.

**P1-C — flake load, explicitly not a burndown.** New `GET /api/v1/analytics/flake-load`: the share
of recent runs carrying at least one retried test. Flaky-test insertion rate tracks the fix rate
even under sustained investment, so a "debt remaining" chart trending to zero is a promise that
cannot be kept — it sits near a floor forever and teaches users the tool is broken rather than that
the target was wrong. A load has no implied zero and is read against a budget the team picks. The
payload states that framing, and a test forbids "remaining"/"burndown"/"backlog" wording in it.
Below 10 runs it reports `null` plus a reason instead of a share computed from three runs.

The audit half of P1-C found **nothing to correct**: the existing weekly flaky-debt review is
already a worklist (quarantined / stale / ready-to-promote / newly-flaky), not a count trending to
zero.

Two issues found by this phase's multi-pass review and fixed before merge: the timeline strip was
unbounded (now capped at 120 cells, keeping the newest, and it *says* "last 120 of 200 runs" rather
than truncating silently), and a `scoped is None` guard raised an unreachable 422 whose message
described a case that lands elsewhere (kept — dropping it would let `None` reach a project-scoped
query and widen it to every project — but corrected and documented).

26 regression tests across backend and frontend, each verified to fail under deliberate mutation of
the property it guards.


### 2026-08-14 — Feat: test-intelligence Phase 0 — measure before building

Phase 0 of `architecture/TEST_INTELLIGENCE_PLAN.md`. **No user-visible behaviour changes**: this
phase exists to answer, on real data, whether the later phases are worth building — and to close
the one prerequisite gap they all depend on. Migrations `0129` and `0130`.

**P0-1 — the environment dimension.** `test_runs` recorded branch, commit, CI provider and the
OpenShift fields but nothing answering "which environment did this run execute against?". Two
downstream items need it: the per-test history timeline (the practitioner ask is explicitly a
history annotated with environment metadata) and the flakiness score, which fuses *environment
consistency* as one of four signals. Added as an optional field on both ingest paths — omitting it
records "not known".

The load-bearing decision is in `services/run_environment.py`: an unrecorded environment resolves
to `None`, **never** to a shared literal like `"default"`. Coalescing would put thousands of
unrelated historical runs in one synthetic group, and the environment-consistency signal would then
read "perfectly consistent" for a corpus that simply never recorded it — a fabricated consistency,
the same class of mistake as a fabricated confidence. Where nothing was recorded we derive a
best-effort key from the platform fields and *mark it derived* so consumers can weight it down.
Feature branches collapse to one `topic` class rather than one group per branch, which would
otherwise manufacture thousands of trivially "inconsistent" environments.

**P0-2 — classifier calibration.** The product already classifies failures partly by matching error
signatures. Published measurement of that exact technique (ICST 2024, 230,439 failures, 22 projects)
found specificity ranging from 100% to no better than random *across projects*, driven by whether
failures carry distinctive exception types. So `flaky_classifier_calibration` backtests our own
classifier per project and stores the measured specificity. Below a 30-sample floor it stores
`NULL` plus a reason — never a number. Runs on a nightly beat (05:45 UTC), since the backtest is
retrospective and must not sit on a request path. Nothing consumes the result yet; Phase 0 measures,
a later phase gates on it.

**P0-3 — readiness census.** `GET /api/v1/metrics/flaky-readiness` answers whether a project has the
history to support a windowed posterior at all. The evidence behind probabilistic scoring comes from
hyperscale monorepos; a self-hosted team's corpus may be an order of magnitude thinner, and a
fingerprint seen three times can only produce a number that is mostly prior. Reports
`available: false` with a concrete reason below threshold, mirroring `/metrics/tia-readiness`, and
publishes the thresholds it judged against so the verdict can be argued with.

Three defects found by the multi-pass review of this phase and fixed before merge:

- The OpenShift derivation read `oc_namespace`, but the column is **`ocp_namespace`**. `getattr`
  with a default swallowed the typo, so the branch was dead code that failed silently on every
  OpenShift run.
- `environment` was accepted by the pipeline but **no caller passed it** — the column would have
  shipped and stayed NULL forever. Now threaded through the wire schema and every ingestion hop,
  with a test that walks the whole seam.
- The readiness census counted test-case **rows**, not distinct runs, so a parameterised or retried
  test inflated the count and a shallow project could clear a gate named "runs per fingerprint" —
  fabricated readiness inside the gate built to prevent it.

28 regression tests pin the honesty properties rather than happy paths, and each was verified to
fail under deliberate mutation of the property it guards.

### 2026-08-14 — Docs: phased implementation plan for the test-intelligence roadmap

`architecture/TEST_INTELLIGENCE_PLAN.md` turns the verified findings in
`research/TEST_INTELLIGENCE_RESEARCH.md` into a dependency-ordered, seven-phase plan. Plan only —
no behaviour, schema or code is changed by this commit.

Two structural findings from auditing the current tree shape it:

- **`FailureCluster` cannot carry systemic clustering.** It is keyed `test_run_id` and built by
  semantic embedding of error messages, whereas the research's systemic-flakiness finding clusters
  *across* runs by *literal co-failure*. Different scope and different mechanism, so the plan adds
  a new entity rather than overloading it. This also sharpens the calibration work: today's
  clustering keys on error-message semantics, precisely the mechanism whose specificity was
  measured swinging from 100% to no-better-than-random across projects.
- **There is no environment dimension** on `test_runs`, but environment consistency is load-bearing
  for both the flakiness score and the per-test timeline — so it becomes a Phase 0 prerequisite.

The inventory also found two things already built that the research assumed missing: `perf_baselines`
already maintains Welford duration statistics per (project, fingerprint) — the duration-variance
signal is free reuse — and the quarantine policy already carries `sla_days`, `auto_create_defect`
and `auto_promote`, narrowing that item to ownership routing, a cap, and the unmasking safeguard.

Phase 0 is measurement that gates the rest: it censuses whether projects at this scale have the
history for a windowed posterior at all, rather than assuming hyperscale magnitudes transfer.
Six decisions are recorded as open and assigned to the phase that needs them — most consequentially
whether a flaky verdict may ever auto-suppress a failure (recommended: never, since ~1 in 6
newly-flaky tests reflected a real bug).

The plan also carries the research's corrections forward as explicit non-goals: do not design to the
81% detection figure, and do not cite the refuted 75%-at-birth claim.

### 2026-08-14 — Feat(cli): `testlookup doctor` self-host setup diagnosis

Added a `testlookup doctor` command that gives a fresh self-hoster a single, ordered verdict on
their local setup instead of discovering each failure one command at a time. It runs three checks
in order — **profile** (is a server URL resolved from the saved profile or `TESTLOOKUP_URL`?),
**server** (is that URL reachable, and which build is it? via probe-free `GET /health/version`),
and **auth** (are this profile's credentials accepted? via an authenticated `GET /api/v1/projects`
probe) — short-circuiting downstream checks once an earlier one makes them moot. The command exits
non-zero only on a hard failure (no URL, unreachable server, or rejected credentials), so it
doubles as a CI preflight; a reachable-but-not-yet-authenticated profile is a `warn` and exits 0.
Supports `--output json` for machine consumption. Reuses the existing `client.request` transport
and the friendly `map_connection_error` hints. Regression tests cover the full check ladder and the
exit-code contract.

### 2026-08-13 — Feat: Phase 3 multi-agent test intelligence

Governed multi-agent workflow and capability planning; evidence-authority snapshots with
sanitization, provenance, critic verification, replay and retention; recursive agentic runtime
projections with cluster-child investigations, durable dispatch/outbox, scoped budgets,
cancellation and stale recovery; Decision Intelligence UI, report versioning, structured feedback,
action ledger, evaluation cycles, memory lifecycle. Migrations `0119` and `0120`.

CI additions: a `postgres-integration` job (migrations applied and rolled back against a clean
database) that `build-images` now depends on, and a late-ack broker redelivery check.

Fixes applied while integrating against latest `main`:

- **`_allocate_cost` could allocate more than the aggregate budget.** Whole micro-dollars were
  allocated exactly in integer space, but dividing each share back to a float reintroduced
  representation error, so the per-stage parts summed to marginally *more* than the whole
  (`0.100001` → `0.10000100000000003`). A budget whose parts exceed their aggregate is an
  over-spend, so any drift is now shaved off the largest share.
- **`change_ownership_agent` emitted an uncontracted analytic payload**, violating the
  `agents.contract-metadata` ratchet. Its single `_result` choke point now stamps
  `ChangeOwnershipAgentOutput` via `validate_agent_contract`, so all eight return paths are
  covered at once.
- **Checkpoint restore was silently dead whenever logging was configured.** `_load_checkpoint`
  logged its success line with stdlib-style positional `%s` args, but the module binds a
  `structlog` logger whose `BoundLogger.info` signature is `(event, **kw)` — so the call raised
  `TypeError`, the function's blanket `except Exception` swallowed it, and every restore returned
  `None` after logging `checkpoint_load_failed`. Unconfigured structlog hands back a lazy proxy
  that tolerates positional args, which is why this only surfaced in the test suite when some
  earlier test had already imported `app.main` — a real production defect wearing the costume of a
  test-ordering flake. The `backend.structlog-positional-args` guard missed it because its regex is
  line-based and this call spans four lines; an AST sweep finds **83 more multi-line instances of
  the same class** (mostly `worker/tasks.py`), all pre-existing on `main` and left for a scoped
  follow-up. The checkpoint test now configures `structlog.BoundLogger` itself, so the guard is
  deterministic instead of order-dependent.
- **`worker-children` existed only in the dev compose file.** Released stacks would have started
  without a consumer for the `agent_children` queue, so cluster-child investigations would have
  queued forever. Added to `docker-compose.release.yml` with its own low concurrency.
- `resolve_authorized_test_case` (new authorization on the analyze endpoints) is now modelled by
  the analyze regression tests, which previously stubbed only the analysis lookup.
- Test expectations updated to pin — rather than bypass — three deliberate behaviour changes: the
  `BudgetedLLM` wrapper (the single boundary enforcing pipeline budget and prompt redaction), the
  `react_triage` v3 prompt (citations are server-derived; the model must return an empty
  `evidence_references`), and the outbox relay's two-phase commit (claim committed before publish
  so a crash cannot double-publish).

### 2026-08-13 — Productionization: the CLI now explains an unreachable server instead of dumping a traceback

The CLI is the first thing a new self-hoster runs (`testlookup upload …`, `testlookup health`).
Its shared HTTP client only mapped *HTTP-status* errors (401/403/404/422/timeout) to friendly
messages via `map_http_error`. But a server that is **not up yet**, a mistyped URL, or a DNS
failure raises an `httpx.ConnectError`/timeout *before any response exists* — so nothing mapped it,
and the command surfaced a bare `[Errno 111] Connection refused` (often with an empty message and a
generic exit 1) at the highest-friction moment of adoption.

Added `errors.map_connection_error(exc, base_url)`, which turns any `httpx.RequestError` into an
actionable `CLIError`: connection failures say *"Cannot reach the TestLookup server at <url>. Is it
running? Check the URL with 'testlookup auth login --url <url>' (or the TESTLOOKUP_URL env var)."*,
while client-side timeouts keep the dedicated `EXIT_TIMEOUT` code (matching the existing 408/504
mapping). It is wired into the shared `client.request` / `client.download` paths and the upload
command's own httpx call, so every command benefits. Regression tests assert connect errors and
timeouts become the right `CLIError` (with the base URL and the correct exit code), that the upload
path is covered, and — critically — that real HTTP-status errors still flow through
`map_http_error` unchanged (the new transport guard does not swallow a 404).
### 2026-08-13 — Feat: cheap `GET /health/version` build-identity probe

Added a dependency-free `GET /health/version` endpoint that returns the running
image's `version`, `build` provenance (git `revision` + `built_at`), `env`, and
`uptime_seconds`. It answers "which commit + build is this pod running?" fast
enough for a post-deploy CD smoke test or an uptime monitor.

The `build` block was already surfaced on `GET /health/details`, but that
endpoint probes six services (Postgres, Mongo, Redis, MinIO, Ollama, ChromaDB)
and is documented as "not intended for K8s probes (too slow)"; root `GET /`
returns the version but not the revision or build date, so neither could verify
a *specific* build cheaply. The new endpoint runs no probes — a regression test
asserts every dependency probe raises if invoked from this path — so a
self-host operator gets a fast rollout-verification check without shelling into
the pod.
### 2026-08-12 — Productionization: the first-run guide now emits a runnable upload command

The empty-dashboard `FirstRunGuide` (shown on a fresh install with no runs) walks a new self-hoster
through their first ingest. Step 2's copy-paste CLI command was hard-coded with a `<project-id>`
placeholder — so the "copy" button handed the user a command that fails until they hunt down and
paste the project UUID themselves, right at the highest-friction moment of adoption.

When the dashboard is scoped to a concrete project, the guide now splices that project's real id
into the command, so the copy button yields an immediately-runnable
`testlookup upload file results.xml -p <real-uuid> -b <build>` (the CLI's `-p` flag takes the
project **ID** — see `cli/testlookup_cli/commands/upload.py`). In "All Projects" mode, where there
is no single project, the `<project-id>` placeholder is preserved; `-b <build>` stays a placeholder
in both cases since the build label is per-run and only the user knows it.

The step-building logic moved to a co-located pure helper (`firstRunSteps.ts`) so the substitution
is unit-testable in isolation and the component file keeps exporting only its component (no new
`react-refresh/only-export-components` lint warning). Regression tests assert the id is spliced in
when scoped, the placeholder survives for undefined / empty / whitespace-only ids, and the copied
string matches what is rendered.
### 2026-08-13 — Docs: test-intelligence research corpus (`research/`)

Adds `research/` — the evidence base for the feature-improvement roadmap. A deep-research pass
over practitioner surveys, hyperscale engineering case studies (Google, Atlassian, Meta, GitHub,
Dropbox, Facebook) and peer-reviewed work on flaky tests, failure triage and predictive test
selection: **25 sources, 124 extracted claims, 25 put to a 3-vote adversarial panel — 18 survived,
7 were killed, merged into 11 findings.**

Tracked here rather than under `docs/` (gitignored, local-only) because the roadmap cites it and
the extraction phase is expensive to reproduce: the generating workflow's cache is session-bound,
so `claims-corpus.md` + `claims-raw.json` + `synthesis.json` are the permanent record. Every claim
carries its source, verbatim quote and verification status.

Findings that bear on existing subsystems:

- **Fingerprint clustering does not transfer across projects.** ICST 2024 (230,439 failures, 22
  projects) measured per-project specificity from 100% down to no better than random, driven by
  whether failures carry distinctive exception types. Per-project calibration and a measured
  specificity gate on auto-suppression are now roadmap items.
- **Most flaky failures are systemic** — 75% fail in co-occurring clusters, dominated by networking
  and external-dependency causes (EASE 2025) — so cluster-level triage is a better unit than
  per-test, and a more verifiable AI output than per-test defect explanation.
- **A pass→fail transition is a weak regression signal** (~84% of Google's involve a flaky test),
  but ~1 in 6 newly-flaky tests reflected a real bug — so flaky must never imply auto-suppress,
  which constrains how the release gate may discount flaky failures.
- Independent support for the **Fixer's budgeted, draft-PR-gated shadow design**: an enterprise
  agentic-repair study observed agents reaching green by weakening assertions and deleting tests.

The report states verification tier per finding and lists the 7 refuted claims explicitly — five of
which had appeared in earlier drafts — so retracted figures are not silently reused.

### 2026-08-11 — Chore: close the design-audit palette-token ESLint ratchet (`no-restricted-syntax` → error)

The `no-restricted-syntax` rule in `frontend/eslint.config.js` — which flags raw Tailwind palette
classes (`text-emerald-400`, `bg-red-900/40`, `border-amber-700/60`, …) that bypass the per-theme
CSS-token system (`--status-*`, `--gate-*`, `--color-*`) and are each a light-theme legibility
defect — was the last frontend lint rule still at `warn`. Every raw palette class in `src/` had
already been mapped, page by page, to its semantic token, leaving zero UI sites.

The only remaining literal matches were assertion guards in two test files (`RightRail.test.tsx`,
`VerdictBand.test.tsx`) that name the raw classes to prove they are **absent**, not to render them.
Those now carry scoped `eslint-disable`s with a one-line justification, mirroring the existing
avatar-color-swatch and test-only-`any` exemptions. With those exempt, the rule is promoted to
`error`, so a token-bypassing palette class can no longer be reintroduced in app code. A
source-text promotion regression test (`paletteRatchet.promotion.test.ts`) locks the severity in,
matching the sibling `no-explicit-any` / `no-non-null-assertion` ratchet guards.

### 2026-08-12 — Deps: bump prettier 3.8.1 → 3.9.6 (`^3.9.4`), superseding Dependabot #335

The dev-only formatter `prettier` was pinned at `3.8.1` in the frontend lockfile. Dependabot #335
(3.8.1 → 3.9.4) had sat open and conflicted (`mergeable_state: dirty`) since July with automatic
rebases disabled after 30 days, so it could not land on its own. Superseded with a clean bump from
the current `main`: the `frontend/package.json` spec moves from `^3.4.2` to `^3.9.4` and the
lockfile resolves to `3.9.6` (the newest release satisfying the range, which subsumes the 3.9.4
target). prettier 3.9 brought parser upgrades and small formatting refinements (e.g. Angular
`@content (name)` spacing) — none of which touch this TypeScript/React codebase's output.

prettier is a **manual** convenience only (`npm run format` → `prettier --write src`); it is not
part of the CI lint/type-check/build gate and there is no `prettier --check`, so the bump has no
behavioural surface to unit-test and no committed-formatting drift to reconcile. Validated on the
bumped lockfile: `npm run lint` (0 errors), `npm run type-check`, and `npm run build` all green —
the same validation path used for the prior dev-tooling bumps (tailwindcss, vitest). The lockfile
diff is limited to prettier's own `version`/`resolved`/`integrity` entries.

### 2026-08-11 — Fix: the run header did not account for every test in the run

Found during UAT of the ingest journey. A JUnit report of 6 tests (2 pass / 2 fail / 1 `<error>`
→ BROKEN / 1 skip) uploaded through the UI stored **perfectly** — but the run header rendered:

```
2 passed   2 failed   1 skipped   / 6 total
```

Five of six accounted for. The infrastructure error was invisible on the primary screen a user
reads about a run, and the arithmetic visibly did not close — which undermines trust in every
other number on the page.

`RunDetailPage` rendered exactly four spans (passed / failed / skipped / total). It is the same
vocabulary-subset class as the Integration Health trends bug and the "New failures (24h)" KPI
fixed earlier, this time in the UI — and it also missed `unknown_tests`, which migration 0118 had
already added to the model and API.

`broken` and `unrecognised` now render when non-zero, so the buckets reconcile with
`total_tests`, while an all-green run stays uncluttered. `skipped` also stops borrowing the
`--status-broken` colour token, which would have read as two broken counts side by side.

The regression tests assert the **invariant, not the markup**: the rendered per-status buckets
must sum to `total_tests`. A status that gains a data column but not a header bucket fails there.

### 2026-08-11 — Fix: project forms were unusable with a screen reader

Found during UAT of the onboarding journey — the first real task a new user performs.

Every field in the **New Project** dialog rendered a visible `<label>` ("Project Name *",
"Slug *", …) that was **not associated with its control**. Measured live on the deployment, all
five create-dialog inputs reported:

```
hasIdLabel: false, hasWrappingLabel: false, ariaLabel: null, ariaLabelledBy: null
```

Only a placeholder. A screen reader announces "edit text, blank" for the required Project Name
field, and because placeholders vanish on first keystroke, even a sighted user loses the field
name while typing. That is WCAG 2.1 **1.3.1 Info and Relationships** and **3.3.2 Labels or
Instructions**, on the primary onboarding form.

All **15** labels across the create and edit dialogs are now wired with `htmlFor`/`id`. No visual
or behavioural change — the same markup, correctly associated.

The regression tests query by accessible name only (`getByLabelText` resolves through the same
accessibility tree a screen reader uses), so they fail on unassociated markup rather than
asserting on implementation details.

### 2026-08-11 — Fix: every suite-filtered dashboard request returned HTTP 500 (regression from #492)

`GET /api/v1/metrics/summary?project_id=…&suite_name=api` → **500**. `suite_name` is a
documented query param and the Overview page's suite filter goes through it, so picking a suite
took the whole dashboard down.

`_period_stats` has two returns. The suite-scoped early return (inside `if suite_name:`) returned
three keys; the unscoped return grew a fourth, `total_executions`, and the caller reads it
unconditionally:

```python
total_executions = cur["total_executions"]        # KeyError on the suite path
```

**This was self-inflicted.** #492 ("Total executions KPI counted runs, not executions",
2026-08-08) added the key to the unscoped branch and to the caller but not to the suite branch.
It has been broken on the deployment since that date. It survived because nothing exercised
`_period_stats` with a suite name — the endpoint's tests never combine `project_id` with
`suite_name`, and no exploratory pass had crossed those two filters either.

The regression test guards the **class**: the two returns must stay key-for-key identical, and
the caller may not read a key neither branch returns. The real defect is a two-branch function
whose branches drifted, not the one missing key.

### 2026-08-11 — Fix: the "New failures (24h)" KPI did not count BROKEN

`metrics_service` states the rule in its own comment, three lines from the offending code —
*"a 'failure' is FAILED **or** BROKEN — the canonical failed set used everywhere else"* — and
applies it in `_evaluated` and in the trend query, whose comment describes this exact bug being
fixed there: *"a day whose only failures were BROKEN charted as a flat 100%"*.
`new_failures_24h` was the last holdout.

Reproduced on a throwaway project holding one PASSED, one FAILED and one BROKEN test (the BROKEN
one ingested from a JUnit `<error>` element):

```
new_failures_24h : 1        <- truth is 2
avg_pass_rate_7d : 33.3     <- 1/3, so BROKEN *is* counted as a non-pass
```

One payload, two definitions of failure. A day whose only failures were infrastructure errors
showed zero new failures on the dashboard while the pass rate beside it fell.

The fix imports `flaky_signals._FAILED_STATUSES` rather than writing a fifth copy of the
constant — it already exists in four modules (`flaky_signals`, `analysis_report_service`,
`digest_content_service`, `agents.ingestion_agent`). They agree today; a fifth copy is how they
stop agreeing. **The four existing duplicates are left alone and flagged** — consolidating them
is a refactor across four modules with no bug behind it today.

### 2026-08-11 — Fix: Test Management's suite filter was the only case-sensitive one

Third find in the same class: a canonical rule with a hand-rolled copy that drifted.
`test_management_service` filtered with a bare `suite_name == suite_name`, bypassing both
`normalize_suite_name` and `analytics_service._effective_suite_sql()`. Measured live — same
project, same suite, three surfaces:

| endpoint | `suite_name=api` | `suite_name=API` |
|---|---|---|
| `analytics/coverage` | 5 | **5** |
| `runs/compare` | 5 | **5** |
| `test-management/cases` | 5 | **0** |

Two of three answer the same question the same way for either spelling; this one silently
returned nothing. The UI populates its filter from the data so it sends the exact case, but the
API is public — a CLI, SDK or MCP caller passing `"API"` got an empty list and no error.

The automation list additionally now honours the effective-suite rule `backend/CLAUDE.md` states
as a hard convention: for a `live_stream` run the SDK sends the suite once at session-create, so
it lands on `TestRun.primary_suite_name` while per-event `TestCase.suite_name` stays NULL, and a
bare per-row filter returns nothing for those runs. The run-level arm is restricted to
`live_stream` — a multi-`<testsuite>` upload has an authoritative per-row value that must win,
the shape #559 gave `run_compare_service` after it was found returning other suites' tests.

**Stated honestly**: the live-stream half is *not* reproducible on the homelab — no live_stream
run there has both a NULL per-row suite and a `primary_suite_name`, so the fallback has nothing
to recover. It is fixed because it is the documented rule and the query already joins `TestRun`,
not because a probe showed it failing. The case-sensitivity half was reproduced.

### 2026-08-11 — Fix: run compare scoped to the wrong tests and used the wrong pass-rate base

Two shared rules had hand-rolled copies in `run_compare_service` that no longer agreed with
them. Both reproduced on live data (Checkout Service runs 101 and 102).

**Suite scoping over-matched.** `_load_test_rows` applied its run-level fallback
unconditionally, with a comment asserting *"if this run's `primary_suite_name` matches, ALL
test cases for the run belong to that suite"*. That is false for a file upload carrying several
`<testsuite>` blocks. Run 101 (`trigger_source=api`, `ingestion_source=upload`,
`primary_suite_name='api'`, per-row suites `api`/`regression`/`smoke`) scoped to `api` returned
**all 12 rows instead of 5** — the diff for suite "api" listed `test_inventory_sync` (a FAILED
`regression` test) and `test_discount_stacking` (also `regression`). `smoke` (3) and
`regression` (4) scoped correctly, so the same page behaved differently per suite.

The fallback is now keyed to `trigger_source = 'live_stream'`, mirroring
`analytics_service._effective_suite_sql()` — the canonical answer to "which suite does this test
belong to". Nothing is lost: on the deployment every NULL/blank per-row suite belongs to a
live_stream run (8 rows), while 1,493 api+upload and api+sdk runs have none.

**The pass rate used a different denominator than the rest of the product.** `_summary_dict`
returns the stored `run.pass_rate` when unscoped (canonical: `passed / (passed + failed +
broken)`) but recomputed `passed / total` when scoped to a suite. So one page reported two
bases — run 101 (10 passed / 1 failed / 1 skipped) showed **90.91%** unscoped and **83.33%**
the moment a suite was selected, with no pass or fail having changed.

`test_summary_dict_scopes_counts_to_suite_cases` asserted `33.333` for 1 passed / 1 failed /
1 skipped, pinning the buggy formula; it now asserts `50.0`. That is not a weakened test — it
is the same denominator `test_pass_rate_excludes_skipped` was written to eliminate, whose
docstring describes this exact formula as the bug and records the fix landing in
`_update_run_aggregates`. run_compare simply kept the pre-fix version.

Scoped and unscoped summaries also now carry `unknown_tests`, so the per-status counts add up
to `total_tests` here the way they do everywhere else after 0118.

### 2026-08-11 — Fix: a test result with an unrecognised status was erased, and the run went green

**Migration `0118`** adds `test_runs.unknown_tests`.

`LiveEvent.status` is a free-form `Optional[str]` — documented as
`PASSED | FAILED | SKIPPED | BROKEN`, never validated. Streaming four events to
`POST /api/v1/stream/ingest`, three `PASSED` and one `"FAIL"` (a plausible typo, and the
spelling several frameworks use natively), was accepted with `{"accepted": 4}` and produced:

```
status=PASSED  total_tests=3  passed=3  failed=0  skipped=0  broken=0  pass_rate=100.0
```

while the run's own `test_cases` rows were:

```
test_alpha                 PASSED
test_beta                  PASSED
test_gamma                 PASSED
test_delta_REALLY_FAILED   UNKNOWN     <- persisted, and uncounted
```

A release gate reading that run would green-light a build whose test failed.

`RedisLiveRunState.record_test_event` had a four-entry `field_map` and did
`counter_field = field_map.get(status_upper)` — `None` for anything else — with the increment
behind `if counter_field:`, so **no counter moved at all**, not even `total`. `total` then fell
back to `passed + failed + skipped + broken` = 3 and the fourth test disappeared from the run's
own arithmetic, even though `_event_to_row` had written its row as `UNKNOWN`.

Now: unrecognised statuses bucket as `unknown` rather than incrementing nothing; `unknown` is
included in every `total` fallback and persisted via the new column on both ingest paths; and
`terminal_run_status` grades a run with uninterpretable results **STOPPED** rather than PASSED.
That extends the rule the helper's own docstring already established — a run we cannot vouch for
must not masquerade as green — and real failures still outrank it, so `FAILED` is unchanged.

The file-upload path had a milder form of the same hole: `total_tests` there is a `COUNT(*)`, so
`UNKNOWN` rows sat inside the total with no column reporting them and the four status columns
simply did not add up. Both paths now agree.

**Not fixed here, flagged instead**: `LiveEvent.status` still accepts any string. Rejecting
unrecognised values at the boundary would be the loudest fix, but it turns a partially-usable
batch into a 422 for clients already sending e.g. `"error"` — a compatibility call, not an
agent's to make.

### 2026-08-11 — Fix: Integration Health trends hid timed-out and auth-rejected probes

The probe service persists **five** statuses — `healthy`, `degraded`, `down`, `timeout` and
`auth_error` (`skipped` is dropped before insert). `GET /integration-health/trends` counted all
five toward `total_probes`, and therefore let all five drag `uptime_pct` down, but only ever
reported three of them.

A provider whose API token had expired rendered as:

    Uptime 0%   Healthy 0   Degraded 0   Down 0

— which reads as "never probed" rather than "your credentials are being rejected", on the one
page whose job is to say which integration is broken and why. The Trends tab now carries
**Timeout** and **Auth failed** columns, so the per-status counts account for every probe in
`total_probes`.

The same block also computed `avg_response_ms` inside the per-`(provider, status)` loop, so a
provider's "Avg Latency" was whichever *status group* the database returned last — order-dependent,
since a `GROUP BY` has no defined row order — and an `if row.avg_ms:` guard silently skipped any
group averaging exactly `0.0`. Latency is now its own per-provider aggregate, excluding the
`response_ms = 0` sentinel a `down` probe records when it never got a response.

On the homelab the two defects cancelled (ollama's only non-healthy group is `down`, whose
sentinel zeros the falsy guard skipped), so the displayed 47ms was correct by accident. Deployed
values are unchanged; the accident is gone.

Not reproducible on a deployment with every optional integration disabled — proven from the
persisted status vocabulary, with a regression test that reads that vocabulary out of the prober
rather than hard-coding it, so a sixth status fails the build instead of silently vanishing.

### 2026-08-10 — Fix: two router-level lists returned soft-deleted projects

`GET /runs/failed-ids` and `GET /agents/pipelines` build their queries **inline** rather than
delegating to a service, so neither the original sweep nor the widened service sweep could see
them. Both scoped only inside a conditional branch an ADMIN never enters.

| endpoint | deleted-project rows | live rows |
|---|---|---|
| `/runs/failed-ids` | **1,133** FAILED runs | 17 |
| `/agents/pipelines` | **861** pipeline runs | 13 |

`failed-ids` returned its full cap of **1,000 ids** from that pool — and its own `limit` exists
*"to prevent runaway fan-outs"*, so the fan-out was almost entirely work against projects nobody
can open. `pipelines` defaults to `limit=20`, so its 13 real rows were crowded out completely.

Twelfth and thirteenth surfaces in this family. The progression is the point: instances 1–9 were
found one at a time; #555 came from widening the sweep to the *router-computes / service-skips*
shape; these came from widening it again to **routers that never call a service at all**. Each
widening found what the previous model of the defect could not express.

Tenant isolation is preserved in both — including `failed-ids`' fail-closed empty branch — and
pinned by tests.

### 2026-08-10 — Fix: the suites and quarantine lists returned soft-deleted projects

Both applied their project filter only when the caller supplied one. The routers pass
`project_ids=None` for an ADMIN, so the unscoped lists applied **no project filter at all**.
Measured live:

| list | deleted-project rows | total returned |
|---|---|---|
| `/api/v1/suites` | **93** of 103 — 90% | 103 |
| `/api/v1/quarantine` | **3** of 3 — **100%** | 3 |

Every proposal in the Quarantine queue was for a test in a project nobody can open.

Tenth and eleventh surfaces in this family, and both were found by **widening the class sweep after
#554**. The original sweep looked for `if project_id:` guarding a filter inside one function; this
shape is split across two files — the *router* computes
`project_ids = None if accessible is None else list(accessible)` and the *service* skips filtering
when it receives `None`. A single-function scan cannot match that.

Membership confinement and the fail-closed empty branch are preserved in both, and pinned by tests.
`quarantine/stats` (zeros, different code path) and `webhooks` (no rows) were checked in the same
pass and are **not** claimed as covered.

### 2026-08-10 — Fix: the canonical test-case list returned soft-deleted projects

`list_canonical_test_cases` applied a project filter only when the caller supplied one. The router
passes `project_ids=None` for an ADMIN (no membership confinement), so the unscoped list applied
**no project filter at all**. Measured live:

| projects | cases |
|---|---|
| `is_active = false` (deleted) | **1,205** across 36 projects |
| `is_active = true` | 32 across 2 projects |
| **API, unscoped** | **1,237** |

**97% of the Test Management canonical-case list** was tests belonging to projects the user cannot
open, filter by, or navigate to.

Ninth surface in this family (#535, #538, #539, #541, #547, #549, #550, #551). Found by giving
`canonical-test-cases/:canonicalId` its first coverage — the *detail* endpoint's edge cases were all
correct (200 / 404 for a missing id / 422 for a malformed one); the **list** behind it was not.

The membership confinement and its fail-closed empty branch are both preserved and pinned by tests.

### 2026-08-10 — Fix: the Live page listed one run twice, under slug and UUID

`list_active_sessions` merges Redis state with two DB queries and dedups on `run_id`. Redis keys a
run by whatever the SDK supplied — frequently a slug like `local-abc12345` — while the DB rows
carry the UUID persisted for it, so the comparison never matched.

Measured live (after the deleted-project fix trimmed the list to two rows, both of which turned out
to be the same run):

| build_number | run_id | source |
|---|---|---|
| `sdk-probe-1` | `sdk-probe-1` | Redis (slug) |
| `sdk-probe-1` | `bd337e00-38ae-50ee-b2e5-0f21077a711b` | DB (UUID) |

Dedup now compares `canonical_test_run_uuid(...)` — the module's existing mapping for exactly this,
whose own docstring records three earlier call sites that drifted apart the same way. This is a
fourth.

The `TestRun` loop also compared `str(run.id)` against what is now a set of UUIDs, which would
silently never match; it compares UUIDs directly.

**This is the opposite of the older dedup bug**: "dedup by `run_id`, NOT `build_number`" still
holds — two genuinely different runs may share a build number. Here one run carried two identities.

### 2026-08-10 — Fix: the Live page listed sessions from soft-deleted projects

`list_active_sessions` unions **three** sources — the Redis active set, completed `LiveSession`
rows, and `live_stream` `TestRun` rows — and each was filtered only by the two *conditional* scopes
(`if project_id:` / `elif allowed_project_ids:`). An ADMIN with no project pinned matches neither,
so nothing restricted any of the three.

Measured live: of 9 entries returned, **4 belonged to two deleted probe projects**.

The exclusion is applied to all three sources — filtering one would leave the other two leaking,
and merging them is this endpoint's entire job.

Eighth surface in this family (#535 runs, #538 dashboard, #539 analytics, #541 ROI, #547 trends,
#549 defect KPI, #550 releases).

**Not changed**: the endpoint also returns *completed* sessions. Its docstring says so — "List
active + recent live sessions", with `days` bounding the completed set. The `/stream/active` name
is loose, but the contract is deliberate, and a test now pins it so nobody "fixes" it away.

### 2026-08-11 — Fix: onboarding auto-detection never credited the telemetry step

`auto_detect_progress` auto-completes `create_project`, `upload_run`, and `connect_jira` by
probing real state, but it stopped there — the `connect_telemetry` step (whose description is
literally "Add Splunk, OCP, or Slack integration") was never detected. A self-hoster who wired
up Splunk, OCP, or Slack got no credit for it, so the setup wizard sat stuck below 100% until
they manually skipped a step they'd actually completed.

Jira and telemetry both live in the one `integrations_config` `AppSetting` row, so the fix reuses
that same lookup: if any of `splunk_enabled` / `ocp_enabled` / `slack_enabled` is on, the
telemetry step auto-completes — symmetric with the existing `jira_enabled` check, and pending
otherwise. Stage-only, following the service/router transaction boundary.

### 2026-08-10 — Fix: the releases list returned soft-deleted projects

`list_releases` applied its project filter only when one was supplied. Measured live:

| projects | releases |
|---|---|
| `is_active = false` (deleted) | **36** |
| `is_active = true` | 2 |
| API, unscoped | **38** |

**36 of 38 rows** on the Releases page belonged to projects the user cannot open, filter by, or
navigate to.

Seventh surface in this family (#535 runs, #538 dashboard, #539 analytics, #541 ROI, #547 trends,
#549 defect KPI). The first six were each found by noticing a wrong number; this one was found by
sweeping for the **class** — any query whose project filter is conditional on `project_id` — which
is what six repeats should have prompted sooner.

Tenant isolation (`accessible_project_ids`) and the explicit project pin are both preserved and
pinned by tests.

### 2026-08-10 — Fix: the Overview defect KPI counted soft-deleted projects

`get_dashboard_summary`'s active-defects query, and `count_open_critical_defects`, both applied
their project filter only `if project_id:`. Unscoped, they counted every project. Measured live:

| surface | value |
|---|---|
| Overview KPI (`metrics/summary.active_defects`) | **4** |
| Defects page (`analytics/defects`, fixed earlier) | **0** |
| DB, live projects | **0** |
| DB, soft-deleted projects | 4 OPEN |

So the KPI advertised four active defects and clicking through landed on an empty list — all four
belong to two deleted probe projects.

`count_open_critical_defects` feeds the release-gate `max_p0_defects` hard cap, so a P0 on a
project nobody can open could block a release on a project that is live.

**Sixth surface in this family, and the one the previous guard could not catch**: the F-066 test
scans for functions summing `test_runs` columns, and a `COUNT(defects.id)` does not match that
shape. The scan now covers defect and run counts as well as run sums.

### 2026-08-10 — Docs: the summary report's pass rate does not match the Overview headline

`summary_report_service` documented `weighted_pass_rate_pct` as *"matches the /overview headline so
two surfaces agree"*. It does not, and cannot — the two are computed over different populations.
Measured live on Checkout Service (30d):

| surface | population | rate |
|---|---|---|
| `/overview` (`avg_pass_rate_7d`) | executions | 81.0% |
| `metrics/trends` (weighted) | executions | 81.03% |
| `analytics/coverage` | executions | 81.0% |
| **summary report** (`weighted_pass_rate_pct`) | **unique tests** | **83.3%** |

DB truth: 47 passed / 9 failed / 2 broken / 2 skipped across 60 executions of 12 unique tests.
`47/58 = 81.03`; `10/12 = 83.3`. The BROKEN executions disappear from the report because a unique
test carries a single status, which is exactly why the rates diverge.

**No behaviour changed.** Both figures are internally correct, and which one a user should see as
"the" pass rate is a product decision — switching the basis would break this report's counts
matching Coverage's `unique_tests`. The comment now states the real relationship with the measured
numbers, and a test pins each basis so the semantics cannot drift while that decision is pending.

### 2026-08-10 — Fix: the trend chart plotted soft-deleted projects

`get_trend_data` built its scope as `"AND tr.project_id = :project_id" if project_id else ""` — the
exact shape `_period_stats` carried before it was fixed, **in this same module**. Unscoped, nothing
restricted the query. Measured live at `days=4`:

| source | total |
|---|---|
| trend series, summed | **44,061** |
| DB, live projects only | 192 |
| dashboard KPI (fixed earlier) | 192 |

So the chart and the number printed beside it disagreed by **229× on the same screen** — the
cross-surface disagreement class this repo keeps hitting, this time between two elements of one
page.

Fifth surface in this family (`my_failures` → `/runs` → dashboard summary → analytics → ROI →
trends). It survived the earlier pass because that pass fixed the function the bug was *measured*
in and did not sweep its siblings in the same file. The regression test now asserts the property
for **every** run-aggregating query in the module and fails if a new one appears uncovered, so a
sixth cannot slip through the same way.

### 2026-08-10 — Fix: a digest subscription was largely immutable after creation

Two drifts between `DigestSubscriptionCreate` and `DigestSubscriptionUpdate`, both measured against
the live deployment.

**1. Four of six schedules could not be set by PATCH.** The update schema's pattern stayed at
`^(DAILY|WEEKLY)$` while the enum, the create schema, the Celery beat and the UI selector all grew
`WEEKLY_RETRO`, `PER_RUN`, `PER_RELEASE` and `PER_SUITE`:

| PATCH `schedule` to… | result |
|---|---|
| `DAILY` / `WEEKLY` | 200 |
| `WEEKLY_RETRO` | **422** |
| `PER_RUN` / `PER_RELEASE` / `PER_SUITE` | **422** |

Since create was the only route to those values, a `WEEKLY_RETRO` subscription PATCHed to `DAILY`
could not be put back — the remedy was delete-and-recreate.

**2. `scope_type`, `scope_value` and `trigger_filter` could not be changed at all.** Settable at
create (fixed in the previous entry) and stored on the row, but absent from the update schema —
so Pydantic dropped them and PATCH returned **200 with the old values retained**:

```
PATCH {"trigger_filter":"all","scope_type":"project","scope_value":"smoke"}
-> 200, still trigger_filter='failed_only' scope_type='suite' scope_value='api'
```

`trigger_filter` gates delivery, so a user could not switch an existing subscription between
"everything" and "failures only". The frontend's own `updateSubscription` is typed to send all
three.

`project_id` is deliberately left non-updatable and a test now pins that: it is
authorization-checked once at create and the delivery task never re-checks membership, so allowing
it on update would let a caller re-point an existing subscription at another tenant's project.

The vocabulary test now covers **every** request schema carrying a schedule pattern, discovered by
inspection rather than named — the original bug was one schema being forgotten, and listing them
would rebuild the same trap.

### 2026-08-10 — Fix: digest subscriptions discarded their own scope and trigger filter

`POST /api/v1/digests/subscriptions` built the row from a hand-written keyword list that omitted
`scope_type`, `scope_value` and `trigger_filter` — though `DigestSubscriptionCreate` declares all
three (with patterns and defaults), `DigestSubscriptionResponse` returns them, and each has its own
column. Measured live:

| sent | stored |
|---|---|
| `scope_type="suite"` | `'project'` |
| `scope_value="api"` | `None` |
| `trigger_filter="failed_only"` | `'all'` |

…all behind a **201**, with the response echoing the wrong values back.

**`trigger_filter` is not cosmetic.** The delivery task gates on it:

```python
if sub.trigger_filter == "failed_only" and _failed_tests == 0:   # skip
if sub.trigger_filter == "degraded_only" and _pass_rate >= 90:   # skip
```

A user who subscribed to *failures only* was stored as *all*, and received every all-green digest
they had explicitly opted out of — on a schedule. `scope_type`/`scope_value` currently have no
reader in the backend, so those two are inert; they are fixed alongside because the API accepts and
echoes them, but the harm above is the reason this matters.

Second instance of the shape, found by a request-body contract sweep of every router that builds an
ORM row from a Pydantic payload. The first was `page` on saved views (#544). Handlers that build the
row from `payload.model_dump()` cannot have this bug.

### 2026-08-10 — Fix: a saved view's `page` was accepted, discarded, and unfilterable

Two halves of one broken field, found by sweeping every MCP tool's query params against the params
its endpoint actually declares (66 call sites; this was the only mismatch).

**1. `POST /api/v1/saved-views` silently discarded `page`.** The handler built the row from a
hand-written field list that omitted it, while `SavedViewCreate` declares it, `SavedViewResponse`
returns it, the column stores it, and `PATCH` sets it — PATCH `setattr`s over the payload instead
of naming fields. The caller got a **201** and a response whose `page` was `null`, having just
supplied one:

| action | stored `page` |
|---|---|
| `POST` with `page="coverage"` | `None` |
| `PATCH` with `page="trends"` | `'trends'` |

**2. `GET /api/v1/saved-views` had no `page` filter.** The MCP tool `list_saved_views` advertises
*"page: Optional — restrict to a specific dashboard page"* and sent it. FastAPI ignores undeclared
query params, so it was dropped and the tool returned everything while promising scoping. Measured
live with two views present: no filter → 2 rows, `?page=trends` → 2 rows, `?page=zzz-no-such-page`
→ 2 rows.

That is the silent-wrong-answer half of the digests bug (#534), where a 404 was masking a filter
that never applied. Here there was no 404 to notice. Half 2 alone would have been cosmetic — you
cannot usefully filter by a field nothing persists — so both are fixed together.

### 2026-08-10 — Fix: the Java SDK could not log in behind a reverse proxy

`TestLookupReporter.login()` built its client with `HttpClient.newHttpClient()`, which defaults to
**HTTP/2**. Over plain `http://` that attempts an h2c upgrade, which Traefik — and reverse proxies
generally — reject. Measured against the live deployment:

| client version | result |
|---|---|
| `HTTP_2` (the default) | **HTTP 400 — "Invalid HTTP request received."** |
| `HTTP_1_1` | **HTTP 200, token returned** |

The error names nothing to do with the protocol, so it reads as a server fault or bad credentials.

The constructor **already pinned** `HTTP_1_1` for the instance client, so sessions and event
batches were fine — this static helper was the one that was missed. That made `login()`, the
documented entry point, the only call in the SDK that could not succeed behind a proxy. Go, JS,
Python and the CLI were all unaffected because they use HTTP/1.1.

Also adds a **Java SDK CI job**. The client had 29 passing unit tests and nothing ever ran them:
`sdk-cli-test` covers only the Python SDK and CLI, so `client/go`, `client/js` and `client/java`
had no coverage at all. That gap is why this shipped — and without the job the regression test
guarding it would be decorative, the same trap `sdk-cli-test` was created to fix.

### 2026-08-10 — Fix: report share links pointed at localhost

`POST /api/v1/reports/runs/{run_id}/share` built its URL from an **SSO** setting:

```python
base_url = settings.SAML_BASE_URL  # reuse the base URL setting
```

`SAML_BASE_URL` defaults to `http://localhost:8000` and has no reason to be set on a deployment
that doesn't use SAML — and the config's own localhost warning for it is gated on `SSO_ENABLED`,
so on a non-SSO deployment nothing ever flagged the default.

Measured against the live homelab, which sets `PUBLIC_BASE_URL` correctly:

| | result |
|---|---|
| `PUBLIC_BASE_URL` (configured) | `http://testlookup.local` |
| `settings.public_base_url` would resolve | `http://testlookup.local` |
| `SAML_BASE_URL` (default, SSO off) | `http://localhost:8000` |
| issued `share_url`, followed verbatim | **connection failed** |
| same token on the real ingress | **HTTP 200, the report** |

The deployment was configured correctly and the feature still emitted a dead link — this was never
a misconfiguration. The link was otherwise valid; only the host was wrong. A share link exists to
be sent to someone else, and both the CLI (`testlookup reports share`) and the UI's
copy-to-clipboard on the release-gate page handed out the localhost URL.

Now uses `settings.public_base_url` (`PUBLIC_BASE_URL` → first `CORS_ORIGINS` entry → localhost),
the property already used by the GitHub checks, GitHub PR-comment and GitLab integrations.

### 2026-08-10 — Fix: ROI metrics counted soft-deleted projects

All 11 counts in `get_value_metrics` were guarded by `if project_id:` alone — the shape that made
the dashboard over-count (#538) and the analytics helper leak (#539). Unscoped, nothing restricted
them, and the `TestRun` joins existed *only* on the scoped path, which is precisely why the
unscoped path counted everything.

Confirmed by reproducing the service's real predicate rather than assuming. A first pass logged
this as "needs confirmation" because the numbers did not corroborate a naive comparison: the API
returned `flaky_tests_identified: 3` while the coach table held 5 live rows and 5 deleted ones.
Neither matched — the metric counts only rows whose `status_history` actually oscillates. Running
that predicate over the live rows resolved it:

| source | count |
|---|---|
| live project (Checkout Service) | 2 |
| soft-deleted project (ZZ Probe, multi-day trends) | 1 |
| **total — exactly the unscoped API response** | **3** |

User-visible: the ROI page renders `FLAKY TESTS FOUND 3` in All-Projects scope. Only the *hero*
hours-saved number is gated on `available`; the component cards render regardless. An ROI figure
gets quoted to stakeholders, so counting projects nobody can open overstates the product's own
value.

Scoped behaviour is unchanged — both helpers keep the pin when a project is supplied; only the
life-cycle restriction became unconditional.

### 2026-08-10 — Fix: Coverage named one project for rows spanning several

`coverage_stats` groups suite rows by suite name alone, so in All-Projects scope a row can
aggregate several projects — `api`, `smoke` and `regression` are the most collidable suite names
there are. The row was then labelled `MAX(p.name) AS project_name`, stating one project as fact.
Measured live, the `api` row summed two projects (5 unique tests + 10) and attributed all 15 to
whichever name sorted highest.

**Low severity, stated plainly: no consumer reads the field today.** `CoverageSuite` in the
frontend types has no `project_name`, the MCP tool `get_coverage_report` takes a *required*
`project_id`, and `analysis_report_service` scopes its call too. A live probe of the rendered page
showed suite rows carrying no project attribution at all. This fixes a payload that would lie to
the next consumer, not a visible defect.

`project_name` is now emitted only when the row belongs to exactly one project, with a new
`project_count` alongside it so a roll-up is distinguishable from a single-project row. The
aggregation is deliberately unchanged — regrouping by `(project_id, suite)` would change what the
All-Projects view means and break the UI's one-row-per-suite keying.

### 2026-08-10 — Fix: analytics was built entirely from deleted projects

`_tenant_filter` is the single scoping helper behind every analytics query (9 call sites:
flaky tests, failure categories, top-failing, coverage, defects, …). It answered *who may see
a project*, not *whether the project still exists* — and `DELETE /projects/{id}` is a **soft**
delete that only flips `is_active`. On the admin path the tenancy clause is empty by design,
so the query ran with no project restriction at all.

Measured live, 90-day window:

| surface | API returns | DB, live projects |
|---|---|---|
| `analytics/coverage` executions | **47,105** | 672 |
| `analytics/coverage` suites | **27** | 7 |
| `analytics/coverage` unique tests | **1,612** | 32 |
| failures behind `failure-categories` | **5,675** | 72 |

The Coverage page was not merely inflated — it was built *entirely* from unreachable data.
All 27 suites belonged to 13 soft-deleted projects, every one a `ZZ … delete me` throwaway,
each rendered **with its deleted project's name**. The deployment's two real projects did not
appear on their own Coverage page at all.

Fourth surface in this class (`my_failures` → `/runs` #535 → dashboard metrics #538 →
analytics), so the fix goes in the shared helper rather than on the endpoints: one chokepoint
every analytics query already passes through, which a tenth call site inherits for free.
Tenancy behaviour is unchanged — the life-cycle clause is additional, and the existing
contract tests still assert the tenancy fragment exactly.

### 2026-08-10 — Fix: dashboard metrics aggregated over deleted projects

`_period_stats` added a project condition **only when one was supplied**. Unscoped — the
dashboard's own "all projects" view — nothing restricted it, so every soft-deleted project
was counted. Measured live:

| surface | executions |
|---|---|
| `metrics/summary` (unscoped) | **44,315** |
| DB, live projects | 192 |
| DB, deleted projects | 44,123 |

`192 + 44,123 = 44,315` exactly. **99.6% of the headline came from projects the user
cannot see, open or navigate to.**

**Found by re-verifying an earlier fix, and partly self-inflicted.** #535 excluded deleted
projects from `GET /api/v1/runs` and left this aggregation alone, so the runs list reported
68 runs while the dashboard headline reported 44,315 executions. Before that change the two
were at least *consistently* wrong; afterwards they disagreed — the cross-surface
disagreement class this repo keeps hitting (F-013, F-014, F-038, F-039).

The exclusion is unconditional: putting it behind `if project_id:` would place it in the
one branch that cannot over-count.

**Scope deliberately narrow.** A survey found 80 of 82 TestRun-aggregating functions carry
no `is_active` filter — but nearly all are *project-scoped*, where the caller has already
resolved one project and the filter is redundant. Only cross-project aggregation is
affected. This fixes the one measured; the survey is recorded in the ledger rather than
turned into a blanket edit nobody verified.

An existing test stubbed `app.models.postgres` with a bare `SimpleNamespace` and broke on
the new `Project` import. That file already ships `_FakeModelsModule` for exactly this
reason ("keeps this test from re-breaking with an ImportError unrelated to what it
asserts"); the metrics case now uses it.

### 2026-08-10 — Fix: the CLI crashed on a legacy console *after* succeeding (F-051)

`testlookup auth login` exited **1 with a traceback** on a Windows cp1252 console —
after the login had already completed and the profile was saved:

```
UnicodeEncodeError: 'charmap' codec can't encode character '✓'
```

The credentials were fine; the command reported failure anyway. A CI wrapper reads the
exit code, not the profile on disk.

Measured per helper with `PYTHONIOENCODING=cp1252`:

| helper | glyph | stream | before |
|---|---|---|---|
| `print_success` | `✓` | stdout | **exit 1, UnicodeEncodeError** |
| `print_error` | `✗` | stderr | exit 0 |
| `print_warning` | `⚠` | stderr | exit 0 |

Rich already degrades its **own** rendering — a full `projects list` table renders fine
under cp1252, because Rich substitutes ASCII box-drawing when the encoding cannot carry
the Unicode characters. What it does not do is rescue a literal glyph handed to it inside
markup; that is just text, passed straight to an encoder that cannot represent it.

The glyph is now chosen against the destination stream's real encoding: a UTF-8 terminal
still gets `✓` (verified: output bytes `â`), a legacy console gets `[OK]`.
All three helpers are covered, not only the one that crashed.

Deliberately **not** a global `sys.stdout.reconfigure`: mutating the process's streams
from a library import has a far larger blast radius than choosing a character, and would
change byte-for-byte output for every consumer. A stream with no `encoding` (StringIO,
pytest capture) is treated as capable, so test harnesses keep the Unicode form.

Verified end to end: `auth login` on a cp1252 console now exits **0** with
`[OK] Logged in as admin (profile: default)`.

### 2026-08-10 — Fix: the runs list returned runs from deleted projects

`DELETE /projects/{id}` is a **soft** delete — it flips `is_active` to False. `my_failures`
already carries a helper for this whose docstring names the problem exactly:

> `DELETE /projects/{id}` is a SOFT delete — it flips `is_active` to False. **Only the
> project LIST honours that flag**, so a deleted project's failures kept appearing in the
> assignment inbox: actionable work items for a project the user cannot open, filter by,
> or navigate to, and which is gone from every project picker.

That was fixed for the assignment inbox alone. `GET /api/v1/runs` had the identical defect
— the same shape as the authorization sweep, where a guard was corrected in one router
while identical copies survived elsewhere.

Measured on the live deployment: **43 deleted projects holding 1,422 runs** against 2 live
ones holding 67. The project picker showed 2 projects; the unscoped runs list returned rows
from 13. (Many of those 43 were throwaway projects created during exploration, so that
ratio is deployment-specific — but one deleted project is enough to put unreachable runs
in the list, and every window metric derived from runs counted them.)

The exclusion goes in the **shared** `filters` list, reused by both the row query and the
count query. A count that includes rows the list excludes is the next bug along —
`/analytics/defects` already shipped exactly that (`items: []` with `total: 5`).

**BEHAVIOUR CHANGE:** runs belonging to archived projects no longer appear in `/api/v1/runs`
or in metrics derived from it. Retention purge is unaffected — it addresses projects by
explicit id.

An existing IDOR regression test asserted "no `IN (`" on the admin path to mean "no
membership constraint". The activity exclusion is also an `IN`, so that assertion was
tightened to name the membership form specifically rather than conflating the two.

### 2026-08-10 — Fix: an MCP tool called a route that does not exist

`list_digest_subscriptions` called `GET /api/v1/digests`. The digests router mounts
`/subscriptions`, `/preview` and the `/subscriptions/{id}` verbs beneath that prefix —
never the bare prefix. Verified against the running server:

```
GET /api/v1/digests               -> 404
GET /api/v1/digests/subscriptions -> 200
```

`client.get` calls `raise_for_status()`, so the tool did not degrade — it raised, and
every invocation surfaced an error to the model.

**A second defect hid behind the first.** The tool advertises a `project_id` argument and
passed it as a query param. `list_subscriptions` declares no such parameter, and FastAPI
ignores undeclared query params — so correcting the path alone would have produced a tool
that silently returned **unfiltered** results while its docstring promised scoping. The
404 was masking a silent-wrong-answer bug. Filtering now happens on the returned rows,
which carry `project_id`, and the docstring says where the filter is applied.

Found by diffing every `/api/v1/...` literal in `mcp/tools/` against the live route table:
**58 paths referenced, 1 wrong**. The new test derives its cases from the source, so a tool
added later against a route that does not exist fails the same way.

**Also checked and clean:** the MCP client posts `data=` (form-encoded) to the login
endpoint — the contract the CLI got wrong — and all five verbs (`get/post/put/patch/delete`)
carry consistent 401 re-auth-and-retry.

### 2026-08-10 — Fix: `testlookup auth login` could not succeed against any server

Two independent defects on the CLI's primary authentication path, both reproduced
against the live deployment.

**The request body was the wrong encoding.** `client.login` posted `json={...}`;
`POST /api/v1/auth/login` takes `OAuth2PasswordRequestForm = Depends()`, which is
form-encoded only. Measured side by side against the running server:

```
json= -> 422 {"detail":[{"loc":["body","username"],"msg":"Field required"}]}
data= -> 200 {"access_token": "eyJ..."}
```

The 422 was then reported as **"Login failed — check username and password"** with
*correct* credentials — a protocol mismatch blamed on the user, who would rotate
passwords chasing a bug in the client.

**`--url` ignored `TESTLOOKUP_URL`.** Declared as `typer.Option("http://localhost:8000")`,
so with the variable set — and honoured by every other command — login still dialled
localhost and failed with "All connection attempts failed". The one command that
establishes a session was the one that ignored the environment.

Verified end-to-end after the fix: login saves a real JWT and `auth whoami` reports
`Authenticated: True` against the homelab.

**Also found, recorded not fixed:** on a Windows cp1252 console the CLI crashes with
`UnicodeEncodeError: '✓'` when printing its success tick. The login itself has
already completed and the profile is saved, so the credentials are fine — but the process
exits 1 with a traceback, which a CI wrapper would read as failure. Needs a console-safe
output path rather than a one-character patch.

### 2026-08-10 — Fix: "test notification" reported success when SMTP was not configured

Measured on the live deployment, which has an empty `SMTP_HOST`:

```
POST /api/v1/notifications/test  {"channel": "email"}
-> 200 {"status": "sent", "channel": "email"}
```

Nothing could have been delivered. The whole point of a "send test notification" button
is to answer *is my channel configured?* — it answered yes when the answer was no.

The asymmetry sat inside one function. `_dispatch_to_channel` guards Slack honestly
(`return "failed", "No Slack webhook URL configured"`), while the email branch checked
only that a recipient address existed. `email_service.send_notification` then returns
**early and silently** when SMTP is disabled — a bare `return` behind a `logger.debug` —
so no exception reached the caller and the "no exception means it worked" path reported
success.

**Not just a misleading button.** That return value is what gets written to
`NotificationLog`, so every suppressed email was recorded as **delivered** — the
notification history asserted deliveries that never left the box.

Email now matches Slack: an unconfigured channel is a `failed` with an actionable reason.

**Also examined and found clean:** notification history and unread-count are user-scoped;
`mark_notification_read` filters on `user_id == current_user.id`, so no cross-user IDOR;
Slack and Teams already reported missing webhooks correctly.

**Recorded, not fixed — needs a product decision.** No part of the notification delivery
chain checks `AI_OFFLINE_MODE`, though `ai_config_resolver`'s own docstring claims *"every
other outbound integration … reads `settings.AI_OFFLINE_MODE` directly"* and that the env
var "means the same thing everywhere". With `AI_OFFLINE_MODE=true`, configured SMTP/Slack/
Teams channels would still send. Either the code or that claim is wrong, and making
offline mode silence notifications is a behaviour change worth deciding deliberately
rather than inferring.

### 2026-08-10 — Fix: a missing bucket 500'd the retention preview and could half-finish a purge

`POST /projects/{id}/retention-policy/preview` returned **HTTP 500** the moment a project
had anything to purge:

```
botocore.errorfactory.NoSuchBucket: An error occurred (NoSuchBucket) when calling
the ListObjectsV2 operation: The specified bucket does not exist
```

It worked while the answer was zero — with no runs past the cutoff there are no artifact
prefixes to list — so the bug stayed hidden until the feature had something to say.

Two consequences, the second more serious:

- **Preview is read-only and still crashed.** It is the operation admins are told to run
  before enabling retention ("preview is how admins decide whether to enable"). On any
  deployment where nothing has uploaded an artifact yet — the default state — that
  decision could not be made.
- **Execute would abort mid-purge.** The documented order is Mongo → MinIO → Postgres.
  Mongo deletes happen at step (2); the MinIO listing that raises is at step (3). A purge
  would delete Mongo documents, throw, and never reach the Postgres deletes or the audit
  row — a partial purge with no record of itself, on a path whose own docstring notes the
  stores are "inherently non-transactional".

`list_objects` and `delete_prefix` now treat a missing bucket as zero objects. Narrow on
purpose: `AccessDenied`, network failures and everything else still raise, because
"nothing there" and "we could not look" must not render identically.

**The rest of retention was exercised end-to-end on a throwaway project and is clean:**
preview and execute agree exactly (2 runs / 8 test cases predicted, 2 runs / 8 cases
deleted), the cascade reaches test_cases, the purge-audit row records per-store counts,
execute is gated behind a type-the-project-name confirmation, and a second execute is
idempotent.

### 2026-08-10 — Fix: "Upload Report" now actually uploads, under any scope

Reported twice. The first time the sidebar link was a silent no-op. The fix for that
rendered an explanation — *"choose a project to upload into"* — and was reported again,
correctly: **a user who clicks "Upload Report" wants to upload**, and being told to go
and change a header selector first is still a dead end.

That fix addressed the *silence*, not the *goal*. Worse, the probe suite passed
throughout, because it asserted the behaviour that had been decided rather than the one
that was asked for.

The upload panel now opens under **any** scope and asks for the project itself, with a
required picker sourced from the same store that backs the header selector. Scope is a
question, not a refusal.

Role remains a genuine block: `POST /api/v1/ingest/file` rejects a non-QA-Engineer with
403, so opening the panel would only defer the failure to submit time. That distinction —
a precondition the UI can satisfy versus one it cannot — is now what the tests pin.

Verified end-to-end on the live deployment: from All Projects, the deep link opens the
panel, the in-modal picker selects a project, and the upload completes
(`202` → *"Processed 4 tests · 2 passed · 1 failed · 1 skipped"*).

### 2026-08-10 — Fix: creating a saved view didn't verify the project, though listing them did

Low severity, stated precisely: `SavedView.project_id` is read only to filter the list and
to unset sibling defaults, so the row grants no data access. This is write-side
pollution, **not** a disclosure.

What made it worth fixing is the asymmetry. When the read path on this router was guarded,
the write path was left alone — so on the live deployment a zero-membership VIEWER could:

```
POST /api/v1/saved-views {"project_id": "<theirs>", ...}   -> 201
GET  /api/v1/saved-views?project_id=<theirs>               -> 403
```

Create it, then be forbidden from reading it back. A guard on one half of a resource and
not the other becomes a real finding the moment someone consumes the field — the digests
router already carries a note saying exactly that about `saved_view_id`.

Found by extending the authorization sweep to **non-GET** methods, which the earlier pass
had not covered even though two of its findings (digest subscriptions, chat sessions) were
POST-body leaks. Of 23 non-GET endpoints taking a `project_id`, this was the only gap;
the rest carry `require_role`, `require_project_access` or `resolve_project_scope`.

### 2026-08-10 — Fix: a failing test was hidden by a later same-named passing one

Persistence is keyed on `(test_run_id, test_fingerprint)` and `_upsert_test_case` does a
blind `existing.status = status`, so when one report named the same test more than once
the **last occurrence won** and every earlier one was discarded.

Measured on the live deployment with three same-named cases, one failing:

| report | run verdict |
|---|---|
| `fail, pass, pass` | **PASSED** — 0 failures |
| `pass, pass, fail` | FAILED — 1 failure |

Identical inputs, opposite verdicts, decided by document order — and the run that
genuinely contained a failure was the one reported green.

**This shape is common, not exotic.** Retry frameworks emit the failed attempt and the
passing retry as sibling `<testcase>` elements with the same name. That fail-then-pass
ordering is exactly what resolved to PASSED, so the signal this product exists to
surface was the one most reliably dropped.

Duplicates are now collapsed **within one payload** before persistence, worst outcome
winning (`failed` > `broken` > `skipped` > `passed`; ties keep the later entry, the prior
behaviour). `_upsert_test_case` is deliberately unchanged, so a *separate* re-ingest of
the same run still overwrites and a corrected report can still flip a verdict.

Also covered in the same exploration and found **clean**: malformed, truncated,
non-XML, empty and format-mismatched uploads are all handled honestly — the upload
returns 202, and `GET /ingest/uploads/{task_id}` reports `state: failed` with an
actionable code (`empty_report`) rather than failing silently.

### 2026-08-09 — Security: `/onboarding/events` served instance-wide analytics to anyone

The handler took the authenticated user and **threw it away**:

```python
async def list_usage_events(..., _: User = Depends(get_current_active_user)):
    return await get_usage_events(db, event_name=..., project_id=pid, limit=limit)
```

`get_usage_events` applies no scoping either — and no project filter at all when none is
named. So any authenticated account read the most recent product usage events across
every project, each carrying another user's `user_id`, a `project_id`, and the raw
`event_payload`. Every other endpoint in this router uses `require_project_access()`;
this one alone did not, and *could not* — it discarded the identity it would have needed.

Confirmed live with a zero-membership VIEWER after seeding one event (the table was
empty, and "no data" is not "no leak").

Fixed as **ADMIN-only**, matching `/audit-dashboard/export` — the other instance-wide
analytics export here. Membership scoping was the alternative, but this is cross-project
analytics by nature and **has no consumer**: no frontend, CLI, MCP or SDK caller
references it. Admin-only breaks nothing and matches what the endpoint is.
### 2026-08-10 — Fix: onboarding showed a `testlookup upload` command the CLI rejects

The first-run guide (shown on an empty dashboard — the first thing a new self-host
user reads and copies) displayed `testlookup upload results.xml`. There is no bare
`upload <file>` command: `upload` is a Typer group whose only leaf commands are `file`
and `dir`, and `--project`/`--build` are both required. Pasting the guide's command
failed immediately with a Typer usage error — exactly at the moment adoption is most
fragile.

Corrected the displayed command to the CLI's own documented form,
`testlookup upload file results.xml -p <project-id> -b <build>`, and fixed the two
matching `user-guide/getting-results-in.md` snippets (the general CLI example and the
CI step) that used the same broken bare form and omitted the required `--build`. A
frontend regression test pins the exact command string so an accidental revert to the
bare form is caught.

### 2026-08-09 — Security: `scope=team` guarded cross-user escalation but not cross-tenant (HIGH)

`/api/v1/me/assigned-failures` drops the per-assignee filter when `scope=team`, and
honours that scope for QA_LEAD and ADMIN only. The source comment states the intent:

> Team scope is only honoured for QA_LEAD / ADMIN. Anyone else silently falls back to
> `mine` so the URL can't be tampered with to leak cross-user data.

It did exactly that — and nothing else. Dropping the assignee filter without adding a
project filter left the query bounded by **nothing**.

Confirmed live with a QA_LEAD holding zero memberships (0 projects visible):

```
GET /me/assigned-failures?project_id=<theirs>&scope=team -> total 11
    test_refund_flow (api), test_discount_stacking (regression)
GET /me/assigned-failures?scope=team&days=90&size=100    -> total 72, 2 projects
GET /me/assigned-failures/count?scope=team               -> {"count": 72}
```

Instance-wide reach for a normal tenant role — the property that made F-035
(`POST /search/reindex`) serious.

Both handlers now resolve the caller's scope: a named project is verified (403 for a
non-member), an unnamed one bounds `team` to the caller's memberships. ADMIN stays
unrestricted.

`scope=mine` is untouched and always was safe — it filters on
`assigned_to_user_id == current_user.id`. It also **skips the membership lookup
entirely**: the sidebar polls the count endpoint, and an existing regression test pins
its query count, so the scope resolution runs only when it can change the answer.

### 2026-08-09 — Security: the access check sat in the branch that cannot leak (10 handlers)

Third recurrence of one bug. The digests (F-033) and chat (F-040) fixes each corrected
their own router; sweeping all 45 GET endpoints that take an optional `project_id` —
enumerated from the running app, not by grep — found **ten more** with the same shape:

```python
if not project_id:                      # fires only when there is nothing to guard
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        return []
...                                     # runs with the caller's project_id, unguarded
```

Two confirmed live with a zero-membership VIEWER: `release-gate-policies` returned another
tenant's full rule document (go/no-go thresholds, pass-rate minimum), and
`test-management/plans` returned their plan. The other eight — saved views, managed cases,
strategies, audit log and four export endpoints — are fixed on the shared code path; no
data of those types existed on the probe deployment to repro individually.

All now call `resolve_project_scope` unconditionally. `runs.py` matches the shape but is
correct (it compensates in the `else` branch) and is deliberately not changed.

**New gate `backend.project-scope-guard-placement`** fails CI when an access check sits
inside an `if not project_id` branch. Grepping for the guard cannot catch this class —
`get_accessible_project_ids` *is* imported and *is* called — and the architectural
authorization ratchet only matches routers whose *path* declares `{project_id}`. After
three recurrences, placement needed its own guard.

### 2026-08-09 — Security: chat took `project_id` from the caller unchecked (HIGH)

Three endpoints in `routers/chat.py` accepted a client-supplied `project_id` and never
verified membership. The read is **F-033 character-for-character** — the same guard in
the same wrong branch, in a router the post-F-033 sweep did not reach:

```python
if not project_id:                       # fires only when there is nothing to guard
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        return []
return await chat_service.get_run_summaries(db, project_id, days)
```

Confirmed on the live deployment with a throwaway VIEWER holding **zero memberships** —
0 projects visible and 0 summaries on the guarded branch, but **5 records** for another
tenant's project: build numbers, pass rates, failure counts, executive summaries,
`markdown_report`, `is_regression`.

`POST /chat/sessions` and `POST /chat/sessions/{id}/messages` are worse than a leaky
read: `send_message` resolves `session.project_id or payload.project_id` and hands it to
the ConversationAgent, which is what fetches the data to answer with. So an unverified
binding is a standing handle on another tenant's project, and a per-message `project_id`
re-points an otherwise legitimate session. Confirmed live: the same VIEWER created a
session bound to another tenant's project (201).

Session **ownership** was never the problem — `require_session_access` is creator-only
and correct. It is the project named inside the session that went unchecked.

All three now resolve scope through `resolve_project_scope` (403 for a non-admin naming
a project they do not belong to; ADMIN unrestricted). `run-summaries` also filters by the
caller's membership set when no project is named, so a non-admin gets *their* summaries
instead of the blanket empty list the old guard returned.

**Why the ratchet missed it:** `test_architectural_authorization.py` matches routers whose
*path* declares `{project_id}`; here it arrives as a query parameter and two body fields.
Grepping for the guard also looks fine — it is imported and called, just in the branch
that cannot leak.

### 2026-08-09 — LLM cost is now measured (it was $0.00 for every call)

TestLookup could not measure LLM cost **at all**. There was not one token-price constant
in the backend, and every `cost_usd` traced back to a literal `0.0` — including the one
carrying the comment *"cloud cost lands via base metering when known"*, which it never did.

The consequence was not a missing report. `llm_cost_budget` — 24 tests, a per-project
`hard_cap_usd`, an admin settings page — metered **$0.00 for every call**, so the USD cap
**could never trip**, and no cost-reduction work could be justified or proved.

Phase 0 of the Batch-API PRD:

- **`services/llm_pricing.py`** — per provider/model rates (USD per million tokens) with
  separate input / output / cached-read / cache-write rates, the Batch-API 50% multiplier,
  and `LLM_PRICE_OVERRIDES` for deployments whose negotiated rates differ from list.
- **Priced centrally in `mark_stage_done`.** Every agent stage funnels through it, so the
  meter now covers the whole pipeline instead of the call sites that remembered — which
  was none of them. A caller supplying real billing still wins.
- **The input/output split is preserved.** All three token-capture sites collapsed usage to
  `total_tokens` and passed it as `input_tokens`. Output costs several times more than
  input on every cloud provider, so that systematically under-stated the bill.
- **A $0.00 now says which kind it is.** `self_hosted` (Ollama et al — correct, there is no
  per-token charge) is distinguishable from `unpriced` (a cloud model we have no rate for),
  and the latter logs a warning and increments `testlookup_llm_unpriced_calls_total`
  instead of quietly understating spend. Stage events carry `cost_source`.
- **New gate `backend.cloud-providers-are-priced`** — a non-self-hosted provider without
  price-table entries fails CI, because the failure mode is silent by nature.

`PRICE_TABLE_UPDATED` records when the rates were last checked. These are list prices for
reference, not a quote; override per deployment rather than editing the table.
### 2026-08-09 — Fix: the "Upload Report" sidebar link silently did nothing

Reported: `/runs?upload=1` "displays the runs page". It did. The sidebar entry
deep-links there, and the page opened the upload panel only when the scope was a
single project — but **All Projects is the default scope**, so the plain link hit that
branch every time. The user saw the sidebar highlight "Upload Report" as the active
page, and an ordinary runs list underneath it. The only copy explaining why lived in a
disabled button's `title` attribute, which nobody arriving from that click ever hovers.

Confirmed on the live homelab before the fix — scope `all`, upload button present but
disabled, drawer closed.

- The page now **renders the reason** it could not honour the request: pick a project
  (the common case, and the one the user can fix), or the QA Engineer role.
- `?upload=1` already survived in the URL, so selecting a project opens the panel
  automatically — verified live, and the banner says so.
- The deep link also **skipped the role check** the button applies, so a non-QA-engineer
  could open a panel that `POST /api/v1/ingest/file` then rejected with 403. The backend
  did enforce it, so this was a misleading affordance rather than a privilege gap. The
  link now honours the same three conditions as the button.

The earlier manual-upload probe tested the header button *after explicitly selecting a
project* and never followed the sidebar link, which is why this shipped as "verified".
### 2026-08-09 — Fix: `WEEKLY_RETRO` digests were unreachable from the UI *and* the API

The auto-retro digest (Tier 2 item 12) was built end-to-end — `DigestSchedule.WEEKLY_RETRO`
in the ORM, a Monday 07:00 UTC beat entry, a retro renderer in `digest_content_service` —
with **no way to create the subscription**:

- `DigestsPage` offered five schedules; `WEEKLY_RETRO` was not among them.
- `DigestSubscriptionCreate.schedule` carried a regex that **excluded** `WEEKLY_RETRO`, so
  even a hand-rolled API call 422'd. The vocabulary lived in three places and nothing
  forced them to agree — the repo's producer/consumer drift class.

Fixed both halves. The option is **gated on the `weekly_retro_digest` flag**, because
`dispatch_scheduled_digests` silently skips `WEEKLY_RETRO` subscriptions when the flag is
off — an ungated option would let a user create a subscription that never delivers and
never says why.

The regression test derives its cases from the `DigestSchedule` enum rather than naming
`WEEKLY_RETRO`, so the *next* enum member fails until it is wired through too.

### 2026-08-09 — Perf: suite-history query 349ms → 50ms (F-P16 root cause)

`compute_suite_history` was **369 ms of the 513 ms** `GET /api/v1/test-management/suites`
endpoint — the single largest cost, and invisible to four earlier hypotheses because it
lives in a service the router calls rather than in the router's own SQL. Found by enabling
`log_min_duration_statement` for one request.

Two changes, both verified against the live database to return **byte-identical rows**
(`EXCEPT` in both directions, 0 rows each way):

- **`UNION` → `UNION ALL`.** The branches are disjoint by construction: path A emits
  `(run, primary_suite_name)`, path B emits `(run, tc.suite_name)` only where it
  `IS DISTINCT FROM` primary, and already de-dupes itself. Measured on live data, the dedup
  removed **0 of 3824 rows** — pure sort cost. The function's own docstring already claimed
  `UNION ALL`; the code disagreed. 349.7 → 290.4 ms.
- **Carry `from_primary` instead of a correlated `EXISTS`.** The join needed "is this row the
  run-level suite?" and asked the database per row via a subquery over `test_runs`.
  `run_effective` already knows — it is the branch the row came from. 290.4 → 52.9 ms.

Measured on the **real generated SQL**, not a reconstruction: **49.6 ms**.

Both queries in the service are converted; the second reaches the flag through its `filtered`
CTE, which is `SELECT *`.

Regression: `test_suite_history_query_shape.py` (6 of 7 fail before) pins the shape, since
the same rows can be produced either way and a correctness test passes on the slow version.
Suite counts stay covered by the existing 160 suite tests, all passing.

### 2026-08-09 — UI consistency: every colour now comes from a theme token

Follow-up to the status-colour fix. Removed the remaining inconsistency sources so the same
meaning renders the same colour on every page and tab.

- **1,405 raw Tailwind palette classes migrated** across 77 files (`text-emerald-400`,
  `bg-gray-700`, `border-neutral-500`, …). These render a fixed palette colour regardless of
  theme, so they could never match a token — a `text-emerald-400` "passed" label was always
  going to disagree with `var(--status-passed)`.
  - status families map by **meaning**: green/emerald/teal → passed, red/rose → failed,
    amber/orange → broken, yellow → skipped, purple/violet/pink → flaky, blue/sky/indigo → accent
  - neutrals map by **lightness and property**: text → `--color-text` / `-secondary` / `-muted`,
    surfaces → `--color-bg` / `-card` / `-hover`, borders → `--color-border`
- **`formatters.ts` — the shared status→colour helper** — was still returning
  `text-emerald-400`. It is the canonical mapping used app-wide, so it was a central source
  of the mismatch.
- **The retired lime `#b8f24a` survived as a `var(--color-accent, #b8f24a)` fallback** in
  ComputeCanvas (3 sites). It only renders if the token is undefined, but it is stale and
  points at a colour the retheme deliberately removed.
- 6 more files had status literals the first pass missed (`#fdba74`, `#fde68a`,
  `rgba(250,204,21)`, …).

**Corrected an error in my own migration:** the blanket amber/orange → `--status-broken` rule
collapsed SKIPPED and BROKEN onto one colour, when a `--status-skipped` token exists. Two
distinct states rendering identically would have been a regression disguised as consistency.
Fixed in `formatters.ts` and `CanonicalDetailPage.tsx`.

**Left alone deliberately:** whites, blacks and grey overlays used for shadows and scrims.
They are not status or surface colours and folding them into tokens would be wrong.

Lint: **572 warnings → 0**. 736 tests pass (matching baseline exactly), build/typecheck clean,
theme-token guard and bundle budget both pass.

### 2026-08-09 — Fix: status colours differed between pages (1,167 hardcoded literals)

Reported: the "passed" green in the Failure-signature table on `/runs` did not match the
green in Suite-coverage-breakdown on `/coverage`.

Confirmed. `/coverage` used `var(--status-passed)` (`#34a06b`); `/runs` hardcoded `#34d399`
(Tailwind emerald-400) inline. **Two different greens for the same meaning, on two pages.**

The cause was much wider than those two tables: **1,167 inline colour literals across 42
files**, which bypass the theme tokens entirely. The retheme normalized the tokens, but
components that never used tokens were untouched by it — so the six themes were consistent
with each other and inconsistent with the components.

- 39 files migrated. Solid literals map to the semantic token (`var(--status-passed)` etc.);
  `rgba(...)` with alpha maps to `color-mix(in srgb, var(--token) N%, transparent)`, matching
  the existing derived tokens (`--status-*-bg`, `-bd`, `-bg-soft`) that already existed for
  exactly this purpose and were being bypassed.
- Grouped by **meaning, not hue**: `#86efac`, `#34d399`, `#22c55e`, `#10b981` and `#3fb950`
  all meant "passed" and now collapse onto one token. That is the actual fix — a lighter and
  a darker green for the same state is the inconsistency, not a design.
- Neutrals, white, black and greys are deliberately **left alone** — they are overlays,
  shadows and surfaces, not status colours, and folding them into status tokens would be wrong.

Guard: `check-theme-tokens.mjs` now fails on any inline status literal in `src/**/*.tsx`,
verified by reintroducing `#34d399` and watching it fail.

736 tests pass, 0 lint errors, build clean.

### 2026-08-08 — Fix: webfont leftovers the retheme missed (found by deploy validation)

Validating the retheme against the **served** bundle — not the repo — caught `IBM Plex Mono`
still in the shipped CSS. Three sources fed webfont names into the build from outside
`index.css`, so every theme block was clean while the stylesheet was not:

- `tailwind.config.js` `fontFamily` fell back to `'Sora'` and `'IBM Plex Mono'` after the CSS
  var. Since neither is loaded any more, those entries could only ever resolve on a machine
  that happened to have the font installed — a rendering difference between developers, not
  a fallback.
- `AppLogo.tsx` hardcoded `'JetBrains Mono, monospace'` on 4 inline styles.
- `AppLogo.tsx` also hardcoded `'Inter, sans-serif'` on 2 more. **Inter was never loaded at
  all**, so the wordmark had been falling back to generic `sans-serif` and never followed the
  theme — that predates the retheme.

All now use `var(--font-mono)` / `var(--font-sans)`, so the logo tracks the active theme.

The guard was extended to scan `tailwind.config.js` and every component for inline
`fontFamily` values that don't go through a token. **That extension is what found the Inter
pair** — my first version only checked inside the six `[data-theme]` blocks, which is exactly
why the leftovers survived the original change.

### 2026-08-08 — Retheme: normalized theme tokens across all six themes

Implements `TestLookup Retheme Preview.dc.html` from the Claude Design project
("Testlookup application slides"), whose drop-in artifact is `retheme/theme-tokens.css`.

- **Status hues unified.** One green, one red, one amber, one gold, one violet across every
  dark theme. They had drifted — `#7ce0a0` / `#43e0a0` / `#3fb950` were all "passed", so a
  passed pill meant a different colour depending on the active theme. `lab` keeps darker
  equivalents because it is the light theme and needs the contrast.
- **Neon lime accent retired** (`#b8f24a` → `#3b82f6`); passed-green softened to `#34a06b`;
  Signal's green-tinted surfaces neutralized to charcoal; Midnight's green primary button
  retired for blue.
- **Aurora background glows removed** — every theme now sets `--app-bg-image:none`.
- **System font stacks everywhere**, replacing Sora / Plus Jakarta Sans / Space Grotesk /
  Archivo / IBM Plex Mono / JetBrains Mono.

Derived tokens were not touched: they resolve through `var()`, so they adapt automatically —
which is what made a token-only change viable.

Two consequences the design spec did not cover, both handled here:

- **Theme-picker swatches are hardcoded in `themeStore.ts`.** Left alone they would have
  shown the *old* palette — a lime chip for a blue theme. All six swatches and two hint
  labels realigned, and the regression test now asserts each swatch equals its theme's actual
  `--color-bg` / `--color-bg-card` / `--color-accent`, so they cannot drift apart again.
- **The webfonts were still being loaded.** `index.html` pulled 8 Google Font families that
  no theme references any more. Removed, along with both preconnects, and the CSP tightened
  to drop `fonts.googleapis.com` / `fonts.gstatic.com`. This matters beyond the saved request:
  TestLookup is offline-first, and in an air-gapped deployment that request does not fail
  fast — it stalls until timeout. The CSP's own explanatory comment still claimed
  "Scoped to 'self' + Google Fonts"; corrected.

Guard: `frontend/scripts/check-theme-tokens.mjs`, wired into CI beside the bundle budget.
It reads the real `index.css`, `index.html` and `themeStore.ts` and **fails on the
pre-retheme files with 12+ specific violations**.

It also measured the drift more precisely than the brief did: the spec named three
conflicting "passed" greens; there were **five**, alongside five reds, four ambers, five
golds and five violets. A node script rather than a vitest file because `npm run build` runs
`tsc` over `src/` with no node types, and vitest stubs CSS imports to empty (`css: false`),
so `?raw` on a stylesheet yields nothing.

### 2026-08-08 — Celery workers expose their own metrics; the last ghost alert is backed

- Completes the ghost-metric work. `TestLookupTaskLatencyHigh` alerts on
  `celery_task_runtime_seconds` (p99 > 120s) and nothing emitted it. The backend could not:
  only the worker sees task execution, so this needed a worker-side scrape target.

- **Multiprocess mode is the crux.** Prefork children each keep a private registry, so the
  process serving `/metrics` would otherwise publish only its own view. Children write to
  `PROMETHEUS_MULTIPROC_DIR` (an `emptyDir` at `/tmp/prometheus-multiproc`) and the parent
  aggregates via `MultiProcessCollector` on `worker_ready`.

- Without that directory set the server is deliberately **not** started — publishing one
  child's numbers as if they were the whole worker is worse than publishing nothing. That
  also means Compose deployments simply run without it, no config required.

- `--max-tasks-per-child` recycles children constantly, so `worker_process_shutdown` calls
  `mark_process_dead`. That clears the dead PID's *gauge* files; counter and histogram files
  stay, because a task that ran is a task that ran.

- Buckets go to 600s. prometheus_client's defaults top out at 10s, which would dump every
  slow task into `+Inf` and make a p99 near the alert's 120s threshold meaningless — the
  metric would exist and still not answer the question it was added for.

- All four workers annotated for scraping on 9100; `beat` deliberately excluded — it
  schedules and executes nothing.

- Verified end-to-end: a recorded observation aggregates back out through
  `MultiProcessCollector` with the `le="120.0"` bucket and both labels present.

- Regression: `test_worker_metrics_exposure.py` (13 of 18 fail before) checks the three
  things that each fail silently on their own — the env var matching a **real mount path**,
  the scrape annotation matching the **actual container port**, and the volume being
  ephemeral so a restart cannot resurrect dead children.

- `KNOWN_INERT` in the queue-depth test is now empty. **No alert rule references a metric
  nothing emits.**

### 2026-08-08 — Failure-signature table on /runs is paginated

- `SignatureClusterCard` rendered every member of the primary cluster **plus** every outlier
  in one flat list. The card sits beside the scorecard in a fixed-height row, so a window with
  more than a handful of matching builds stretched the page. The cluster is *expected* to be
  large — its whole premise is "many builds share one signature".

- 8 rows per page, using the app's existing `Pagination` component, which renders nothing at
  a single page — so small clusters look exactly as they did.

- The page resets when the cluster changes (project switch, time-window change), otherwise a
  viewer parked on page 3 lands on an empty table. Done as a **render-time state adjustment**
  rather than an effect: an effect would paint the stale page first and re-render, and this
  repo makes synchronous setState inside `useEffect` a lint *error* for exactly that reason.

- The page index is also clamped during render, so a cluster that shrinks cannot show a blank
  table for the frame before the reset applies.

- Regression: `RunsPage.signaturePagination.test.tsx` — 7 tests covering the slice, the pager
  appearing only past one page, the total reporting all rows rather than the page size, the
  reset on cluster change, the shrink clamp, and the untouched empty state.

### 2026-08-08 — Fix: TopBar controls bunched mid-header; theme panel overlapped content

- The header is `flex items-center gap-4` with the search box at `flex-1 max-w-md`. Once
  search hit its max width it stopped growing, so the notification bell, colour-theme picker
  and profile menu sat immediately after it — clustered in the middle on a wide screen, with
  the right side of the header empty.

- That is what made the theme picker look like it overlapped page content: its dropdown is
  `absolute right-0 w-64`, so anchored mid-header it dropped a 16rem card over the middle of
  the page instead of hugging the edge the way the other menus do.

- `ml-auto` on the first trailing sibling absorbs the free space and pushes it — and every
  sibling after it — to the right edge. One class, no DOM restructuring, so the existing
  TopBar tests are untouched.

- Regression: `TopBar.alignment.test.tsx` (2 of 3 fail before the fix) pins that something in
  the header absorbs the free space **and** that the theme picker comes after it in DOM
  order — `ml-auto` only pushes later siblings, so order is load-bearing, not incidental.

### 2026-08-08 — Fix: the Celery queue-backlog alert could never fire

- `testlookup-alerts.yml` defines `TestLookupCeleryQueueBacklog` on
  `sum by (queue_name) (celery_queue_length) > 100` — *"Workers may need scaling."*
  **Nothing emitted `celery_queue_length`.** Verified against the live deployment's
  `/metrics`: 0 samples. The alert read as healthy forever.

- That is precisely the condition the worker CPU-throttling fix earlier today was about:
  throttled workers draining slowly would back the queue up, and the alert built to catch it
  was inert.

- The gauge is now emitted by the **backend** — the process Prometheus scrapes; a gauge set
  inside a worker never reaches the scrape — and refreshed **at scrape time** rather than by
  a beat task. A timer-refreshed gauge keeps reporting its last value when the scheduler is
  itself the unhealthy component, i.e. reports "queue is fine" during the incident it exists
  to flag. Covers all 12 queues including the 8 ingestion shards.

- Best-effort: a broken Redis never breaks the scrape, and one failing queue does not hide
  the others. Losing the whole scrape during an incident is worse than losing one gauge.

- **Static analysis got two of four wrong.** `http_requests_total` and
  `http_request_duration_seconds_bucket` looked like ghosts too — they never appear as
  `Counter(...)` in this repo — but they are emitted at runtime by
  prometheus-fastapi-instrumentator. Scraping the live endpoint is what settled it.

- Regression: `test_celery_queue_depth_metric.py` (7 of 8 fail before the fix), including a
  generalised check that no alert rule references a Celery metric nothing emits.

- **Known gap, deliberately not fixed here (F-P15):** `celery_task_runtime_seconds_bucket`
  (`TestLookupTaskLatencyHigh`) is still inert. Backing it needs the Celery workers to expose
  their own scrape target — separate processes from the backend — which is infra work beyond
  this change. It is listed explicitly in the regression test's `KNOWN_INERT` set, so a *new*
  ghost still fails while this one stays visible instead of silently tolerated.

### 2026-08-08 — Fix: four config knobs that did nothing, incl. an uncapped semantic cache

- Swept all 205 `Settings` fields for ones nothing reads. A config field nobody reads is the
  worst kind of configuration bug: the operator sets it, restarts, and behaviour is
  unchanged — it *looks* like it worked. Four were dead.

- **`SEMANTIC_CACHE_MAX_DOCUMENTS` (`= 10000  # cap ChromaDB collection size`)** — never
  read. `semantic_cache_store` upserted unconditionally, so the per-tenant ChromaDB
  collection **grew without bound for the life of the deployment**. A test elsewhere asserted
  its default value while no code consumed it. Now enforced: oldest-first eviction by the
  `cached_at` metadata the store path already wrote, pruning to 90% of the cap so it runs in
  occasional batches rather than on every store once at the ceiling. `0` means uncapped.
  Best-effort throughout — cache upkeep must never fail the analysis that triggered it.

- **`KNOWLEDGE_SYNC_TIMEOUT_SECONDS` (`= 60`)** — never read; the connectors hardcoded
  10s/15s/20s. No hang risk (timeouts existed), but an operator raising it for a slow
  air-gapped Confluence got nothing. Content fetches in the Confluence/Jira/URL connectors now
  use it. Health-check probes keep their short fixed timeouts — a liveness probe should not
  inherit a bulk-fetch budget.

- **`DEEP_CLUSTER_THRESHOLD` / `DEEP_MAX_CLUSTERS_PER_RUN`** — removed. They described a
  Jaccard similarity-clustering design that was never built: `flaky_investigator.cluster_failures`
  groups by *exact* error signature and stack fingerprint, so there was no threshold to tune
  and no cluster list to cap.

- New guard `backend.settings-are-consumed`. It found `SEMANTIC_CACHE_MAX_DOCUMENTS`, which my
  manual sweep had missed, and was verified to fail on an injected dead knob.
  `CORS_ORIGINS_RAW` is allowlisted — it is consumed by a property inside `config.py` itself.

- Regressions: `test_semantic_cache_size_cap.py` (9 of 9 fail before the fix), covering the
  boundary at exactly the cap, `0` = uncapped, oldest-first order, rows with missing
  `cached_at` evicted first rather than becoming immortal, and that a failing collection never
  raises into the caller.

### 2026-08-08 — Fix: SWR poll cadences drifted off the tier config, unenforced

- `config/refreshIntervals.ts` states *"All hooks should import from here — never hardcode
  intervals"* and defines four tiers. Nothing enforced it, and the drift ran **2:1 against
  the rule**: 10 hooks hardcoded 30 literals while 5 imported the tiers.

- Two hooks polled at **2s and 3s** — faster than `REALTIME` (5s), which the config assigns
  to *"live execution dashboards, agent pipelines"*, i.e. exactly those hooks. `useAgentRuns`
  ran 4 SWR subscriptions at 2s/3s/5s/5s simultaneously.

- Poll cadence is server load, and nobody can see the app's total request rate when it is
  spread across ten files. All literals now resolve to a tier; `refreshInterval: 0` (SWR's
  "polling disabled") and the SWR 2 function form (`useFixer`, genuinely dynamic) are left
  alone.

- **Behaviour change:** `useAgentRuns` 2s/3s → `REALTIME` (5s), following the tier the config
  already assigns to agent pipelines.

- New guard `frontend.refresh-intervals-from-config` in `scripts/quality_gate.py`, alongside
  the existing frontend conventions. Verified it fails on a reintroduced literal — and it
  caught a file this change itself had reverted mid-work.

- 726 frontend tests pass (matching the pre-change baseline exactly), typecheck clean,
  0 lint errors.

### 2026-08-08 — Perf: recharts was preloaded on every page, including login

- `manualChunks` forced recharts/d3 into a named `charts` chunk. **Naming it made it a
  shared chunk**, which rolldown then hoisted into the entry's static imports — so
  `index.html` carried `<link rel="modulepreload" href="/assets/charts-*.js">` and every
  page load fetched and parsed 529,975 raw / 183,037 gzip of charting code. Including the
  login page, where no chart renders.

- The three pages that use recharts (Overview / SuiteDetail / ValueMetrics) were already
  lazy-loaded. **A chunk being split out is not the same as a chunk being deferred** — the
  split was working exactly as configured and defeating the lazy routes anyway.

- Measured on the real build, eager assets referenced by `index.html`:

  ```
  before   891,879 raw / 251,676 gzip
  after    502,199 raw / 143,255 gzip     (-44% raw, -43% gzip)
  ```

- Chart routes get **smaller** too: left to natural chunking recharts splits into
  AreaChart / BarChart / CartesianChart totalling 391,686 raw versus the forced chunk's
  529,975, because only what is used is included.

- Guard: `frontend/scripts/check-bundle-budget.mjs`, wired into the existing CI build step
  (no extra build cost). It asserts recharts is absent from every modulepreloaded chunk and
  caps eager gzip at 180,000. **Verified it fails on the previous config** — both checks
  fire (+73,228 over budget). It also refuses to pass if it parses zero eager assets or if
  lazy chunks vanish entirely, so it cannot report success while measuring nothing.

- 726 frontend tests pass.

### 2026-08-08 — Docs: the benchmark claimed pg_trgm indexes it does not use

- The `keyword_search` scenario described itself as *"uses pg_trgm indexes from wave #5
  fix"*. Measured against the live deployment (46,990 `test_cases`), all four trigram
  indexes report **`idx_scan = 0`** — never used — while other indexes on the same table
  accumulated 414k scans over the same window. `EXPLAIN ANALYZE` confirms a Seq Scan.

- The planner is not obviously wrong: the whole predicate costs 3.8ms and `LIMIT 20` lets
  a seq scan stop early at this corpus size.

- **The predicate is not what makes search an outlier.** It measures 122.8ms p50 — 5.8x the
  median endpoint across an 18-endpoint sweep — while the filter is 3.8ms and the pagination
  count is 30.5ms. The remainder is unaccounted and is left as an open question rather than
  guessed at.

- Whether the ~20MB of unused GIN indexes (plus write amplification on the hottest insert
  path) should be dropped is a **scale** question that 47k rows cannot settle; they may well
  be chosen on a corpus an order of magnitude larger. Deliberately not answered.

- Description corrected; no behaviour change.

### 2026-08-08 — Fix: `requires_human_review` ignored the configured confidence threshold

- The gate threshold is admin-configurable (`ai_confidence_threshold`, default 80).
  `analysis_router` evaluated it, recorded `threshold_check` into `routing_metadata`, and
  set `low_confidence` / `confidence_gate_status` — but left `requires_human_review` at
  whatever the engine chose. `rules_engine` chooses it from a **hardcoded** `confidence < 70`.

- That is the field that survives: `ai_analysis` has **no** `low_confidence` column, so the
  persisted row carries only `requires_human_review`. `run_intelligence_service` serves it,
  `analytics_service` counts it as `needs_review`, and the UI falls back to it
  (`result.low_confidence ?? result.requires_human_review`). `services/agent.py` already
  derived it from the gate; the router did not, so the verdict depended on which path ran.

- **This does not misbehave at default settings, and the ledger says so.** Rules analyses
  carry no `evidence_references`, so `_validate_confidence` caps them at 50 — below both
  the hardcoded 70 and the default threshold of 80. Verified live: `pattern.oom` and
  `pattern.connection_refused` both landed at confidence 50, `requires_human_review=t`,
  `gate_passed=false` — in agreement. The disagreement needs a threshold at or below the
  capped confidence, which an admin lowering the bar to surface more AI suggestions creates.

- The router now derives `requires_human_review` from the gate, mirroring `agent.py`, so the
  configured threshold is authoritative on every path.

- Regression: `backend/tests/regression/test_requires_human_review_follows_gate.py`
  (1 of 6 fails before the fix — the other 5 pin the arithmetic and hold either way).
  502 tests pass across the confidence / gate / rules-engine slice.

### 2026-08-08 — Fix: the decision trail could not say why the LLM didn't run

- Every `ai_analysis` row on the live deployment recorded:

  ```
  mode_requested  = "auto"
  mode_resolved   = "rules"
  fallback_from   = null
  fallback_reason = null
  ```

- `auto` prefers ML, then LLM, then rules — so resolving to rules means *something* ruled
  the LLM out. `analysis_router` logged `auto_mode_ollama_model_unavailable` and then
  discarded the reason, so three different causes (Ollama unreachable / no provider
  configured / a trained ML model out-ranking it) produced **byte-identical trails**. The
  record that documents itself as authoritative could not answer the first question an
  operator asks when AI analysis looks thin.

- Added `resolve_analysis_mode_with_reason()` returning `(mode, reason)`;
  `get_analysis_mode()` is now a thin wrapper that discards the reason, so no caller
  changes. The pipeline's mode snapshot carries `resolution_reason` into
  `AIAnalysis.routing_metadata`.

- The existing structural guard asserted `get_analysis_mode` by *name* in the snapshot
  function; it now accepts either spelling and additionally requires `resolution_reason`,
  so it pins the behaviour rather than the identifier.

- Regression: `backend/tests/regression/test_mode_resolution_reason_is_recorded.py`
  (8 of 8 fail before the fix), including a test that the two rules-resolving paths give
  *different* reasons — identical strings would restore the useless state.

### 2026-08-08 — Fix: rules-mode analyses recorded provenance claiming an LLM ran

- `_validate_confidence` post-processes the output of **every** analysis engine, but its
  adjustment reasons were written as though an LLM had always produced the result.

- Observed on the live deployment — a real `ai_analysis` row with no model call in it:

  ```
  llm_provider  = none
  llm_model     = rules_engine
  analysis_mode = rules
  confidence_adjustments = [
    {"rule": "no_evidence_references",
     "reason": "LLM returned no evidence_references"},        <-- no LLM ran
  ]
  ```

- `confidence_adjustments` is served by `decision_trail_service` and typed in
  `frontend/src/types/decisionTrail.ts`, so this is provenance on the AI-trust contract
  asserting a model call that never happened — the same family as the fabricated
  confidence scores removed earlier.

- **Not an edge case.** `auto` resolves to rules whenever no ML model is trained and
  Ollama is unreachable, which is the normal path on air-gapped and CPU-only installs.
  The homelab resolves to rules on every analysis.

- Reasons now name the engine that actually ran (`rules engine` / `ML model` / `LLM`),
  degrading to a neutral `analysis` for an unrecognised mode rather than defaulting back
  to "LLM" — that default was the bug.

- Regression: `backend/tests/regression/test_confidence_adjustment_provenance.py`
  (6 of 7 fail before the fix), including a test that the LLM path still says "LLM" so
  the fix cannot scrub accurate provenance.
### 2026-08-08 — Fix: Celery workers ran more processes than their CPU limit allowed

- `--concurrency=N` forks N worker processes. Three of the four workers had a cgroup
  `limits.cpu` **below** N cores, so those processes could not exceed the cap between
  them and the concurrency flag promised parallelism the cgroup would not allow:

  ```
  worker        --concurrency   limits.cpu   ratio
  critical            2           1000m       2:1
  ingestion           4           1000m       4:1
  default             4            500m       8:1
  ai                  2           2000m       1:1   <- the only correct one
  ```

- **Measured** on the ingestion worker, 80-run burst, reading `/sys/fs/cgroup/cpu.stat`
  directly (metrics-server's ~60s scrape is far too coarse for a burst):

  ```
  limit 1 core ..... throttled 36-74x per pod, every run .....  5.0 runs/s
  limit 4 cores .... throttled 0x ............................. 23.5 runs/s
  ```

  Back to back at the same corpus size. Peak demand was 1.46 cores in one pod.

- **The CPU average hides this.** It read 0.64 of 1.00 cores during a throttled run —
  apparent headroom — because averaging over the window includes the idle tail that
  throttling itself creates. `nr_throttled` is the field that settles it.

- Limits raised to match concurrency; `requests` deliberately unchanged. A limit is a
  ceiling, not a reservation, so this costs nothing while idle.

- Only the ingestion worker was measured. `critical` and `default` are corrected on the
  same arithmetic and are labelled as such in the manifest.

- Regression: `backend/tests/regression/test_worker_cpu_limits_match_concurrency.py`
  asserts `limits.cpu >= --concurrency` for every worker — 3 of 6 fail before the fix,
  exactly the three misconfigured workers.

### 2026-08-08 — Fix: throughput budgets were defined but nothing could read them

- `performance_budgets.py` calls itself the single source of truth and carries four
  `THROUGHPUT_BUDGETS`. They were only ever serialized into `get_all_budgets()` — a
  reporting dict. **There was no lookup accessor**, so no caller could check one, and
  the load harness compared p95 latency only.

- Not theoretical. Measured on the live deployment, `keyword_search` throughput:

  ```
  postgres capped at 1 core .....  17.4 rps   vs the 20.0 budget   BREACH
  postgres raised to 4 cores ....  28.7 rps   vs the 20.0 budget   pass
  ```

  The same regression that blew the search latency budget also blew a *second*
  written-down budget, and the harness printed "All budgets met" both times.

- Added `get_throughput_budget()`; `Scenario` gained `throughput_op`, bound on the two
  search scenarios; `--check-budgets` now evaluates measured rps alongside p95 and
  counts each as a check.

- Budgets this harness does not drive (`run_ingestion`, `live_events_batch`) are now
  **named as unchecked** rather than silently omitted — a budget nothing exercises is
  not a passing budget, and omitting it is what let the summary line imply coverage
  the run never had.

- `throughput_op` is set only where the mapping is real. A throughput budget describes
  a workload; binding one to an unrelated scenario would manufacture pass/fail signal
  out of an unrelated measurement, which is the exact failure being fixed.

- Regression: `backend/tests/regression/test_throughput_budgets_are_checked.py`
  (6 of 7 fail before the fix), including a guard that every declared `throughput_op`
  resolves to a real budget — an unresolvable name returns 0 and is skipped, which is
  how the latency gate went inert in the first place.

### 2026-08-08 — Fix: the "Total executions" KPI counted runs, not executions

- `metrics_service` built the dashboard KPI from the run count:

  ```python
  total_exec = cur["total_runs"]          # func.count(TestRun.id)
  "total_executions_7d": {"value": total_exec, ...}
  ```

- **Measured live** on the seeded project (5 runs x 12 tests):

  ```
  coverage.summary.total_executions   60     ← executions
  summary.total_executions_7d          5     ← runs, labelled "Total executions"
  recomputed from run rows            60     ← ground truth
  ```

  A **12x understatement** on a headline KPI. Both the field name
  (`total_executions_7d`) and the UI label (`label="Total executions"`) say executions — the
  *value* was the outlier, so the value is what changed.
- Root of the substitution: the unfiltered branch of `_period_stats` never selected `sum_total`,
  while the **suite-filtered branch already did**. With no executions figure available on that
  path, the run count was the nearest thing to hand.
- **Release readiness is deliberately untouched.** `total_exec` stays a run count where it feeds
  `if total_exec <= 0` (the "no evidence in the window" gate) and `_compute_readiness(total_runs, …)`.
  Neither has a threshold that scales, so swapping in a 12x larger value would not have changed
  today's verdicts — but it would have made the readiness contract silently wrong. Runs and
  executions are now separate values, and a test asserts `_compute_readiness` still uses its
  argument only as `<= 0`, so acquiring a scaling threshold later fails loudly.
- Found by a cross-surface consistency audit: comparing one quantity across every endpoint that
  reports it. Pass rate and unique-test counts came back consistent in the same sweep.

### 2026-08-08 — Fix: per-suite pass rate counted skipped tests in the denominator

- **Product decision:** a skipped test is *not evaluated* — it never ran, so it is neither a pass
  nor a failure and belongs in no pass-rate denominator.
- Most of the codebase already worked that way (`analysis_report_service`, `metrics_service._evaluated`,
  and the `coverage_stats` **summary** block). The **per-suite rows** were missed. Measured live:

  ```
  summary.avg_pass_rate = 81.0                        ← matches the dashboard
  suites[api]: passed=20 failed=3 skipped=2  pass_rate=80.0
  ```

  `20 / (20+3+2) = 80.0`. Excluding skips gives `20 / 23 = 87.0`. One response carried a correct
  summary rate and an inconsistent per-suite rate, so a user comparing a suite against the headline
  saw a gap with no remaining cause.
- `skipped` is still reported per suite, and `total_executions` deliberately stays `COUNT(*)` — a
  volume figure, not a rate. Only the **rate** excludes skips.
- Worth recording: the summary block's own comment claimed it was *"the last surface still dividing
  by COUNT(*)"*. It wasn't — the suite rows in the same function still were. **A comment asserting
  completeness is not evidence of it.**

### 2026-08-08 — Fix: `/analytics/defects` counted defects it refused to list

- `analytics_service.list_defects` ran two queries that disagreed by construction:

  ```sql
  -- rows
  FROM defects d
  JOIN test_cases tc ON tc.id = d.test_case_id     -- INNER JOIN

  -- total
  SELECT COUNT(*) FROM defects d                   -- no join at all
  ```

  Any defect with a NULL `test_case_id` was dropped from `items` and still counted in `total`.
- **Confirmed live** on a project with five defects:

  ```
  GET /api/v1/analytics/defects?project_id=<pid>&days=90
  {"items": [], "total": 5, "page": 1, "size": 20, "pages": 1}
  ```

  An empty list, a total of five, and a page count derived from the total.
- **A NULL `test_case_id` is normal, not exotic** — two independent parts of the system produce it:
  - `_find_recent_test_case_id`, whose own docstring says *"Returns None when no match is found —
    the caller stores the defect with a NULL test_case_id rather than failing the intake."* The
    ordinary intake path creates these rows whenever a failure signature matches no current test
    case.
  - `ForeignKey("test_cases.id", ondelete="SET NULL")` — the schema deliberately lets defects
    outlive their test cases, which is exactly what the retention purge (US-11.4) does. A defect
    whose test case was purged goes invisible while still inflating the count.

  The table's partial unique index (`resolution_status = 'OPEN' AND test_case_id IS NOT NULL`) is
  further evidence the NULL case was designed for.
- Fix: `LEFT JOIN test_cases`. The joined columns come back NULL, which the `dict(row._mapping)`
  response already handles. Making the *count* match the inner join was the other option and is
  worse — it would hide legitimate defects consistently instead of showing them.
- The regression test pins the general invariant, not just this table: **no row-eliminating join
  may appear in the row query without also appearing in the count query.**

### 2026-08-08 — Fix: the Overview blockers panel described its own number three ways, two of them false

- `new_failures_24h` is a **fixed 24-hour** count computed in `metrics_service` as
  `status == FAILED AND created_at >= now - 24h`. It ignores the dashboard's time-window
  selector entirely — verified live against a freshly-ingested run:

  ```
  days=1   new_failures_24h=3
  days=7   new_failures_24h=3
  days=30  new_failures_24h=3
  days=90  new_failures_24h=3
  ```

- Yet the panel described that one number three different ways in the same box. Captured from
  the live page with a 7-day window selected:

  ```
  badge:  "3 new · 24h"                                      ← correct
  body:   "3 new failures in the window."                    ← 7 days, not 24 h
  footer: "Showing the 3 failures since the last green run"  ← no green run involved
  ```

  The empty state carried the same claim (*"No failing tests since the last green run."*).
- *"Since the last green run"* is the most misleading of the three: it names a
  regression-since-green baseline that appears nowhere in the computation, and it would drive
  different triage than "failed in the last day".
- Fix: the copy now says 24 h consistently. The badge and the KPI label both already said 24 h,
  so the metric's intent was never in doubt — only the prose disagreed with it. A comment on
  `BlockersPanel` records why the panel is window-independent, so the next person doesn't
  "helpfully" reintroduce the window wording.

### 2026-08-08 — Fix: a transient `kubectl` blip aborted the whole deploy at Step 3

- The namespace guard conflated two very different failures:

  ```bash
  if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then ... else kubectl create namespace ...
  ```

  `get` exits non-zero both when the namespace is absent **and** when the API server cannot be
  reached. This cluster's control plane is intermittently interrupted (Norton intercepts `:6443`
  → `wsarecv: An existing connection was forcibly closed`), so `get` failed spuriously, the
  `else` branch ran `create`, and `AlreadyExists` aborted the run under `set -euo pipefail` —
  **after** the images had been built and pushed, which is the expensive part.
- **Observed twice**: `deploy11.log` and `deploy14.log`, both ending at Step 3 with
  `DEPLOY_EXIT=1`. Not bad luck — a reliability defect.
- Fix: `kubectl create namespace --dry-run=client -o yaml | kubectl apply -f -`. Idempotent, so
  a spurious read failure is harmless, while a genuine API outage **still fails loudly** because
  `apply` itself errors. Both properties are pinned by tests, because the tempting "fix" here is
  `|| true`, which converts a noisy abort into a silent half-deploy.
- **Test-validity note.** The first version of these tests stubbed `kubectl` via a script on
  `PATH` and appeared to pass. It was not being used: under Git-for-Windows bash an extensionless
  stub is not treated as executable, so `command -v kubectl` resolved past it to the real binary
  and the tests were quietly talking to the live cluster (`Error from server (NotFound)` gave it
  away). `os.access(path, os.X_OK)` returns True for any existing file on Windows, so it does not
  catch this. The stub is now a **shell function**, which shadows the external command
  unconditionally and needs no PATH, no chmod, and no production change.

### 2026-08-08 — Security: `/audit-dashboard/events` leaked other tenants' audit trails

- The router's docstring promised *"Tenant isolation: non-admin users only see events for their
  projects."* Nothing implemented it. Three separate defects:
  1. **Unscoped fall-through.** The short-circuit fired only for a caller with **zero**
     memberships. A member of even one project fell through with `project_id=None` into a
     completely unscoped query.
  2. **A named project was never checked.** `project_id` was treated as a filter, never as a
     permission — supplying a project you cannot access returned 200.
  3. **Instance-wide sources always included.** `SettingsAuditLog` and `IdentityEvent` have no
     `project_id` column, so their sub-queries were never filtered by anything; every QA_LEAD
     received the whole instance's settings-change and SSO/SCIM history.
- **Confirmed live.** A QA_LEAD made a member of exactly one throwaway project (control:
  `GET /projects/<seeded>` → 403, one project visible):

  ```
  GET /audit-dashboard/events?days=365&page_size=50        (no project_id)
  total=78  rows=50  by_source={access: 12, settings: 10, test_management: 28}
  rows carrying ANOTHER project's id: 28
    test_management sync_deleted 3dfc96cf-37e6-4ea0-884d-9a82480479a7

  GET /audit-dashboard/events?project_id=<inaccessible>    200 (not 403)
  ```

- **An earlier pass filed this as "needs confirmation" and understated it.** The probe account
  had **zero** memberships, so it hit the early-return — the one path that behaved. Adding a
  single membership exposed the cross-project leak. A negative result from an account with no
  memberships proves nothing about tenant isolation.
- Fix: `resolve_project_scope` in the router (403 for a named project the caller cannot access)
  and a real `allowed_project_ids` scope inside `query_unified_audit`. An empty membership set
  produces a false predicate, not an unfiltered query. The two sources with no project column
  are **omitted** for a restricted caller rather than leaked — they cannot be attributed to a
  project, and the alternative is handing every QA_LEAD the instance's settings history.
- `/audit-dashboard/export` is unchanged: it already requires ADMIN, who is unrestricted by
  design.

### 2026-08-08 — Security: `POST /search/reindex` had no authorization at all

- The endpoint took neither a user nor a role, and never validated `project_id`:

  ```python
  @router.post("/reindex")
  async def trigger_reindex(project_id: str | None = None, full: bool = False):
      task = reindex_search.apply_async(kwargs={"project_id": project_id, "full": full})
  ```

  Every other endpoint in `routers/search.py` depends on `get_current_active_user`.
- **Confirmed live.** Unauthenticated access *is* stopped by the global auth middleware, so this
  was never an anonymous vector: `POST /api/v1/search/reindex` with no header → **401**. But
  authentication was the only barrier. As a **VIEWER** — the lowest role, holding **zero project
  memberships**:

  ```
  POST /api/v1/search/reindex?full=true
  200 {"task_id":"d96026cb-…","status":"queued","mode":"full"}
  ```

  A full rebuild of the entire instance's search index, queued by a user who cannot see a single
  project.
- Fix: authorization now mirrors the blast radius rather than one flat role. A **named project**
  requires QA_LEAD **and** membership (`resolve_project_scope`); the **unscoped instance-wide**
  rebuild requires ADMIN, since it is the only variant that crosses tenants.
- No UI impact: `searchService.reindex` exists but no component calls it.
- **Latent mismatch noted, not changed:** that service sends `project_id` in the request *body*
  while the endpoint reads it from the *query string*, so a future caller would silently hit the
  global (now ADMIN-only) path. Flagged rather than fixed here to keep this change to the
  security defect.
- Found by sweeping the class behind the digests IDOR: endpoints taking `project_id` outside a
  path param, which the authorization ratchet cannot see. Most sweep hits were false positives
  (`ingest`, `api_keys`, `knowledge_sources`, `summary_report`, `set_suite_owner` are all
  properly guarded, several via inline logic or a service-layer check a grep misses).

### 2026-08-08 — Security: the digests router took `project_id` from the caller unchecked

- Two endpoints in `routers/digests.py` accepted a client-supplied `project_id` and never
  verified membership.
- **`GET /api/v1/digests/preview`** — the access check ran **only when `project_id` was
  absent**, i.e. only when there was nothing to guard:

  ```python
  if not project_id:
      accessible = await get_accessible_project_ids(db, current_user)
      if accessible is not None:
          return DigestContentResponse(...)      # empty
  digest = await generate_digest(db, project_id, period)   # unguarded
  ```

- **`POST /api/v1/digests/subscriptions`** — no check at all; `payload.project_id` went
  straight onto the row.
- **Confirmed live** with a QA_ENGINEER holding no membership in the target project. Control
  first: `GET /projects/<pid>` → **403**, `GET /projects` → **0 visible**. Then:

  ```
  GET  /api/v1/digests/preview?project_id=<pid>
       {"project_name":"Checkout Service","total_runs":5,"avg_pass_rate":81.1,
        "new_regressions":5,"latest_run_total_tests":12,...}

  POST /api/v1/digests/subscriptions {"project_id":"<pid>",...}
       201 → is_active:true, schedule:DAILY, report_attachment:true, next_delivery_at:<tomorrow>
  ```

- The subscription is the more serious of the two: the delivery task in `worker/tasks.py` reads
  `project_id` off the claimed row and calls `generate_digest` with it, looking the user up only
  to obtain an email address. **Nothing re-checks membership at send time**, so the row is a
  standing instruction to mail another tenant's digest — with the full HTML analysis report
  attached — on a schedule.
- **Why the architectural ratchet missed it:** `test_architectural_authorization.py` matches
  routers whose *path* declares `{project_id}`. Here the id arrives as a query parameter and a
  body field, so the class is outside what that ratchet inspects. Grepping for the guard also
  looked fine — `get_accessible_project_ids` *is* imported and called in `preview`, just in the
  branch that cannot leak.
- Fix: both endpoints resolve scope via `resolve_project_scope`, which raises 403 for a
  non-admin naming a project they are not a member of and leaves ADMIN unrestricted. ADMIN
  behaviour and the "no project named" empty-preview behaviour are unchanged.
- `saved_view_id` is accepted by the same payload but is never read by
  `digest_content_service` or the delivery task, so it carries no data and is deliberately not
  covered.

### 2026-08-07 — Fix: `deploy-homelab.sh --skip-build` aborted on a correct answer

- The flag exists for one situation: a deploy whose images built and pushed fine but whose apply
  phase died partway, so you resume without paying for a ~15-minute rebuild. It never worked.
- `_existing_common_tag` intersected the three registry tag lists with
  `comm -12 <(... | sort -ru) ...`. **`comm` requires ascending input.** Given descending input
  it warns `input is not in sorted order` on stderr and **exits 1** — while still writing the
  correct answer to stdout. Measured against the live registry (97 backend / 128 frontend /
  98 mcp tags):

  ```
  comm OWN exit = 1
  stdout lines  = 21, first = build-20260807-231731     ← the right answer
  ```

- Under the script's `set -euo pipefail` that status propagated out of
  `BUILD_TAG=$(_existing_common_tag)` and killed the run. Two consequences, both confirmed by
  emulating the script:
  1. `--skip-build` aborted every time, **on a correct answer**.
  2. The author's own "no build-YYYYMMDD-HHMMSS tag exists for ALL THREE images" diagnostic was
     **unreachable** — the script died before evaluating `[ -z "$BUILD_TAG" ]`, so an operator
     saw only `comm: file 1 is not in sorted order`.
- Fix: sort **ascending** for `comm` (what it requires), then reverse to take the newest. Uses
  `sed -n '1p'` rather than `head -n 1`, because `head` exits after one line and SIGPIPEs `sort`,
  which `pipefail` would also treat as a failure. The selection step is split into
  `_newest_common_tag`, taking the three lists as arguments so it is testable with no registry.
- **Found by hitting it.** The first deploy of the UI fixes died at Step 3 when the
  `if kubectl get namespace` guard caught an intermittent connection reset and the `else` branch
  ran `kubectl create namespace` into an `AlreadyExists`. The documented recovery — re-run with
  `--skip-build` — then failed for this separate reason.
- Regression test: `backend/tests/regression/test_deploy_tag_resolution.py` runs the function
  under the same `set -euo pipefail`, asserting the **exit status** as well as the value, since
  "correct result, non-zero status" is the failure mode that made this look like a registry
  problem.
- **The fixture choice is load-bearing.** `comm` reports disorder only when the merge has to
  advance one side past the other, so three *identical* lists walk in lockstep and the broken
  implementation exits 0 on them. A first draft of this test used identical lists and passed
  against the unfixed script — it could not have caught the bug. Measured against the pre-fix
  code: identical x3 → `rc=0`; one list shorter → `rc=1`; 97/128/98 (the real registry) →
  `rc=1`. Every case now uses lists that differ, and the suite is verified to fail pre-fix and
  pass post-fix.

### 2026-08-07 — Fix: `/search` "Queries today" invented a number

- The KPI was fed `recents.length * 24` — the count of searches in **this browser's**
  `localStorage` (`tl.search.recent`), times an arbitrary 24 — and rendered through a compact
  number formatter as a platform metric.
- **Measured live** with three seeded recent searches:

  ```
  INDEX FRESHNESS    —static
  LATENCY P95        —ms
  QUERIES TODAY      72   no data      ← 3 × 24
  ZERO-RESULT RATE   —target ≤ 5%
  ```

  The tile displayed a number **while its own sub-label said "no data"**, and it was the only
  one of the four that didn't degrade honestly — the other three already render an em dash when
  their metric is unavailable.
- No query-volume metric exists in the backend (the source called it "a P2 backend ask"), so the
  honest rendering is the em dash its neighbours use. `queriesToday` is now `number | null`, so
  when that endpoint lands the real value flows straight through.
- Found by a targeted sweep for this class after the compliance-pack checksum
  (the compliance-pack rail) — both were disclosed in plain English in their own source comments.

### 2026-08-07 — Fix: the compliance-pack rail displayed a fabricated SHA-256

- The "Compliance packs" panel on `/releases` rendered, for a release with **no pack at all**:

  ```
  ZZ Probe Release 9.9.9
  4.2 MB · sha256:d6b975f7…            [Download]
  ```

  Confirmed live. Neither value came from a compliance pack:
  - `sha256:` was `fakeSha(id)` — a 32-bit `hash * 31 + charCode` over the release **UUID**,
    zero-padded to 8 hex chars. Not SHA-256, not computed over any archive, and identical on
    every render because it depended only on the id.
  - `4.2 MB` was a string literal, shown for every released row.
  - `GET /api/v1/releases/{id}/compliance-packs` returned `[]` for the very release showing
    that checksum.
  - Both buttons were `<button type="button">` with no `onClick` — clicking did nothing.
- The rows were derived from release **stage** alone; the panel never fetched a pack, so it
  had no basis for any of it.
- This is the worst possible place for a placeholder: the feature's premise is *"sealed with a
  tamper-evident SHA-256 checksum chain"*, and a fabricated `sha256:` beside a release name in
  a panel titled "Compliance packs" is exactly the pixel an auditor would screenshot. The app
  already ships the honest implementation — `CompliancePackPanel` lists real packs and
  downloads real bytes carrying the real `manifest_sha256` — so this was a decorative mock
  sitting next to working code.
- Fix: the rail cannot list real packs (it is client-derived from the portfolio's
  `DerivedRelease[]`; packs are a per-release fetch), so it becomes what it can honestly be —
  a pointer to the releases *eligible* for a pack, whose action opens the release and its real
  pack panel. The invented checksum, the invented size, and the "pack pending" claim are gone,
  and the previously inert buttons now work.
- Same class as the two fabricated "AI confidence" values removed earlier.

### 2026-08-07 — Fix: frontend telemetry silently discarded the batch it failed to send

- `flush()` in `utils/errorReporting.ts` emptied the buffer **before** calling
  `navigator.sendBeacon`, and ignored the boolean it returns. `false` does not mean "sent" —
  it means the user agent refused to queue the request. The events were already gone.
- **Measured live** (Chromium, error-boundary reports carrying a 4 KB stack + 2 KB component
  stack, the shape the reporter actually captures):

  | batch | bytes | `sendBeacon` |
  |---|---|---|
  | 1 error | 6,303 | `true` |
  | 5 errors | 31,419 | `true` |
  | 10 errors | 62,814 | **`false`** |
  | 20 errors | 125,614 | **`false`** |

  The same 125,614-byte body over `fetch` was accepted with **202**, so the payload was never
  the problem — only the transport has the limit (Chromium's per-origin beacon quota is 64 KB).
- Ten boundary errors inside one 5 s batch window is not exotic: a component throwing on
  every render produces them in a fraction of a second — which is exactly the incident the
  report exists for. The failure was silent in both directions, since the `catch` blocks are
  deliberately empty ("never throw from error reporting"), so a page could melt down and the
  backend would see nothing at all.
- The pre-existing `fetch` fallback was **unreachable**: it sat in the `else` of
  `if (navigator.sendBeacon)`, so it only ran in a browser with no `sendBeacon` — i.e. never.
- Fix: batches are split into pieces that fit under the quota; a refused piece is put **back**
  into the buffer and retried instead of dropped; stacks are truncated at capture (4000 /
  2000 chars — the backend already truncates at 5000, so nothing extra is lost) so one
  enormous stack cannot produce an unsendable chunk; and the buffer is bounded (50 errors /
  50 vitals) with the overflow **reported as a synthetic event** rather than dropped quietly,
  so the backend can tell a truncated report from silence.
- Found while settling a parked question from the exploratory walk (`requestfailed` on
  `POST /api/v1/observability/frontend` across 17 of 18 pages). That turned out to be a
  **measurement artifact** — the probe navigated away with no dwell, so the only flush that
  could run was the `pagehide` one, aborted during teardown. With a 12 s dwell the telemetry
  posts 202 normally. `tests/probe-telemetry.spec.ts` records both results, and keeps the
  quota measurement live so a future UA moving the limit reports the new number.

### 2026-08-07 — Fix: flaky frontend test (`AIConfigPage` "re-checks on demand")

- CI failed intermittently with
  `TestingLibraryElementError: Unable to find an element with the text: Re-check`,
  the DOM still showing the loading placeholder. Confirmed **pre-existing on main** (run
  31153043675 had the identical failure), not introduced by any branch.
- Root cause: the test waited on a **mock call count**
  (`waitFor(() => expect(mockGetModelStatus).toHaveBeenCalledTimes(1))`) and then immediately
  queried the **DOM**. The mock is invoked before its promise resolves, and the page's
  loading placeholder is gated on the *config* fetch — so the wait proved nothing about
  whether the button had rendered.
- **Reproduced deterministically** by delaying `mockGetAIConfig` 80ms: the old pattern fails
  with the exact CI error, the new one passes under the identical delay.
- Fix: `await screen.findByText('Re-check')` — the correct synchronisation point, and a
  stronger assertion, since it proves the button actually rendered.
- A scan found this was the **only** instance of `waitFor(mock…)` immediately followed by a
  DOM query across all 118 frontend test files.
### 2026-08-07 — Fix: `/stream/active` skipped project_id validation for admins

- As an ADMIN, `GET /api/v1/stream/active?project_id=all` (and `?project_id=not-a-uuid`)
  returned **200 with an empty session list**; a non-admin sending the identical value got a
  clean **400**. The live dashboard silently showed nothing, with no error to explain it.
- Root cause: the UUID parse and its 400 were nested inside `if accessible is not None:`.
  `get_accessible_project_ids()` returns `None` for an ADMIN, so the whole block was skipped
  and a raw string reached the query.
- **Same role-dependent shape as the `/metrics` 500 fixed earlier this session** — the check
  that guards the value nested inside the check only non-admins trigger. Only the symptom
  differs: this query degrades to "no match" instead of raising, and an empty page with no
  explanation is the harder failure to diagnose, not the easier one.
- `ALL_PROJECTS_ID` ("all") is a frontend-only sentinel, so one stale link or missed SPA
  guard produces it.
- Validation now runs first, for every role; the 403 for a real-but-inaccessible project is
  preserved, as is the unscoped admin view when no `project_id` is passed.
- 6 regression tests, verified 3 failed → all pass; 28 passed across the live/stream suite.

### 2026-08-07 — Fix: ROI `flaky_tests_identified` disagreed with the coach headline (F-010, completing)

- The earlier F-010 fix corrected the coach's `total_flaky` response field but left
  `value_metrics_service` doing `select(count(FlakyCoachResult.id))`. On 30 days of history
  the same project reported **coach `total_flaky` = 1** and **ROI
  `flaky_tests_identified` = 5**.
- **Found by seeding multi-day data.** The previous 5-run, single-timestamp fixture could not
  produce a coach table mixing oscillating and persistently-broken entries, so the divergence
  had nowhere to show. This is the coverage gap the exploratory ledger had recorded.
- The two numbers legitimately differ in *source*: the coach table deliberately KEEPS
  persistent regressions (downgraded recommendation + "treat as a regression" advice, FLK-P1).
  Their presence as rows is correct; counting them as **flaky** is not — and this one is an
  ROI figure that gets quoted.
- Extracted the coach's predicate into `test_health_coach_service.history_is_intermittent`;
  both surfaces now call it, so a third definition cannot appear. `quarantine_recommended`
  deliberately still counts QUARANTINE rows — a different question.
- 14 regression tests, verified 3 failed → all pass; 414 passed across the
  coach/flaky/value-metrics/quarantine suites.

### 2026-08-07 — Fix: module-level `AsyncSessionLocal` pinned a disposed engine (F-027 root cause)

- Bulk ingest failed on most first attempts with
  `asyncpg InterfaceError: cannot perform operation: another operation is in progress`,
  always at `finalize_run`'s first query. **Found by instrumenting the live worker** after two
  earlier hypotheses were disproved by measurement.
- Instrumentation for one 60-run ingest: **32 tasks, 32 engine builds, 32 successful disposes,
  0 dispose failures — and 162 errors.** Every task built and tore down its own engine
  cleanly, which eliminated fork inheritance (`engine_cached=0` in all 4 children), stale-loop
  teardown, and cross-task pool reuse simultaneously.
- **Root cause:** PEP 562 `__getattr__` runs *once per importing module*, so
  `from app.db.postgres import AsyncSessionLocal` at **module level** permanently binds that
  factory. `worker/tasks.py::_run_async` disposes the engine and clears both `@lru_cache`es
  after every task — so modules importing **inside a function** (`tasks.py`) re-resolved and
  got a fresh factory, while **module-level** importers (`ingestion_pipeline`, `ingestion`,
  ~38 others) kept the factory of the **disposed** engine. Deterministic failure from task #2.
- Teardown was never broken; a stale *reference* surviving it was. That is why the dispose
  code reads correctly and the bug persisted.
- **Fix:** `AsyncSessionLocal` resolves to a callable proxy — module-level binding stays
  stable, resolution happens late. **No call site changed.**
- Also: `_run_async`'s teardown `except Exception: pass` now logs. Silent failure is what let
  this hide.
- **Verified live** on `build-20260807-221334` with the same 60-run ingest that exposed it:

  | | before | after |
  |---|---|---|
  | finalized at t+0 | 8 / 60 | **60 / 60** |
  | still IN_PROGRESS after 5 min | 48–50 | **0** |
  | "another operation is in progress" | 162 | **0** |
  | `InterfaceError` | 358 | **0** |
  | task retries | many | **0** |
  | aggregates | 40 executions, 92.5% | **600 executions, 90.0%** (ground truth) |

- 9 regression tests, verified 4 failed → all pass; 253 passed across the DB/worker/ingest
  suites. `backend.analysis-router` baseline refreshed (line-keyed guard; the two entries are
  pre-existing allowlisted calls that shifted when instrumentation was removed).

### 2026-08-07 — Fix: failure-category items contradicted by_kind (F-015) (WIRE-SHAPE CHANGE)

- `GET /analytics/failure-categories` returned, in ONE payload:
  `items: [{category: "UNKNOWN", count: 11, kind: "unknown"}]` while `by_kind` correctly
  reported 2 of those 11 as `infrastructure`. Items were aggregated per *category* then
  labelled with `failure_kind(category, None)` — a category-only derivation that cannot
  apply the BROKEN nudge, because the nudge needs the per-row status the aggregate discarded.
- **Not cosmetic:** `FailureAnalysisPage` FILTERS the category distribution card on
  `item.kind`, so selecting "infrastructure" silently missed genuine infrastructure
  failures — the one kind that must never be triaged as a product bug. The page's
  client-side `by_kind` fallback also sums `item.count` per `item.kind`, inheriting the
  same error.
- **Wire-shape change:** `items` is now grouped by **(category, kind)**, so a category may
  appear once per kind. That is faithful — a category legitimately contains failures of more
  than one kind — and every count is preserved exactly.
- **Consumer check:** the MCP `get_failure_categories` tool sums `item.count` for its total,
  which splitting preserves; only listing granularity changes, and it becomes more accurate.
  `OverviewPage` reads `by_kind`, which is untouched.
- An existing test pinned the old shape *and its contradiction* ("Historical per-category
  items preserved… category-only derived kind"). It was updated with the reasoning recorded
  inline, not silently re-valued.
- 8 regression tests, verified 3 failed → all pass; 164 passed across the
  analytics/failure-kind/coverage/overview suites.

### 2026-08-07 — Fix: Coverage counted skipped tests in its pass rate (F-014)

- Coverage read **78.3%** where the dashboard read **81.0%** for the same project and window
  (47/60 vs 47/58). Coverage divided by `COUNT(*)`, which includes SKIPPED.
- The codebase already states the rule in **four** places — `analysis_report_service`
  ("skips don't count"), `ingestion._update_run_aggregates`, `metrics_service._evaluated`,
  and `metrics_service.get_trend_data`. Coverage was the sole outlier, so it moved.
- Denominator is now `passed + failed + broken`. **`total_executions` deliberately keeps
  `COUNT(*)`** — a skip genuinely is an execution — and a test pins that distinction, since
  collapsing the two would silently change a different, correct number.
- 10 regression tests, verified 1 failed → all pass; 205 passed across the
  coverage/analytics/metrics suites.

### 2026-08-07 — Fix: Flaky Coach `total_flaky` counted non-oscillating tests (F-010)

- For one project at one moment the app reported three different numbers: dashboard
  `flaky_test_count` **2**, `/failures` verdict **2**, flaky coach `total_flaky` **5**.
- Not cosmetic: `value_metrics_service` counts `FlakyCoachResult` rows into
  `flaky_tests_identified`, so the looser definition was being reported as a
  **stakeholder-facing ROI figure**.
- `total_flaky` now counts only entries that actually oscillate (≥2 pass↔fail transitions),
  derived from the stored `status_history` — **no migration**. Manual-triage rows carry a
  sentinel history and are always counted: a human's call outranks the heuristic.
- **The list is deliberately unchanged.** A first attempt filtered persistent regressions out
  of the coach entirely; an existing test caught it
  (`test_flaky_signals::test_refresh_downgrades_persistent_regression_off_quarantine_track`),
  which pins FLK-P1 behaviour where a regression IS surfaced with a downgraded recommendation
  and "treat as a regression, not a flake" advice. Removing those rows would have deleted
  useful triage information and made that copy unreachable. Only the count was wrong, so only
  the count changed — and a test now pins that the list is not filtered.
- All four surfaces now share `flaky_signals.MIN_FLIPS_FOR_INTERMITTENCY` by construction.
- 12 regression tests, verified 2 failed → all pass; 400 passed across the
  coach/flaky/quarantine/value-metrics suites.

### 2026-08-07 — Fix: prefork workers inherited the parent's DB connection pool

- Bulk ingest failed on most FIRST attempts with
  `asyncpg InterfaceError: cannot perform operation: another operation is in progress`
  during `_update_run_aggregates`. That message means one connection was driven by two
  coroutines at once.
- Mechanism: Celery's default pool is **prefork** — the ingestion worker runs
  `--concurrency=4`, i.e. four children forked from one parent. `get_engine()` /
  `get_session_factory()` are `@lru_cache`'d, so anything touching the DB in the parent
  before the fork leaves every child sharing one SQLAlchemy pool — and the same open asyncpg
  **sockets**. There was **no Celery signal handler of any kind** in `celery_app.py`.
- `worker/tasks.py::_run_async` already handles the *event-loop* half of this (BUG-003: a
  pool bound to a since-closed loop). It cannot help here — that is per-process bookkeeping,
  this is one pool shared *across* processes by `fork()`.
- Added a `worker_process_init` handler that gives each child its own engine. It **clears**
  the caches and deliberately does **not** `dispose()` — disposing inside a child would close
  sockets the parent and sibling children still hold. A test asserts that distinction.
- 8 regression tests, verified 5 failed → all pass; 146 passed across the worker/celery/task
  suites.
- **Verification status:** the mechanism is confirmed from the traceback and the worker
  configuration, but the end-to-end proof is a fresh 60-run bulk ingest showing the
  InterfaceError gone. Result recorded in the exploratory ledger (F-027).

### 2026-08-07 — Fix: a retried ingest could never succeed (runs stuck IN_PROGRESS forever)

- Bulk-ingesting 60 runs via `POST /api/v1/ingest/file` left **56 of 60 permanently
  `IN_PROGRESS`** with zero progress across repeated polls. The ingestion worker showed
  **38 distinct task ids** in retry loops and **145 duplicate-key events**:
  `UniqueViolationError: duplicate key value violates unique constraint "test_runs_pkey"`,
  retrying every ~2 minutes. Redis queue depths all read **0** — the tasks were in retry-ETA,
  not queued, so "the queue is empty" was actively misleading.
- Root cause: `routers/ingest.py` mints `run_id` up front and passes it to
  `ingest_uploaded_file.delay(run_id=…)`. The task inserts a `TestRun` with that id; if it
  fails *after* the insert, Celery retries with the **same** id and dies on the primary key —
  so the task can never succeed. **Any transient failure became a permanently stuck run**,
  and dashboards silently under-reported because those runs' aggregates stay zero.
- Violated a convention the repo states in `backend/CLAUDE.md`:
  *"Per-(entity, run) writes must be idempotent."*
- Fix: an explicit `run_id` that already exists **in the same project** resumes that row.
- **This does not reopen the bug `reuse_existing=False` prevents.** That branch refuses to
  merge on a *fuzzy* `(project_id, build_number)` match, where a typed or timestamp-defaulted
  build label could blend two unrelated datasets. Resumption matches the caller's **own
  explicit primary key**, so it can only resume the run this same task created; project
  scoping stops a cross-tenant id resolving. Tests pin both properties.
- 8 regression tests, verified 5 failed → all pass; 174 passed across the ingestion/pipeline/
  run suites.

### 2026-08-07 — Release gate now honours the configured pass-rate threshold (BEHAVIOR CHANGE)

- **Measured on the live homelab** across nine pass-rate levels (throwaway project, default
  `synthesized` path, no ReleaseGatePolicy):

  | pass rate | 100 | 95 | 89 | 83 | 70 | 64 | 62 | 50 | 0 |
  |---|---|---|---|---|---|---|---|---|---|
  | **before** | GO | GO | GO | GO | GO | GO | NO_GO | NO_GO | NO_GO |
  | **after** | GO | GO | CG | CG | CG | CG | NO_GO | NO_GO | NO_GO |

- **Two defects in that one table.** (1) The configured `RELEASE_PASS_RATE_THRESHOLD` (90)
  gated nothing — only `0.7 x 90 = 63` acted as a NO_GO floor, so a build with a **third of
  its suite failing** was reported ship-ready. (2) `CONDITIONAL_GO` was **unreachable**: the
  conditional band is composite ∈ [20,55), but the synthesized path scores every
  analysis-driven dimension 0, so composites ran 5–13 then jumped to 60 via the hard-floor
  bump. One of the product's three documented states could never occur there.
- **Fix:** a pass rate above the hard floor but **below the configured threshold** now yields
  `CONDITIONAL_GO`. This reuses the operator's own configured number rather than introducing
  another constant, and it is policy-driven — `policy_evaluator_service` passes
  `thresholds["pass_rate_minimum"]`, so a project's ReleaseGatePolicy controls the band.
- **Rule ordering is load-bearing:** both NO_GO rules are evaluated first, so the new band can
  never *soften* a NO_GO. Pinned by tests.
- `verdict_driver` gains `pass_rate_below_threshold`, distinguishing "your configured
  threshold held this back" from "the risk model held this back".
- **BEHAVIOR CHANGE:** runs between the floor and the threshold move GO → CONDITIONAL_GO.
  Deployments relying on a clean GO below their own bar will see verdicts change — that is
  the intent.
- Two existing tests updated, both after checking what they protect: a policy test pinning
  that a custom `hard_floor_factor` is respected (intent intact — it still escapes NO_GO,
  just to CONDITIONAL_GO), and `TestVerdictsAreUnchanged` from the earlier transparency PR,
  **renamed** rather than silently re-valued since the mapping has now changed on purpose.
- 26 regression tests, verified 9 failed → all pass; **227 passed** across the release-gate suite.

### 2026-08-07 — Security fix: pending-defect review leaked across projects

- `GET /api/v1/deep-investigate/defects/pending-review` selected **every** defect with
  `approval_status == PENDING_REVIEW`, with no project filter of any kind. The only gate was
  `require_role(QA_LEAD)`, which checks the caller's ROLE, not their project membership —
  and QA_LEAD is not a global role here (`get_accessible_project_ids()` resolves non-admins
  to the projects they belong to).
- Net effect: a QA lead of one project received the `title`, `severity`, `component` and
  `owner_team` of pending defects belonging to **every other project on the deployment**.
- Now scoped via `Defect.project_id.in_(accessible)` for non-admins. `accessible is None`
  means ADMIN and stays legitimately unscoped — unlike the `/metrics` and `/stream/active`
  bugs earlier this session, where the `None` branch skipped a check that should have run
  for everyone. Here `None` is correct; the defect was the total absence of a filter.
- `defects.project_id` is already indexed (`ix_defects_project_id`) — the schema anticipated
  this filter.
- **Why the architecture ratchet missed it:** `test_architectural_authorization` requires a
  `require_*_access` guard for routers with a `{project_id}`-style **path param**. This
  endpoint has no path param, so it was never in scope.
- 7 regression tests, verified 5 failed → all pass; 74 passed across the deep-investigation /
  defect / authorization suites. Includes a breadth check that no other `select(Defect)` in
  the router is unscoped, and a guard that the filter precedes pagination.

### 2026-08-07 — Fix: trend line used a different pass-rate denominator than the headline

- **Completes the previous pass-rate fix, which was incomplete.** That change fixed
  `_period_stats` (the dashboard headline) to count BROKEN, but `get_trend_data` in the
  **same module** computes its own daily pass rate in SQL and still excluded it. Live, for
  one window: `/metrics/summary` -> **81.0%** (47/58) while `/metrics/trends` -> **83.9%**
  (47/56). Same project, same 7 days, two numbers - a disagreement the partial fix *created*
  between a headline and the chart directly beneath it.
- The trends query already SELECTed `broken` for display while omitting it from its own
  denominator. A day whose only failures were BROKEN charted as a flat 100%.
- Denominator is now `passed + failed + broken`, matching `_evaluated()`. Skips stay out.
- **Guard widened:** the previous PR asserted both branches of `_period_stats` used the
  shared helper - scoped to one function, so this sibling bug passed straight through it.
  The new guard scans the **whole module** for any two-term pass-rate denominator.
- Two of the new assertions initially passed against the buggy source (one matched the
  `broken` display column, one used a whitespace pattern the SQL doesn't contain). Both were
  rewritten to operate on the extracted denominator; verified 3 failed -> 9 passed.
### 2026-08-07 — Fix: drop the inert `frame-ancestors` directive from the CSP meta tag

- Follow-up to the clickjacking fix. Now that the header is real, `index.html` still declared
  `frame-ancestors 'none'` in its `<meta>` CSP, where browsers **ignore** it — and log
  *"The Content Security Policy directive 'frame-ancestors' is ignored when delivered via a
  `<meta>` element"* on **every page load**. Observed again on `/failures` after the header
  fix shipped.
- The directive protected nothing from that position; the response header
  (`frame-ancestors 'none'` + `X-Frame-Options: DENY`, all three nginx configs) is what
  enforces it. Removing it clears constant console noise that would otherwise camouflage a
  genuine CSP violation. Every directive that *does* work in a meta tag is untouched.
- Tightened the guard tests: the previous check was `"frame-ancestors" not in index_html`,
  which a comment discussing the directive would satisfy. It now parses the meta tag's
  `content` attribute, asserts the directive is absent there, asserts the **header** still
  carries it, and asserts the effective meta directives survive.
- 15 tests, verified 1 failed → all pass; `tsc --noEmit` clean.

### 2026-08-07 — Fix: release-gate snapshot now explains its own verdict

- The quick-look (`synthesized`) release-readiness response returned
  `recommendation: GO` with `pass_rate: 83.33` and `input_snapshot.threshold: 90.0` —
  a pass rate 7 points **under** the only threshold in the payload, reported as a clean GO.
  Read literally, the response contradicted itself.
- The verdict is not wrong: `threshold` is **not** the GO cutoff. Pass rate only forces a
  verdict below `HARD_FLOOR_FACTOR` (0.7) of it; above that the composite risk score
  decides. Nothing in the payload said so.
- The snapshot now also publishes `no_go_floor_pct` (the cutoff actually applied),
  `hard_floor_factor`, and `verdict_driver` (`pass_rate_floor` | `composite_risk`).
  **No verdict changes** — a test pins the mapping as unchanged.
- `0.7` was duplicated as a literal in `release_council_service` and as a default in
  `criticality_service`. It is now one exported `HARD_FLOOR_FACTOR`, imported by both, so
  the value that decides the verdict and the value published alongside it cannot drift.
- Measured behaviour recorded in the ledger (throwaway project, cleaned up): the gate is
  **binary at 63%** — 100/95/89/83/70/64% all GO, 62/50/0% all NO_GO — and
  **CONDITIONAL_GO never occurs** on this path, because composites run 5→13 then jump to 60,
  clearing the entire [20,55) band. Whether a 64%-pass run should be a clean GO, and whether
  CONDITIONAL_GO should be reachable, are **product calls left open** — they change
  ship/no-ship recommendations.
- 16 regression tests, verified 2 failed → all pass; 151 passed across the release-gate suite.

### 2026-08-07 — Fix: deleted projects' failures stayed in the assignment inbox

- `GET /me/assigned-failures?scope=team` returned failures belonging to a **deleted**
  project. Found by accident: an earlier exploratory iteration created a throwaway project,
  probed it and deleted it — two iterations later its two failures were still sitting in the
  team inbox alongside the live project's eleven.
- `DELETE /projects/{id}` is a **soft** delete (`projects.py:165` sets `is_active = False`),
  and only the project *list* honoured that flag (`projects.py:32`). The inbox therefore
  listed actionable work for a project absent from every project picker, which the user
  cannot open, filter by, or navigate to.
- Both the inbox list **and** the `/count` badge now apply the same restriction via a shared
  `_live_projects_only()` helper — fixing only one would have left the sidebar badge
  advertising work the page cannot display, the same list/badge disagreement this module's
  own comments warn about. A test asserts neither endpoint hand-rolls the predicate.
- Related and deliberately **not** changed: `GET /projects/{id}` still returns **200** for a
  soft-deleted project. Whether it should 404 is a product call with real blast radius —
  audit trails and historical run pages legitimately resolve deleted projects by id.
- 6 regression tests, verified 4 failed → all pass; 322 passed across the
  my-failures/assignment/project surface.

### 2026-08-07 — Fix: pass rate could report 100% while tests were BROKEN (BEHAVIOR CHANGE)

- Demonstrated on a throwaway project ingested for the purpose — one run of
  **10 PASSED / 0 FAILED / 2 BROKEN**:
  - dashboard `/metrics/summary` → **`avg_pass_rate_7d = 100.0%`**
  - coverage `/analytics/coverage` → `avg_pass_rate = 83.3%`
- Two of twelve tests did not pass and the headline said 100%. That is a false
  statement, not a denominator preference — and it propagates: `_compute_readiness()`
  and the release-gate pass-rate bands consume this number, so a policy of
  "pass_rate ≥ 95 → GREEN" would return GREEN while a sixth of the suite was broken
  by infrastructure.
- Root cause: `_period_stats` computed `passed / (passed + failed)` at **both** call
  sites, silently dropping BROKEN.
- This contradicted the codebase's own definition in three other places —
  `analysis_report_service` (*"evaluated = passed + failed + broken; skips don't count"*),
  `ingestion` (`executed = passed + failed + broken`) and `run_status` — and contradicted
  `metrics_service` itself fifteen lines below the bug, where the flaky heuristic documents
  FAILED *or* BROKEN as *"the canonical failed set used everywhere else"*.
- Both sites now derive the denominator from a shared `_evaluated()` helper. **Skips stay
  excluded** — a skipped test was never evaluated.
- **Behavior change:** pass rate drops wherever BROKEN tests exist (83.9% → 81.0% on the
  exploratory dataset) and can no longer read 100% when tests are broken. Release-gate
  verdicts derived from it may change accordingly — that is the point.
- Existing `test_metrics_weighted_pass_rate.py` mocks gained `sum_broken`; every expected
  value there is unchanged, since those scenarios have no broken tests. Found by
  exploratory testing; 12 new regression tests, verified 6 failed → all pass.

### 2026-08-07 — Fix: /failures headline claimed stable regressions "oscillate" (BEHAVIOR CHANGE)

- The FAILURE VERDICT card on `/failures` rendered **"Flaky · 5 tests intermittent"** and,
  as a statement of fact, *"5 tests show pass/fail oscillation on the same SHA. Re-runs may
  pass without fixing the underlying race or fixture issue."* It then used
  `test_discount_stacking` — broken in four consecutive builds — as its headline example.
  That test does not oscillate, and re-running it will never make it pass.
- This was the **third** independent flaky detector to derive flakiness from a failure
  ratio alone (`analytics_service.flaky_tests`, band 0.05–0.95, ≥3 runs, no order term),
  after `metrics_service` and the flaky-coach quarantine recommendation. The agents already
  had it right — `anomaly_agent`: *"Flaky classification requires status transitions
  (oscillation, not regression)"* — only the read paths did not.
- The auto-detector now also requires ≥2 pass↔fail transitions in run order, via `LAG()`.
- **Drift guard:** the threshold now lives once, in `flaky_signals.MIN_FLIPS_FOR_INTERMITTENCY`,
  and all three surfaces import it instead of each defining a copy. A regression test
  asserts they are the same object.
- Manually-triaged `FLAKY_TEST` rows merged in below the auto query are deliberately **not**
  gated — a human calling a test flaky should not be overridden by the detector.
- **Behavior change:** `/failures` reports fewer flaky tests (5 → 2 on the exploratory
  dataset) and no longer advises re-running a permanently-broken test.
- Found by exploratory testing driving the live SPA; 14 regression tests running the real
  production SQL, verified 7 failed → all pass.

### 2026-08-07 — Fix: Flaky Coach recommended quarantining stable regressions (BEHAVIOR CHANGE)

- `GET /projects/{id}/flaky-coach` recommended **QUARANTINE** for `test_discount_stacking`
  — a test that fails in four consecutive builds and passed only once, at the start — with
  the top action *"Quarantine this test immediately to stabilize the CI pipeline"*.
  Quarantine suppresses a test from blocking CI, so the product's advice was to hide a
  reproducible product bug behind a "flaky" label.
- Root cause: `_compute_quarantine_recommendation()` took `failure_rate` and nothing else.
  A rate-only rule recommends quarantine **most strongly exactly when it is most harmful**,
  because a permanently-failing regression has the highest failure rate there is.
- The service already computed the signals that tell a flake from a break
  (`status_volatility`, `intermittency_label`) and rendered them on the *same card* — it
  just never fed them into the decision. The card contradicted itself, saying both
  "Quarantine this test immediately" and "gather more runs to confirm the flake versus an
  emerging regression".
- A test that flips fewer than `_MIN_FLIPS_FOR_QUARANTINE` (2) times in its window is now
  routed to **INVESTIGATE**, with advice that names it a regression: *"Treat as a
  regression, not a flake"*, *"Do NOT quarantine: suppressing it would hide a reproducible
  failure from CI"*, *"Bisect to the change that first broke it"*.
- **Behavior change:** consistently-broken tests move QUARANTINE → INVESTIGATE and get
  different advice. Genuine flakes (≥2 flips) are still quarantined. The verdict vocabulary
  is deliberately unchanged — adding a value would risk the documented strict-enum-over-
  `String(N)` silent-422 class. Callers with only aggregate counts and no ordered window
  (`test_case_history_service`) pass no flip count and keep their previous behaviour.
- Found by exploratory testing against a live deployment; 23 regression tests, verified
  13 failed → all pass.

### 2026-08-07 — Fix: stable regressions were counted as "flaky" (BEHAVIOR CHANGE)

- `flaky_test_count` (dashboard KPI, and `known_flaky` on the summary report) came from a
  failure **ratio** alone — 10%–90% over a test's last 10 executions — with no regard for
  the **order** of those results. A test that passed once and has failed on every run
  since sits at 0.8 and was reported as flaky; so was a test that failed for the first
  time in the very latest run (0.2). Both are regressions — the highest-value items on a
  QA lead's plate — and both were labelled noise.
- The app's own flake engine already disagreed: `flaky_signals._label()` classifies the
  low-volatility case as `persistent_regression` and states outright that it must not go
  on the flake track. The headline KPI contradicted it.
- The window now also requires **at least 2 pass↔fail transitions** in run order
  (`_FLAKY_MIN_FLIPS`), computed in SQL with `LAG()` over the bounded window. One flip is
  a state change (a test broke, or got fixed); only from the second does a test return to
  a state it had already left, which is what intermittency means.
- **Behavior change:** `flaky_test_count` will drop for most projects, and
  `flaky_rate_pct` on the summary report drops with it. This is a correction, not a
  regression — on the exploratory dataset the count goes 5 → 2, and the two that remain
  are precisely the tests that alternate. Tests that recover after failing (including
  `BROKEN` infra blips) are still counted; always-passing and always-failing are still
  excluded, and a test that has been solidly green for a full window still ages out.
- Found by exploratory testing against a live deployment. Regression test executes the
  real production SQL against in-memory SQLite; verified 4 failed → all pass.

### 2026-08-07 — Fix: clickjacking protection was inert (CSP delivered only via `<meta>`)

- `frontend/index.html` declared `frame-ancestors 'none'` in a
  `<meta http-equiv="Content-Security-Policy">` tag, with a comment stating the intent
  ("prevent clickjacking (X-Frame-Options equiv)"). **Browsers ignore `frame-ancestors`
  in a meta tag** — Chromium logs it on every page load — and the deployment sent no CSP
  or `X-Frame-Options` response header. Every page was framable. Found by exploratory
  testing against a live deployment.
- All three frontend nginx configs (image template, homelab overlay, openshift overlay)
  now send `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` and `Referrer-Policy` as **response headers**.
- **nginx inheritance trap handled:** `add_header` is inherited only by blocks that
  declare none of their own, so `location = /index.html` (which sets Cache-Control) would
  have silently dropped every security header — the one document clickjacking targets.
  The headers are repeated in each such block, and a regression test walks every
  `location` to keep it that way.
- Configs verified with `nginx -t`; the meta tag stays for the directives that *are*
  honoured there.

### 2026-08-07 — Fix: `project_id=all` crashed the metrics endpoints for admins

- **`GET /api/v1/metrics/summary` and `/metrics/trends` returned 500** when handed the
  frontend's `ALL_PROJECTS_ID` sentinel (`"all"`) — or any non-UUID — while signed in as an
  **ADMIN**. Found by exploratory testing against a live deployment.
- **Role-dependent, which is why it survived:** `_project_in_scope()` already rejected a
  non-UUID, but it only runs when `get_accessible_project_ids()` returns a set. For an ADMIN
  it returns `None`, skipping the scope check entirely, so the raw string reached a UUID
  column comparison. Non-admins got an empty payload; admins got a crash.
- Both handlers now validate up front and answer **400 "Invalid project_id — expected a
  UUID"**, matching `/api/v1/runs`. `project_id=None` remains valid (it means "all
  projects"). `/metrics/tia-readiness` was already correct (422) and is unchanged.
- Regression test pins both handlers, so a new metrics endpoint that forgets the guard fails CI.

### 2026-08-05 — MFA enrollment, login challenge, and admin policy UI

The frontend half of the TOTP MFA work: everything a user or an admin needs to
enrol, sign in with a second factor, and set workspace policy. Backend contract
unchanged — `backend/**` was not touched.

**Login (`pages/LoginPage.tsx`)** — the existing `mode` state machine grew two
steps to match the three possible 200 bodies from `POST /auth/login`:

- `mfa_required` → a numeric code field (`autocomplete="one-time-code"`,
  autofocus, paste-tolerant) with a "use a recovery code instead" toggle. A
  **wrong code keeps the challenge** — the user retries in place rather than
  being thrown back to the password field with a still-valid challenge
  discarded. The challenge is short-lived, so the remaining time is shown as a
  live countdown; when it lapses (locally or per the backend) the panel says so
  and offers the way back to the password step instead of failing silently.
- `mfa_enrollment_required` → enrollment is walked inline using the
  `enrollment_token`, explaining *which role* the requirement comes from, and
  sign-in is completed with the `tokens` that `enroll/confirm` returns on that
  path.

**Profile → Security (`components/mfa/MfaSecuritySection.tsx`)** — enrol,
regenerate recovery codes, disable. Two states are handled explicitly rather
than collapsed into "off": `sso_managed` renders an explanation and **no
controls** (the IdP owns that user's second factor), and `secret_unreadable`
renders a loud broken state — enrolled but the seed cannot be read, so TOTP
logins will fail and only an administrator can fix it. Rendering that as "not
enrolled" would push the user into a 409 they cannot escape.

**Recovery codes** are shown exactly once, all ten, with Copy and Download, and
cannot be dismissed until the user ticks an explicit acknowledgement.
Regeneration gets the same screen plus a warning that the previous set is now
dead.

**Admin policy (`pages/settings/MfaPolicyPage.tsx`, route
`/settings/mfa-policy`)** — `require_mfa`, `required_for_role`, and the lockout
trio, ADMIN-gated in-component (the backend remains the real boundary).
Switching `require_mfa` on requires a confirmation that states the consequence:
everyone in scope is forced to enrol at next login, SSO users are exempt, and a
lost device with no recovery codes left needs an admin-run breakglass script.

**⚠ Interceptor change — `services/api.ts` no longer refreshes the token on a
401 from `/api/v1/auth/mfa/*`.** Every wrong TOTP code and expired challenge is
a 401, and `/auth/mfa/verify` is called from the login screen where there is no
session at all; without this a typo triggered a refresh, the refresh failed,
`refreshAccessToken` called `logout()`, and MFA looked broken. The exclusion
list also now covers `/auth/register` and `/auth/dev-login` alongside the
existing `/auth/login` and `/auth/refresh`. Side effect worth knowing: a
genuinely expired session on `/auth/mfa/status` now surfaces as a read error
rather than silently refreshing — every other read on the page still drives the
refresh.

New dependency: **`qrcode.react` ^4.2.0** — the backend returns `otpauth_uri`
and no image by design. Pure TS/React, zero runtime dependencies, no native
build step, renders inline SVG. The manual-entry secret is shown next to the QR
in every case, because QR-only setup locks out anyone configuring this on a
desktop.
### 2026-08-06 — App error-boundary fallbacks migrated to per-theme status tokens

- **Frontend** — the two last-resort error screens no longer hard-code Tailwind palette classes that bypass the per-theme CSS-token system and read poorly on light themes. `ErrorBoundary`'s error-detail text now uses `--status-failed` (was `text-red-400`) and `SectionErrorBoundary`'s warning icon uses `--status-broken` (was `text-amber-400`), mapped by semantic role. Removes 2 `no-restricted-syntax` palette warnings; extended each component's existing test with a regression guard against reintroducing raw palette classes.

### 2026-08-05 — TOTP MFA, recovery codes, account lockout (closes the compliance gap)

> **⚠ BEHAVIOR CHANGE 1 — account lockout is ON by default.** Ten consecutive
> failed sign-in attempts (failed passwords *and* failed second factors both
> count) lock an account for 15 minutes; the correct password then gets `429`
> with `Retry-After` instead of a session. Nothing had to be enabled for this
> to start applying. Failures while already locked do not extend the lock, a
> fully successful sign-in resets the counter, and attempts against usernames
> that do not exist are never counted. Turn it off with
> `PUT /api/v1/settings/mfa-policy {"lockout_enabled": false}`.
>
> **⚠ BEHAVIOR CHANGE 2 — `POST /api/v1/auth/login` can return two new 200
> bodies.** It still returns `TokenResponse` for a normal sign-in, but now also
> returns `{mfa_required: true, challenge_token, expires_in, methods}` when the
> account has a second factor, and `{mfa_enrollment_required: true,
> enrollment_token, expires_in, required_for_role}` when policy requires one
> and the account has none. **A client that assumes `access_token` is present
> on any 200 will break** the moment anyone enrolls. All three are 200 because
> all three follow a correct password; a 4xx for "now do your second factor"
> would be a lie the SPA's global error handling would act on.
>
> **⚠ BEHAVIOR CHANGE 3 — `POST /api/v1/auth/refresh` re-checks MFA policy.**
> Enabling the requirement now invalidates live sessions for in-scope users
> within one access-token lifetime (401, "sign in again to enroll") instead of
> grandfathering them for up to `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (7 days).
>
> **⚠ BEHAVIOR CHANGE 4 — `POST /api/v1/auth/dev-login` refuses an account with
> MFA enabled** (403). It was already development-only, but "the dev backdoor
> is an MFA bypass" is not a sentence worth leaving true.

Until now `user-guide/compliance.md` had to say, in three separate places, that
the product has no MFA and no account lockout. It now has both.

- **TOTP (RFC 6238), migration 0117.** Six digits, 30-second period, ±1 step of
  drift. New `pyotp==2.9.0` (pure Python, no transitive deps — vendorable into
  the air-gapped bundle as-is). Deliberately **no** `qrcode`/`Pillow`: the API
  returns the `otpauth://` URI and the SPA renders the QR, keeping a native
  imaging toolchain out of the backend image. New endpoints under
  `/api/v1/auth/mfa`: `enroll/start`, `enroll/confirm`, `verify`, `disable`,
  `recovery-codes`, `status`.

- **The second factor is never a claim on an access token.** `get_current_user`
  trusts any JWT with `type: "access"`, and `bootstrap.register_routers` mounts
  it router-wide, so a "half-authenticated" access token carrying an
  `mfa_pending` marker would have been fully authenticated on ~300 handlers
  that have never heard of the marker — a complete bypass. Instead
  `core/security.create_mfa_token` mints tokens with their own `type` claims
  (`mfa_challenge`, `mfa_enroll`); `decode_token(expected_type=...)` rejects a
  mismatch **at the decode layer**, before any user is loaded. They live 5
  minutes, are single-use (consumption reuses the existing `revoke_jti`
  denylist rather than adding a second store with its own availability
  semantics), and `create_mfa_token` raises if asked to mint type `access`.
  Tested against a real mounted protected route, not a stub.

- **Replay of a code inside its own window is rejected.**
  `users.mfa_last_used_step` holds the highest accepted time-step; a code is
  honoured only when its step is strictly greater.

- **"Enrolled but the seed will not decrypt" denies (503) — it does not pass.**
  `secret_service.read_secret` returns `None` for both "never stored" and
  "stored but undecryptable", so an `APP_SECRET_KEY` rotation without
  `APP_SECRET_KEY_PREVIOUS` makes every enrolled seed read as absent. Treating
  that as "MFA is off" would silently disable the control workspace-wide at
  the worst possible moment. `mfa_service.load_totp_secret` distinguishes the
  states; login, refresh, and verify all refuse the broken one. New
  `secret_service.expire_secret` clears the ciphertext as well as flagging the
  row, so a revoked seed is not left recoverable in the database.

- **Recovery codes** — ten single-use codes (80 bits, `XXXX-XXXX-XXXX-XXXX`),
  shown exactly once, SHA-256 digests only. Reissuing invalidates the previous
  set. New `mfa_recovery_codes` table; a used code keeps its row with `used_at`
  set so "a code was burned on <date>" stays answerable.

- **Lockout state is Postgres, not Redis** (`users.failed_login_attempts`,
  `locked_until`, `last_login_at`). The in-process limiter in
  `main.rate_limit_auth` is per-worker and cannot express "this account has
  failed N times across the fleet". Postgres can, is already read on the login
  path, survives a restart, and — unlike a Redis counter — has no
  fail-open/fail-closed dilemma to resolve, which matters given that a Redis
  outage is already an auth outage.

- **Workspace policy** — `GET`/`PUT /api/v1/settings/mfa-policy` (read QA_LEAD+,
  write ADMIN). Stored as an `app_settings` row keyed `mfa_policy` rather than
  a `SSOConfiguration`-style single-active-row table: it is four scalars with
  no accompanying object, and the `app_settings` route inherits
  `log_settings_change` for free. Writes also emit `MFA_POLICY_UPDATED` to the
  identity trail. "Require MFA for role X **and above**".

- **SSO/SCIM accounts are exempt from the requirement, deliberately.**
  `/api/v1/sso/acs` does not consult the policy at all — the IdP has already
  asserted the identity, and a local factor it knows nothing about is a
  lock-out risk for no added assurance. `mfa_service.is_sso_managed` checks
  **two** signals, because `scim_service.scim_create_user` only creates the
  `federated_identities` link `if sso_config_id and external_id`: a SCIM user
  provisioned without either looks purely local, so the unconditional
  `SCIM_USER_CREATED` identity event on the same path is the fallback. The
  exemption is from the *requirement* only — an SSO user who enrolls
  voluntarily is still challenged on the local password path.

- **API keys are never MFA-gated**, by construction: enforcement happens where
  interactive credentials are minted (`/auth/login`), not per request, so
  CI-embedded keys keep working. `mfa_service.mfa_gate_applies` is written as
  `credential_kind(user) == CREDENTIAL_KIND_JWT` so an unknown credential kind
  falls out of "interactive" rather than being assumed to be one. Keys may read
  `/auth/mfa/status` but cannot enroll, disable, or reissue recovery codes.

- **Breakglass is `backend/scripts/mfa_breakglass.py`, not an endpoint** — a
  user who can reset their own factor does not have one, and an endpoint that
  resets someone else's is an account-takeover primitive. Gated on
  `MFA_BREAKGLASS_ENABLED` in the backend environment (mirroring
  `SSO_ADMIN_FALLBACK_ENABLED`: env-only, no in-app toggle, default off),
  requires `--reason`, supports `--dry-run` and `--keep-lockout`, and writes
  `MFA_BREAKGLASS_RESET` in the same transaction as the reset. Note
  `ADMIN_FALLBACK_LOGIN` is the *SSO* fallback and does not rescue a lost TOTP
  device.

- **Fixed a live rate-limiter bug found while testing this.**
  `main.rate_limit_auth` rebuilt its `@limiter.limit` decorator on every
  request. `Limiter.limit` registers under `f"{module}.{func.__name__}"`, which
  was always `app.main._limited`, so each request *appended* another limit to
  the same list: request N evaluated N limits and recorded N hits against one
  counter. The nominal 10/minute on `/auth/login` therefore locked an IP out
  after roughly four or five attempts, got stricter the longer the process
  ran, and leaked the registration list unboundedly. The decorators are now
  built once, one per path, with distinct names — and the bucket key is now
  `client-ip + path`, so two paths sharing a limit string no longer share a
  counter (they only stayed separate by accident of 10/5/30 being distinct).

- **Audit** — `IdentityEventType` gains `MFA_ENROLL_STARTED`, `MFA_ENABLED`,
  `MFA_DISABLED`, `MFA_VERIFY_SUCCESS`, `MFA_VERIFY_FAILED`,
  `MFA_RECOVERY_CODE_USED`, `MFA_RECOVERY_CODES_REISSUED`,
  `MFA_BREAKGLASS_RESET`, `MFA_POLICY_UPDATED`, `ACCOUNT_LOCKED`,
  `ACCOUNT_UNLOCKED`. All ≤ 40 chars, with a test that fails CI if a future
  value outgrows the `String(40)` column (the documented enum/column drift
  trap). There is deliberately **no** per-attempt `LOGIN_FAILED` event:
  `identity_events` has no retention clock, so anything an unauthenticated
  attacker can emit at will would accumulate forever.

- **Tests** — `backend/tests/test_mfa.py`, 88 tests against the real models,
  the real router wiring, and the real service code over in-memory SQLite
  (new test-only `aiosqlite==0.20.0`). Nothing overrides an auth dependency —
  `tests/integration/conftest.py`'s `auth_as` replaces
  `get_current_active_user` wholesale and would have exercised none of this.

- **Docs** — `user-guide/administration.md` gains a full MFA/lockout/breakglass
  section (including the "503 for everyone after a key rotation" failure mode);
  `architecture/SECURITY.md` §1/§1a/§7 replace "MFA must come from your IdP"
  with how this works and where it stops. **`user-guide/compliance.md` still
  says "no MFA, no account lockout" in the CC6.1 row, the §164.312(d) row, and
  the "what TestLookup does NOT provide" list — those three are now stale and
  need a follow-up edit.**

### 2026-08-05 — Auth: stop laundering 503/non-401 errors into 401; credential-kind plumbing; consistent password caps

> **⚠ BEHAVIOR CHANGE — a Redis outage now returns 503 to API clients instead of 401.**
> This is the behaviour `architecture/SECURITY.md` §7 has claimed since
> 2026-08-03; it just wasn't what clients received. Anything that treats
> "not 2xx" as "log out and re-authenticate" will now see a retryable 503
> where it previously saw a 401 — which is the point. `Retry-After: 5` is
> on the response.

- **The shared dual-auth dependency no longer swallows non-401 errors (the bug).**
  `get_current_user_or_api_key` and `get_api_key_context` both did
  `except HTTPException: pass` around the bearer path so a request carrying both
  an `Authorization` header and an `X-API-Key` could fall back to the key. That
  catch was indiscriminate. When fail-closed token revocation started raising
  **503** ("revocation status cannot be verified — Redis is down"), the 503 was
  caught here and reissued as a generic 401 "Authentication required". Nothing
  in `backend/app` depends on `get_current_user` directly and
  `bootstrap.register_routers` injects the dual-auth dependency router-wide, so
  the 503 was unreachable in practice: a Redis outage arrived at the SPA as
  401-everywhere, `authStore.fetchUser` logged the user out, and the axios 401
  interceptor burned a refresh — **precisely the re-login loop that fail-closed
  revocation was designed to prevent**. Now **only 401 falls through**; every
  other status propagates unchanged. Both call sites share one helper
  (`_bearer_user_or_fall_through`) that enumerates what `get_current_user` can
  actually raise, so the two cannot drift apart again.
- **Which statuses do what, and why.** 401 falls through — signature/exp
  invalid, missing/unparseable `sub`, non-`access` token type, jti on the
  denylist, `iat` before the user's cutoff, user row gone. All of those mean
  "this bearer token is not a usable credential", so trying the other one is
  correct. 503 propagates — the credential may be fine; it is the *server* that
  cannot check it. 403 (were it ever raised here) would propagate too:
  "authenticated but not allowed" is never "try another credential". Non-
  `HTTPException` failures (e.g. a DB error) were never caught and still aren't.
- **`credential_kind(user)` — JWT auth is now distinguishable from API-key auth.**
  Previously the only per-request marker was the API-key *project* binding, which
  is `None` for a **user-scoped** API key and therefore indistinguishable from a
  JWT. Every auth dependency — `get_current_user`, `_validate_api_key`, and the
  separate `get_streaming_api_key_context` path — now also stamps `"jwt"` /
  `"api_key"` on the request-local user object, using the same mechanism as the
  existing project binding rather than a second one. `credential_kind()` returns
  `None` for a user object that did not come from an auth dependency — "unknown"
  never reads as "jwt". **Pure plumbing: no route reads it and no route behaves
  differently.** A later MFA gate consults it to exempt API keys, since a key
  embedded in CI can never answer a challenge. The streaming path deliberately
  records only the kind, not the project binding, which it has never set.
- **Password length caps are consistent.** `UserCreate.password` was capped at
  128 (bcrypt DoS, audit item S4) but `ChangePasswordRequest` and
  `FirstTimeResetRequest` were not — the same unbounded value registration
  rejected went straight into `bcrypt.hashpw` via `/auth/change-password` and
  `/auth/first-time-reset`. All password-accepting schemas now share
  `MAX_PASSWORD_LENGTH = 128`. It is a resource guard, not a password policy —
  **no complexity rules were added**. The cap cannot reject anything that works
  today: bcrypt reads only the first 72 bytes, so a longer password could never
  have been set. `ChangePasswordRequest.current_password` gains the cap but not
  the minimum — rejecting a short one at the schema would leak that no short
  password can be the current one.
- **Found, not fixed (needs a policy decision, so not a drive-by).** The pinned
  `bcrypt` (5.0.0) does **not** silently truncate past 72 bytes — it raises
  `ValueError`. So a 73–128 character password still 500s inside
  `get_password_hash`/`verify_password` on register, change-password,
  first-time-reset, and login. The cap narrows the window; closing it means
  choosing between rejecting at 72, truncating, or pre-hashing, which is a
  password-policy call. Relatedly, `LoginRequest` is **unreferenced** —
  `POST /auth/login` binds `OAuth2PasswordRequestForm`, whose `password` is
  uncapped and is the live unauthenticated bcrypt surface. The schema was capped
  anyway so it is correct the day it is wired up.

### 2026-08-04 — Commit ranges: persist supplied base, non-green baseline fallback, TIA-readiness metric

> **⚠ BEHAVIOR CHANGE — commit ranges now resolve for projects that never go green.**
> `resolve_commit_range` previously required a **fully all-green** prior run to
> anchor a range. A project with a persistent flaky/failing tail — exactly the
> attribution audience — never had one, so every run resolved to `unavailable`
> forever and `run_commit_ranges` stayed near-empty. There is now a fallback
> anchor (most recent *completed* prior run with a commit hash, pass/fail
> irrelevant). Effect: runs that used to show "no commit range" will start
> showing one, and the GitHub connector will start firing on projects where it
> previously short-circuited (`1 + COMMIT_RANGE_FILE_FETCH_LIMIT` calls per
> resolve, still behind the 6 h re-resolve cooldown). The fallback anchor is
> **weaker** and is labelled as such — see `base_source` below. Nothing is
> fabricated: a run with no prior completed run still writes an explicit
> `unavailable` row.

- **A supplied commit range keeps its base (the actual bug).** `store_supplied_range` hard-coded `base_commit=None`, so even when a CI/SDK caller pushed a real range the stored row could not say what the range was measured *from* — making it unreconstructible, and useless as test-impact training data. `commit_range` now accepts **either** wire shape on `IngestPayload`, `LiveSessionCreate`, and the `/ingest/file` form: the legacy bare list (unchanged, still valid) **or** `{base, head, commits: [...]}` (`SuppliedCommitRange`, aliases `base_commit`/`from_commit` and `head_commit`/`to_commit` accepted so callers need not guess our spelling). Fully optional, fully back-compatible. Supplied-wins precedence over the connector is untouched. A caller who sends only the bare list still gets `base_commit = NULL` — recorded honestly, not guessed at — and a `base == head` boundary is dropped rather than stored as an anchor to an empty range.
- **`base_source` — a weak anchor never gets to look like a strong one.** New column (migration 0116) recording *which* anchor produced `base_commit`, alongside the existing `source` (which records how the *commits* were acquired): `supplied` | `green_baseline` | `last_completed_run` | `unavailable`. `green_baseline` and `supplied` are strong ("landed since a known state" is literally true); `last_completed_run` is explicitly weak — the range can contain changes that were already in the baseline's tree when it failed. Surfaced on the commit-range and suspects endpoints as `base_source` plus a derived `base_anchor_is_strong`, so the UI can caveat rather than assert. The label is validated at the write: an unknown value, or any label at all with no `base_commit`, degrades to `unavailable` — it can never over-state what was found. Rows written before 0116 read as `unavailable`, not as green.
- **Anchor candidates must now PREDATE the run.** Both the green-baseline and fallback queries gained a `coalesce(end_time, created_at) < this run` clause. Without it a *later* run could be selected as the base and the compare would be inverted or empty — a fabricated range wearing a real one's clothes.
- **The 25-commit changed-file cap is now a deliberate, configurable choice.** Per-commit changed files are one GitHub API call each, so the cap is a rate-limit knob, not a modelling one: an authenticated PAT gets 5 000 REST calls/hour and one resolve costs `1 + N`, giving ~192 resolves/hour at N=25, ~98 at N=50, ~49 at N=100. The two consumers want different answers and neither is wrong — suspect ranking is a path-overlap heuristic whose signal decays fast past ~25 commits, while a test-impact corpus loses *everything* from a commit stored with `files: []`. So the **default stays 25** and the value moved to `COMMIT_RANGE_FILE_FETCH_LIMIT` (clamped to 1..100, read per call), letting a deployment building a corpus pay the rate-limit cost knowingly. The arithmetic and the reasoning live next to the constant, not only here.
- **`GET /api/v1/metrics/tia-readiness?project_id=…&days=90` — the Epic-10 go/no-go, measured.** Per-project (never a fleet average, and `project_id` is required for exactly that reason): how many runs carry a *usable* range — `source` resolved **and** at least one commit carrying `files` — over what calendar span, across how many distinct paths, plus the `source` / `base_source` mix and `file_detail_coverage`. Mirrors the value-metrics availability gate: below any published threshold it returns `available: false` with an `insufficient_data_reason` naming the single next thing to fix ("none of the 12 resolved ranges contain any commits", "…none carry per-commit changed files", "only 4 of 60 runs carry a usable commit range (need 30)", too-short span, too-few paths). The thresholds (30 usable runs / 14 days / 25 distinct paths) are returned in the response because they are a judgement call — a floor below which training is obviously premature, not a promise it will work above them. The scan is bounded at 1 000 rows and says so via `scan_capped`, so the numbers are a floor rather than a silent truncation. **No part of the TIA model itself is built here** — this slice only makes the corpus real and measurable.
- **Migration 0116** — adds `run_commit_ranges.base_source` (`String(30)`, NOT NULL, default `unavailable`) with a conservative backfill: only rows that actually have a `base_run_id` *and* a `base_commit` become `green_baseline` (the sole pre-0116 producer of a base); nothing else gets an anchor invented for it. Replaces `ix_run_commit_ranges_project` with `ix_run_commit_ranges_project_resolved (project_id, resolved_at)` — the readiness scan windows by `resolved_at` within a project and the single-column index forced a sort over every range the project ever had; the composite still serves the old project-only lookups via its leftmost prefix, so keeping both would only cost write amplification. Real downgrade (drops the column, restores the old index).
- Tests: `backend/tests/test_commit_range_readiness.py` (34) — supplied base persisted in both shapes + alias spellings + supplied-wins precedence intact, anchor selection across green/fallback/none, the anchor label proven un-overstatable (no base and unknown-label both degrade), the cap decision pinned (default 25, configurable, clamped), readiness across zero/all-unavailable/no-file-detail/sparse/short-span/narrow-path/adequate, and the 0116 chain + downgrade + ORM-vs-migration agreement.
### 2026-08-04 — SDK/CLI collect the commit range locally (US-8.1 follow-up; unblocks Epic 10)

- **`run_commit_ranges` finally has a producer.** Epic 8 (migration 0110) added a `commit_range` field to `IngestPayload` / `LiveSessionCreate` and `commit_attribution_service.store_supplied_range` to persist it — but a repo-wide grep for `commit_range` returned **zero hits** under `cli/`, `client/`, so nothing ever filled it. The only populating path was the GitHub connector, which needs a PAT, a matching repo, `AI_OFFLINE_MODE` off, the `github_checks` flag on, **and** a fully-green baseline run — yielding nothing on air-gapped installs or any project with a persistent failing tail. New `client/commit_range.py` + `cli/testlookup_cli/commit_range.py` (deliberate copies, same duplication contract as `ci_context.py`, pinned by a drift test) walk **local git** instead: no token, no network, no green baseline. This is the input commit attribution consumes today and the input a TIA model (Epic 10) would train on.
- **Collected per commit, oldest→newest:** `sha`, author **name**, first line of the message, ISO author date, and the changed file paths — matching the `SuppliedCommit` shape and the field semantics `_fetch_connector_range` already stores (name, not email; no diffs, no contents). Caps are enforced **client-side** so the payload isn't silently truncated server-side: 100 commits, 500 files/commit, 512 chars/path. Over 100 commits, the **newest** 100 are kept (the likeliest culprits) rather than letting the server's `raw_commits[:100]` keep the oldest.
- **Base resolution, strongest tier first:** explicit caller (`--commit-range-base` / `commit_range_base=`) → `TESTLOOKUP_COMMIT_RANGE_BASE` → `testlookup.commit_range_base` → the CI provider's own diff base → `git merge-base` vs the default branch → `HEAD^`. A base *the user* supplied that doesn't resolve emits **nothing** — it is never silently swapped for a guess; only the inferred tiers fall through. Nothing resolves ⇒ nothing is sent, because an absent range is honest and a wrong one poisons the model.
- **CI diff-base variables were verified against each vendor's published reference, not guessed.** GitHub Actions: `GITHUB_EVENT_PATH` payload `pull_request.base.sha`, else `GITHUB_BASE_REF`, else push-event `before`. GitLab: `CI_MERGE_REQUEST_DIFF_BASE_SHA` → `CI_MERGE_REQUEST_TARGET_BRANCH_SHA` → `CI_COMMIT_BEFORE_SHA` → target branch name (the documented all-zero sentinel is rejected). Jenkins: `CHANGE_TARGET` (Branch API), then git-plugin `GIT_PREVIOUS_SUCCESSFUL_COMMIT` / `GIT_PREVIOUS_COMMIT`. Azure DevOps: `SYSTEM_PULLREQUEST_TARGETBRANCH` / `…TARGETBRANCHNAME` — `SYSTEM_PULLREQUEST_SOURCECOMMITID` is deliberately *not* used as a base, it is the commit under review. **CircleCI is omitted on purpose:** it publishes no built-in *environment variable* carrying a diff base (`pipeline.git.base_revision` is a pipeline value the user must map in themselves), so it falls through to local git rather than inventing a variable name.
- **Shallow clones degrade, they don't fabricate.** `--depth 1` is the CI default and the base usually isn't in history. Detected via `git rev-parse --is-shallow-repository`; the range collapses to **head-only** — the one commit provably present — never a synthesised base. A full clone with no resolvable base sends nothing instead.
- **Cannot fail a test run, cannot dirty stdout.** Every git call is an argv **list** with `shell=False` and a timeout (the Fixer's `runners.py` discipline); refs sourced from the environment are shape-validated so an option-shaped value (`--upload-pack=…`) can never reach git as a flag. Missing binary, non-repo, timeout, or any unexpected exception is caught, logged at DEBUG, and the field is omitted. Nothing prints to stdout, so `testlookup upload … --output json | jq -r '.run_id'` stays parseable.
- **Wired on all three transports** — CLI multipart `/ingest/file` (`commit_range` JSON array field, already parsed server-side; `upload dir` walks git **once** for the whole directory), `TestLookupReporter.session()` → typed field on `/stream/sessions`, and `LiveStream` → `meta.metadata["commit_range"]`, the same `extra_metadata` channel `ci_context` rides and which `upsert_test_run` already reads back. Opt-out via `--no-commit-range`, `TESTLOOKUP_COMMIT_RANGE=0`, `testlookup.commit_range: false`, or `collect_commit_range=False`.
- **JS / Java / Go SDKs are unchanged and still send only CI context.** Their `ci-context` ports are pure env-var reads; commit collection needs subprocess execution, timeout handling, shallow detection, and cap enforcement, which is new machinery rather than a parallel edit — and `client/js` has no test runner configured at all. Design note is in the module docstring; those SDKs can still supply a base through `TESTLOOKUP_COMMIT_RANGE_BASE` or upload via the CLI. Not half-shipped in three languages.
- **Tests:** 165 new — `client/tests/test_commit_range.py` (141, parametrized over both module copies, driven against **real** temporary git repos including a real `--depth 1` clone), `client/tests/test_reporter_commit_range.py` (15), `cli/tests/test_upload_commit_range.py` (9).

### 2026-08-04 — AI trust: provenance on the analysis contract + confidence-gated automation (US-15.1/15.2)

> **⚠ BEHAVIOR CHANGE — defect promotion holds more work for human review.**
> `services/action_policy.py` gated AI-confidence on a hard-coded `< 60`. It
> now reads **`ai_confidence_threshold`** from the effective AI config, whose
> default is `AI_CONFIDENCE_THRESHOLD` = **80**. Effect: defect promotions
> scoring **60–79** used to auto-approve and now land in `pending_review`.
> Fewer auto-promotions, more items waiting on a human. Nothing is lost — the
> promotion is created either way, it just starts in `pending_review` — and the
> old behaviour is one setting away: set **AI Settings → confidence threshold
> to 60** on `/settings/ai` (or `AI_CONFIDENCE_THRESHOLD=60` in the
> environment). Severity and Jira-bound rules are unchanged; this only affects
> promotions that previously cleared on confidence alone.

- **`AnalysisResponse` can finally say which engine answered (US-15.1).** The primary AI-card contract carried `llm_provider` / `llm_model` / `confidence_basis` but **no** `mode_used` / `fallback_from` / `fallback_reason`, so a card produced by the rules engine after Ollama went missing was indistinguishable from a real LLM analysis. New optional `provenance` block: `mode_used`, `mode_requested`, `mode_resolved`, `fallback_from`, `fallback_reason`, `fallback_occurred`, `llm_provider`, `llm_model`, `prompt_versions`, `confidence_basis`, `threshold_check`. Populated **verbatim** from `AIAnalysis.routing_metadata` — nothing recomputed, nothing inferred. Rows analysed before routing metadata existed get `provenance: null`, **not** a block of nulls dressed up as an answer (no backfill: a plausible guess about which engine ran two months ago is worse than a blank). Every pre-existing field on the response is untouched.
- **`POST /api/v1/analyze` now records its own routing decision.** `run_triage_agent` stamps a `_routing` dict on every exit path — ReAct success, the single-shot fast classifier, the LLM-model-missing → rules-engine fallback (`fallback_from="llm"`, `fallback_reason="llm_model_not_available"`), the context-window failure, and the generic error stub. Canned-stub paths leave `mode_used` **null** so nothing claims authorship of a fallback message. The blob is persisted into `routing_metadata` (merged, never a wholesale replace — an earlier pipeline run's `kind_evidence` / `confidence_basis` survive) so a later `GET /analyze/{id}` renders the same provenance.
- **One confidence gate, one place: `services/confidence_gate.py` (US-15.2).** Resolution precedence `ai_config` override → env default, published through `get_effective_ai_config()` so it shares that resolver's 60 s Redis cache and its DB-failure fallback. A settings outage degrades to the env default rather than opening or closing gates unpredictably; a junk stored value (non-int, out of 0–100) is **dropped**, not obeyed — one bad settings write cannot brick every gate, and `/settings/ai` drops it the same way so the page and the gate can never disagree about the number automations obey. `AIConfigRead` gains `ai_confidence_threshold_source` (`ai_config` | `env_default`), mirroring the `ai_offline_mode_source` pattern.
- **Boundary pinned at `>=`** — a score *exactly at* the threshold **passes**. Not arbitrary: every pre-existing gate was written `confidence < THRESHOLD → needs review` (`analysis_agent._validate_confidence`, `services/agent.py`, `ml/classifier.py`), which is the same boundary, so centralising changes nothing at the edge. A **missing** confidence never passes and is reported as `not_evaluated`, not as low — "we did not judge this" is a different claim from "we judged it poor", and collapsing them would have the UI assert something the backend never checked.
- **Below-threshold ⇒ deterministic fallback, marked explicitly.** Defect promotion holds for human review (see the behaviour change). The analysis contract carries `low_confidence: bool` and `confidence_gate_status` (`above_threshold` | `below_threshold` | `not_evaluated`) plus the evaluated `confidence_gate` — the UI renders "low confidence — needs human review" from a field, and never re-derives it by comparing `confidence_score` against a threshold it guessed at.
- **The decision trail records the threshold check.** `analysis_router.classify_test` appends `_routing["threshold_check"]` = `{threshold, observed_confidence, passed, source}` before stamping, so it rides the existing `routing_metadata` path with no migration. `AnalysisAgent._validate_confidence` **re-evaluates against the final, post-adjustment confidence** — reusing the threshold and source the router already resolved, so the recorded check can never describe a number that was subsequently capped, nor a policy that was not in force. Surfaced on `PerTestRouting.threshold_check` with a `below_threshold_count` rollup on `DecisionTrailResponse`. `services/agent.py` also moved `requires_human_review` from the happy path only (where it read `settings` directly) to a single exit that gates every path.
- **Two thresholds deliberately left alone, with the reasoning written down where the constant lives.** `kind_evidence.KIND_DISPLAY_CONFIDENCE_FLOOR` (60) stays independent: a *display floor* for PR-comment labels and an *action gate* answer different questions, nothing acts on a label, and coupling them would mean tightening the action gate silently strips labels off PR comments. The analysis-retry floor (`_RETRY_CONFIDENCE_THRESHOLD = 40`) and auto-triage floor are internal control-flow knobs about whether to spend more compute, not about whether to act on a conclusion — left as-is.
- **Fixer is explicitly out of scope, and the module says why.** `select_candidates` picks on quarantine state, flip rate and prior attempt count — entirely deterministic inputs with no AI conclusion to gate. Attaching a confidence would mean inventing one, which is exactly the dishonesty this story exists to remove. Bringing it in later needs a confidence attached to the **fix** (e.g. a generation-stage score validated against merged-vs-abandoned draft PRs), not to candidate selection.
- **Honesty, stated in the code and the docs:** the threshold is a **policy dial, not a calibration**. The confidences it compares are self-declared — every rules-engine band is `heuristic_estimate` (`confidence_bands.py` says outright there is no labelled corpus to calibrate against) and a human correction pins a hard-coded 95. "Confidence ≥ 80" means *the engine claimed at least 80*, not *this is right 80% of the time*. Raising the gate buys caution, not accuracy. `user-guide/ai-features.md` gains "Which engine actually answered" and "The confidence gate" sections saying so.
- Tests: `backend/tests/test_ai_trust_provenance.py` (+53) — provenance present / absent-not-fabricated / fallback surfaced; threshold precedence and junk-override rejection; the `>=` boundary at `T-1`, `T`, `T+1` on both the gate and defect promotion; below-threshold → `pending_review` + the low-confidence marker; all four `threshold_check` keys landing in `_routing`; the post-adjustment re-evaluation preserving threshold + source; and a regression pin that replacing `confidence < AI_CONFIDENCE_THRESHOLD` with the gate did not move the boundary.
### 2026-08-04 — AI trust chrome: shared AISuggestion contract, copy audit, hedging guard (US-15.1)

- **New `frontend/src/components/ai/AISuggestion.tsx` — one trust contract for every AI-produced conclusion.** Generalised from the header row `components/failures/KindEvidence.tsx` pioneered: an **"AI-suggested" badge that always renders**, an **optional** confidence that never renders without its calibration-basis chip, a routing-provenance line (which engine answered, on which model), a loud **fallback notice** when the requested engine was unavailable and something else answered, evidence links, an optional low-confidence state, and confirm/correct actions. Confidence being optional is load-bearing — the chat copilot and the Fixer have none, and the chrome must not imply one exists. An absent or unrecognised basis reads **"estimated", never "calibrated"**: rules-engine confidences are self-declared `heuristic_estimate` (`backend/app/services/confidence_bands.py` — there is no labeled corpus), and nothing may read as calibrated on the strength of a missing field.
- **`basisLabel` was copy-pasted verbatim in three files** (`KindEvidence.tsx`, `ai/AIAnalysisPanel.tsx`, `investigator/InvestigatorCockpit.tsx`). All three now import the one implementation, which also absorbs the `llm_weighted` case only the cockpit knew about.
- **Adopted on five surfaces**: the primary AI card (`AIAnalysisPanel` — the whole conclusion now sits inside the chrome, so there is exactly *one* confidence figure instead of two), deep investigation (`DeepInvestigationPage` — `origin` and `confidence_basis` have been on the wire in `types/deep-investigation.ts` since AI-F4 and were **never rendered**; seeded demo rows now say so out loud), run intelligence (`AIConfidenceCard` gains a basis chip, provenance from `provenance.generated_by`, and a real fallback notice), chat (`AssistantMessageExtras` renders the badge on **every** assistant answer — previously it rendered nothing at all when a message had no tool trace — with **no** confidence), and the Fixer's attempt reasoning.
- **Confirm/correct reuses the existing loop**, not a second one: `POST /api/v1/feedback/{analysis_id}` via `aiFeedbackService`, and the US-2.4 correction dialog lifted out of the 3 000-line `FailureAnalysisPage` into `components/ai/CorrectClassificationModal.tsx` (now accepts a pre-resolved `analysisId`, skipping the fingerprint lookup). **The buttons render only when an analysis id exists** — a dead "Correct" button is worse than none.
- **Two fabricated confidences removed.** `/intelligence` rendered each run's **pass rate under an "AI confidence" column header**, clamped to a floor of 20 and invented 88/55 when `pass_rate` was null — the code comment admitted the real value was never wired. The column is now "Pass rate", shows the pass rate verbatim, and renders an em dash when there is none. `/coverage`'s workflow ribbon carried a hardcoded `confidencePct: 85` and rendered "% confidence" over the deterministic composite coverage score; both are gone — it reads "% coverage score".
- **Copy audit.** "Root Cause Summary" → "Suggested root cause". "High confidence — the **root cause is** well-supported by tool evidence" → "the **suggestion is** well-supported … Confirm it before acting" (the old line asserted the cause was known). Run intelligence's "recommendation supported by evidence" → "the suggestion is supported …". A cluster with no AI finding now says so ("No AI finding recorded for this cluster yet") instead of labelling its cohesion score "Confidence". Pipeline **stage** names ("Root Cause Analysis" on `/agents`, `/agent-workflow`, the deep-investigation ribbon) are deliberately untouched — they name a stage, not a conclusion — as are API field names like `root_cause_summary`.
- **New quality gate `frontend.ai-output-hedging`** (`scripts/quality_gate.py`, no baseline, passing at zero). Fails CI on four verdict phrasings in rendered copy — a bare `Root cause:`, `Root Cause Summary`, `the cause is …`, `is caused by` — while allowing hedged forms and stage names. Comments are stripped before matching (with line numbers preserved) because comments legitimately quote the phrasings being banned. Its docstring carries an explicit **Deliberate blind spots** paragraph, following the `backend.audit-write-discipline` precedent: runtime-assembled copy, backend/model-authored prose (the LLM's own `root_cause_summary` is data, not source), the naive `//` stripper, semantics rather than spelling, and exempted test files.
- Also fixed in passing: `DeepInvestigationPage` normalises `confidence_score` to a 0-1 fraction — a producer emitting `91` used to render as "9100%" once the value reached a percentage formatter.
- Tests: `components/ai/AISuggestion.test.tsx` (+18, the full prop contract), plus trust-chrome and honesty-fix assertions folded into the existing `AIAnalysisPanel`, `AssistantMessageExtras`, `DeepInvestigationPage`, `RunIntelligencePage`, `IntelligenceHubPage`, `CoveragePage` and `FixAttemptsSection` suites, and 5 guard tests in `scripts/test_quality_gate.py` (synthetic offender caught; hedged copy, stage names, comments and test files pass clean).

### 2026-08-03 — Compliance-pack flag description drops the word "signed" entirely (migration 0115) + fresh installs + MCP tool doc

- **Migration 0115** — the `release_compliance_pack` feature-flag description now reads *"Generate ZIP compliance packs for release decisions, sealed with a tamper-evident SHA-256 checksum chain (no HMAC, no PKI). Tier 1 item 4."* 0114's wording ("…not cryptographically signed") was accurate but still put *signed* in front of an admin skimming Settings → Feature Flags, and left "does this claim signing?" answerable only by parsing the sentence. Naming the mechanism instead of negating a claim needs no reader to catch the "not". Guarded by an **exact match on the two known prior values** (0066's original and 0114's replacement) rather than 0114's `LIKE '%signed%'`, so an operator who has reworded the description themselves is never clobbered — including one whose wording happens to contain "signed". Downgrade restores 0114's text.
- **Migration 0066's seeded description was corrected to the same final string, byte for byte.** 0066 is the migration a brand-new database actually runs, and it still INSERTed "Generate **signed** ZIP compliance packs…"; fresh installs were only corrected incidentally, by 0114's `LIKE` guard happening to match the row it had just seeded. Now the three populations converge with no incidental matching: a fresh install seeds `_FINAL` and both data migrations no-op; a pre-0114 install goes 0066 → 0114 → 0115; an at-0114 install goes 0115 alone.
- **The `generate_compliance_pack` MCP tool advertised "Produces a signed ZIP"** — that docstring *is* the tool description an agent reasons over, so an agent asked "is this pack signed?" would have answered yes on the strength of it. It now says tamper-evident (not tamper-*proof*), names the SHA-256 manifest chain, and states there is no HMAC and no PKI. `mcp/README.md`'s tool table corrected to match. Both were missed by the US-13.3 finding-4 sweep, which covered `compliance_pack_service.py`, the ORM column comment, the generated pack README and `user-guide/compliance.md`.
- Regression tests (`backend/tests/regression/test_compliance_pack_tamper_evident_labelling.py`, +7). 0066's seeded literal is extracted from its SQL via AST (adjacent string literals fold at parse time, so the assertion is on the real value rather than on how it happens to be line-wrapped). Two strengths of check, deliberately: the **flat** rule — the words *signed / signing / signature* must not appear at all — applies to 0066's seed, 0115's `_FINAL` and the MCP tool docstring, all of which name the mechanism; the older **negation-window** rule still applies to 0114 and the generated pack README, which are shipped/applied and must not be edited. Plus: 0066's seed and 0115's `_FINAL` must be identical (or the flag reads differently depending on a deployment's age, with nothing to reconcile them), and 0115's two guard values must still match 0114's `_OLD` and `_NEW` exactly (drift there strands a whole population on the stale text, silently).

### 2026-08-03 — Feature-flag description corrected: compliance packs are tamper-evident, not signed (migration 0114)

- **Migration 0114** — the `release_compliance_pack` feature-flag description seeded by 0066 claimed packs are "**signed**". They are not: there is no HMAC and no PKI, only a SHA-256 chain (`manifest.json` digests every file; `compliance_packs.manifest_sha256` digests the manifest). That text is user-visible in Settings → Feature Flags, so correcting 0066's source would only have helped fresh installs — this data migration fixes deployed instances too. Guarded by `LIKE '%signed%'` so an operator-edited description is never clobbered; downgrade restores the original wording.

### 2026-08-03 — Security: AI_OFFLINE_MODE is now a hard egress ceiling; token revocation fails closed

> **⚠ BEHAVIOR CHANGE — read this before upgrading.**
> `AI_OFFLINE_MODE` defaults to **true**. If your deployment enabled cloud LLM
> egress by unticking "Offline Mode" on `/settings/ai` (a database override)
> *without* also setting the environment variable, **outbound LLM calls will
> stop after this upgrade** — `get_llm()` raises
> `"AI_OFFLINE_MODE=true but LLM_PROVIDER=… — refusing to call external API"`
> and analysis degrades to the rules tier.
> **Remedy: set `AI_OFFLINE_MODE=false` in the backend environment (and the
> Celery workers') and restart.** The stored setting alone is no longer
> sufficient, by design. `/settings/ai` now shows the toggle disabled with this
> reason whenever the environment pins it.
>
> A second, smaller change: with Redis down, authenticated requests now get
> **503** instead of being served with the revocation check skipped. See below.

- **`AI_OFFLINE_MODE` (environment) is a hard ceiling on outbound LLM egress**, not a default. `llm_factory.get_llm()` resolves its offline flag through `services/ai_config_resolver`, which honoured a DB override (`app_settings.ai_config.ai_offline_mode`) settable by any ADMIN from `/settings/ai` — so cloud API calls could be re-enabled **with no environment change**, while Jira, webhooks, GitHub, GitLab, the Fixer and the Investigator all read `settings.AI_OFFLINE_MODE` directly and stayed blocked. An air-gapped operator reasonably read the env var as a kill switch; for LLM egress alone, it was not. The merge is now `effective_offline = env_offline OR db_override_offline` — the stored override may only ever make the system **more** restrictive.
- **Implemented once, in the resolver**, so every consumer inherits it: `resolve_offline_mode()` / `apply_offline_ceiling()` in `services/ai_config_resolver.py` clamp on both the DB-load path **and** the Redis cache-hit path (a cache entry written by a pre-fix process must not re-open egress for up to the 60 s TTL). Verified consumers: `llm_factory.get_llm()` (refuses to construct an OpenAI/Gemini/Anthropic client), `GET/PUT /api/v1/settings/ai`, and `GET /api/v1/settings/ai/model-status` (which reads the same `_load_ai_config`, so the fallback-chain card now reports the effective state). `get_embedding_model()` and the non-LLM integrations already read the env var directly and are unchanged.
- **Provenance is published, not inferred** — the resolved config carries `offline_mode_source` (`env` | `override` | `not_offline`) and `offline_mode_env_pinned`; `AIConfigRead` gains `ai_offline_mode_source` + `ai_offline_mode_env_pinned` (and `ai_offline_mode` is now the *effective* value). The AI settings page renders the toggle **disabled**, with a "Pinned by environment" chip and a note naming the variable and the exact remedy — a silently-ignored click is how an operator ends up believing egress is on when it is off. `PUT /settings/ai` with `ai_offline_mode=false` while the env pins it is refused with **409** (payload is well-formed; it conflicts with deployment state), not recorded-and-ignored. A suppressed override logs `ai_offline_override_suppressed` at WARNING.
- **Token revocation no longer fails open** (`core/token_revocation.py`). The read path skipped the check entirely when Redis was unavailable, so a token that had been logged out — or killed by a password change — kept working for the rest of its lifetime (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default **720**, i.e. 12 h). It now raises `RevocationUnavailable`, and `get_current_user` answers **503 with `Retry-After`**, not 401: the credentials may be perfectly valid, it is the server that cannot verify them, and a 401 would send the SPA into a re-login loop that cannot succeed while blaming the user's password for a cache outage. Errors are logged with structlog kwargs (`token_revocation_unavailable`) and counted on `testlookup_auth_failures_total{reason="revocation_unavailable"}`.
- **Chose strict fail-closed over a last-known-good snapshot.** A snapshot would have to SCAN `auth:revoked_jti:*` (one key per logout across the fleet, unbounded in principle) on a timer in every worker process, against the Redis that also carries the Celery broker and live event streams — and would still need to fail closed past a staleness ceiling. It buys availability only for the pre-outage window, adds per-process divergence, and cannot help with revocations issued *during* the outage, because those writes fail too.
- **Availability trade-off, stated plainly: a Redis outage is now an authentication outage.** `/auth/login` and `/auth/refresh` are Postgres-only and keep working — they will mint tokens — but every request carrying one gets 503 until Redis returns. Recovery is to restore Redis. New env-only escape hatch **`AUTH_REVOCATION_FAIL_OPEN`** (default `false`) restores the old behaviour for an operator who consciously accepts unenforced revocation; there is no in-app toggle and every use logs at ERROR.
- **Write path is deliberately still best-effort**: `revoke_jti` / `revoke_all_user_tokens` cannot record anything with Redis down, so a logout issued *during* an outage is not persisted and that token works again once Redis recovers. They now log ERROR + a metric instead of WARNING, but do not raise — raising would 500 `/auth/logout` and `/auth/change-password`, whose Postgres refresh-family revocation (the durable half) must still commit. Documented as a residual gap in `architecture/SECURITY.md` §7.
- Docs: `architecture/SECURITY.md` §5 gains the ceiling semantics (formula, centralisation, provenance, 409) and §1 + §7 replace the "revocation fails open" caveat with the fail-closed behaviour, the auth-outage trade-off, the recovery lever, and the remaining write-path gap.
- Regression tests: `backend/tests/services/test_ai_config_resolver.py` (+7 — all four (env, DB) combinations with provenance, cache-hit re-clamp, the pure-OR helper), `backend/tests/test_ai_offline_ceiling.py` (8 — `get_llm` refusing OpenAI/Anthropic under the ceiling and still allowing a legitimate cloud setup, `_load_ai_config` provenance both ways, the 409, an unrelated PUT still working, derived keys never persisted), `backend/tests/core/test_token_revocation.py` (fail-open tests inverted to fail-closed, + broken-connection, structlog-kwargs assertion, escape hatch, empty-jti, loud write path), `backend/tests/core/test_deps_revocation_fail_closed.py` (5 — 503-not-401 with `Retry-After` and no user lookup, escape hatch, revoked-jti and cutoff still 401, clean token still resolves), `frontend/src/pages/settings/AIConfigPage.test.tsx` (+3 — disabled toggle with env var + remedy, editable when unpinned, backend refusal surfaced).
### 2026-08-03 — Audit-integrity honesty: docstrings match enforcement, new write-discipline guard, packs relabelled tamper-evident

Fixes two verified honesty findings from the US-13.3 compliance audit. Both were **claims that outran the implementation**; the fixes make the claims true (or correct them), and add the guard that keeps one of them true. No schema change, no migration, no behaviour change.

- **Audit tables said "immutable"; nothing enforced it (finding 3).** `settings_audit_log`, `access_audit_logs`, `test_case_audit_logs` and `identity_events` carried docstrings claiming immutability while the entire migration set contains exactly **one** trigger (the search-vector trigger in `0001`) — no UPDATE triggers, no restricted grants, no WORM storage. Worse, the US-11.4 retention purge *deliberately deletes* two of them on the audit clock, which flatly contradicts "immutable". All four docstrings now state the real guarantee and its boundary: **append-only by application convention**, no UPDATE path exists in application code, rows are removed only by `services/retention_service.py` on the per-project **audit clock** (`ProjectRetentionPolicy.audit_days` — floor 365 d, default 2555 ≈ 7 y, validated `>= runs_days`), and durability past that is the operator's (WORM / object-lock, restricted grants, off-host backups). Per-table specifics are recorded too: `settings_audit_log` is never purged (no project scope; it holds the purge-audit rows themselves), and `identity_events` is on no retention clock at all.
- **New guard `backend.audit-write-discipline`** (`scripts/quality_gate.py`, now **17** guards) — the house pattern is a guard, not a promise. It fails CI on any code under `backend/app/` that UPDATEs an audit table or DELETEs from one outside the single allowlisted deleter. Catches the shapes the code could actually violate: Core `update(AccessAuditLog)` / `sa.update(...)`, ORM bulk `db.query(...).update(...)`, attribute assignment / `setattr` / `session.delete` on a **fetched** audit row (scope-isolated per function, so a `row` in one function can't be confused with a `row` in another), Core `delete(...)`, and raw SQL `UPDATE` / `DELETE FROM` / `TRUNCATE` of those tables. Constructing a row and setting its fields before `flush` stays legal — that is one INSERT. Blind spots are documented in the guard docstring rather than implied away (runtime-assembled SQL, mutation via a helper that takes the row as a parameter or a closure over an outer binding, Alembic migrations, anything done outside the app). Ships at **zero violations with no baseline file** — an absolute rule, not a ratchet. `services/retention_service.py` is allowlisted for DELETE only, with the reason inline; it may still never UPDATE.
- **Compliance packs were labelled "signed"; they are only tamper-evident (finding 4).** `compliance_pack_service.py` carried a `# ZIP assembly + signing` section header, but there is **no HMAC and no PKI** anywhere in the path — integrity is a plain SHA-256 chain (`manifest.json` digests every file; `compliance_packs.manifest_sha256` digests the manifest). The header, the module docstring, `_build_manifest`, the `manifest_sha256` column comment and the pack's own generated `README.md` now say **tamper-evident checksum chain**, name the residual risk explicitly (an actor who rewrites *both* the MinIO object and the DB row forges a self-consistent pack), and point at the operator-side mitigation (WORM / object-lock on the `compliance-packs` bucket, own-key custody of an off-host copy of the digest). Signing is **not** implemented — that is a product decision, not a bug fix, and the code now says so.
- **Role-hierarchy claims (finding 5): swept, nothing stale.** A full scan of the tracked tree for four-role enumerations found none — `TESTER` (between `VIEWER` and `QA_ENGINEER`) is present in every list (`README.md`, `README_FULL.md`, `THREAT_MODEL.md`, `UserGuides/TESTLOOKUP_USER_GUIDE.md`, `user-guide/compliance.md`, `core/deps.py`, `routers/users.py`, `services/sso_service.py`, `core/config.py`, and the frontend equivalents). Pinned by a new regression test so it cannot drift back.
- Tests: `backend/tests/test_architectural_audit_write_discipline.py` (23 — tree is clean, no baseline file exists, each of the eight violation shapes is caught, three no-false-positive cases incl. the scope-isolation shape that a naive module-wide pass got wrong, the allowlist fires on exactly `retention_service.py` and nothing else when cleared, the allowlist exempts DELETE only and never UPDATE, and the four docstrings still describe the real guarantee), `backend/tests/regression/test_compliance_pack_tamper_evident_labelling.py` (4 — no signature claim in the code or the generated pack README, residual risk documented, and a tripwire if a signing helper ever lands without a relabel), `backend/tests/regression/test_role_hierarchy_is_five_tiers.py` (14). `architecture/DEVELOPER_GUIDE.md` documents the new guard (and gains the `ai.prompt-manifest-sync` row its guard count was already missing).

### 2026-08-03 — Air-gapped offline install bundle (US-13.1)

- **`deploy/images.manifest.txt` — single source of truth for container images.** Four plain-text columns (ref · role `app|infra` · surfaces `compose,k8s` · bundle class `core|llm|excluded`), deliberately not YAML so bash (awk), Python (stdlib) and the CI guard all parse it without PyYAML. The image list was previously triplicated across the compose files, `k8s/overlays/openshift-artifactory/kustomization.yaml` and a hardcoded `INFRA_IMAGES=` string in each of the two OpenShift scripts; both scripts now derive it via `scripts/release/image-manifest.sh` (`manifest_refs <surface> <role> <bundle>`), env override preserved.
- **`make offline-bundle` → `dist/testlookup-offline-<version>.tar.gz`** (`scripts/release/offline-bundle.sh`). Builds all three app images, pulls every third-party image, `docker save`s one tar per image, and assembles: `images/*.tar` + `images.list`, the rendered air-gap Kubernetes manifests (kustomize over the `openshift-artifactory` overlay, `*_PLACEHOLDER` tokens intact) plus the raw Kustomize sources, the compose files, `MANIFEST.json` (image refs, tar sha256, image ids, repo digests, per-image SBOM status), `checksums.sha256`, a README and the import script. `--with-llm` adds Ollama + ChromaDB. `created_at` is passed **in** (`--created-at` / `SOURCE_DATE_EPOCH`, wall clock only with a warning); with a fixed timestamp and a fixed image set two builds are byte-identical file-for-file (verified locally — only `repo_digest` moves, and only if the images were retagged in between). Everything goes through `$DOCKER` using flags podman implements identically. `sha256` is a hard requirement, never degraded to "unavailable" — an unverifiable bundle defeats the point.
- **`import-bundle.sh` — zero-internet import.** Verify checksums (fail-closed: mismatch, or no sha256 tool, aborts) → `docker load` every tar → retag to `<registry>/<ref>` → optional login + push → substitute the `*_PLACEHOLDER` tokens in the rendered manifests (the same mechanism `deploy-openshift-artifactory.sh` uses, so an un-rendered apply still fails at ImagePull instead of hitting the wrong registry) → print the exact next commands for Compose / Kubernetes / OpenShift. Naming contract matches `mirror-images.sh` verbatim: infra at `<registry>/<image>:<tag>`, app at `<registry>/testlookup/<name>:<version>`.
- **Compose can now be air-gapped at all** — new `docker-compose.airgap.yml` override. `docker-compose.release.yml` hardcodes Docker Hub refs for postgres/mongo/redis/MinIO with no knob to redirect them; the override re-points *every* image at `${TESTLOOKUP_REGISTRY}` and both it and `TESTLOOKUP_VERSION` are `:?`-required so a missing value fails loudly rather than silently pulling `docker.io`. Chose an override file over inlining `${REGISTRY:-docker.io}/` prefixes so `make dev` and the published `install.sh` one-liner are provably unchanged.
- **Drift guard** — `scripts/release/check_image_drift.py` (stdlib only) asserts, in both directions, that the compose files, every `image:` in `k8s/**`, the `openshift-artifactory` `images:` block (incl. "every image is remapped" and "app images keep `APP_TAG_PLACEHOLDER`") and the OpenShift mirror scripts all agree with the manifest. New CI job **Images — Manifest drift** runs it, its 14 regression tests (`scripts/release/test_image_drift.py`, which prove it *fails* on drift) and `bash -n` over the release/OpenShift scripts. The existing `k8s-image-pin-check` job keeps its name and gains a compose `:latest`/untagged check delegated to the same implementation (`--only latest`) — `docker-compose.dev-lite.yml` was shipping `minio/minio:latest` + `minio/mc:latest`, now pinned to the main compose tags.
- **Network-blocked CI** — new `.github/workflows/offline-bundle.yml`: job 1 builds the bundle online (created_at derived from the commit timestamp); job 2 downloads it and does every import step inside `docker run --network none --pull=never` containers — busybox is loaded *from the bundle* and used to verify `checksums.sha256`, isolation is proven by asserting the container **cannot** reach the internet, then `docker load` + retag + an offline `import app.main` in the backend image and `import mcp` in the MCP image, plus a `MANIFEST.json` consistency check. Deliberately **not** reusing `backend-test` (its `services:` containers are pulled from Docker Hub). Job name and comments state plainly that this proves *bundle integrity + offline import*, not a full-stack e2e.
- **SBOM, honestly** — `docker save` carries layers and config, **not** the BuildKit SBOM/provenance attestations `release.yml` produces (those live in the registry manifest list). The bundle therefore prefers `syft` on the build host (real SPDX per image under `sbom/`), and otherwise writes an explicit `"sbom": {"status": "unavailable", "reason": …}` per image. It never implies an SBOM exists when it does not; the CI verifier asserts that invariant.
- **MinIO skew: kept, not reconciled.** Compose stays on `RELEASE.2024-11-07T00-52-20Z` (MinIO stripped the object browser out of the community console in the 2025-04 releases — moving forward would break the `:9001` console `make dev` documents); k8s stays on `RELEASE.2025-09-07T16-13-09Z` (downgrading a live MinIO server is riskier than leaving it pinned). Both tags ship in the bundle, both are pinned by the drift guard, and the reasoning lives inline in the manifest so the skew stays a decision rather than an accident.
- `make build` now also builds the MCP image (it was building only backend + frontend while `release.yml`, the compose files and `k8s/base/mcp-deployment.yaml` all ship three) — a bundle built on it would have been an incomplete deployment. Same fix in `build-push`.
- Docs: new `user-guide/air-gapped-install.md` (end-to-end procedure for Compose / Kubernetes / OpenShift, prerequisites, bundle contents, the SBOM caveat, the MinIO skew, LLM-model transfer, troubleshooting, maintainer notes); `architecture/DEPLOYMENT.md` gains an air-gapped topology row, the airgap-override description, a §4b on the image manifest and the new workflow.
### 2026-08-03 — Offline model pack + live AI model-status (US-13.2)

- **New `GET /api/v1/settings/ai/model-status`** (QA_LEAD+, read-only, mirrors the `GET /settings/ai` guard) — live, honest state of the local model backend: `ollama_reachable` + `ollama_error` + `ollama_base_url`, `installed_models`, `required[]` (`name` / `purpose` llm·embedding·classifier / `present` / runtime-aware `remedy`), `fallback_chain[]` (`mode` / `available` / `reason`, ordered ml → llm → rules to mirror `analysis_router.get_analysis_mode()`'s auto resolution), `offline_mode`, `llm_provider`, `analysis_mode`, `checked_at`. Never raises: an unreachable Ollama, an unreadable `app_settings` row, or a broken ML model dir each degrade into fields rather than a 500 — the page has to render precisely when things are broken.
- **Availability is measured, not assumed** — LLM tier: Ollama reachable **and** the configured model installed (cloud providers: key set and not blocked by offline mode; self-hosted OpenAI-compatible endpoints are reported available but explicitly *unverified*). ML tier: `MLClassifier.is_available()` (a trained model on disk). Rules: always available, terminal. "Ollama unreachable" and "model missing" are kept strictly apart — a connectivity failure never hands out a `pull` recipe, since pulling cannot be the fix yet.
- **Exact-tag model matching** — `qwen2.5:14b` does not satisfy `LLM_MODEL=qwen2.5:7b` (a missing tag normalises to `:latest`). Deliberately stricter than `analysis_router`'s family-prefix auto-probe: the router's looseness only decides whether to *try*, whereas this endpoint answers "did my model pack import correctly?", where "close enough" is a wrong answer.
- **One probe, one remedy string** — new `backend/app/services/model_status_service.py` owns the single `GET /api/tags` probe and the runtime-aware `ollama pull` recipe (compose vs kubectl). `routers/health.py::_check_ollama` and `services/agent.py::_model_missing_hint` now delegate to it instead of carrying forked copies, so the command shown in the UI is the one written into a degraded analysis. `/health/details` output shape is unchanged — including its `AI_OFFLINE_MODE` skip guard, which is coherent (offline mode = local models only) but keyed on the setting next to the decisive one; a deployment running Ollama with offline mode *off* still reports `skipped` there. Documented rather than changed; the new endpoint keys off the effective provider instead and is the accurate source.
- **AI settings page shows the live chain** (`/settings/ai`) — the hardcoded fallback strings ("ML if trained, else LLM if available, else Rules") described a runtime fact the page never checked. Replaced with a **Model Availability & Fallback Chain** card fed by a new `useAIModelStatus` SWR hook (ACTIVE tier + Re-check button, for operators watching a side-load land): offline-mode indicator, reachability line with the base URL and installed count, per-required-model Installed/Missing with the exact remedy, and each tier rendered available/unavailable **with its reason** plus an Active marker. `activeTier()` respects a pinned mode — a pinned tier that cannot run degrades to rules, not to "first available". Mode descriptions now describe the engine and assert nothing about availability. A failed status fetch says only that the *check* failed. The existing configuration form is untouched.
- **`user-guide/offline-model-pack.md`** — the model manifest (`qwen2.5:7b` + `nomic-embed-text` as supported defaults; 3B/8B/14B/32B and `mxbai-embed-large` as known-workable alternates) with disk/RAM/quality-latency tradeoffs and the re-embedding warning for embedding-model swaps; the true air-gap import procedure (Ollama has no save/load — archive `blobs/` + `manifests/` from the model store, checksum, transfer, restore into the compose `ollama_models` volume or the `ollama-models-pvc` PVC at `/root/.ollama/models`, verify via the new endpoint), sha256 generation/verification, why model files are not in the code bundle, and how the pack relates to `AI_OFFLINE_MODE`.
- Regression tests: `backend/tests/test_offline_model_pack.py` (30 tests — probe reachable/non-200/transport-error/never-raises, exact-tag matching incl. the same-family-wrong-size negative, all-present happy path, chain order, ML tier availability + probe-failure degradation, unreachable-still-returns-chain-with-rules and no pull recipe, missing-model remedy, embedding miss not disabling the LLM tier, classifier entry, cloud-blocked-by-offline-mode / no-key / self-hosted-unverified, empty-config env fallback, health delegation + no forked `/api/tags` call, shared pull recipe in `agent.py`, QA_LEAD route gating + below-QA_LEAD 403, response-model round trip); `frontend/src/pages/settings/AIConfigPage.test.tsx` (8 hermetic tests — live chain per tier with reasons, Active marker under auto vs a pinned mode, missing-model remedy, unreachable-vs-missing, failed-check messaging, re-check, and the existing form still saving).
### 2026-08-03 — Compliance control-family mapping: SOC 2 / GDPR / HIPAA (US-13.3)

Documentation only — no application code changed.

- **`user-guide/compliance.md` → "Control-family mapping"** — three tables mapping shipped capability to control families so an auditor can be pointed at a specific surface instead of commissioning a discovery project. **SOC 2 TSC** (CC6.1 access + authentication, CC6.2/6.3 provisioning via SSO/SCIM, CC6.6/6.7 boundary + secrets, CC7.1/7.2 monitoring, CC7.3/7.4 incident = nothing, CC8.1 change management + product supply chain, CC9.2 vendor risk, A1.2 backup/recovery, C1.1/C1.2 retention + redaction); **GDPR** (Art. 5(1)(c) minimisation, 5(1)(e) storage limitation, 15/20 access & portability, 17 erasure, 25 by-design, 30 records, 32 security, 44+ transfers); **HIPAA Security Rule** (§164.308 administrative, §164.310 physical, §164.312 technical). Every row carries a concrete pointer — UI path, endpoint, table, or source file — plus its limitation inline.
- **Framing is enablement, never certification** — the document states explicitly that TestLookup is not certified against any standard, that certification attaches to an organisation rather than software, and that the customer's own controls complete each row. Every claim was verified against the code before it was written.
- **New "What TestLookup does NOT provide" section** — the two structural caveats (bulk data at rest is **not** application-encrypted, only secrets are; TLS/in-transit is inherited from the deployment target with no in-cluster mTLS) plus the gaps found while verifying: no MFA/lockout/password-complexity, no subject-level GDPR access or erasure workflow (users deactivate rather than delete; `DELETE /api/v1/projects/{id}` is a soft delete), audit append-only is an application convention with no DB-level enforcement, compliance packs are tamper-**evident** not signed, no incident-response or alerting features, no BAA or certification, physical safeguards belong to the host, and backup/DR is Compose-only with scheduling/off-host copies/encryption/restore drills left to the operator.
- **New "Evidence-gathering quickstart"** — a request-to-surface table (audit dashboard, compliance pack, retention policy + purge audit, identity events, audit CSV export, offline-mode posture, CI gates).
- **New "How a pack proves it hasn't been altered"** — documents the `manifest.json` → `compliance_packs.manifest_sha256` hash chain honestly as tamper-evidence rather than a signature, with the WORM/own-key-signing recommendation for non-repudiation.
- **`architecture/SECURITY.md`** — new §7 "Where these properties stop" (secrets-only encryption, deployment-inherited TLS, audit append-only-by-convention, Redis-outage fail-open on token revocation) and §8 pointing at the control-family mapping; the old §7 becomes §9. No content duplicated between the two documents.

### 2026-08-02 — Retention & purge controls (US-11.4)

- **Per-project retention policies** — new `project_retention_policies` table (migration **0113**; one row per project, missing row → code defaults, disabled by default, server defaults mirror ORM defaults) with four independent clocks: raw events 90 d (Mongo raw/live payloads + `TestRun.event_archive` strip — the documented-but-never-enforced 15-day purge finally has an owner), runs 365 d (test_runs + full Postgres cascade + run-scoped Mongo docs), artifacts 180 d (run upload prefixes + pipeline artifacts in object storage), audit 2555 d (access/test-case audit rows, AI provenance, pipeline event log, **expired** compliance packs — `retention_expires_at` is now actually enforced). `settings_audit_log` is NEVER purged.
- **API** — `GET/PUT /api/v1/projects/{id}/retention-policy` (GET any member incl. `last_purge`; PUT ADMIN, partial body, bounds raw/artifacts 7–3650 / runs 30–3650 / audit 365–3650 + cross-check `audit_days ≥ runs_days` → 422), `POST .../retention-policy/preview` (ADMIN, synchronous dry run — per-class cutoffs + candidate counts, writes nothing, works while disabled), `POST .../retention-policy/purge` (ADMIN, typed-name confirmation → 422 on mismatch, 409 while disabled, 202 enqueue).
- **Scheduled purge** — `nightly-retention-purge` beat (02:00 UTC) sweeps enabled projects; per-project try/except isolation; purge order pins the cross-store correctness: materialize test-case/pipeline/live-slug/prefix mappings from Postgres FIRST, then Mongo deletes (batched `$in` ≤ 500), then MinIO, then the cascading run delete, then event-archive strip, then audit-clock deletes. Non-transactional across stores by nature → the job is re-entrant (same cutoffs re-find the remainder). Every execute purge writes a `settings_audit_log` record (`retention_purge:{project_id}`: mode, cutoffs, per-store counts, duration, errors) AFTER the purge commit on its own session.
- **Provenance survives run purges** — migration 0113 flips `ai_provenance_records.run_id` `CASCADE` → `SET NULL` and adds a backfilled `project_id` (CASCADE) + index, so provenance is deleted only on the audit clock; the purge also stamps `project_id` onto not-yet-backfilled rows before the run link detaches.
- **StorageProvider gains deletes** — `delete_object` + `delete_prefix` on the ABC and both S3/Local implementations (Local reuses the path-traversal guard; `delete_prefix` refuses empty/bucket-wiping prefixes; S3 batches `DeleteObjects` at 1000). New Mongo `run_id` index on `live_execution_events` so the purge doesn't collection-scan.
- Regression tests: `backend/tests/test_retention_purge.py` (35 tests — policy CRUD/bounds/cross-check/stage-only, the Mongo→MinIO→Postgres ordering pin via a shared event journal, preview-writes-nothing + full contract keys, runs-vs-raw-vs-audit cutoff math, event-archive strip on the raw clock NULLing both columns, re-entrancy zero-candidates, provenance FK contract + audit-clock delete, migration 0113 chain/single-head/downgrade contract, Local storage traversal guard + prefix counting + bucket-wipe refusal, confirmation/409/404 gating, beat-sweep failure isolation + zero-count audit rows). Docs: `user-guide/administration.md` → "Data retention & purge".
### 2026-08-02 — Retention & purge admin UI (US-11.4 UI)

Frontend for the PMF retention initiative (backend built in parallel against the same pinned contract):

- **New `/settings/retention` page** (`frontend/src/pages/settings/RetentionPage.tsx`, registered in `App.tsx` managementRoutes + an Archive card on `/settings` next to Project Data) — ADMIN-only in-component gate (EmptyState for non-admins, ProjectDataPage pattern), concrete-project only (All-Projects → select-a-project state, zero fetches).
- **Policy form** — enabled toggle ("The nightly purge only runs for projects where this is enabled"), four day-count windows (Raw events, Runs & analysis, Artifacts, Audit trail) with helper text per data class, inline contract bounds (raw_events/artifacts 7–3650, runs 30–3650, audit 365–3650) plus the audit ≥ runs cross-rule, Defaults/Customized source badge, changed-fields-only `PUT /api/v1/projects/{id}/retention-policy`, dirty-guarded form seeding (background SWR revalidation can't wipe unsaved edits), local toasts for non-axios errors only.
- **Purge preview** — `POST …/retention-policy/preview` dry run (works while the policy is disabled): per-class cutoff timestamps + a candidates table (runs, test cases, per-Mongo-collection doc counts, MinIO objects, event-archive rows, audit rows, provenance rows, expired compliance packs) with an honest point-in-time note (the nightly job may differ slightly). Inline loading/error states.
- **Danger zone** — "Purge now" behind a typed-project-name confirmation modal (mirrors ProjectDataPage's ResetConfirmModal; sends `confirmation_name`); disabled with an explanatory title while the saved policy is disabled (the backend 409s in that state); 202 → "Purge queued — check back for the audit record" toast; `last_purge` summary (when / mode / counts) rendered underneath.
- **Failed GET → form withheld** — error state with Retry instead of a defaults-seeded form (a Save over a failed fetch could clobber a working policy — the #433 lesson).
- **Contract plumbing** — `frontend/src/types/retention.ts` (contract-verbatim types + `RETENTION_BOUNDS` + contract defaults), `retentionService` on the shared axios base, `useRetentionPolicy` SWR hook + imperative update/preview/purge helpers.
- **Tests** — `RetentionPage.test.tsx`: hermetic 14-test suite (real SWR hook + mocked service + real `ALL_PROJECTS_ID`, GitLabIntegrationPage style) covering the admin gate, All-Projects no-fetch, defaults + both badge states, bounds + cross-rule save blocking, changed-fields-only PUT shape, no-op save disabled, preview counts incl. per-collection Mongo docs, preview error, purge modal name-match + `confirmation_name` payload, disabled-policy purge lockout, GET-failure form withholding + retry, and last_purge rendering.

### 2026-08-01 — ROI: engineer-hours-saved model — tunable assumptions + monthly trend + digest line (US-12.1/US-12.2)

- **`GET /api/v1/value-metrics` extended with the documented hours-saved model** (existing keys preserved): `available` + `insufficient_data_reason` (gate: ≥14 days between first/last ingested run AND ≥1 nonzero leg in the last 30 days — headline hidden until then), `headline` (`hours_saved_30d`, `fte_equivalent_30d` = hours/173.2), `monthly` trend (ISO `YYYY-MM-01` buckets, ascending, data months + current; new `months` query param 1–24, default 6), `assumptions`, `assumptions_source`, `methodology_version`. Cached in Redis ~10 min with the assumptions fingerprint in the key.
- **Three legs, honestly labeled** — triage: `clustered_failures × triage_minutes_per_failure / 60` (cluster-instances, NOT distinct defects; `auto_triaged` AI analyses reported for context only, never multiplied — no double counting); quarantine: `runs_unblocked_proxy × blocked_run_wait_minutes / 60` (a documented **proxy**: runs whose every failing case sat inside an active quarantine window — no persisted "run unblocked" verdict exists); dedup: `duplicates_absorbed × defect_filing_minutes / 60` (`SUM(size−1)`, "duplicate failures absorbed", never "defects deduped"). All legs project-scoped via joins; quarantine join runs on indexed fingerprint columns only.
- **Per-project tunable assumptions** — new `value_metric_assumptions` table (migration **0112**, one row per project, missing row → code defaults, server defaults mirror ORM defaults) + `GET/PUT /api/v1/projects/{project_id}/value-metrics/assumptions` (GET any member; PUT QA_LEAD+ with `require_project_access`, bounds 0 < x ≤ 480 → 422, service stages / router session commits). Defaults: triage 20 min (inside the published 15–25 min refocus band; ~3 h/non-trivial-failure anchor documented), wait 30, filing 15. `/by-team` and the legacy scalar now draw from the same rate card.
- **`GET /api/v1/value-metrics/methodology`** — static model documentation (legs, formulas, inputs, caveats, defaults, research anchors); linked from the headline number. Prose version in `user-guide/value-metrics.md`.
- **Digest headline (US-12.2)** — weekly/daily digests append "≈ N engineer-hours saved in the last 30 days (see /value-metrics)" as a text line + an email header stat tile, only when the availability gate passes; suppressed on zero-change windows and on any computation fault.
- Regression tests: `backend/tests/test_roi_hours_saved.py` (assumptions CRUD round-trip incl. never-commits, bounds 422, leg math, availability gate, monthly wire format, model contract shape, methodology anchors, quarantine-state parity, digest present/absent matrix, migration 0112 chain); `test_roi02_value_metrics.py` updated to the new rate card + response shape.
### 2026-08-01 — ROI UI: hours-saved headline + methodology + assumptions editor + Overview KPI (US-12.1/US-12.2 UI)

Frontend for the PMF hours-saved ROI model (backend built in parallel against the same pinned contract):

- **`/value-metrics` upgraded to the hours-saved model** (`frontend/src/pages/ValueMetricsPage.tsx`) — new "Eng-hours saved · last 30 days" headline with FTE equivalent; the headline number itself (and an explicit "How is this calculated?" link) opens a methodology panel rendering the `/api/v1/value-metrics/methodology` legs/formulas/inputs/caveats/research notes. Monthly stacked-bar trend (recharts, theme tokens only) of `hours_triage`/`hours_quarantine`/`hours_dedup`, plus honest model-count cards ("Duplicate failures absorbed", "Runs unblocked (estimate)" with a proxy tooltip). **Honest empty state:** `available=false` renders the `insufficient_data_reason` notice — never "0 hours saved". Legacy counters/cards unchanged below.
- **Assumptions editor (QA_LEAD+, per-project)** — three minute-valued model inputs with client-side bounds (0 < x ≤ 480), changed-fields-only `PUT /api/v1/projects/{id}/value-metrics/assumptions`, Defaults/Customized source badge that flips via SWR revalidation after save, local toasts for non-axios errors only (shared interceptor owns axios toasts). Hidden for viewers and in All-Projects mode.
- **Overview "Eng-hours saved" KPI card (US-12.2)** (`frontend/src/pages/OverviewPage.tsx`) — mirrors the existing KpiCard pattern, links to `/value-metrics`, rendered ONLY when the model reports `available` (omitted entirely otherwise — no dash-card). Fetches via `useValueMetricsKpi()` sharing the page's SWR key shape for fetch dedup.
- **Contract plumbing** — `frontend/src/types/valueMetrics.ts` (contract-verbatim types; `ValueMetricsLegacy` base keeps workflowPresets fixtures lean), `valueMetricsService` gains `months` param + methodology + assumptions get/put with a flat-or-nested response normalizer (contract ambiguity — reconcile with backend), `useValueMetrics` gains `months` + bound `refresh`, new `useValueMethodology` (lazy) and `refreshValueMetrics`.
- **Tests** — `ValueMetricsPage.test.tsx` rebuilt as a hermetic 11-test suite (real SWR hook + mocked service + real `ALL_PROJECTS_ID`, GitLabIntegrationPage style): insufficient-data honesty, headline/FTE, methodology panel content, ascending chart series, changed-fields-only PUT, bounds validation, badge flip, permission/all-projects hiding. `OverviewPage.test.tsx` +3 (card present when available, omitted when unavailable/unloaded). `useValueMetrics.test.ts` extended for the months window.

### 2026-08-01 — MCP server: pin `mcp<2.0.0` (CrashLoopBackOff on fresh builds)

- **`mcp/requirements.txt` — `mcp>=1.0.0` → `mcp>=1.0.0,<2.0.0`.** A fresh image build resolved the new mcp 2.x, which removes `mcp.server.fastmcp` — `server.py:67` raises `ModuleNotFoundError` at startup and the pod CrashLoopBackOffs (caught deploying to the homelab 2026-08-01; July images had mcp 1.28.1). The server is written against the 1.x FastMCP API; upgrading to 2.x is a separate migration.

### 2026-07-16 — GitLab integration hardening (post-merge review fixes)

- **Read/write schema split (drift-proof GET)** — `GitLabConfig` split into `GitLabConfigWrite` (PUT body: strict `mr_comment_mode` Literal, `^https?://`-validated `base_url`, write-only `token`) and `GitLabConfigRead` (structurally token-free; `mr_comment_mode` is a plain `str`, GitHub-sibling pattern), and `_to_config` coerces any drifted row value to `failures_only` — a manually fixed-up / partially rolled-back row can no longer turn GET into a ResponseValidationError 500 (empty settings page). `response_model_exclude={"token"}` is gone; the read model simply has no token field.
- **Commit-status session scope** — `post_commit_status_for_run` restructured to the MR-note shape: read run + integration + PAT into a context in ONE short session, close it, then SSRF-check and POST with **no DB session held across the HTTP round-trip**, persisting outcomes via `_record_outcome`. Transaction-ratchet allowlist tightened 5 → 1 commit (total 63 → 59).
- **Guards** — `commit_hash` is URL-quoted in the statuses path (CI-supplied value can no longer rewrite the authenticated request path); the commit-status repo guard is now mandatory (NULL/empty `ci_repo` → `repo_mismatch`, empty `project_path` → `no_project_path`, mirroring the MR path); an id-less marker note no longer degrades into "post a new note" (duplicate-note spam); `base_url` scheme is enforced at the schema AND normalized in `upsert_integration` (a schemeless host silently no-ops the SSRF guard); ORM `server_default` added to `enabled`/`has_pat` to stop autogenerate drift vs migration 0111 (no migration needed).
- **Dispatch isolation** — the ingestion pipeline gives the MR-note and commit-status posts each their own try/except (`gitlab_mr_note_unhandled` / `gitlab_commit_status_unhandled`) so an MR-note failure can't suppress the commit-status post; the router's PUT/test endpoints now emit audit-value structlog events.
- **Review test gaps closed** (`backend/tests/services/test_gitlab_integration_service.py`) — note-list pagination (marker on page 2 → 2 GETs + PUT; short first page → 1 GET), commit-status SSRF-block/repo-mismatch/NULL-`ci_repo`/empty-`project_path` with zero-HTTP assertions, commit-hash quoting, `upsert_integration`'s 3-way token contract (None/`""`/value) with never-commits assertions, `PRIVATE-TOKEN` header transport + PAT-absent-from-results, drifted-mode coercion, write-schema validation; dead test scaffolding deleted.
- **CI recipe corrected** (`user-guide/reference/testlookup-gitlab-ci.yml`) — CLI invocation fixed to the shipped form (`testlookup upload file … --build "$CI_PIPELINE_IID"`, no nonexistent `--api-url`); `TESTLOOKUP_API_URL` → **`TESTLOOKUP_URL`** (the variable clients actually read — the old name silently targeted localhost); install lines made pasteable (apt-get git on `python:3.11-slim`, correct dist names `testlookup-cli`/`testlookup-reporter` with `#subdirectory=`, loud replace-me placeholder, repo-internal fallback dropped); the live job no longer `|| true`s its whole body — it tolerates red tests but fails on reporter/config breakage and verifies ingestion (runs-list lookup + optional `ci-verdict` gate); self-referential `variables:` block deleted; nav path fixed to Settings → GitLab Integration (`/settings/gitlab`) here, in `administration.md` (heading + "Merge-request comment" control name), and the `getting-results-in.md` anchor.
### 2026-07-16 — GitLab settings page hardening (post-merge review fixes)

Post-merge review fixes for the PR #394 frontend (`frontend/src/pages/settings/GitLabIntegrationPage.tsx` + `useGitlabIntegration.ts` + `types/gitlab.ts`):

- **Error-state guard (Major)** — a failed config `GET` now renders an explicit error state with a Retry button and **withholds the form/Save entirely**; previously the page silently seeded the form with contract defaults, so one Save after a transient fetch failure would overwrite a working integration with defaults.
- **Dirty guard on revalidation** — any in-progress edit (field change, half-typed PAT, armed token removal) sets a dirty flag that skips the render-time re-seed, so a background SWR revalidation can no longer wipe unsaved edits. A successful save clears the flag and re-seeds from the saved response.
- **"Remove token" affordance** — the contract's `token: ""` clear semantics are now reachable from the UI: a confirm-gated "Remove token" button arms the clear (with a visible pending state + cancel), applied on the next save.
- **`autoComplete="new-password"` on the PAT input** (threaded through `FieldText`) so password managers neither save the PAT nor autofill the user's password into it.
- **Test-connection disabled while dirty** (title: "Save your changes first — Test uses the saved configuration") — it probes the SAVED config, not on-screen edits.
- **No duplicate error toasts** — save/test failures from axios rely on the shared interceptor's server-detail toast; a local toast remains only for non-axios failures.
- Nits: exhaustive `Record<MrCommentMode, string>` labels (select options derived from it; value narrowed via the record keys instead of a blind cast), hook fetcher narrows `projectId` once (no unreachable `?? ''` fallback), `DEFAULT_GITLAB_CONFIG` frozen.
- Tests rewritten to run the **real SWR hook** against a mocked service layer (was: hook mocked) with the **real `ALL_PROJECTS_ID`** via `importActual` (the old mock invented `'__ALL_PROJECTS__'`; real value `'all'`). 14 tests (was 8): + All-Projects empty state with zero fetches, GET-error state (no Save, Retry refetch), token input cleared/re-hidden after save, `token: ""` sent after Remove-token, Test-disabled-while-dirty, permission-denied read-only view.
### 2026-07-16 — Fixer agent security + reliability hardening (backend audit)

- **PAT redaction in stored errors (Blocker)** — the Docker runner now scrubs the clone token from any captured git output before it leaves the runner (`runners.redact_secrets`), and the workflow defensively re-scrubs every infra-error / PR-failure reason against the integration PAT before persisting — a failed clone can no longer leak the token into the API-readable `FixAttempt.reason`. Regression tests at both layers (`test_clone_failure_error_is_redacted`, `test_stored_error_reason_never_contains_pat`).
- **workflow_dispatch run correlation (Blocker)** — the dispatch runner no longer reads "the latest workflow run": it captures UTC-now before dispatching, threads a unique `correlation_id` through the dispatch inputs, polls `?event=workflow_dispatch&created=>=<dispatch-time>&per_page=10`, and only accepts a run whose `name`/`display_title` echoes the correlation id (head-branch match as fallback). No attributable run by the deadline → honest "not observed" error carrying the last poll status — a foreign run's conclusion can never mint a `validated` (and hence a PR).
- **LLM / GitHub HTTP moved outside DB sessions (Blocker)** — the candidate loop now closes its session after advancing to `generating`, runs diagnosis+generation with no transaction open, reopens to record results; the PR-open path likewise commits the `validated` state, closes, performs the GitHub round-trips, and reopens to persist the PR outcome (mirrors the existing validation pattern).
- **runner_image argv-injection validation (Major)** — `runner_image` must match a strict image-reference regex (`state.RUNNER_IMAGE_RE`), enforced at the config write path (router field validator + `fixer_service._coerce_runner` cleans stored rows) AND re-asserted in `build_docker_run_argv` (raises) plus an early error result in the runner — `--privileged` can no longer land in `docker run`'s flag position.
- **Dispatch lock (Major)** — `gate_fixer_run` now takes a Redis `SETNX fixer:run:{project_id}` lock (2h TTL, released in `workflow._finalize`; freed on enqueue failure by both dispatch paths), closing the beat-vs-manual double-dispatch race. Fails open with a warning when Redis is down (the DB active-run gate still applies).
- **Bounded + incremental PR-outcome sweep (Major)** — the beat sweep is capped at the 200 oldest open-PR attempts, snapshots rows+credentials then closes the session before any HTTP, reuses ONE `httpx.AsyncClient`, persists in batches with per-attempt commits, and advances `pr_state` ONLY after `record_fix_outcome` succeeds — a failed feedback write is retried next sweep instead of being silently dropped. (Commits are Celery-beat-owned in `agents/fixer/pipeline.py`; the transaction ratchet governs `app/services/` and is unaffected.)
- **Checkout verification + real default branch (Major)** — `git checkout` is rc-checked (non-zero → `error`, never a validation of the wrong ref); SHA-like refs use `git fetch --depth 1 origin <sha> && git checkout FETCH_HEAD`; `ValidationSpec.ref` now carries the repo's real default branch (resolved once in `_github_ctx` via the hoisted `pipeline.fetch_default_branch`), falling back to `main` only when unknown. Clone host is derived from `integration.api_base_url` (GHE-safe) instead of hardcoding github.com.
- **Candidate query O(1) (Major)** — `select_candidates` orders + limits in SQL (`flip_rate` desc nulls-last, `last_failure_at` desc) and replaces the per-row prior-attempt COUNT with one GROUP BY over the capped set.
- **Minors** — run POST is now project-scoped `require_project_role(QA_ENGINEER)` and returns 503 when the broker enqueue fails; ephemeral containers get `--name fixer-{uuid}` with best-effort `docker rm -f` on timeout + process reap; the world-writable workspace chmod is removed (worker UID 1000 matches the container `--user`); kill-switch read faults now fail CLOSED and the check reuses the candidate's session; `_finalize` always runs (run-level try/except → `status="error"`) and stamps `ledger_run_id` with a single UPDATE; poll/timing uses `time.monotonic()`; generation errors store only the exception type (full text at debug); GitHub PR-create errors store the parsed `message` not the raw body; the beat schedule filter moved into SQL (`budgets->>'schedule'`), gate errors are caught by type, lost slots are logged, and both beat tasks bind task context; `ValidationSpec.env_allowlist` is now wired into the docker argv as pass-through `-e NAME` for well-formed names only.
- Tests: `backend/tests/test_fixer_audit_hardening.py` (17 new pins) + PAT-leak and harness updates in `test_fixer_pipeline_e2e.py`.

### 2026-07-16 — Attribution + CODEOWNERS hardening (backend audit)

Fixes for the verified 2026-07 backend-audit findings in commit attribution (Epic 8 US-8.1/8.2) and CODEOWNERS/assignment (US-8.3/8.4). Backend-only; no migration.

- **Connector session/pooling/fan-out fixes (`services/commit_attribution_service.py`)** — `resolve_commit_range` is now phase-split: all DB reads (run fields, existing row, baseline, integration + PAT) happen on a short-lived internal session that is **closed before any HTTP**; the GitHub fan-out runs with no session/transaction open; only the final upsert touches the caller's session (which still owns the commit — no new service commits, transaction ratchet unchanged). The whole fetch reuses **one pooled `httpx.AsyncClient`**; per-commit changed-file detail is fetched with `asyncio.gather` in chunks of 8 and capped at **25** commits (down from 100 — ranking beyond that is noise per the module's own honesty note, and 101 sequential calls per resolve burned GitHub rate limit). The SSRF guard now also covers the detail fan-out: the resolved `api_base` host is re-checked **once** before the per-commit GETs (DNS-rebinding window), not per URL. Supplied `files` entries get a 512-char per-string cap; `score_commits` computes `path_overlap` once per file instead of twice.
- **6h re-resolve cooldown (kills GET-amplified GitHub calls, findings #5 + correctness minor)** — an `unavailable` row younger than 6 hours now suppresses re-resolution, both inside `resolve_commit_range` and via a shared `needs_resolution()` gate in the `commit-range` / `suspects` GET handlers (previously *every* GET on a range-less run re-ran the connector — up to 101 GitHub calls per request). Stale rows re-resolve after the cooldown; `supplied`/non-empty rows never re-resolve from the GET path. The router also drops a redundant second run load, and the resolve's blanket `except` is narrowed to `(httpx.HTTPError, SQLAlchemyError)` so programming errors surface to the isolated-step handler instead of being logged away.
- **Upsert race fix** — `_upsert_range` now uses PostgreSQL `INSERT … ON CONFLICT (run_id) DO UPDATE` instead of read-then-insert, so a concurrent resolve (finalize racing a lazy GET) can't raise `IntegrityError` and poison the caller's session; non-supplied writes carry a `WHERE source != 'supplied'` guard so a racing supplied row is never clobbered (supplied always wins, now race-proof).
- **GitHub-faithful CODEOWNERS glob semantics (`services/codeowners_service.py`, finding #9) — BEHAVIOR CHANGE for assignment** — path-rule matching replaces `fnmatch` (where `*` spans `/`, so `docs/*` matched `docs/a/b/c.md` and owners over-matched) with a CODEOWNERS-faithful `_codeowners_pattern_to_regex` (lru-cached): `*` never crosses a segment (**`docs/*` no longer matches nested paths — use `docs/**`**), `**` spans segments (`a/**/b` also matches `a/b`), `?` is one non-`/` char, no-slash patterns match at any depth (`*.py`), a leading `/` or any interior `/` anchors to the repo root, and a trailing `/` matches everything under the directory. Imports now store the **raw** CODEOWNERS pattern verbatim; rows imported earlier (old normalized form, e.g. `build/**`) keep matching equivalently. Assignments/inbox reasons/coverage all flow through this one matcher.
- **Member-scoped handle resolution (security minor)** — `resolve_handles_to_users` now requires `project_id` and joins `ProjectMember`, so a CODEOWNERS `@handle`/email resolves only to users who are members of the project — previously a matching non-member received assignments (and stack-trace context in their inbox). Strict: admins resolve only if they're members too.
- **Bulk import ops + 5000-entry cap** — the CODEOWNERS re-import replaces prior rows with one bulk `DELETE` (rowcount-reported) instead of a per-row ORM delete loop, and rejects files parsing to more than 5,000 entries with a clear `ValueError` that `POST …/codeowners/import` maps to a 422. Imported rules occupy the `0..N` priority band; hand-authored path rules that must always win should use `priority >= 1_000_000`, and the import now WARNs when hand-authored rules sit inside the imported band. Also: an empty Contents-API `content` now reports `empty_content` instead of a "successful" zero-rule import, and inbox reason-derivation failures log at warning with the affected row count.
- **Backfill de-N+1 (`services/failed_test_assignment_service.py`, finding #13)** — `backfill_unassigned_failures` builds a per-project `ProjectAssignmentContext` (owner config, QA-lead/admin pools, path rules + member-scoped handle map) **once** and shares it across that project's runs — previously each of up to 200 runs re-ran the full lookup set (~1,400 queries per sweep). The single-run path is unchanged (context built on demand). Path rules now load lazily only when a failure actually reaches the path-owner branch; `assigned` counts report the UPDATE's `rowcount` (effect) instead of intent; and the failure/coverage queries select a bounded 4000-char `substr` of `stack_trace`/`error_message` (only the path locator consumes them) instead of whole multi-MB blobs.
- Tests: `test_commit_attribution_service.py` rewritten for the phase-split (connector-target extraction, pooled-client fetch, capped + chunked fan-out, once-per-fetch SSRF re-check, cooldown matrix, `needs_resolution` gate, ON-CONFLICT SQL shape incl. the supplied-wins guard, per-file-path cap); `test_codeowners_service.py` gains a 14-case glob-semantics battery (GitHub's own `docs/*` example, root anchoring, dir rules, `**` zero-dir, legacy-row compat), bulk-delete/rowcount import tests, the 5000-entry rejection, the band-overlap warning, and member-scoping assertions. Both architectural ratchets, `quality_gate.py`, and ruff green.
### 2026-07-25 — Design-audit token ratchet: VerdictBand blocker-severity icons

- **`frontend/src/components/releases/VerdictBand.tsx`** — the release-health hero band's per-blocker severity icons now use per-theme status tokens instead of raw Tailwind palette classes: `resolved` → `text-[var(--status-passed)]`, `warn` → `text-[var(--status-broken)]`, `red` → `text-[var(--status-failed)]` (was `text-emerald-400` / `text-amber-400` / `text-red-400`). Fixes light-theme legibility for these icons and drops the file's `no-restricted-syntax` (palette) warning count to zero. The `GATE_ACCENT` map already used `--gate-*` tokens and is unchanged.
- **Regression test** (`frontend/src/components/releases/VerdictBand.test.tsx`) — renders the band with one blocker of each severity and asserts each maps to its status token, guarding against a regression back to the raw palette classes.
### 2026-07-27 — Theme tokens: Integrations settings "(set)" indicators (palette ratchet)

- **`frontend/src/pages/settings/IntegrationsPage.tsx`** — the four "(set)" credential indicators (shown next to Jira / Splunk / OpenShift / GitHub secret fields once a token is stored) migrated from the raw `text-emerald-400` palette class to the per-theme success token `text-[var(--status-passed)]`. Raw palette greens are illegible in the light theme; the token resolves per-theme via `index.css`. Semantic role: "credential is configured/present" → success. Drops the file's `no-restricted-syntax` (palette) warning count to zero.
- **Regression test** (`IntegrationsPage.test.tsx`, new) — asserts one "(set)" indicator renders per stored-token provider, that the indicator carries `text-[var(--status-passed)]` and no `emerald` class, and that it is omitted when no token is stored.
### 2026-08-01 — Compute-graph RightRail activity icons → theme tokens (design-audit palette ratchet)

- **`frontend/src/components/agents/computeGraph/RightRail.tsx`** — the Activity-tab stage-event icons used raw Tailwind palette classes (`text-emerald-400` / `text-red-400` / `text-amber-400`) that bypass the per-theme CSS-token system and read poorly in light themes. Migrated by semantic role: `completed` → `text-[var(--status-passed)]`, `failed` → `text-[var(--status-failed)]`, `retry` (warning/retry) → `text-[var(--status-broken)]`; the `started` (`--color-accent`) and default (`--color-text-muted`) icons were already tokenised. No behavioural change — icon selection and copy are untouched.
- Tests: new `frontend/src/components/agents/computeGraph/RightRail.test.tsx` renders the rail on the Activity tab with one event of each kind and asserts the icons carry the per-theme status/accent tokens and that no raw palette classes remain. Validated with `npm run lint` (file's `no-restricted-syntax` warn count 3 → 0), `type-check`, `build`, and the new test. Frontend-only; no backend files touched.

### 2026-07-16 — GitLab integration: MR notes + commit statuses + CI recipe (PMF backlog Epic 3 US-3.1/3.2/3.3)

- **Per-project GitLab connector (US-3.1, `backend/app/services/gitlab_integration_service.py` + `backend/app/routers/gitlab_integration.py`)** — mirrors the GitHub integration for GitLab (self-managed or gitlab.com). Config contract (frontend built in parallel): `GET`/`PUT /api/v1/projects/{project_id}/integrations/gitlab` ↔ `GitLabConfig` (`enabled`, `base_url`, `project_path`, `mr_comment_mode`, `commit_status_enabled`, `has_token`, `last_error`, `last_error_at`); the PAT is **never returned** — `has_token` is the only token signal, and `PUT` accepts an optional write-only `token` that sets/rotates it via `secret_service` (scope `gitlab_integration`, key `project:{id}:pat`). `POST .../integrations/gitlab/test` → `{ok, detail, project_id_resolved}` probes `GET /api/v4/projects/:path`. `PUT`/`test` are QA_LEAD+; `GET` is project-member and returns the **default** config (disabled, `https://gitlab.com`, empty path) when unconfigured rather than 404. All routes are `require_project_access`-guarded (authorization ratchet). Self-managed base URLs supported (`{base}/api/v4`); `group/project` paths are URL-encoded (`%2F`) for the API path, numeric ids pass through.
- **Sticky MR note (US-3.2)** — one note on the run's merge request (`TestRun.pr_number` == `CI_MERGE_REQUEST_IID`), upserted by a hidden HTML-comment marker (`<!-- testlookup-mr-summary:{project_id} -->`) so re-runs UPDATE the same note (`GET`→find→`PUT` else `POST` against `/merge_requests/:iid/notes`). The note **body, baseline selection, flaky set, and newly-failed/known-flaky/fixed classification are reused verbatim** from `github_pr_comment_service` (the renderer is called and its first marker line swapped) — the GitLab surface never disagrees with the GitHub one. Honors `mr_comment_mode` (`off` / `failures_only` (default, still updates an existing note red→green) / `always`).
- **Commit status (US-3.2)** — `POST /api/v4/projects/:id/statuses/:sha` reflecting the run verdict (green → `success`, any failure → `failed`) with a Run-Intelligence deep link; gated by the per-project `commit_status_enabled` toggle (on by default).
- **Guardrails (mirrored, not forked)** — hard `AI_OFFLINE_MODE` kill switch + a new `gitlab` feature flag (`_post_allowed`); the SSRF egress guard is **imported verbatim** from `github_checks_service._ssrf_block_reason` (no duplicated CIDR logic — blocks loopback/link-local/unspecified, allows RFC1918 for self-managed on a private net); repo-match guard (run's `ci_repo` must equal the configured `project_path`, case-insensitive — MR IIDs are project-scoped); `last_error`/`last_error_at` bookkeeping for Integration Health; single-retry etiquette on list/note posts, `resilience.async_retry` on the commit status; **never raises** into ingestion. Dispatched from `ingestion_pipeline` in the same post-finalize block as the GitHub check-run / PR-comment posts (own try/except, never blocks ingestion).
- **Migration 0111** (`down_revision=0110`) — `gitlab_integrations` (`project_id` UNIQUE, `enabled`, `base_url` default `https://gitlab.com`, `project_path`, `mr_comment_mode` default `failures_only`, `commit_status_enabled` default true, `has_pat`, `last_posted_at`, `last_error`, `last_error_at`, timestamps, `updated_by_user_id`; PAT lives in `secret_service`, not a column) + seeds the `gitlab` feature flag (disabled). Downgrade drops the table + flag.
- **US-3.3 recipe + docs** — `user-guide/reference/testlookup-gitlab-ci.yml` (reference include covering both the JUnit-artifact upload path and the live-streaming SDK path, both MR-scoped so `CI_MERGE_REQUEST_IID` is captured); a **GitLab** integrations section in `user-guide/administration.md`; and a GitLab pointer in `user-guide/getting-results-in.md`.
- Transaction-ratchet allowlist: `gitlab_integration_service.py` added at cap 5 (all Celery-worker-owned commits — MR-note `_record_outcome` + the four commit-status delivery-outcome branches; `upsert_integration` is stage-only), total raised 58 → 63.

### 2026-07-16 — GitLab integration settings page (PMF backlog Epic 3 UI)

- **New settings page `/settings/gitlab`** (`frontend/src/pages/settings/GitLabIntegrationPage.tsx`) — mirrors the GitHub Checks page for GitLab. QA_LEAD+ (via `usePermissions().canAccessManagement`) configures a single GitLab project per TestLookup project: **enabled** toggle, **base URL** (default `https://gitlab.com`, self-managed help text), **project path** (`group/project`), **PAT** (write-only), **MR comment mode** select (off / failures only / always), and a **commit-status** toggle. Registered in `App.tsx` under `managementRoutes` (QA_LEAD+ gating, alongside `settings/github`) and surfaced as a `GitMerge` card in the settings index (`frontend/src/pages/SettingsPage.tsx`). Project-scoped: renders a "select a project" empty state in All-Projects mode (`ALL_PROJECTS_ID`).
- **Write-only PAT UX per the pinned contract** — the token is never returned; `has_token` is the only signal. When a token is stored the page shows "Token set" + a "Replace token" affordance; otherwise a password input. Save (`PUT`) includes the `token` field **only when the user entered one** (omitted = leave the stored PAT unchanged), matching the contract's omit/`null`/`""`/value semantics.
- **Test connection** button hits `POST …/integrations/gitlab/test` and surfaces `ok` / `detail` + the resolved project id. `last_error`/`last_error_at` render as an integration-health warning when present.
- **Contract-verbatim types + single-axios service + SWR hook** — `frontend/src/types/gitlab.ts` (`GitLabConfig`, `GitLabConfigWrite`, `GitLabConnectionTest`, `MrCommentMode`, `DEFAULT_GITLAB_CONFIG`), `frontend/src/services/gitlabIntegrationService.ts` (rides the shared `services/http` axios base), and `frontend/src/hooks/useGitlabIntegration.ts` (SWR read + imperative test helper). Form seeding uses the render-time reset pattern (no effect) to satisfy the `set-state-in-effect` guard.
- Tests: `frontend/src/pages/settings/GitLabIntegrationPage.test.tsx` (8 — PUT payload shape incl. token-only-when-entered, edited base_url/project_path/mode/commit-status carry-through, has_token set-vs-replace UX, test-connection ok+resolved-id / failure surfacing, integration-health warning). `npm run type-check` (strict) + eslint clean (pre-existing palette warnings only). Frontend-only; no backend files touched.

### 2026-07-16 — Commit attribution: ranges + suspect ranking, bisect re-added (Epic 8 US-8.1/US-8.2)

- **Commit-range association per run (US-8.1, `backend/app/services/commit_attribution_service.py`)** — resolves the commits landed since a run's last-green baseline (`base` = last-green run's `commit_hash` on the same branch → main/master fallback; `head` = this run's `commit_hash`). Two acquisition paths, supplied-wins priority: (1) **supplied/air-gapped** — callers push a `commit_range` list (`[{sha, author, message, files}]`) on ingest, stored verbatim, **no VCS call**; (2) **connector** — fetches `compare/{base}...{head}` + per-commit changed files from the configured GitHub integration, reusing `github_checks_service`'s PAT / SSRF / `AI_OFFLINE_MODE` + `github_checks`-flag / repo-match patterns (capped ~100 commits). When neither yields data an honest **`unavailable`** row is persisted. Resolution runs in `finalize_run` via the existing `_run_isolated` helper (stage-only service — the per-step session owns the commit; no new transaction-ratchet allowance).
- **Migration 0110** (`down_revision=0109`) — `run_commit_ranges` (`run_id` UNIQUE → idempotent per run; `project_id`, `base_commit`, `head_commit`, `base_run_id`, `source` ∈ connector|supplied|unavailable, `commits` JSONB, `resolved_at`); downgrade implemented.
- **Suspect ranking (US-8.2)** — `rank_suspects(run_id, cluster_id|fingerprint)` scores each commit in the range with a **deterministic heuristic (no LLM)**: `0.70·path_overlap + 0.20·recency + 0.10·author_prior`. Path/package overlap between a commit's changed files and the failing test's derived location (stack-trace path via `locate_in_trace`, plus `class_name`/`package_name` tokens) **dominates**; recency breaks ties; touching the same module elsewhere in the range is a small nudge. Framed as **suspects, never culprits** — every ranking is inspectable (rationale lists the overlapping files, recency rank, changed-file count) and carries the monorepo caveat (file owner ≠ commit author). Honest `available:false` when there's no range; degrades to a recency ordering (flagged) when the trace yields no location.
- **Endpoints** (`backend/app/routers/commit_attribution.py`, registered after `run_intelligence`) — `GET /api/v1/runs/{run_id}/commit-range` and `GET /api/v1/runs/{run_id}/suspects?cluster_id=|fingerprint=`, both `require_run_access`-guarded (authorization ratchet); each lazily resolves the range once on a dedicated write session so the GET stays read-only. Ingest surfaces extended with an optional bounded `commit_range`: `IngestPayload`, `LiveSessionCreate` (stashed in `extra_metadata`, stamped at live-run persist), and the `/ingest/file` form (JSON string).
- **Bisect re-added (frontend)** — `FailureAnalysisPage`'s "Start bisect" CTA (removed by US-2.4 "until Epic 8") is back, wired to a new **"Who / what changed" Suspects panel**: ranked suspect commits with score, commit deep links, an expandable "Why this suspect" rationale (overlapping files), the suspects-not-culprits/monorepo copy, and honest empty states (no range / recency-only). SWR hooks `useCommitRange` / `useSuspects` on the single-axios `commitAttributionService`.
- Tests: `backend/tests/test_commit_attribution_service.py` (32 — scoring matrix incl. path-overlap-beats-recency + recency-tiebreak + inspectable rationale + no-overlap degradation + author-prior + determinism, locator derivation, supplied normalization/bounds, deep-link derivation, read-model honesty, connector fetch with the GitHub layer mocked incl. repo-mismatch/no-PAT/SSRF, resolve orchestration supplied-wins/connector/unavailable, migration 0110 contract); frontend `SuspectsPanel.test.tsx` (ranking + rationale expansion + empty state + recency-only warning) + the updated bisect assertion in `FailureAnalysisPage.test.tsx`. Both transaction + authorization ratchets, `quality_gate.py`, ruff, and tsc/eslint clean.
### 2026-07-16 — CODEOWNERS import + blame-aware path-owner assignment (Epic 8 US-8.3/US-8.4)

- **CODEOWNERS import (US-8.3, `backend/app/services/codeowners_service.py`)** — parses a GitHub CODEOWNERS file (`# comments`, blank lines, `pattern @user`/`@org/team`/`email` owners, multi-owner, last-match-wins) and maps each line to a **`path`** rule in the existing `service_ownership_rules` table — **no migration**. Provenance is marked with `service_name="CODEOWNERS"` (the `created_by` column is a `users.id` FK, so it can't hold a sentinel); a re-import replaces only CODEOWNERS-sourced rows and leaves hand-authored rules untouched. File order is encoded as `priority` so later lines win under the resolver's priority-DESC iteration (matching GitHub). Two acquisition paths: **fetch** over the GitHub Contents API (reuses `github_checks_service`: PAT via `secret_service`, `_ssrf_block_reason` egress guard, `async_retry`, `AI_OFFLINE_MODE` + `github_checks` gate; never raises; falls back `.github/CODEOWNERS` → `CODEOWNERS` → `docs/CODEOWNERS`) and **paste-text** (air-gapped, no egress).
- **Endpoints (`routers/ownership.py`, QA_LEAD+ / project-scoped)** — `POST …/ownership/codeowners/import` (`{source:"github"|"text", text?}` → `{imported, rules_created, rules_replaced, source, coverage}`) and `GET …/ownership/codeowners/coverage` (% of recent locatable failing-test paths matched by a path rule, bounded lookback). The router owns the single commit (transaction ratchet); the service `add`/`delete`/`flush` only.
- **Blame-aware auto-assignment (US-8.4, `services/failed_test_assignment_service.py`)** — a new precedence step **after** the explicit `TestSuiteOwner` lookup and **before** the QA-lead pool: derive the failure's repo-relative path via `github_checks_service.locate_in_trace` (Python/JS-TS; Java deliberately unlocated), glob-match the project's active `path` rules, resolve the matched owner `@handle` → `User` by username/email (no new column). **Monorepo guardrail:** assignment is by *path ownership*, never by last committer (that surface does not exist). Non-locatable failures and unresolvable handles (`@org/team` or unknown `@handle`) skip to the QA-lead pool — never assigned to a stranger. Idempotent (NULL-only writes) and best-effort. The `/my-failures` inbox surfaces a read-time reason (e.g. "via CODEOWNERS: src/api/\*\*").
- **Frontend** — Ownership Editor gains an **Import CODEOWNERS** dialog (GitHub-fetch vs paste-text toggle, preview count), a **coverage %** badge (`useCodeownersCoverage`), and a **CODEOWNERS** tag on imported rules; `/my-failures` rows show the assignment reason subtly when present.
- Tests: CODEOWNERS parser matrix, import idempotency + provenance + last-match-wins priority, `@handle`→User resolution, fetch (mocked Contents API + offline + SSRF + 404 fallback), coverage, assignment precedence (path-owner beats pool, loses to explicit owner, unresolvable-handle/non-locatable → pool, NULL-only idempotency) + reason derivation; frontend hooks/service/reason-display. Existing assignment-mock sequences updated for the added path-rule query.

### 2026-07-15 — Fixer settings + fix-attempts surfaces (Agentic plan AI-2 UI)

- **Fixer card on Settings → AI Agents** (`FixerConfigCard`) — beside the Investigator card: enabled toggle; mode selector capped at **shadow / suggest** with the trust-ladder copy ("suggest opens DRAFT pull requests — merging is always human"; `act` is deliberately absent this wave); schedule (daily / weekly / off); a runner section whose fields switch on type (`none` default; `docker` → runner image + command template; `workflow_dispatch` → workflow ref) with an inline warning when `mode=suggest` and `runner=none` quoting the backend's 422 rule ("a validation runner is required before the Fixer may open PRs"); a `test_globs` add/remove editor; the four budget fields (max tests per run, attempts per test, validation reruns, concurrent open PRs). Save via `PUT …/fixer/config`; **Run now** (`POST …/fixer/run`) toasts per the 202/403/409/422 contract, surfacing the response detail.
- **Fix Attempts section on Settings → Agent Activity** (`FixAttemptsSection`) — every attempt with a status chip that gives the honest outcomes their own tone and tooltip (`rejected_globs` = "patch touched files outside the allowed test globs", `skipped_budget` = "a Fixer budget was exhausted"), the validation reruns (`5/5`), and the draft-PR link when one was opened; rows expand to fetch the single attempt (`GET /fixer/attempts/{id}`) and show the candidate patch, the reason, the runner-log digest, and the governance-ledger run id; paginated. Empty states distinguish "Fixer disabled" from "no attempts yet", both pointing at the settings card.
- SWR hooks (`useFixerConfig` / `useFixAttempts` / `useFixAttempt`) poll every 5 s while any attempt on the page is still in flight and stop once all are terminal. Contract-verbatim types in `types/fixer.ts`; single-axios service. Built against the pinned Wave-D API contract in parallel with the backend.
- Tests: 29 across the two components (+ their hosting pages) and the polling hook — config PUT payload incl. runner conditional fields + the suggest-without-runner warning, Run-now toasts per error code, the honest-status tooltips, row expansion + patch/reason/digest/ledger detail, pagination, and the poll start/stop. `tsc` + eslint clean.
### 2026-07-15 — The Fixer: budgeted validated-fix agent, draft-PR gated (Agentic plan AI-2)

- **The Fixer (backend, `backend/app/agents/fixer/`)** — a scheduled, budgeted agent that selects flaky/quarantined tests, generates **test-code-only** candidate fixes, **validates them by rerunning the test in a sandbox**, and — suggest mode only — opens a **draft PR**. The air-gapped answer to hosted auto-fix: nothing is surfaced unless it validated. Pipeline per candidate: `selected → diagnosing → generating → validating → {validated | rejected_globs | failed_validation | pr_opened | error | skipped_budget}`. Diagnosis reuses the latest completed investigation verdict + memory recall + flip history (never reruns the Investigator). Generation is ONE registry prompt (`fixer_generate_patch`, attested `wave-d-fixer`); offline/no-LLM records the attempt honestly and does nothing.
- **The binding invariant — zero unvalidated fixes surfaced** — encoded in the pure `workflow.classify_validation_terminal`: a PR opens ONLY from a `validated` result in suggest mode with an open-PR slot and a usable GitHub integration. `error` (infra trouble ≠ a `failed` validation), shadow mode, and the `NoRunner` default NEVER open a PR. Pinned by `tests/test_fixer_invariant.py` (the single most important test) and the e2e per-mode matrix.
- **Test-code-only** — a generated unified diff must touch ONLY files matching the policy `test_globs`; anything else is rejected **structurally, before any execution** (`rejected_globs`). Glob matcher supports `**` (`pipeline.patch_touches_only_test_globs`).
- **Three runners behind `ValidationRunner.run_validation(spec) -> ValidationResult`** (`validated` iff ALL M reruns pass): `NoRunner` (the `type:"none"` default — always `error`), `DockerEphemeralRunner` (flagship — ephemeral container on the worker: shallow clone at ref with the scoped token confined to the clone argv, apply patch, run M times, destroy workspace; CPU/mem/pids/timeout limits, `--network=none` by default, non-root user, no secret/DB mounts), and `WorkflowDispatchRunner` (GitHub `workflow_dispatch` + poll conclusion, reusing the `github_checks_service` PAT/SSRF/error patterns; reference workflow shipped in `user-guide/reference/testlookup-fixer-validate.yml`). **Docker argv-safety is security-critical**: built as a strict argv LIST via `build_command_tokens`/`build_docker_run_argv` — never `shell=True`, never f-string-interpolating an untrusted test name/patch into a shell string; a malicious selector stays one inert element (pinned by `tests/test_fixer_runners.py`).
- **Draft PR opener (suggest mode)** — branch `testlookup/fix-flaky-<fingerprint12>`, DRAFT PR titled "Attempt to fix flaky test … — validated M/M reruns" with the reasoning, a validation table, ledger deep link, and TestLookup attribution; reconstructs the patched test files on the branch via the GitHub git-data API (`pipeline.apply_unified_diff`). Concurrent-open-PR budget enforced.
- **Config on the shared `agent_policies` table** (`agent_id='fixer'`) — no new config table: `enabled`/`mode` map to columns, the `budgets` JSONB carries the runner block + globs + per-run/per-test budgets + validation reruns + concurrent-PR cap + schedule. `act` mode is rejected at the policy layer. Budgets `3/2/5/2`, globs `tests/** · **/*.spec.* · **/*.test.*`, schedule `off`, all default-disabled shadow.
- **Migration 0109** (`down_revision=0108`) — `fix_attempts` table (contract shape + project FK, indexes on `(project_id, created_at)` and `(fixer_run_id)`); downgrade implemented.
- **Endpoints** (`backend/app/routers/fixer.py`, registered in bootstrap) — `GET/PUT /projects/{id}/fixer/config` (PUT QA_LEAD+), `POST /projects/{id}/fixer/run` (202; 403 disabled / 409 already-running / 422 runner-required-for-suggest), `GET /projects/{id}/fixer/attempts`, `GET /fixer/attempts/{attempt_id}` (with patch + log digest + ledger link). Project guards per the authorization ratchet; the attempt route resolves to its project (IDOR discipline).
- **Scheduler + outcome loop** — daily/weekly beats (`dispatch_scheduled_fixer_runs`) fan out per enabled project by schedule; kill switch (`enabled=false` mid-run) stops cooperatively between candidates. A 30-min beat (`poll_fixer_pr_outcomes`) polls fixer PRs and reuses `feedback_service.record_fix_outcome` — merged → `fixed`, closed-unmerged → `not_fixed` (no duplication of that AI-5 service). Ledger via a generalized `agent_investigation_service.write_agent_run_row` (agent_id `fixer`); compliance packs pick up fixer runs automatically (agent-agnostic `_gather_agent_activity`).
- **Prompt registry** — new `fixer_generate_patch` (v1) registered + manifest rewritten + eval-gate attested (`wave-d-fixer`, offline golden).
- **Seeded demo + live attempt** — `backend/tests/fixtures/flaky_demo_repo/` (a one-test time-flaky pytest project); the FakeRunner drives the per-mode e2e, and `tests/test_fixer_runners.py` attempts a real `DockerEphemeralRunner` validation against it using a locally-present `python:*` image, skipping honestly when docker/image is unavailable (CI never depends on docker).
- Tests: `tests/test_fixer_invariant.py` (validated-only-PR + outcome mapping, DB-free), `tests/test_fixer_runners.py` (argv-safety, NoRunner/FakeRunner, live-docker attempt), `tests/test_fixer_glob_rejection.py`, `tests/test_fixer_config.py` (config + attempt contract key-sets, act rejected, diff applier), `tests/test_fixer_pipeline_e2e.py` (per-mode/budget/kill-switch over an in-memory session), `tests/test_fixer_api.py`. Quality gate: `runners.py`/`pipeline.py` added to the agent support-file set (sandbox executors / pure helpers, not BaseAgent pipeline agents).

### 2026-07-15 — Confidence-weighted kind triage feeds the gate (Agentic plan AI-4)

- **Evidence-checklist kind enrichment** — new `backend/app/services/kind_evidence.py`: the US-9.1 derived failure kind (a single-signal function of the classifier verdict) is upgraded to an auditable checklist record `{kind, confidence, confidence_basis, checks:[{check, verdict, detail}]}` weighing five existing signals — `memory_recall` (prior human corrections are authoritative; prior analyses of the fingerprint vote), `infra_shape` (the error text vs the rules-engine `_PATTERNS` table — one source of truth), `history_pattern` (intermittent / chronic-never-passed / new-vs-passing-history), `status_signal` (BROKEN = unexpected-error → infra-leaning; FAILED = checked assertion → product-leaning), and `classifier` (the engine verdict the kind derives from, with its band). **Deterministic — NO new LLM call**; weighing is modest and pinned (+5 per supporting check, −15 per contradicting check, clamped to [0, 90]); the basis is honest about that: `heuristic_estimate`, unless a human correction agreeing with the kind **pins confidence to 95 with basis `human_corrected`** — a new member of the shared basis vocabulary (`confidence_bands.py`; deliberately NOT a valid rules-band basis — a static table can't claim human confirmation; UI chips map it to "human-corrected" in the Confidence+Why panel and the investigator cockpit). Persisted at `AIAnalysis.routing_metadata.kind_evidence` where the AI-F4 `_audit` block lands (no migration; `TestCase.status` added to the analysis metadata fetch so the status check works at write time); reads compute on demand when the blob is absent (no backfill).
- **Gate confidence floor (US-9.3 extension)** — `PolicyKindBudget` gains optional `min_confidence_to_excuse` (0-100, default **null = byte-identical to US-9.3**, pinned by `tests/test_kind_gate_confidence_floor.py` including a structural-equality replay of the US-9.3 fixture with confidences present). With a floor set, only failures whose per-failure kind confidence (evidence-checklist confidence, falling back to classifier confidence) meets it count toward the excusable budget; below-floor / unknown-confidence failures **count as product** (conservative), missing confidence data (pre-AI-4 snapshots) excuses nothing. The decision trail's kind breakdown gains a `floor_rejected` per-kind map (only when a floor is configured — floor-less trails keep the exact US-9.3 shape), budget rows name the floor, and the counterfactual mentions it. The release-risk agent supplies `failure_kind_confidences` alongside `failure_kind_counts` in the policy context and freezes both into the input snapshot; the policy simulator replays them. Policy editor's Failure-Kind Weighting section gains the optional floor input (empty = no floor).
- **Evidence surfacing** — `/failures`: the What's-failing kind badge opens an evidence popover (check rows with ✓/✗/·/— verdicts + details, confidence, basis chip, "AI-classified" copy retained) via new `GET /api/v1/analytics/kind-evidence` (project+fingerprint or test_case_id lookup; tenant-scoped like its siblings; `{found:false}` → plain badge). Run-detail failure rows carry the same badge+popover: the kind itself rides the existing `TestCaseSummary.failure_kind` computed field (included field = no extra call — the cheaper option), evidence fetched on demand by test_case_id only when opened. `GET /api/v1/analyze/{test_case_id}` now returns `kind_evidence` (stored blob or computed on demand). Badge+popover live in shared `frontend/src/components/failures/KindEvidence.tsx` (page re-exports `FailureKindBadge` for compatibility).
- **PR comment + check-run labels (display-floor 60)** — newly-failed PR-comment rows and check-run annotations (+ non-locatable rows) gain the kind label `Infrastructure (82% conf, AI-classified)` **only when the per-failure kind confidence ≥ 60** (`kind_evidence.KIND_DISPLAY_CONFIDENCE_FLOOR`) — below it, no label at all (don't decorate with noise). One shared `kind_labels_for_test_cases` source feeds both surfaces so they always agree; known-flaky/fixed rows never carry labels; label-less rows render byte-identically to before.
- **Kind precision per tier (honest)** — `ai_eval_service.compute_kind_classification_metrics`: kind is a pure function of the category, so kind precision is computable from any item carrying category labels on both sides (feedback datasets AND golden items); per-tier attribution reads the new `metadata.analysis_tier` (recovered from `routing_metadata.analysis_mode` in `build_dataset_from_feedback`); items without a recorded tier land in tier `"unknown"` with an explicit "not attributable" note — no tier is guessed, and label-less datasets return `{computable: false, reason}` instead of fake numbers (the AI-F4 precedent). Exposed as an additive `kind_metrics` sub-key of the classification metrics (flat keys unchanged), so eval runs and the eval gate's `current_metrics` carry it automatically.
- Tests: `tests/services/test_kind_evidence.py` (34 — full assembly matrix per check, correction pin + `human_corrected` basis + vocabulary guards, weighing bounds, display floor, persistence round-trip incl. stored-blob preference and on-demand compute), `tests/test_kind_gate_confidence_floor.py` (14 — null-floor structural-equality pin, below-floor→product, unknown-confidence→below-floor, missing-data excuses nothing, trail counts + counterfactual mention, agent confidences + snapshot freeze), PR-comment/check-run label tests (display-floor gating on both surfaces), kind-metric tests (honest not-computable + per-tier unknown note), frontend `KindEvidence.test.tsx` (lazy fetch, checklist rendering, basis chips, honest empty state). Existing kind/gate/PR-comment/checks/eval/router suites all green; both architectural ratchets, `scripts/quality_gate.py` (the line-keyed analysis-router baseline entry for `routers/analyze.py` re-pointed 147→157 after the read-path addition — same pre-existing call, no new bypass), ruff, eslint (0 errors), and `tsc --noEmit` pass.
### 2026-07-15 — Ask-AI becomes a tool-using copilot with action handoffs (Agentic plan AI-6)

- **Bounded tool loop (backend)** — when the resolved analysis mode is `llm` and the chat session is project-scoped, `ConversationAgent.chat` now runs a ReAct-style copilot loop (`create_react_agent` + `AgentExecutor`, same mechanism as the triage agent — chosen over native function-calling because it works reliably on Ollama-class local models) over a new read-only toolset instead of the single-shot retrieval. Hard bounds: ≤ 6 tool calls (`max_iterations`), wall-clock capped at `AI_TIMEOUT_SECONDS` (executor `max_execution_time` + an outer `asyncio.wait_for`), per-call and total token budgets on tool observations (reusing `resilience.estimate_token_count` / `truncate_to_token_budget`). ANY loop failure — timeout, iteration-cap stop, LLM error, empty output — falls back to the unchanged single-shot path. In rules/ML mode or "all projects" scope the loop never engages; the pre-AI-6 behavior is pinned byte-identical by test.
- **Read toolset (`backend/app/tools/chat_read_tools.py`)** — seven read-only tools wrapping existing services/queries (shapes mirror the MCP read tools; mcp/ code is never imported): `list_recent_runs`, `list_run_failures`, `get_failure_clusters`, `check_quarantine_status` (quarantine manifest states + AI flaky flags), `get_release_gate_verdict` (latest `ReleaseDecision`), `recall_failure_history` (AI-F3 memory recall), `count_failure_kinds` (7-day failure-kind aggregate). Tenancy is bound SERVER-SIDE via a ContextVar exactly like the AI-F3 recall tool — the LLM's tool input is free text only, never an identifier; without a bound project every tool answers "unavailable". Tools never raise into the loop.
- **Tool-use transparency** — the chat response payload gains `tool_trace: [{tool, summary}]` ("checked flaky/quarantine status for 'X' — 1 record(s), 0 actively quarantined"); it is persisted on the ChatMessage inside the existing `sources` JSON column as a carrier entry (no migration) and rendered in the UI as a subtle collapsed "How I looked this up · N checks" section per assistant message (`components/chat/AssistantMessageExtras.tsx`).
- **Action handoffs (human submits, agent never does)** — deterministic `suggested_actions: [{type, label, prefill}]` derived ONLY from structured tool findings (never LLM text): a flaky-but-not-quarantined test yields `propose_quarantine`, a repeat offender with prior corrections/analyses yields `create_jira` (capped at 3). The UI renders one-click buttons that OPEN the existing audited dialogs pre-filled: a lifted, prefill-friendly `components/quarantine/ProposeQuarantineModal.tsx` submitting through `flakyQuarantineService.propose` (the US-2.4 flow, rationale tagged `ask-ai-copilot`), and the existing US-6.1 `CreateJiraIssueModal` (server-side preview/dedup unchanged).
- **Prompts (AI-F2 discipline)** — `chat_system` bumped to v2 (the single-shot path must never fabricate tool activity now that some answers carry a real trace) and new `chat_copilot_react` v1 (bounded-loop ReAct template with grounding + budget-honesty rules); manifest rewritten + fresh offline eval-gate attestation (`--attest wave-c-chat --offline`, verdict PASS). `ai.prompt-manifest-sync` guard green.
- API: `SendMessageResponse` gains additive `tool_trace` / `suggested_actions` (both `[]` on the single-shot path); `chat_service.send_message` passes them through.
- Tests: `backend/tests/test_chat_copilot.py` (18 — per-fetcher SQL project-scoping leakage guard + no-context refusal, per-call truncation + total budget exhaustion, tool never raises, iteration-cap and timeout both fall back to single-shot, multi-hop fixture "which teams own this week's new failures and are any quarantined?" resolved via 2 scripted tool calls with a `FakeListChatModel`, trace + actions persisted in the sources carrier, deterministic action shapes + cap, rules-mode byte-identical pin, all-projects never engages the loop); `test_prompt_registry.py` updated (chat_system v1 pin replaced by a deliberate-v2 test, new chat_copilot_react anchors); frontend `AssistantMessageExtras.test.tsx` (6 — collapsed trace expands, quarantine dialog opens pre-filled and submits the audited payload, Jira modal receives the prefilled identity, `splitMessageSources` carrier unpacking + legacy nulls). `scripts/quality_gate.py` (16 guards), ruff, tsc, eslint (0 errors) all green.
### 2026-07-15 — Proactive narratives: Investigator verdicts in reports, digests, and team flaky-debt reviews (Agentic plan AI-7)

- **"Agent investigations" section in the attached analysis report (US-7.5 extension)** — `analysis_report_service` gains a `_collect_investigations` collector (READ-ONLY over the persisted `agent_investigations` rows — the report quotes stored verdicts, never re-runs anything) and a new section between Release gate and Open defects: one paragraph per COMPLETED investigation on a run in the window — primary-cause badge (shared `CAUSE_DISPLAY` vocabulary), confidence % + AI-F4 basis (the winning hypothesis's `confidence_basis`), the "2 validated / 2 invalidated / 1 inconclusive" hypothesis tally, a 1-2-sentence narrative excerpt (pure `narrative_excerpt` clipping, quoted with a "quoted from the stored verdict" note), and a deep link into the cockpit (`/deep-investigate/{run_id}`). Capped at 5 with the standard "+K more — open dashboard" overflow line; omitted with "No completed agent investigations in this window." when none exist; section opens with the pinned trust label ("AI-generated by the Investigator agent (shadow mode — informational only…)"). All strings pass the report's `_e` escaping.
- **Digest delta line (US-7.4 extension)** — `compute_digest_deltas` now carries `investigations: {completed, top: [{run_build, primary_cause, confidence}] (≤2, by confidence)}` read from completed investigations in the window. `investigator_delta_line` renders exactly ONE AI-labeled line in the Slack/Teams text and the email HTML delta block ("AI Investigator (shadow — informational): 2 investigations completed — #142 → infrastructure (78%)… — evidence in the report"); zero-investigation windows add nothing — not even an empty line — and `is_zero_change` deliberately ignores investigations (informational, never a "change").
- **Weekly flaky-debt review (new `services/flaky_debt_review.py`)** — a per-team TEXT-ONLY draft built from the quarantine lifecycle rows: active quarantines (owner from the US-5.4 lifecycle `owner_user_id`), stale-over-SLA entries, ready-to-promote list (US-5.5 streaks), newly-flaky-in-window, each with a deterministic suggested next step (promote > review SLA breach > investigate new flake > re-check rationale) and dashboard links; every draft opens with the pinned automation label. Teams are resolved via the US-7.3 ownership rules (fallback "Unassigned"); empty teams are omitted entirely. **Delivery:** teams WITH an active `team_notification_channels` row get their draft on their channel via a new Monday 07:10 UTC beat task (`dispatch_weekly_flaky_debt_reviews`, per-project fail-open, `NotificationLog` audit rows via `record_team_delivery_logs`); teams WITHOUT a channel fold into the project's weekly digest as a "Weekly flaky-debt review" section (escaped `<pre>` blocks in email / appended text blocks in Slack). The fold-in is **unconditional-when-data-exists**: the opt-in subscription flag was skipped deliberately — the `send_when_unchanged` / `report_attachment` precedents each required a migration (0106/0107) and this slice ships with NO migrations.
- **Narrative source honesty (no new LLM calls)** — every narrative sentence on these surfaces is quoted from the Investigator's persisted verdict; the report/digest/review paths are deterministic and offline-safe. Guarded two ways in `tests/test_proactive_narratives.py`: a source tripwire (the three modules must never reference `llm_factory` / `get_llm` / `prompt_registry`) and a runtime tripwire (`get_llm` monkeypatched to explode while every surface renders — the Investigator-offline-test pattern). No prompts were added or changed, so no prompt-registry/manifest/attestation update was needed (`ai.prompt-manifest-sync` guard green).
- Tests: `backend/tests/test_proactive_narratives.py` (29) — report section (label/badge/tally/deep-link, cap-5 + overflow, omitted-empty line, hostile-narrative escaping, collector serialization incl. winning-hypothesis basis), digest delta line (exact formatting, present/absent in text + HTML, XSS escaping, zero-change predicate unaffected), flaky-debt matrix (next-step priority, per-team grouping with multi-bucket membership, empty-team omission, bucket caps, fold-in excludes channel-routed teams, daily digests never carry it, review fault never kills the digest, channel delivery loop + audit + per-project fail-open), and the no-LLM guards. Existing report/digest/routing/investigator suites (179 tests) stay green; `scripts/quality_gate.py` (16 guards) and ruff pass.
### 2026-07-15 — Investigator over MCP + fix-outcome learning loop (Agentic plan AI-5)

- **Investigator over MCP (AI-5)** — new `mcp/tools/investigations.py` (registered in `mcp/server.py`) exposes the Wave-B Investigator's pinned REST contract (#383) to any MCP-capable agent: `start_investigation(run_id)` (wraps `POST /api/v1/runs/{run_id}/investigations`; the API's 403 policy-disabled / 409 already-running / 429 daily-budget answers come back as structured `{ok:false, status_code, detail}` results, and the docstring pins that the Investigator is shadow-mode — it diagnoses, the calling agent acts), `get_investigation(investigation_id)` (full detail — status, budget vs spend, five hypothesis boards, verdict — with long prose compact-rendered via pure `_compact_detail`/`_truncate` helpers: 400-char summaries/evidence, 1200-char narrative, evidence capped at 5 items with an honest elision marker), and `list_investigations(project_id, limit)` (clamped 1–100). New resource `testlookup://runs/{run_id}/investigation` returns the latest investigation for a run (run → project → newest-first list filter → full detail), or `{"investigation": null}` with a pointer at `start_investigation`. MCP surface is now **62 tools / 11 resources / 7 prompts**.
- **`record_fix_outcome` — the fix loop feeds the label system (AI-5 × AI-F1)** — new MCP tool in `mcp/tools/feedback.py`: `record_fix_outcome(project_id, fingerprint, outcome: fixed|not_fixed|reverted, reference?, comment?)` with client-side validation (`_fix_outcome_body`: closed outcome vocabulary, fingerprint required). Backed by a thin new endpoint `POST /api/v1/projects/{project_id}/fix-outcomes` (201; on `feedback.lookup_router`, so `require_project_access` applies — authorization ratchet; `Literal` schema closes the vocabulary at validation) → `feedback_service.record_fix_outcome`: resolves the latest analysis for the (project, fingerprint) pair with the same project-scoped join discipline as the US-2.4 lookup (404 when never analysed — nothing to grade), then stages an `ai_feedback` row with **`source="fix_outcome"`**: `fixed` → rating CORRECT (a merged-and-verified fix confirms the diagnosis, so the analysis category becomes a usable training label via `resolve_feedback_label`); `not_fixed`/`reverted` → INCORRECT with **no** corrected category (honestly unusable as a classification label, still real negative signal). `label_provenance` explicitly buckets `fix_outcome` as **`human_indirect`** (it was the default for unknown sources; now pinned) — a coding agent's outcome informs training but never outranks an explicit human correction. Reference/free-text fold into a greppable structured comment (`[fix_outcome:reverted] ref=org/repo#42 …`). No migration.
- **Packaged workflow prompt `fix_this_flaky_test` (mcp.fix_this_flaky_test v1)** — walks an agent through the full flaky-fix loop: flip/step evidence + Investigator verdict (start/poll if none exists) → quarantine/defect history recall → `propose_quarantine` containment (skip if already proposed/live) → reproduce-and-fix-locally guidance (loop runs, no blind retries) → `record_fix_outcome` on merge (negative outcomes first-class) → `correct_classification`/`release_quarantine` follow-ups. Registered through the AI-F2 prompt manifest: `--write-manifest` (33 prompts, new digest) + fresh offline eval-gate attestation `--attest wave-c-mcp --offline` (verdict PASS); `ai.prompt-manifest-sync` guard green.
- **Cookbook + README** — `user-guide/agent-cookbook.md` gains Recipe 6 "Investigate a regression end-to-end" (start → poll → act on the verdict via existing write tools, with the 403/409/429 answers explained) and Recipe 7 "Fix a flaky test and close the loop" (the packaged workflow incl. the human_indirect provenance story); `mcp/README.md` gains the Investigator + fix-outcome tool tables, the new resource/prompt rows, and updated counts.
- Tests: `mcp/tests/test_mcp_investigations.py` (24 — registration/contract-path pins, shadow-mode + structured-error docstring pins, `_fix_outcome_body` validation, `_compact_detail` truncation/elision/no-mutation/degradation, `_clamp_limit`, prompt render + full-loop step ordering) and `backend/tests/test_fix_outcome_endpoint.py` (12 — outcome→rating mapping, `human_indirect` provenance pin, project-scoped latest-first lookup SQL, 404/422 contracts, route shape + `require_project_access` guard + protected-router registration, commit boundary). MCP suite, feedback/label-integrity/lookup adjacents, `scripts/quality_gate.py`, and ruff all green.

### 2026-07-15 — Investigator cockpit + AI-agent governance UI (Agentic plan AI-1 / AI-3)

- **Investigator cockpit (AI-1, frontend)** — the Deep Investigation page gains an "Agent Investigation" section (`frontend/src/components/investigator/InvestigatorCockpit.tsx`) driving the Wave-B investigator contract: an "Investigate this run" CTA (tooltip-disabled when no run is in scope, the viewer lacks QA Engineer, or the project policy disables the agent — with a link to Settings → AI Agents; a 409 on start attaches the cockpit to the already-running investigation), a **hypothesis matrix** of the five fixed root-cause hypotheses (Infrastructure / Commit-caused / Environment / Known-flaky / Regression) with live status icons (pending spinner → running pulse → validated ✓ / invalidated ✗ / inconclusive ?), per-hypothesis confidence with the AI-F4 calibration-basis chip (calibrated / estimated / llm-weighted) and an expandable evidence list whose `url_path` items render as internal links, a **verdict panel** on completion (primary-cause badge, AI-labeled narrative, confidence, recommended actions as text only, and an explicit "shadow mode — no actions were taken" note), **run controls** (cancel while active; spend-vs-budget mini bars for LLM calls / tokens / seconds; details footer with trigger, model, prompt-registry versions), and a compact past-investigations list scoped to the run. Polling: `useInvestigation` (SWR function-form `refreshInterval`) refreshes every 2.5 s while status ∈ queued/running/synthesizing and stops on terminal statuses.
- **Settings → AI Agents (AI-3)** — new `/settings/ai-agents` page (QA_LEAD+ via managementRoutes): per-agent policy card (Investigator ships first) with enabled toggle, **trust-ladder mode selector** (shadow → suggest → act, with doctrine copy per rung; act is disabled with a "coming in a later wave" tooltip), the four budget inputs (positive-integer validation blocks save), the promotion status line ("N shadow runs completed" + note), and PUT-on-save (`{enabled, mode, budgets}`) with success/error toasts. Per-project — All-Projects mode shows a select-a-project notice.
- **Settings → Agent Activity ledger (AI-3)** — new `/settings/agent-activity` page: table of every governed agent run (agent, mode chip, trigger, status, one-line summary, tokens, cost, duration, timestamp) with expandable rows (actions proposed / actions taken, prompt-registry digest, "Open details →" link into the cockpit when `details_path` is present), an agent filter dropdown, limit/offset pagination, and an explanatory empty state. Both pages are linked from the Settings index grid.
- Plumbing: `types/investigator.ts` mirrors the pinned Wave-B API contract verbatim; `services/investigatorService.ts` + `services/agentGovernanceService.ts` sit on the shared axios base; hooks in `hooks/useInvestigation.ts` + `hooks/useAgentGovernance.ts` (named so to avoid clobbering the existing pipeline-view `useAgentRuns.ts`). 403/409 error surfacing rides the shared interceptor's actionable-message toasts.
- Tests (hermetic, mocked services per the established pattern): `useInvestigation.test.ts` (polling starts while active, stops on terminal, never starts when already terminal — fake timers), `InvestigatorCockpit.test.tsx` (all five hypothesis statuses + basis chips, evidence expansion with internal links, cancel call, verdict panel AI-labeling + shadow note + no action buttons, policy-disabled state, start call), `AIAgentsPage.test.tsx` (act disabled with tooltip, exact PUT payload shape, invalid-budget save block, all-projects notice), `AgentActivityPage.test.tsx` (row render, expansion + details link, filter re-query, empty state) — 18 new tests; DeepInvestigationPage test updated to mock the cockpit hooks. `tsc --noEmit` and eslint (0 errors, no new palette warnings) pass.
### 2026-07-15 — The Investigator: hypothesis-loop failure investigation, shadow mode (Agentic plan AI-1 / AI-3 core)

- **The Investigator (AI-1)** — new `backend/app/agents/investigator/` LangGraph package: a plan node gathers ONE deterministic evidence bundle (baseline via the PR-comment `_select_baseline`, `run_compare` newly-failed classification, failure/category counts, infra rule-pack keyword matches, the known-flaky set = active quarantines ∪ flaky-coach cache, failure clusters, memory recall for top flaky offenders), fans out in parallel to **five hypothesis sub-agents** (`infra` / `commit` / `environment` / `known_flaky` / `regression` — each a `BaseAgent` subclass with decision logs, per-stage tracking rows, token/cost metering, and a pure `evaluate(bundle)` threshold matrix documented in `hypotheses.py`), then synthesizes ONE verdict. Each hypothesis does deterministic evidence weighing FIRST and AT MOST one bounded LLM call to re-weigh (`confidence_basis: "llm_weighted"`); **with no LLM configured (`AI_OFFLINE_MODE`, the default) the deterministic thresholds produce the verdict with `confidence_basis: "heuristic_estimate"` — fully functional offline with zero models.** Synthesis precedence is deterministic and pinned by test: highest-confidence validated hypothesis wins; near-ties (<10 points) across explanation families (external: infra/environment · test-side: known_flaky · code: commit/regression) resolve to `unknown` with an honest narrative; same-family ties break to the more actionable member (infra > environment, commit > regression). Recommended actions are TEXT ONLY (quarantine proposal for flaky, infra runbook pointer, commit-diff review, …) — shadow and suggest are identical this slice, `act` is reserved, `actions_taken` is always `[]`.
- **Budgets + cancel** — per-policy budgets enforced at trigger time (`max_runs_per_day`) and in-flight (LLM calls/tokens inside each node's single call, wall-clock via a deadline checked between nodes); exhaustion leaves honest `inconclusive` leftovers plus a budget note in the verdict narrative while the investigation still completes. Cancel is cooperative: `POST /investigations/{id}/cancel` (202 `{"status":"cancelling"}`) sets a flag every node checks; the runner finalizes `status="cancelled"`.
- **API (pinned wire contract — the frontend builds against these shapes verbatim)** — new `backend/app/routers/agent_investigations.py`: `POST /api/v1/runs/{run_id}/investigations` (202 `{"investigation_id"}`, 403 actionable when the policy disables the agent, 409 one-active-per-run, 429 daily budget; QA_ENGINEER+), `GET /api/v1/investigations/{id}` (InvestigationDetail — the UI polls; hypotheses persisted incrementally as nodes finish), the cancel endpoint, `GET /api/v1/projects/{project_id}/investigations` (paged summaries), `GET/PUT .../agent-policies` (PUT is QA_LEAD+; defaults when no row: enabled, shadow, budgets 10/30/60000/300; `shadow_runs_completed` is server-maintained), and `GET .../agent-runs` (the ledger). `{investigation_id}` routes resolve to the project and verify membership on the PROVIDED id (IDOR discipline).
- **Migration 0108** — `agent_investigations` (full detail persistence; **partial unique index enforces one ACTIVE investigation per run** at the DB level; index on `(project_id, created_at)`), `agent_policies` (unique `(project_id, agent_id)`, budgets JSONB, `shadow_runs_completed` promotion counter), `agent_runs` (the AI-3 ledger: mode/trigger/status, summary, actions proposed vs taken, tokens/cost/duration, `prompt_registry_digest`; index `(project_id, agent_id, created_at)`). Downgrade implemented.
- **Triggers** — manual endpoint plus two auto-hooks, each in its own try/except so a broken investigator can NEVER affect the host path: the transition-notification engine enqueues `auto:newly_failing` when newly-failing transitions are detected (pre policy-filter — silencing the notification doesn't silence the shadow investigation), and the release gate enqueues `auto:gate_no_go` when a NO_GO decision is persisted. Both funnel through `agent_investigation_service.maybe_auto_trigger` (own session, never raises, re-checks policy + one-active + daily budget). Execution: `run_agent_investigation` Celery task on the `ai_analysis` queue.
- **Ledger + audit (AI-3 core)** — every terminal outcome (completed/cancelled/failed) writes an `agent_runs` row (summary = one-line verdict, actions_proposed = recommended_actions, `actions_taken=[]` always this slice, prompt-registry digest) and mirrors a durable `agent_run_recorded` event to the Mongo pipeline event log; completed shadow runs increment the policy's `shadow_runs_completed` promotion counter. Compliance packs gain a self-guarding `agent_activity` section (`agent_activity.json`) listing the ledger rows touching the release's run.
- **Prompts (AI-F2 discipline)** — two new registry prompts, `investigator_hypothesis_weigh` (shared by the five sub-agents) and `investigator_synthesis_narrative`, registered in `prompt_registry.py` with manifest rewrite + fresh offline eval-gate attestation (`--attest wave-b-investigator --offline`, verdict PASS); prompt version tags are stamped onto every investigation row. `ai.prompt-manifest-sync` guard green.
- Tests: `backend/tests/test_investigator_hypotheses.py` (22 — the five seeded scenarios: pure-infra, commit-onset, env-drift, all-flaky, mixed→unknown; per-hypothesis threshold/evidence pins incl. the "commit-range contents unavailable" no-git note and memory-recall citations; all synthesis precedence rules + actions mapping), `test_investigator_workflow.py` (10 — offline determinism with a get_llm tripwire, LLM budget-of-zero gate, deadline-exhaustion inconclusive leftover + budget note in narrative, cancel skips for hypotheses and synthesis, graph fan-out topology, merge/summary helpers), `test_investigator_api.py` (19 — EXACT pinned wire shapes for detail/summary/policy/ledger envelopes, 403/409/429 trigger gates, cancel 202/409, policy defaults + PUT round-trip with server-maintained promotion counter, IDOR guard, route-guard wiring), `test_investigator_ledger.py` (16 — ledger row + Mongo mirror + outage tolerance, trigger-gate typed errors, `maybe_auto_trigger` never-raises, hook wiring, compliance section + self-guard). Transaction-boundary allowlist gains `agent_investigation_service.py` (hook-owned single commit; cap 57→58). Both architectural ratchets, `scripts/quality_gate.py` (16 guards), targeted adjacent suites (prompt registry, transitions/routing, agent memory/consistency — 300+ tests), and ruff all green.

### 2026-07-10 — Versioned prompts with enforced eval gate + memory recall in triage (Agentic plan AI-F2 / AI-F3)

- **Prompt registry (AI-F2)** — every prompt that reaches an LLM is now a versioned artifact in `backend/app/services/prompt_registry.py` (`PromptDef(id, version, text)`): 24 backend prompts (ReAct triage system, chat system + history-compression, anomaly narrative, the 5 summary-agent layer prompts, the 3 summary-renderer mode prompts, run-compare system/report, release-risk reasoning, regression-watchman classify, defect promotion, fast classifier, fine-tune reasoning export, and the 5 test-management AI prompts) moved **byte-identically** (pinned by frozen sha256 hashes in `tests/test_prompt_registry.py`), plus the 6 MCP workflow templates (`mcp/prompts/templates.py` hoisted to a `PROMPT_TEMPLATES` dict rendered via `.format` — output bytes unchanged) under `mcp.*` ids. Call sites import `get_prompt_text("<id>")`; texts are edited in ONE file from now on.
- **Enforced eval gate on prompt changes (AI-F2)** — new `prompt_manifest.json` pins `sha256(text)[:12]` + version per prompt (30 entries) and `prompt_manifest_eval.json` records the eval-gate attestation (change id, verdict, gate results, `AIEvalGateRun` id when DB-backed) for the current manifest digest. New quality-gate guard **`ai.prompt-manifest-sync`** (stdlib-only; re-hashes prompts by `ast`-parsing the registry + MCP sources) fails CI on any hash/version drift, stale manifest entries, a manifest change without a fresh attestation, or a non-PASS verdict — a prompt edit cannot ship without an eval run. Tooling: `python -m app.services.prompt_registry --write-manifest | --attest <change-id> [--offline] | --check` (offline mode scores the golden datasets with no-baseline default thresholds and records `mode: offline_golden`; the prompt-independent `duplicate_detection` lexical scorer is recorded as informational). Workflow documented in `architecture/AI_EVALUATION.md` §4b.
- **Prompt-version stamping (AI-F2)** — the pipeline version snapshot (`workflow._runtime_version_snapshot`) gains a `prompt_registry` digest and `execution_metadata` gains the full `prompt_versions` id→`v<version>:<hash12>` map; `analysis_agent`'s per-test `_audit.prompt_versions` and `analysis_router`'s `_routing` decision record now carry registry-derived tags (LLM engine only — rules/ML are prompt-free), so any stored verdict traces to exact prompt bytes.
- **Memory recall in the reasoning path (AI-F3)** — `agent_memory_service` was write-only; new read side `services/memory_recall.py` (`recall_failure_history` + citation-style renderer, never-raises, bounded output) gathers, per project: (a) prior **human corrections** for the fingerprint (authoritative — same source as the analysis agent's learning-loop short-circuit), (b) the latest 3 prior analyses of the same fingerprint (category/confidence/date), (c) top-3 semantically similar past failures via the existing project-scoped ChromaDB memory index, (d) a quarantine/flip-history one-liner. New LangChain tool **`recall_similar_failures`** joins the ReAct agent as its 6th tool — the investigation identity (project/fingerprint) is bound server-side via a `ContextVar` set by `run_triage_agent` (new optional `test_fingerprint` param, passed from `analysis_agent`), so tenant isolation never depends on LLM-provided input. The ReAct system prompt is bumped to **`react_triage` v2** through the registry's own workflow (manifest bump + attestation — dogfooding the F2 gate): six tools, an explicit "consult recall on repeat failures; human corrections are authoritative, cite source `memory`" rule.
- **Chat recall (AI-F3)** — for failure-intent questions that name a specific test, `conversation.py`'s retrieval step now appends a "Prior History for the Referenced Test (memory recall)" section via the same service (recent failing test names matched against the query; project-scoped; best-effort empty on any error).
- Tests: `backend/tests/test_prompt_registry.py` (23 — frozen byte-identity pins for all 21 moved backend constants + 6 MCP templates, react_triage v2 anchors, registry⇄manifest⇄attestation sync green on this tree, hash/version/stale-entry drift detection, manifest-change-without-attestation + non-PASS verdict failures, quality-gate guard mirror agreement + tamper detection, version-tag shape, template render equivalence for the two runtime-composed prompts, call-site equivalence) and `backend/tests/test_memory_recall.py` (16 — the plan's acceptance criterion: a previously human-corrected fingerprint MUST surface the correction in tool output; cross-project leakage guard on semantic recall + SQL project scoping; graceful no-history; never-raises with every store broken; bounded report; tool unavailable without project context; <500 ms latency budget on mocked stores; ReAct 6-tool wiring smoke; chat integration match/scope/never-raises). Transaction-boundary allowlist gains `prompt_registry.py` (CLI-owned `--attest` session; cap 56→57). Full `scripts/quality_gate.py` (16 guards incl. the new one), targeted agent/eval/chat suites (500+ tests), MCP suite (72), and ruff all green.
### 2026-07-10 — DeepFinding reconciled + rule confidences calibrated/labeled (Agentic plan AI-F4)

- **Deep findings are now real pipeline output, not seed fiction** — evidence audit found `deep_findings` (and `failure_clusters`) rows were written **only by the demo seed scripts**: the deep pipeline computed findings-shaped output (`ClusterAgent` → `failure_clusters`, root-cause stage → per-test `analyses`) but never persisted it, and `state["deep_findings"]` was initialized empty and never populated (its `agent_memory_service` consumer was dead code). New `backend/app/agents/deep_persistence.py` runs after every deep pipeline (both `run_offline_pipeline(workflow_type="deep")` and `run_deep_pipeline` paths, before memory persistence so evidence memories now flow): it folds each cluster's member-test analyses into one finding (majority failure category, mean confidence, highest-confidence member's root cause, bounded evidence + deduped actions) and **upserts FailureCluster + DeepFinding per `(test_run_id, cluster_id)`** — idempotent per the repo convention; re-runs update in place and replace stale seed rows. Honesty preserved: `causal_chain` / `affected_services` / `contract_violations` stay `NULL` — nothing computes them (`ContractAgent` / `LogIntelligenceAgent` exist but are wired into no workflow graph; documented on the model). Persistence failure never fails a green pipeline (logged, non-fatal). No migration — existing columns only.
- **No endpoint serves seed-only data undetected** — every DeepFinding row now carries `log_evidence.origin` (`"pipeline"` from the persister, `"seed"` from both seed scripts — `seed_data.py` raw INSERT + `seed_dev_data.py` ORM); `GET /deep-investigate/{run_id}/findings` surfaces `origin` (legacy untagged rows report `"unknown"`) plus `confidence_basis`. Frontend `DeepFinding` type gains the optional fields; the Deep Investigation page keeps working unchanged (additive shape).
- **Rule confidences: named bands with a documented basis (no value changed)** — new `backend/app/services/confidence_bands.py` replaces every hardcoded confidence in `rules_engine.py` (17 keyword patterns + 6 statistical heuristics, incl. the dynamic historical-flakiness formula `min(85, 50 + 2n)`) with a band table carrying `basis` + provenance. Calibration audit: the golden datasets score classification *agreement* on pre-labeled summaries and contain no raw error messages, so per-rule precision is not computable today — **every band is honestly `heuristic_estimate` and every numeric value is byte-for-byte preserved** (zero behavior change; pinned by test). Every rules-engine result now carries `confidence_basis` + `confidence_rule_id`, persisted via the existing `AIAnalysis.routing_metadata` JSONB audit blob (no migration) and surfaced through `ConfidenceWhy.confidence_basis` on the analyze endpoints. UI: the Confidence + Why panel shows a subtle `estimated`/`calibrated` chip with an explanatory tooltip ("estimated heuristic confidence — not empirically calibrated"). `user-guide/ai-features.md` gains a "What confidence scores actually mean" section describing the real semantics per engine.
- Tests: `tests/services/test_confidence_bands.py` (band-table completeness incl. `_PATTERNS` drift guard, basis validity, pre-AI-F4 value pins for all 23 rules + the dynamic formula, end-to-end `classify_test` confidence pins across every rule, basis carried on every result, consumer-threshold audit — `requires_human_review` at 70 both sides of the boundary, `AI_CONFIDENCE_THRESHOLD=80` / auto-triage / retry-threshold constants, triage-gate behavior on the full fixture set) and `tests/test_deep_persistence.py` (synthesis folding + honest-Nones, error-only member skip, writer insert + **idempotent update-in-place** + empty-state no-op, endpoint origin flagging for seed/pipeline/legacy rows, no-seed-only-data guard pinning both seed scripts' origin tags and the workflow wiring). Existing rules/analysis/deep/workflow/memory suites (336 tests) stay green; `scripts/quality_gate.py` passes (analysis-router baseline line-shift re-pinned), ruff clean on touched files, `tsc --noEmit` + AIAnalysisPanel/DeepInvestigationPage vitest pass.
### 2026-07-10 — ML label integrity: human labels first, pseudo-labels capped (Agentic plan AI-F1)

- **Broke the ML learning loop's circular-label dependency.** The classifier previously trained mostly on the LLM's own high-confidence analyses, so retraining taught it to imitate the LLM rather than learn from ground truth. Every training example now carries a derived **label provenance**: `human_direct` (UI feedback card / US-2.4 correction dialog / MCP `correct_classification` — `ai_feedback.source` ∈ {manual, category_correction}), `human_indirect` (Jira-resolution webhook auto-labels), or `llm_pseudo` (confidence ≥ 80 analyses with **no** feedback row). **No schema change** — provenance derives entirely from existing columns (new module `backend/app/services/ml/label_provenance.py`).
- **Composition policy in the trainer** (`_gather_training_data` + `apply_composition_policy`): human labels always included at full `sample_weight`; `llm_pseudo` examples capped at ≤ `ML_PSEUDO_LABEL_CAP` (default 0.30) of the final training set and down-weighted to `ML_PSEUDO_LABEL_WEIGHT` (default 0.3, multiplied into the class-balance weights); when human labels alone are under `ML_MIN_TRAINING_SAMPLES`, pseudo-labels may fill up to the minimum (flagged `cap_exceeded_to_fill_floor`). The deployed model's `training_metadata.json` records the full mix as `label_composition` (counts + fractions per bucket, cap/weight, bootstrap flag). Pseudo selection is deterministic; corrected analyses are excluded from the pseudo pool (the correction already supplies the human label). Human-label gathering prefers explicit feedback over Jira auto-labels, newest first, one label per test case.
- **Honesty surfacing**: below `ML_HUMAN_LABEL_FLOOR` (default 50) human labels, the ML tier reports itself as **bootstrap (LLM-imitating)** — in each analysis's `_routing` decision record (`ml_maturity`), in `GET /api/v1/settings/ai` (`ml_human_label_count/_floor`, `ml_maturity` — the AI settings page shows a factual caveat instead of implying it learns from corrections), and in a new **Training Label Health** card on the AI-eval dashboard backed by `GET /api/v1/ai-eval/label-health` (+ `label_health` in the dashboard payload): live counts per bucket, human share of the pool, floor status, and the last-trained composition. Legacy models without composition metadata are reported as bootstrap, never silently promoted.
- **Eval evidence**: `backend/app/services/ml/label_eval.py::compare_label_strategies` trains pseudo-heavy vs human-weighted models on the same pool and scores both on a human-labeled holdout (per-class deltas); a regression test with a synthetic LLM-bias fixture (timeouts systematically mislabeled) shows the human-weighted policy recovering ground truth the pseudo-heavy regime loses.
- Fixed three latent trainer bugs exposed by exercising the path with a real scikit-learn: the training query joined `tc.run_id` (column is `test_run_id`, so gathering always returned 0 samples and was swallowed), `cross_val_score(fit_params=...)` was removed in sklearn ≥ 1.6 (now tries `params=` first), and `classification_report` without `labels=` crashes when fewer than all six categories are present. `get_training_sample_count` no longer double-counts analyses that already have feedback.
- Tests: `backend/tests/test_ml_label_integrity.py` (30 — provenance derivation per source, label resolution, composition-policy matrix incl. cap/weights/floor-fill/all-human/zero-human/determinism, maturity derivation incl. legacy-metadata case, trainer metadata persistence + insufficient-data composition, routing-record bootstrap caveat above/below floor, label-health API bucketing, comparison-harness regression + guardrails). Existing ML/eval/settings suites stay green; quality gate, ruff, eslint and tsc pass.

### 2026-07-10 — Attached HTML analysis report for daily/weekly digests (PMF backlog US-7.5)

- **Digest emails can carry a full analysis report (migration 0107)** — `digest_subscriptions` gains `report_attachment` (default OFF). When enabled on a project-scoped daily/weekly **email** subscription, the dispatcher attaches `testlookup-report-<project-slug>-<YYYYMMDD>[-weekly].html`: ONE self-contained HTML document (inline CSS, charts as generated inline SVG bars/sparklines, zero external assets, readable without JS — a little vanilla JS only collapses sections) covering the digest window (1d daily / 7d weekly; **weekly needed no new schedule field** — `DigestSchedule.WEEKLY` already exists and the dispatcher already advances weekly subscriptions by one week). Sections: header (project, absolute UTC window, run count, dashboard deep link), executive summary (**unique-tests-across-window semantics matching the Summary Report/Coverage convention** — numbers come verbatim from `build_summary_report` + `coverage_stats`, pinned by a dashboard-parity test — with pass-rate delta vs the equal prior window and a day-by-day runs/pass-rate SVG sparkline on weekly), runs table (build/branch/PR/status/pass rate/duration, deep link per run), failures for investigation (new-vs-recurring via the US-7.4 delta definition, top failing tests with code-quoted first error line, top clusters, failure-kind triad bar), flaky & quarantine (known-flaky, newly-flaky, quarantine debt, top flip-rate tests off the quarantine rows), slowest tests, latest release-gate verdict (+ `conditions_for_go` and the US-9.3 kind-rule counterfactual when present), open linked defects (with the "closed in Jira but still failing" conflict badge), and failures grouped by owning team (ownership rules; a setup nudge when none are configured). Every section degrades to one explanatory line when its subsystem has no data/config; every user-originated string is HTML-escaped; row caps (runs ≤ 50, top failing ≤ 20, clusters ≤ 10, slowest ≤ 10, defects ≤ 20) render "+K more — open dashboard" overflow lines; a 2 MB size guard re-renders with shrunken caps then falls back to a minimal link-out document.
- **Delivery mechanics (never blocks the digest)** — new `send_html_email_with_attachments` in `email_service` (stdlib `email.mime`: `multipart/mixed` wrapping the existing `multipart/alternative` body + one `Content-Disposition: attachment` part per file; the no-attachment path delegates to `send_html_email`, keeping plain digests byte-identical). The dispatcher builds the report via a never-raises wrapper: build failure (or a non-project-scoped subscription) sends the digest anyway with an apologetic note appended to the email body. Slack/Teams digest content is unchanged except a one-line "Full analysis report attached to the email digest" pointer when the flag is on.
- **On-demand download + UI** — `GET /api/v1/projects/{project_id}/reports/analysis?window=1d|7d` returns the same document as `text/html` with `Content-Disposition: attachment` (`require_project_access` + QA_ENGINEER+, per the authorization ratchet) so the report is testable without SMTP. `/reports/summary` gains "Analysis report (1d / 7d)" download buttons; `/settings/digests` gains the *Attach analysis report* checkbox (shown for email + daily/weekly) with help text, and subscription rows show a "Report attached" marker. Settings API (`DigestSubscriptionCreate/Update/Response`) carries `report_attachment`; the create endpoint also now persists the previously-ignored `send_when_unchanged` from the payload.
- Tests: `backend/tests/test_analysis_report.py` (38 — all nine sections' presence with fabricated service data, caps + overflow lines, `<script>` test-name escaping, empty-window/missing-subsystem omission lines, weekly day-bucket math incl. gap fill + weighted merge, filename slugging, size-guard shrink + minimal fallback, dashboard-parity pin, collect never-raises, attachment wrapper never-raises + daily→1d mapping, multipart MIME assembly + SMTP-disabled no-op + no-attachment delegation, Slack note + apologetic-note helpers, schema default-off matrix, ORM default, migration 0107 chain, endpoint content-type/disposition + project-guard). Existing digest/notification/email suites (115) and the architectural ratchets stay green; `scripts/quality_gate.py`, ruff, mypy (new modules), and `tsc` pass.

### 2026-07-10 — Ops package: one-command backup/restore, one-step upgrade, sizing guide (PMF backlog US-11.1 / US-11.2 / US-11.3)

- **`make backup` / `make restore` (US-11.1)** — new `scripts/ops/backup.sh` + `restore.sh` (shared `lib.sh`): every datastore is reached through `docker compose exec/run`, so the host needs only docker + bash — no pg_dump/mongodump/mc installs. Backup emits **one timestamped archive** under gitignored `./backups/` containing `postgres.dump` (pg_dump custom format), `mongo.archive.gz` (mongodump), `minio_data.tar.gz` (the MinIO volume tarred via a throwaway busybox container — zero credentials/network wiring, captures bucket metadata exactly; `QUIESCE=1` stops the app layer first for file-level consistency) and a `manifest.json` (schema v1: app version, git SHA, **alembic head**, per-component sha256, and the documented exclusions — Redis is broker/cache whose stale queue entries must not be replayed, ChromaDB is rebuilt by the hourly `reindex_search` beat). Restore validates the archive, **refuses schema-mismatched restores** (deployed migration head newer than the backup = accidental time-rollback; backup head unknown to the deployed code = backup from a newer version — both refusals explain themselves, `FORCE=1` overrides), requires `CONFIRM=yes` non-interactively, stops the app layer, drop/recreate-restores Postgres, `mongorestore --drop`s Mongo, replaces the MinIO volume, flushes Redis, restarts the stack (backend applies migrations on boot), health-waits, runs the smoke check, and prints a verdict + post-restore checklist (immediate ChromaDB reindex command included). The ancestry classification runs inside the backend container via new `backend/scripts/ops_support.py` (pure alembic `ScriptDirectory` read, no DB) so restore works against release images too; ancestry-unverifiable also fails safe behind `FORCE=1`.
- **`make preflight` / `make upgrade` (US-11.2)** — `scripts/ops/preflight.sh` (read-only: current vs `TAG=` target images, DB migration head vs code head with the pending range listed, docker + host disk headroom with a 2×-postgres-volume rule of thumb, newest backup + the "backup first" reminder) and `scripts/ops/upgrade.sh` (pull release images at `TAG=` — or rebuild when the active compose file builds from source — then **migrate loudly before replacing the app layer**, `compose up -d` in dependency order, readiness wait, smoke check, PASS/FAIL verdict). On any failure it prints step-by-step **rollback instructions referencing the pre-upgrade backup and the previous backend image tag**; automatic rollback is deliberately out of scope because data migrations aren't safely auto-reversible — documented in the script and the admin guide. The release-workflow summary now points upgraders at the two commands.
- **Sizing & capacity guide (US-11.3)** — new tracked `user-guide/sizing.md`: three reference profiles (small ≤50k / medium ≤500k / large 1M+ results/day) with per-service CPU/RAM from the shipped compose limits, the configured throughput guardrails (`INGEST_RATE_LIMIT_PER_MINUTE`, `LIVE_BUFFER_MAX_EVENTS_PER_RUN`, 8 shard queues, Redis backpressure) sourced from `architecture/INGESTION_SCALE.md`/`core/config.py`, the local-LLM add-on cost, and retention-driven disk-growth math (~3 KB/result blended across Postgres+Mongo — estimates explicitly labelled as estimates). Linked from the user-guide README, the administration guide (new **Backup, restore & upgrades** section), and `architecture/DEPLOYMENT.md` (new §6 Day-2 operations).
- Tests/verification: `backend/tests/test_ops_support.py` (refusal-classification matrix incl. multi-head + empty inputs, manifest schema validation, CLI runs against the real migration tree) plus `scripts/ops/verify_backup_scripts.sh` (`make verify-ops-scripts`: bash -n + shellcheck-if-present + the pytest suite). Backup→wipe→restore round-trip exercised live against a throwaway compose project (fresh volumes) — user data volumes untouched.
### 2026-07-10 — MCP write tools + agent cookbook (PMF backlog US-14.1 / US-14.2)

- **MCP write path (US-14.1)** — 9 new tools (49 → 58) so an MCP-connected agent can close the triage loop end-to-end. All are **thin wrappers over existing REST endpoints**: RBAC (QA_LEAD+ for quarantine writes, QA_ENGINEER+ for defect creation, QA_LEAD/ADMIN for reassignment) and audit logging (quarantine transitions → `SettingsAuditLog`, corrections → `ai_feedback`, defects → creator on the row) are enforced **server-side against the JWT identity the MCP server logs in with** — no parallel auth or audit system. New tools: `propose_quarantine` (files a PROPOSED request for QA Lead review — deliberately NOT a direct quarantine; human approval stays in the loop), `release_quarantine(request_id, reason)`, `promote_ready_quarantines(project_id, dry_run=true)` (dry-run lists `ready_to_promote` entries; `dry_run=false` releases max 10/call, each reported individually), `create_defect` (wraps the US-6.1 one-click Jira endpoint incl. `target="webhook"`; `dry_run=true` returns the server's preview and the docstring instructs agents to show the human the preview before creating; dedup-first semantics ride the backend), `correct_classification` (chains the US-2.4 fingerprint→analysis lookup + feedback POST with `rating=incorrect`; reports previous→new category), `assign_failure` + `get_assignment_options` (my-failures reassignment with the valid-assignee picker), and `get_transition_policy`/`set_transition_policy` (US-7.1 notification policy). Write tools return **structured dicts** (`ok`/`action`, or `ok:false` + `status_code` + backend `detail` via a new shared `client.error_payload`) instead of the read tools' markdown strings. Three new tool modules (`tools/feedback.py`, `tools/assignments.py`, `tools/notifications.py`) registered in `server.py`; server instructions gain a write-path section directing agents to dry-run and confirm destructive actions.
- **Agent cookbook (US-14.2)** — new tracked `user-guide/agent-cookbook.md`: connecting Claude Code / Claude Desktop / Cursor (stdio `.mcp.json` blocks) and networked/CI agents (SSE :8002), the dedicated-agent-user + RBAC/audit security model, and five worked recipes (triage the latest run; investigate a cluster + correct a bad classification; quarantine a flaky test + file the Jira ticket; release-readiness Q&A; weekly flaky-debt review with promote-ready dry-run) each with a realistic prompt and the tool sequence that fires. Local-first framing throughout: works fully offline, test data never leaves your network. Linked from `user-guide/README.md` and `cli-sdk-mcp.md`; `mcp/README.md` tool/resource tables updated (58 tools, 10 resources incl. the quarantine-manifest resource that was missing from the table).
- Tests: `mcp/tests/test_mcp_write_tools.py` (22 — registration + declaration checks, dry-run/preview defaults pinned, propose-never-approves guard, and functional coverage of the pure helpers: `_select_ready_to_promote` flag-filter + 10-cap, `_signature_params` fingerprint-XOR-cluster validation, `_correction_body` rating=incorrect + category vocabulary + normalization, `client.error_payload` 403-detail/non-JSON/plain-exception shapes). Full MCP suite 72 passed; CI-equivalent syntax + import validation green; server smoke-load confirms all 58 tools reachable (name-collision ratchet still green).
### 2026-07-10 — Ownership-routed notifications + delta digests (PMF backlog US-7.3 / US-7.4)

- **Transition notifications route to the owning team (US-7.3, migration 0106)** — new `team_notification_channels` table maps an ownership team (the free-text `team_name` the `service_ownership_rules` rows already use — one row per (project, team), NOT per rule, so many rules sharing a team can't drift) to a notification target (`channel_type` = email | slack | teams, `target` = webhook URL or email address). The transition engine (`notification_transitions`) now partitions each run's batch by owner (test → suite → ownership rules → team → channel, via the same `resolve_test_ownership` the quarantine lifecycle uses): owned events deliver **directly to the team's channel** (title tagged "· Team"), everything else keeps today's `NotificationPreference` fan-out **plus an "unowned" coverage-nudge note** listing why events fell back. Cluster-deduped groups route as a unit to the **majority owner** (> half the member tests); **mixed-ownership clusters fall back to default** with a note. **Fail-open contract pinned by tests**: resolver crash → default (`routing_error`), team without channel → default (`no_team_channel`), team-channel delivery failure → events **merged back into the default batch** (`delivery_failed`) — an event is never dropped because routing failed. Routing is engaged only when the project has ownership rules or team channels, so untouched projects keep byte-identical messages. Audit: `notification_logs` gains `routed_team` + `routing_fallback`; every team delivery writes a log row and every decision emits a stable-kwargs structlog event (`transition_routing_decision`). New service `notification_routing.py` (pure partition logic + delivery; single Celery-task-owned commit, allowlisted 55→56). API: `GET/PUT/DELETE /api/v1/projects/{project_id}/ownership/team-channels[/{team_name}]` (`require_project_access`; writes QA_LEAD+). `/ownership` page gains a **Team Notification Channels** section (teams derived from the rules, channel-type select + target input + save/remove). Quarantine hook events (`dispatch_quarantine_transitions`) still go to default channels — noted follow-up.
- **Digests report deltas, not data dumps (US-7.4)** — scheduled digests are now structured around **what changed since the previous send**: the existing `DigestSubscription.last_delivered_at` is the watermark (captured pre-claim in the dispatcher; first delivery falls back to one schedule period). `generate_digest(since=…)` computes a `delta` block — **new failures** (failed in window, not failing in the equal pre-window; count + top 3 by in-window failure count), **newly flaky** (quarantine candidates detected in window), **fixed/recovered** (failed pre-window, ≥1 pass and 0 fails in window), **quarantine debt** (active / stale / ready-to-promote from the US-5.4/5.5 lifecycle fields — a standing stock, deliberately not a "change"), and **gate verdict change** (latest recommendation before vs. inside the window). Absolute totals stay as a secondary line. **Zero-change windows** (no new failures / newly flaky / recoveries / gate change) send a one-liner ("No changes since the last digest — N tests, pass rate X%") or **skip delivery entirely** per the new `send_when_unchanged` subscription flag (migration 0106, default TRUE = today's cadence; skips log a `skipped` NotificationLog row). Email HTML gains a delta-first "Since last digest" block + the compact zero-change page; new `render_digest_text` powers Slack/Teams.
- **Digest delivery fixes (in-scope while rewiring the dispatcher)** — the scheduled dispatcher imported a **non-existent `send_email`** from `email_service`, so every scheduled digest email silently failed since ENT-05: fixed with a real `send_html_email` (pre-rendered HTML body, regression-guarded). Slack/Teams digest subscriptions previously logged "sent" without sending anything: they now actually deliver via the user's `NotificationPreference` webhook (project-scoped wins over global — explicit `NULLS LAST`, Postgres DESC defaults to nulls-first) falling back to the instance-wide settings webhook, and honestly log `failed` when no webhook exists.
- Tests: `backend/tests/test_notification_routing.py` (20 — the routing matrix above, cluster majority/mixed/unowned, delivery-failure merge, note rendering, channel fan-out incl. unsupported-type + never-raises, audit-write outage tolerance) and `backend/tests/test_delta_digests.py` (19 — zero-change predicate matrix incl. debt-is-not-a-change, one-liner formatting, delta-first text + HTML renderings with totals-secondary + XSS escaping, legacy no-delta shape unchanged, `send_when_unchanged` schema defaults, migration 0106 chain, `send_html_email` regression guard). Existing notification/transition/digest/ownership suites green (263 related tests).
### 2026-07-10 — Kind-aware release-gate policy (opt-in) (PMF backlog US-9.3)

- **Policies can weight failure kinds differently — strictly opt-in, no migration** — `PolicyDocument` (the JSON stored in `release_gate_policies.rules`) gains a `kind_rules` block: `{enabled: false, infrastructure?: {max_failures, downgrade_to}, test_code?: {…}}`. Disabled or absent → the verdict computation is **byte-identical to before** (pinned by a regression test comparing full evaluation dicts for block-absent vs block-disabled-with-budgets on the same fixtures). Existing rows read the default via Pydantic — no migration.
- **Verdict semantics** (`policy_evaluator_service._apply_kind_rules`, applied to the BASE recommendation before rule escalation): failures are bucketed by the US-9.1 derived kind triad (`failure_kind.py` over each analysis's classifier verdict); infrastructure/test-code failures **within their configured budget are excluded from the NO_GO trigger but always reported**; exceeding a budget restores full counting for that kind; kinds without a budget always count. If every failure is excused and at least one was, a base NO_GO downgrades — **at most to CONDITIONAL_GO, never GO** (the schema's `downgrade_to` is a single-value `Literal`, making the hard rule unrepresentable). **Product failures can never be excluded; unknown-kind failures conservatively count as product**; missing kind data (pre-feature snapshots, simulator) → no downgrade, recorded in the trail. A failing **BLOCK rule still forces NO_GO** over any kind downgrade (escalation runs after, and the trail is corrected to say so).
- **Traceability** — the persisted `policy_evaluation` gains `kind_breakdown` / `kind_rule_applied` / `kind_counterfactual` plus per-kind `kind_budget_*` rule evaluations (INFO — budgets never escalate) with actual/threshold, so `/release-gate`'s rule list, the decision trail, and the compliance pack's `decision.json` (whole-row serialization) all carry the breakdown, which budget fired, and the counterfactual ("Would have been NO_GO; downgraded to CONDITIONAL_GO because 3 infrastructure failure(s) <= budget 5 (product failures: 0)"). A surviving downgrade also lands in `conditions_for_go` (`[Policy] …`). The release-risk agent freezes `failure_kind_counts` into the decision `input_snapshot`, and `/release-gate-policies/simulate` replays it from there.
- **Policy editor UI (`/policies`)** — new opt-in "Failure-Kind Weighting" section following the page's existing card/form patterns: enable toggle, per-kind budget checkboxes + max-failures inputs (infrastructure, test code), downgrade select hard-limited to CONDITIONAL_GO ("Never GO — hard rule"), and copy stating kinds are AI-classified and that product/unknown always count.
- Tests: `backend/tests/test_kind_gate_policy.py` (26 — identical-when-disabled pin incl. full-dict equality, budget under/at/over matrix, never-to-GO, product never excluded, unknown→product conservatism, unbudgeted-kind counting, missing-kind-data conservatism, excluded-failures-still-reported, BLOCK-rule precedence + trail correction, counterfactual text, `downgrade_to` Literal rejection, policy JSON round-trip through the API create/update schemas, agent kind counting incl. errored analyses → unknown, snapshot freeze). Existing release-gate/policy/compliance suites unchanged and green.

### 2026-07-09 — One-click Jira defects: pre-filled, deduped, status-synced (PMF backlog US-6.1 / US-6.2 / US-6.3)

- **One-click Jira issue from a failure (US-6.1)** — new project-scoped endpoints (`require_project_access` on all three; POST is QA_ENGINEER+): `POST /api/v1/projects/{id}/defects/jira` creates the issue, `GET …/defects/jira/preview` returns the **server-assembled** payload the dialog shows read-only (the create path re-assembles it server-side — a client can never smuggle an edited stack trace), and `GET …/defects/jira/metadata` returns Jira projects + issue types for the pickers (**cached ~5 min**, graceful `available=false` + machine-readable `reason` when Jira is offline-gated/unconfigured/unreachable — never a 5xx). The prefill (new `services/defect_jira_service.py`, stage-only per the transaction ratchet — the router owns the commit) carries: summary `[TestLookup] <test|cluster label>`, failure message + stack trace (truncated 3000 chars), **occurrence history** (first/last seen, failing-run count across runs), branch/build/CI context from the newest failing run, a deep link back to TestLookup, and — reusing the US-2.4 `latest_analysis_for_fingerprint` lookup — a clearly-labelled *"Suggested root cause (AI, confidence X%)"* block. Identity is `fingerprint` (failures/quarantine pages) or `cluster_id` (hashed to a fingerprint-shaped signature). Effective connector config resolves AppSetting overrides → secret-service token → env settings; **`AI_OFFLINE_MODE` is the hard kill switch** (actionable 503, same contract as the promote flow).
- **Recurrence dedup before create (US-6.1, migration 0105)** — `defects` gains `signature_fingerprint` (+ partial composite index) as the dedup key: an OPEN defect already linked to Jira for the same signature short-circuits creation — the existing issue gets a best-effort *"recurred in build X"* ADF comment, `recurrence_count`/`last_recurrence_at` are bumped, and the response carries `deduplicated=true` + the existing link ("Linked to existing KEY (recurrence noted)" in the UI). New issues record the bidirectional link on the Defect row (`jira_ticket_id`/`jira_ticket_url`, `promotion_source="one_click_jira"`, attached to the newest failing TestCase when the open-defect-per-test-case unique index allows).
- **Status sync-back (US-6.2)** — new 15-minute Celery beat `sync_jira_defect_statuses` (offset 5 min from the integration probes) mirrors Jira status onto linked, still-OPEN defects: existing `jira_status` column + new `external_status_at` timestamp, **capped at 50 issues/cycle** (oldest-refreshed first — HTTP errors still stamp the timestamp so one dead issue can't starve the window). **Contradiction flag**: Jira `statusCategory=done` while the signature still produced FAILED/BROKEN executions in the last 7 days → new `external_status_conflict=true`, surfaced as a **"closed in Jira but still failing"** amber badge on `/quarantine` (rows now carry `defect_jira_key/url/external_status/conflict` via a batched Defect lookup) and on the `/defects` table's Jira column (list endpoint now returns the two new fields). The service stages; the beat task owns the commit (no services-ratchet change).
- **Webhook fallback (US-6.3)** — `defect.create_requested` joins the outbound-webhook events registry (payload schema documented in the registry description + user guide): `target="webhook"` on the create endpoint emits the event with the full prefilled payload instead of calling Jira — gated exactly like the rest of the webhook subsystem (`outbound_webhooks` flag + `AI_OFFLINE_MODE`, actionable 503 when gated) and reporting `subscriptions_notified`. `user-guide/defects.md` gains the one-click section + a GitHub-Issues receiver sketch.
- **Frontend** — new `CreateJiraIssueModal` (US-2.4 dialog pattern): read-only prefilled summary/description preview (AI block labelled), Jira project/issue-type pickers from metadata, optional comment, **explicit review-then-create** (dedup case announces itself up front and relabels the CTA "Note recurrence"), and a delivery-target radio where Jira disables with reason copy (offline/unconfigured) and auto-falls-back to **Webhook event** when a receiver is subscribed (`webhook_available` from the metadata probe). Wired on `/failures` (headline failing test, next to Mute test — disabled with tooltip when both delivery paths are dead) and `/quarantine` ("File Jira" on rows without a linked defect; linked rows render the Jira key + mirrored status + conflict badges). New `defectJiraService` + `useJiraDefectMetadata`/`useJiraDefectPreview` SWR hooks.
- Tests: `backend/tests/test_defect_jira_endpoint.py` (18 — dedup links+comments instead of creating, creation payload shape + staged bidirectional link, Jira 4xx → actionable 502, offline-mode 503 + availability-reason ordering, metadata graceful shapes + 5-min cache + unreachable reason, webhook fallback emits the registered event with the prefilled payload / blocked-when-gated, sync mirror + conflict matrix + LIMIT-cap pin + error-stamp rotation + offline skip, route guards incl. QA_ENGINEER role + bootstrap registration, description-text sections, cluster-signature stability) and `CreateJiraIssueModal.test.tsx` (5 — preview render + pickers, submit payload + success flow, dedup announcement + CTA relabel, Jira-disabled/webhook-fallback path, both-paths-dead lockout).
### 2026-07-09 — Check-run annotations + flaky-aware conclusions (PMF backlog US-4.2)

- **GitHub check runs gain per-test annotations** — `github_checks_service` now attaches up to **50 `output.annotations`** (GitHub's per-request cap; overflow is counted in `output.text` as "+K more" — multi-request pagination is a noted follow-up) to every check run it posts. Priority: **newly-failed first** (classified against the SAME baseline the sticky PR comment uses — `_select_baseline` + `run_compare._classify`, so the two surfaces never disagree), then remaining failures by **cluster size** (failures sharing the same first error-message line; the AI `failure_clusters` rows don't exist yet at finalize time, so the message-line grouping is the deterministic post-time proxy). Known-flaky failures (active quarantines ∪ flaky-coach cache, reused from US-4.1) annotate at `annotation_level: warning`; everything else at `failure`. Each annotation carries the test name as title and the first lines of the failure message (bounded 5 lines / 800 chars).
- **Honest locatability** — annotations require a repo-relative `path` + `start_line`; a new best-effort locator (`locate_in_trace`) parses stack traces: Python tracebacks (last repo-relative `File "...", line N` frame — "most recent call last"; site-packages/absolute/drive-letter paths rejected, Windows backslashes normalized), JS/TS stacks (first repo-relative frame, `node_modules` skipped, `webpack://` bundler prefixes stripped). **Java surefire frames are deliberately unlocated** (`Cls.java:42` is a bare file name, not a repo path — the `src/test/java` prefix is unknowable) — non-locatable failures are listed in the check's `output.text` markdown instead of being pinned to a guessed path. Traversal (`..`), URLs, and vendor frames never become paths.
- **Flaky-aware conclusion (`ci-verdict` semantics on the Checks surface)** — a run whose failures are ALL known-flaky/quarantined now concludes **`neutral`** with title "N failures — all known-flaky/quarantined" instead of `failure`; any real failure still concludes `failure`, green still concludes `success`. Fail-honest guard: when per-test rows are unavailable (live-buffer eviction), all-flaky can't be proven and the conclusion stays `failure`. The check summary gains the PR comment's **newly failed / known flaky / fixed** triad (or a "no baseline" note). Enrichment is best-effort: any error degrades to the pre-US-4.2 aggregate-only check, never a skip.
- **No new setting, no migration** — the behavior rides the existing enablement (`github_checks` feature flag + per-project integration `enabled` + `AI_OFFLINE_MODE` hard gate). The feature-flag service resolves unknown keys to False (no code-default mechanism), so a dedicated flag would have required a seed migration — deliberately not added; documented in `user-guide/administration.md`.
- Tests: `backend/tests/services/test_github_checks_service.py` +23 (locator matrix: python deepest-repo-frame / absolute-path rejection / Windows normalization, JS node_modules skip + webpack prefix, Java honest-None, no-trace; partition PR-comment parity; annotation shape + warning level + newly-first-then-cluster-size priority + 50-cap with omitted count + locatable-vs-text split; conclusion matrix neutral-all-flaky / failure-any-real / failure-rows-missing / success-green; summary counts + no-baseline note + overflow note; enrichment-None keeps the pre-US-4.2 payload shape; `_gather_enrichment` empty-rows and baseline-without-rows paths). Existing checks + PR-comment suites unchanged and green.

### 2026-07-09 — Quarantine lifecycle: owner, SLA, auto-promotion (PMF backlog US-5.4 / US-5.5)

- **Quarantine gets an owner, a ticket, and an SLA (US-5.4, migration 0104)** — on activation (the approve hook) `flaky_quarantine_service` resolves an **owner** by mapping the test's suite through the existing ownership rules (`service_ownership_rules` + legacy `component_owner_map`); the rule's `team_contact` is matched against `User.email`/`User.username`, falling back to the approving QA lead. Owner/policy lookups run on their OWN session and never raise — activation can't fail on ownership-resolution errors. When the project policy sets `auto_create_defect`, an **INTERNAL defect record** is staged in the same transaction ("Quarantined flaky test: <name>", body = flake history + /quarantine review link, `promotion_source="quarantine_lifecycle"` — Jira posting is Epic 6, not this slice). `sla_days` is snapshotted and `stale_at = activation + SLA` precomputed; **staleness is DERIVED** (`stale` ORM property: active state + `stale_at <= now`) so `/quarantine` lists and the CI manifest surface `stale` without waiting for a sweep. The nightly maintenance beat gains a `mark_stale_quarantines` pass that emits **`test.quarantine_stale`** once per entry (idempotency anchor: `stale_notified_at`). A re-quarantine (fresh window) restarts the SLA clock.
- **Auto-promotion out of quarantine (US-5.5)** — `update_quarantine_stability(run_id)` rides the same run-finalization Celery task as the transition engine (own try/except, idempotent per (request, run) via `last_stability_run_id` — retries/re-finalizes advance nothing). It counts **consecutive fully-PASSED runs** per active quarantine (`consecutive_passes`); **a single FAILED/BROKEN result resets the streak to 0** (pinned by a regression test) and withdraws any pending promotion flag. At the policy threshold (`promote_after_passes`, default 20): `auto_promote` ON → the row is **RELEASED** (audit action `auto_promote_release`, emits the existing `test.unquarantined`); OFF → `ready_to_promote=true` surfaces on `/quarantine` + the manifest and the optional **`test.ready_to_unquarantine`** notification fires once per crossing (`ready_notified_at`, re-armed when a failure resets the streak).
- **Per-project lifecycle policy** — new `quarantine_lifecycle_policies` table (project-unique; missing row = code defaults: SLA 14d, no auto-defect, no auto-promote, promote after 20 passes) with `GET/PUT /api/v1/projects/{project_id}/quarantine/policy` (`require_project_access`; PUT QA_LEAD+, same shape as the transition-policy endpoints). **US-5.6 (partial, thresholds only)**: the policy also carries `detection_flip_rate_threshold` (default 0.20) and `detection_min_runs` (default 10) and the flaky-sentinel agent now reads them instead of its hardcoded `>= 20% over 10 runs` floor — the detection plumbing itself already existed, so no new detector was built.
- **API surfacing** — `FlakyQuarantineRead` gains `owner_user_id`/`owner_name` (batched username lookup in the router), `defect_id`, `sla_days`, `stale_at`, `stale`, `consecutive_passes`, `ready_to_promote`; manifest entries gain `stale` + `ready_to_promote` (CI can see both without a second call). Two new `NotificationEventType` members join the transition vocabulary end-to-end (policy validator, Slack/Teams emoji+colour maps, settings checkboxes).
- **Frontend (minimal)** — `/quarantine` gains an Owner column (with a defect-record link when one was auto-created), a rose "stale — N days over SLA" badge, an emerald "Ready to promote" badge, and a one-click **Promote out** release button on ready rows (reuses the existing release action with an explanatory note).
- Tests: `backend/tests/services/test_quarantine_lifecycle.py` (20 — pass/fail/skip counter matrix incl. THE single-fail reset regression + retried-run semantics, policy default/explicit resolution, owner resolution via rule contact + no-match + never-raises fallback to approver, `stale` property matrix, stability tracker threshold paths [ready vs auto-release vs idempotent re-run vs flag-off], staleness sweep once-only stamp + notification, event vocab sync, internal-defect staging). Transaction ratchet: `flaky_quarantine_service.py` cap 9 → 11 (staleness sweep + stability tracker, both beat/task-owned sessions).
### 2026-07-09 — Failure-kind triad: product / test-code / infrastructure (PMF backlog US-9.1 / US-9.2)

- **Every analyzed failure now carries a derived `failure_kind`** — `product` | `test_code` | `infrastructure` | `unknown` — the triage axis practitioners actually split work on (dev vs test-suite vs SRE). The kind is **derived, never stored** (no migration): new `backend/app/services/failure_kind.py` is the single canonical mapping over the existing `FailureCategory` vocabulary (PRODUCT_BUG→product; TEST_DATA/AUTOMATION_DEFECT/FLAKY→test_code; INFRASTRUCTURE→infrastructure; UNKNOWN→unknown — every choice documented in the module docstring) plus a **BROKEN-status nudge**: an uncategorised/unknown failure whose status is `BROKEN` (the parsers' shared "unexpected error, not an assertion" outcome) resolves to `infrastructure`. Alias/drift normalisation reuses `category_normalizer`'s map — no second normaliser.
- **Exposed wherever failures are served**: `GET /analytics/failure-categories` gains a parallel **`by_kind` aggregation** (zero counts included, fixed order; the SQL now groups by (category, status) so the BROKEN nudge applies per row — `items` keeps its shape and gains `kind` per item), `GET /analytics/top-failing` items gain `failure_kind` (category-only fidelity — fingerprint aggregates carry no per-row status, noted in code), and the run's test listing (`GET /runs/{id}/tests` + detail) gains `failure_kind` as a Pydantic computed field on `TestCaseSummary` (null for non-failing rows; live-buffer fallback rows get it too). **Clusters skipped**: `FailureCluster` carries `member_test_ids` only, no category data — a majority-kind rollup needs a member join and is left as follow-up.
- **US-9.4 rider — infra rule-pack**: the rules engine already caught OOM / ECONNREFUSED / timeout / 5xx / DNS / TLS; three genuinely-missing infra shapes added (disk exhaustion `ENOSPC`/quota, unreachable network/host `EHOSTUNREACH`/`ENETUNREACH`/no-route, OS resource exhaustion `EMFILE`/EAGAIN/cannot-allocate) + `EAI_AGAIN` on the DNS row — all → INFRASTRUCTURE with tests.
- **`/failures` (US-9.2)** — new **kind filter chip row** (All / Product / Test code / Infrastructure / Unknown, counts from `by_kind`, zero-count kinds stay visible) explicitly labeled **"AI-classified"** — kinds are presented as classifier output with provenance copy, never ground truth. Selecting a kind filters the category-distribution card (verdict/stability model deliberately stays whole-window). Color-coded **kind badges** on the category rows and the What's-failing headline test — tokens are `--kind-*` aliases over the existing palette (`--status-failed` / `--color-purple` / `--status-broken` / `--color-text-muted`), no new hex. The category card's display buckets now also recognise the actual backend enum vocabulary (PRODUCT_BUG/TEST_DATA/AUTOMATION_DEFECT/FLAKY previously all fell through to "Unknown"). Frontend mirror of the mapping in `src/utils/failureKind.ts` (used only as fallback for older cached payloads — the server value wins).
- **`/overview`** — new **"Infra-caused failures %" KPI** (widget `infra_failures_kpi`, customizable like the rest) computed from the same SWR-cached failure-categories payload /failures uses — no bespoke endpoint; meta line carries the "AI-classified · N of M failures" provenance.
- Tests: `backend/tests/services/test_failure_kind.py` (36 — exhaustive enum→kind matrix incl. an exhaustiveness guard, BROKEN-nudge precedence, alias/lowercase drift, `kind_counts` order/zeros, endpoint response shapes for failure-categories + top-failing, `TestCaseSummary.failure_kind` matrix, 10 infra-shape rules-engine pins) and 4 new `FailureAnalysisPage` tests (chips + counts + provenance copy, filter narrows the category card, headline kind badge, client-side fallback when `by_kind` is absent).

### 2026-07-09 — Transition-only notifications: alerts that mean something (PMF backlog US-7.1 / US-7.2)

- **New transition rules engine `backend/app/services/notification_transitions.py`** (US-7.1) — evaluated at run finalization (same post-ingestion orchestration point as the per-run fan-out, own Celery task `dispatch_transition_notifications`, own try/except — ingestion never blocks). Five transition events join `NotificationEventType`: **`test.newly_failing`** (fingerprint FAILED/BROKEN in ≥N consecutive completed runs — N per-project, default 2 — and wasn't confirmed-failing before), **`test.recovered`** (was confirmed-failing, now PASSED), **`test.newly_flaky`** (entered the known-flaky set this run — the definition is REUSED from the PR-comment work: active quarantine ∪ flaky-coach cache), **`test.quarantined`** / **`test.unquarantined`** (event-driven from `flaky_quarantine_service`'s own hook points: approve → quarantined, QA-lead release + recheck auto-release → unquarantined; re-quarantine is deliberately silent — nothing user-visible transitioned). Repeats emit nothing: fail→fail→fail is ONE alert, pass→pass is silence.
- **State store (migration 0103)** — `notification_test_states`: one row per (project, fingerprint) with `state` (passing|failing), `consecutive_failures`, `last_notified_state`, `is_known_flaky`, and `last_run_id` as the idempotency anchor — re-finalizing a run advances nothing and fires nothing (per-(entity, run) idempotency convention). New rows seed `is_known_flaky` from the current flaky set silently, so a pre-existing flaky backlog doesn't flood on first evaluation. SKIPPED results are no-signal (streaks neither grow nor reset).
- **Per-project policy (`notification_transition_policies`, migration 0103)** — `transitions_enabled`, `enabled_events` (JSONB subset of the five), `consecutive_failure_threshold`, and `per_run_events_enabled` (the legacy run_failed/run_passed/high_failure_rate fan-out). **Existing projects keep their current behaviour**: the migration backfills an explicit row (`transitions_enabled=false, per_run_events_enabled=true`) for every project existing at upgrade time; a MISSING row = the new-project default (**transitions ON, per-run spam OFF**) — no project-creation hook needed. The per-run gate in `notification/manager.py` fails OPEN on lookup errors so a policy-table hiccup can never silence legacy installs. Policy is API-settable via `GET/PUT /api/v1/notifications/projects/{project_id}/transition-policy` (`require_project_access`; PUT is QA_LEAD+). Per-channel routing reuses the existing `NotificationPreference.events` lists — no parallel settings system; new-preference defaults now include the transition events (harmless for existing projects — their policy is off).
- **Cluster dedup + run batching (US-7.2)** — newly-failing tests sharing a `FailureCluster` collapse into ONE line ("Cluster '<label>' — N tests newly failing, M suites" + cluster deep link); singleton clusters and clusterless tests fall back to per-test lines with evidence ("failed 2 consecutive runs: b-41, b-42"), capped at 10 lines with a "+K more transitions" overflow. **A run IS the batch**: all transitions from one finalization render into a single message per channel — no timer window. Simplification noted: clusters are written by the async AI pipeline, so first-finalization messages may render per-test (capped) if clustering hasn't landed yet. Delivery reuses the existing Slack/Teams/email fan-out (`_load_and_notify`) incl. NotificationLog audit rows; new event emoji/colour map entries in both channel builders.
- **Frontend (minimal)** — the five transition events appear as checkboxes on Settings → Notifications (type union + `EVENT_LABELS`); new preferences default them on. The per-project policy itself is backend/API-settable in this slice (no dedicated policy UI yet).
- Tests: `backend/tests/test_notification_transitions.py` (23 — full state-machine matrix incl. configurable threshold, idempotent re-finalize, flaky enter/leave/seeded-silent, cluster grouping + singleton fallback + overflow cap, policy resolution defaults vs explicit row, per-run spam gate off/on [existing-project behaviour preserved], quarantine-hook gating + never-raises). Transaction ratchet: `notification_transitions.py` allowlisted at 1 Celery-task-owned commit (total 52 → 53).
### 2026-07-09 — Failure Analysis actions wired: mute→quarantine, classifier corrections (PMF backlog US-2.4)

- **`/failures` "Mute test" is now a real action** — opens a "Mute test (propose quarantine)" modal (reason textarea REQUIRED — it lands in the proposal's `rationale` and the quarantine audit trail) and submits a **manual quarantine proposal** through the existing workflow (`POST /api/v1/quarantine`, QA_LEAD+; lands as PROPOSED pending approval on /quarantine — the success toast mirrors that vocabulary). The modal shows the test's flake history from data already on the page (measured `fail_count/total_runs` intermittency, "already human-flagged", or an explicit "no intermittency signal — muting hides a real regression" warning), and measured flip-rate/pass-count context is forwarded on the proposal for the approving QA Lead. The button renders **disabled with a tooltip** when the identity prerequisites are missing (no project selected / no `test_fingerprint` on the top-failing item). `TopFailingItem` now declares `test_fingerprint` — the backend's top-failing query has always returned it (it GROUPs BY fingerprint); the frontend type just never exposed it.
- **"Classifier hint" placeholders wired to the feedback/correction loop** — both entry points (the category card's low-confidence banner, now "Correct the classification →", and the QA "Recommended actions" row) open a **Correct classification** dialog: current AI category, the 5 real `FailureCategory` choices (UNKNOWN excluded — correcting *to* Unknown teaches nothing), optional comment; submits `rating=incorrect` + `corrected_category` to `POST /api/v1/feedback/{analysis_id}`, which overwrites the analysis category and persists the `AIFeedback` training-signal row (success toast: "Correction recorded — feeds the next training export"). When the test has never been AI-analysed the dialog shows "No AI analysis recorded for this test yet" instead of erroring.
- **New `GET /api/v1/projects/{project_id}/analyses/lookup?fingerprint=…`** — the page identifies tests by fingerprint but feedback keys on `analysis_id`; this bridges the two. Latest `ai_analysis` row via the `TestCase → TestRun` join (project-scoped — fingerprints are NOT salted per project, so the scope filter is tenant-isolation-load-bearing), `require_project_access` guard on its own `feedback.lookup_router` under `/api/v1/projects`, tiny `{analysis_id, failure_category, analyzed_at}` response — **200 with null fields** when no analysis exists (a 404 would surface as an error toast). `failure_category` is a plain string, not the strict enum (String(30)-column drift must not 422 the response). Service is read-only/stage-only per the transaction ratchet. New frontend `useAnalysisLookup` SWR hook (null-key suspended until the dialog opens) + `aiFeedbackService`; `flakyQuarantineService.propose()` added for the mute flow.
- **"Start bisect" REMOVED (not hidden)** — commit attribution (Epic 8) hasn't landed, so there is no backend to drive a bisect; the button and its plumbing are deleted rather than shipping a dead CTA. Reintroduce it alongside Epic 8.
- Tests: `backend/tests/test_analysis_lookup_endpoint.py` (7 — project-scoped latest-first SQL, enum→wire-value normalisation, null-body contract, route shape + `require_project_access` guard, PROTECTED_ROUTERS registration) and 6 new `FailureAnalysisPage` tests (bisect gone, mute modal required-reason gating + propose payload, disabled-without-fingerprint tooltip, correction dialog submit payload, no-analysis empty state, QA rec routes to the dialog).

### 2026-07-09 — Sticky PR summary comment (PMF backlog US-4.1)

- **New `backend/app/services/github_pr_comment_service.py`** — when a run finalizes carrying PR context (`pr_number` + `ci_repo`, US-4.3) that matches the project's configured GitHub repo, TestLookup **upserts ONE comment** on that PR. The comment is keyed by a hidden first-line marker (`<!-- testlookup-pr-summary:<project_id> -->`): the service lists the PR's issue comments (paginated, ≤3×100), PATCHes the marker comment if found, POSTs otherwise — never a second marker comment, so re-runs update in place. Sections: header (pass/fail/skip counts + pass rate + Run Intelligence deep link via `PUBLIC_BASE_URL`), **❌ Newly failed** (≤10 rows: test name + first line of the failure message, code-quoted and truncated at 120 chars, "+K more" overflow), **⚠️ Known flaky** (≤5, explicitly labeled "historically flaky — likely not caused by this PR"), **✅ Fixed** (≤5, failing on baseline → passing now), footer (still-failing-on-baseline count + baseline build label + attribution). No AI content in this slice — analysis may not have finished when the comment posts; a follow-up story appends AI suggestions.
- **Definitions reused, not invented**: the newly-failed/fixed/still-failing split pairs by `test_fingerprint` and classifies via `run_compare_service._classify` — the PR comment always agrees with `/runs/compare`. **Baseline** = latest completed run on `main`/`master`, else the latest completed run on a different branch than the PR run, else no baseline (all failures listed as "Failing" with an explanatory note, no newly/fixed split). **Known flaky** = union of active-quarantine fingerprints (`flaky_quarantine_service.active_quarantines_for_project` — the same set the pipeline tags `quarantined`) and the flaky-coach result cache (`FlakyCoachResult`); a flaky failure lands ONLY in the flaky section.
- **Guardrails mirror the checks service**: hard `AI_OFFLINE_MODE` kill switch + `github_checks` feature flag (`_post_allowed`), PAT from `secret_service`, SSRF egress guard on the QA_LEAD-editable API base, 10s timeout + a single retry on 5xx/network error, errors recorded on `github_integrations.last_error` for Integration Health, and a never-raises entry point invoked from the post-ingestion orchestration right after the check-run post — ingestion never blocks on GitHub. Repo-mismatch guard: PR numbers are repo-scoped, so a run whose `ci_repo` differs from the integration's `owner/name` is skipped.
- **Migration 0102** adds `github_integrations.pr_comment_mode` (`off` | `failures_only` | `always`, default `failures_only`), exposed through the integration GET/PUT and a mode selector on Settings → GitHub. `failures_only` skips green runs with nothing fixed to report **unless** a marker comment already exists — a PR that went red→green gets its comment updated to green.
- Regression tests: `backend/tests/test_github_pr_comment.py` (18 tests — no-PR-context/offline/mode-off/repo-mismatch paths make zero HTTP calls, marker + section/overflow/truncation contract, flaky-only partition, fixed-from-baseline diff, PATCH-vs-POST upsert, failures_only green-skip vs red→green update, SSRF block records the error, single 5xx retry). User guide: GitHub section in `user-guide/administration.md` + a pointer from the CI-context section of `getting-results-in.md`.
### 2026-07-09 — Quarantine manifest + CI verdict: flaky tests stop blocking merges (PMF backlog US-5.1 / US-5.2)

- **New `GET /api/v1/projects/{project_id}/quarantine/manifest`** (US-5.1) — versioned (`version: 1`), CI-consumable manifest of **currently-effective** quarantines only (statuses QUARANTINED / RECHECK_SCHEDULED / RE_QUARANTINED — the new `_ACTIVE_QUARANTINE_STATES` subset in `flaky_quarantine_service`; released/rejected/expired rows and un-reviewed DETECTED/PROPOSED/APPROVED signals never appear). Each entry is the identity tuple a CI-side matcher needs: `fingerprint` (primary — `sha256(class::name)[:16]`, same formula as ingestion), `test_name`, `suite_name`, `class_name` (via outer join to `canonical_test_cases`, nullable), plus `status`/`quarantined_at`/`expires_at`/`reason`. Content-hash **ETag** (sha256 of sorted `fingerprint|status|updated_at` triples — order-independent, changes on any transition) with `If-None-Match` → 304 support (quoted/weak/multi-value forms tolerated) so CI polls cheaply; single indexed query (`ix_fqr_project_status`). Guarded by `require_project_access` (own `manifest_router` under `/api/v1/projects` — the quarantine router's `GET /{request_id}` would swallow literal sub-paths); empty when the `flaky_auto_quarantine` flag is off (quarantine isn't enforced anywhere in that state — CI then fails toward strictness). Also exposed as MCP resource `testlookup://projects/{project_id}/quarantine-manifest` (US-14.3).
- **New CLI command `testlookup ci-verdict --run <id> [--project <id>]`** (US-5.2) — exits **0** when the only failures in a run are quarantined/known-flaky tests, so flaky tests stop blocking merges with zero test-code changes. Fetches the run's FAILED+BROKEN tests (paginated) + the manifest, and partitions them: primary match on the client-side recomputed fingerprint (drift-pinned against the backend's `make_test_fingerprint` by a regression test), fallback exact `(test_name, suite_name)` tuple. Exit contract: **0** = no real failures, **1** = any real failure (`--strict`: ANY failure incl. quarantined), **2** = API/network error or `--timeout-seconds` completion-wait expiry — **fail-CLOSED by default** (a broken TestLookup must not green a build; `--fail-open` flips infra errors to exit 0 with a loud stderr warning). `--output json` emits `{verdict, exit_code, counts, real_failures, quarantined_failures}`; table mode prints a REAL vs QUARANTINED partition. Pure logic lives in stdlib-only `cli/testlookup_cli/verdict_logic.py`; `--project` is inferred from the run when omitted. `upload file --output json|yaml` no longer appends the success line to stdout (keeps `| jq -r '.run_id'` recipes parseable; run_id is already in the document).
- Tests: `backend/tests/test_ci_verdict_logic.py` (16 — partition matrix, exit codes, fingerprint parity with backend), manifest service unit tests (active-state subset, ETag stability/rotation, flag-off short-circuit), and 5 hermetic API integration tests (403 non-member, entry shape + ETag header, 304 round-trip incl. weak form, ETag rotation on state change, empty manifest). Docs: `user-guide/flaky-tests.md` gains a "Gating CI on real failures only" section with GitHub Actions / Jenkins / GitLab recipes; CLI table updated in `user-guide/cli-sdk-mcp.md`.

### 2026-07-09 — SDKs & CLI auto-detect CI context (PMF backlog US-4.3b)

- All four SDKs and the CLI now **auto-detect CI context** (`ci_provider`, `ci_repo`, `pr_number`, `ci_actor`, `ci_run_url`) from standard CI env vars and forward it to the US-4.3a backend contract. One detection matrix, ported identically: **GitHub Actions** (`GITHUB_ACTIONS=true`; PR number parsed from `refs/pull/<N>/merge` on `pull_request`/`pull_request_target` events) → **GitLab CI** (`CI_MERGE_REQUEST_IID`, actor/URL fallbacks) → **Jenkins** (`JENKINS_URL`; `org/name` derived from `GIT_URL`, `CHANGE_ID` for multibranch PRs) → **Azure DevOps** (`TF_BUILD`; `_build/results?buildId=` URL assembled from collection/project/build id) → **CircleCI** (repo from username+reponame, PR from the `CIRCLE_PULL_REQUEST` URL). First match wins; outside CI **nothing is sent** (never guess); malformed ints drop the field (never raise); strings defensively truncated to the backend caps.
- **Precedence** everywhere: detection < config file (`testlookup.ci_*`, Python SDK) < `TESTLOOKUP_CI_PROVIDER|CI_REPO|PR_NUMBER|CI_ACTOR|CI_RUN_URL` env overrides < explicit values (SDK session options / CLI flags).
- **Python SDK** — new `client/ci_context.py` module (shipped via `py-modules`); merged into `/stream/sessions` payloads (reporter + pytest plugin) and, for `LiveStream`, folded into `meta.metadata["ci_context"]` which `upsert_test_run` already reads at persist time. Also fixed a latent `kwargs.pop("machine_id")` KeyError that broke the pytest plugin's session start. **CLI** — `upload file` / `upload dir` gain `--ci-provider` / `--repo` / `--pr-number` / `--ci-actor` / `--ci-run-url` override flags and stamp the fields onto the `/ingest/file` multipart form (deliberate module copy at `cli/testlookup_cli/ci_context.py` — separate package, no cross-dependency; a regression test pins the two copies byte-identical below the docstring).
- **JS/TS SDK** — new `client/js/src/ci-context.ts`; resolved in `startSession` so the jest/mocha reporters get it for free; new camelCase `SessionOptions` overrides. **Java SDK** — new `io.testlookup.CiContext` with `detect(Map<String,String>)` (env injectable — `System.getenv()` is unmockable) + `SessionOptions` builder overrides. **Go SDK** — new `cicontext.go` (`DetectCIContext()` + `SessionOptions` fields).
- Regression tests: `backend/tests/test_ci_context_detection.py` (65 tests — full provider matrix run against **both** Python copies, precedence chain, SDK payload/meta wiring, CLI form fields, copy-drift guard), `client/go/testlookup/cicontext_test.go` (table-driven, `t.Setenv`, ambient-CI-proof), `client/java/.../CiContextTest.java` (17 tests). JS package has no test runner — logic verified via `tsc --noEmit` + ad-hoc runtime assertions.
- User guide: `user-guide/getting-results-in.md` gains a "CI context (PRs)" section (auto-detection coverage + override flags).

### 2026-07-09 — Intelligence panels wired to real data (PMF backlog US-2.2 / US-2.3)

- **`/deep-investigate` Evidence sources card de-stubbed** — the hard-coded five-row fixture (fake "Git history · Live", "Telemetry · Lagging 18s", etc.) is replaced by the real integration-health probes (`useIntegrationStatus` → `GET /api/v1/integration-health/status`): per-provider rows (GitHub/Jira/Splunk/OCP/Slack/Teams/SMTP/Ollama/ChromaDB, generic icon for unknown providers) with probe vocab mapped onto the card's live/lagging/off design (healthy→Live, degraded/timeout→Degraded, else Off) and the probe `message` + "checked Nm ago" as the detail line. An intrinsic "Test results" source (live whenever runs exist) keeps the workspace's own data represented honestly. The verdict facet's `evidenceConnected` count and NO_SOURCES gating now derive from real statuses (NO_FAILURES is checked before NO_SOURCES so a fresh install with no integrations reads "nothing to investigate", not "connect a source"); loading renders a spinner and zero configured integrations renders a "No integrations configured" pointer to Settings → Integration Health.
- **`/deep-investigate` spend & budget de-stubbed** — "Spend MTD" KPI and the estimate-card budget bar read the real per-project LLM meter (`useProjectUsage`/`useProjectQuota` → `GET /projects/{id}/llm-usage` + `/llm-quota`; in All-Projects mode the focused run's project scopes the query). The fabricated $40 monthly cap is gone: no quota → an explicit "no budget set" state; empty meter → honest $0.00. The budget bar's soft-warn marker sits at the quota's real `soft_warn_threshold_pct` instead of a hard-coded 80%.
- **`/deep-investigate` Model routing card de-stubbed** — the static model catalog (invented `text-embed-3-large`/`claude-*` rows with fabricated per-unit rates) is replaced by the focused run's ACTUAL decision trail (`useDecisionTrail` → `GET /runs/{run_id}/decision-trail`): per-stage `analysis_mode`/`execution_path` plus a fallback badge; no run/trail yet → "Routing appears once an investigation runs" empty state. The `rate` column is removed outright (no server-side price book exists) and the estimate card no longer multiplies fake rates: its headline $ is the trail's real `total_cost_usd` for the last investigation ("—" when none), the per-stage breakdown lists workload counts only, and the dry-run toast reports workload + time instead of an invented dollar figure.
- **`/intelligence` Spend panel de-stubbed** — wired to the same `useProjectUsage`/`useProjectQuota` hooks: "Used this month" (labelled for the meter's real MONTHLY period, was "Used today") + LLM-call count, "Avg per call", and a monthly-budget bar with real utilization width; "no budget set" only when the quota truly is absent, and a "select a project" state in All-Projects mode (the endpoints are per-project). Insights/Activity panels intentionally untouched (need new backend endpoints).
- Page tests extended: hermetic mocks for the three hooks + assertions that the fixture rows are gone, real provider rows/spend/routing render, and the no-budget/all-projects empty states appear.

### 2026-07-09 — CI context on runs: repo / PR / actor / run-URL (PMF backlog US-4.3a)

- **Migration 0101** adds `ci_provider`, `ci_repo`, `pr_number`, `ci_actor`, `ci_run_url` to `test_runs` + a partial index `ix_test_runs_project_pr` (`WHERE pr_number IS NOT NULL`) — `pr_number` is the anchor for the upcoming PR-summary-comment and commit-attribution features; `ci_run_url` deep-links back to the CI job.
- All three ingest paths carry the fields: **JSON batch** (`IngestPayload` — bounded optional fields), **file upload** (`/ingest/file` form params → worker task → run), and **live streaming** (`LiveSessionCreate` fields fold into `LiveSession.extra_metadata["ci_context"]` — no LiveSession migration — and stamp the TestRun at both the session-create stub and `upsert_test_run`, fill-if-null so drainer-created rows backfill without overwriting).
- `create_run_from_payload` reuse path (CI retries) backfills **NULL fields only** — never overwrites recorded context (same discipline as the `primary_suite_name IS NULL` guard).
- Regression tests: `backend/tests/test_ci_context_contract.py` (8 tests — schema acceptance/bounds on both payload types, metadata fold incl. explicit-key precedence, reuse-path fill-if-null, migration 0101 contract). `backend.analysis-router` baseline re-keyed (line shift only).
- Next slice (US-4.3b): SDK/CLI auto-detection of these fields from standard CI env vars (GitHub Actions, Jenkins, GitLab CI).

### 2026-07-09 — Ingestion: NUnit3, TRX & xUnit support (PMF backlog US-1.3 / US-1.4)

- **New `backend/app/services/nunit_parser.py`** — parses NUnit3 XML (root `<test-run>`; `nunit3-console` / `dotnet test --logger:nunit`): nested `<test-suite>` trees walked with the nearest **TestFixture** ancestor's `fullname` as the suite (assembly-name fallback); Passed/Failed/Skipped/Inconclusive map to PASSED/FAILED/SKIPPED (Inconclusive → SKIPPED with reason "inconclusive"); a `label="Error"` on a Failed case marks an *unexpected exception* → **BROKEN** — the same FAILED-vs-BROKEN vocab the TestNG parser preserves for `<failure>` vs `<error>`; `<failure><message>`/`<stack-trace>` children carry error details, `<reason><message>` the skip reason; `Category` properties (and legacy `<categories>`) → tags; **float-seconds** `duration` converts to ms; parameterized test-cases parse as plain leaves; one outcome pseudo-step emitted for non-passing cases.
- **New `backend/app/services/trx_parser.py`** — parses Visual Studio TRX (root `<TestRun>` in the VS TeamTest 2010 namespace; `dotnet test --logger trx` / MSTest / vstest): all matching is on **local tag names** so namespace-less legacy exports still parse; `<Results><UnitTestResult>` rows JOIN to `<TestDefinitions><UnitTest><TestMethod>` via `testId` for class/method identity (assembly-qualified `className` stripped at the comma); outcome map Passed→PASSED, Failed→FAILED, NotExecuted/Inconclusive→SKIPPED, Timeout/Aborted/Error→**BROKEN** (infrastructure shapes, with the raw outcome surfaced as the message when ErrorInfo is absent); `<Output><ErrorInfo><Message>/<StackTrace>` extraction; **`HH:MM:SS.fffffff`** durations convert to ms; suite = namespace-qualified class, package = its namespace portion.
- **New `backend/app/services/xunit_parser.py`** — parses xUnit.net v2 XML (root `<assemblies>`, bare `<assembly>` tolerated; `xunit.runner` XML / `dotnet test --logger:xunit`): Pass/Fail/Skip → PASSED/FAILED/SKIPPED (xUnit does not distinguish assertion vs error, so no BROKEN); suite = collection name (assembly file basename fallback); `method` attr → test_name with the display `name` kept as full_name, `type` → class/package; `<failure exception-type=...><message>/<stack-trace>` extraction (exception-type fallback message); `<reason>` CDATA (or nested `<message>`) skip reasons; `<traits>` → `name:value` tags; **float-seconds** `time` converts to ms.
- Wiring: all three registered in `_SUPPORTED_FORMATS`, `_detect_format` (`<test-run` → nunit and `<TestRun` + the VS TeamTest namespace string → trx and `<assemblies`/`<assembly` + `test-framework` → xunit, all sniffed **before** the JUnit `<testsuite` check — a TRX file contains no `<testsuite` marker and would otherwise fall through to the junit default and parse to zero results), and the worker `_parse_file_to_results` dispatch — zipped reports get all three via the existing tier-2 per-entry detection. Upload modal lists the formats with export hints and now accepts `.trx`; CLI `upload` `--format` help + dir glob updated; user-guide format lists updated.
- Regression tests: `backend/tests/services/test_dotnet_parsers.py` (12 tests — mixed-outcome fixtures per format, BROKEN cases (NUnit `label="Error"`, TRX Timeout), all three duration conversions, definitions join + namespace tolerance, traits/categories→tags, malformed input, detection precedence incl. TRX-not-junit). Quality-gate baseline for `backend.analysis-router` re-keyed (+12-line shift in `worker/tasks.py`; same tolerated entries, no new violations).
- Why: closes the .NET slice of the PMF ingestion gap (US-1.3 / US-1.4) — NUnit/MSTest/xUnit shops previously had no first-class report path.
### 2026-07-09 — Ask-AI chat page enabled (PMF backlog US-2.1)

- The fully-built Ask-AI chat page (natural-language Q&A over runs/failures via the configured LLM, `/chat`) was dark — its route commented out of `App.tsx`. Now registered, with the sidebar "Ask AI" entry (AI Reports group) gated on **both** the new `ask_ai_chat` feature flag (migration 0100, seeded enabled) **and** a non-rules AI analysis mode — so installs without an LLM show no dead nav entry. Direct URL navigation always works; the page renders its own "switch to LLM/Auto mode in Settings → AI Configuration" guidance when chat is unreachable.
- Sidebar tests reworked to per-key flag mocking + a new visibility matrix test (flag off / rules mode / config not loaded / enabled). Admins can hard-disable via Settings → Feature Flags.

### 2026-07-09 — Docs-truth pass: supported ingestion formats (PMF backlog US-1.6)

- `user-guide/getting-results-in.md` and the CLI `upload --format` help now list the full, real format set (`junit`, `testng`, `allure`, `cypress`, `playwright`, `pytest`, `robot`, `cucumber`) — both previously stopped at the original 3–5 formats.
- **New ratchet `backend/tests/test_supported_formats_docs_truth.py`**: CI-blocking in both directions — every format in `_SUPPORTED_FORMATS` must be named in the user guide and CLI help, and the user guide's "Supported formats" section must not claim a format the backend doesn't accept (the Robot/Cucumber months-long docs-vs-code drift can't recur silently).

### 2026-07-09 — Ingestion: Cypress/Playwright enabled by default (PMF backlog US-1.5)

- **Migration 0099** flips the `cypress_ingest` and `playwright_ingest` feature flags to `enabled_global=true`. They were seeded OFF by migration 0063, so a fresh install returned 503 on two advertised, documented formats until an admin discovered the flag — a first-run trap. The flag machinery is unchanged (admins can still disable either format per project/globally from Settings → Feature Flags); only the default changes. **Note for existing deployments:** if you deliberately disabled these formats, re-disable once after migrating.
- Regression test `backend/tests/test_ingest_flag_defaults.py` pins the migration contract: it flips exactly the router-gated keys, upgrade enables / downgrade disables (guards the copy-paste inversion). Router comment updated to reflect the new default.

### 2026-07-09 — Ingestion: Robot Framework & Cucumber support (PMF backlog US-1.1 / US-1.2)

- **New `backend/app/services/robot_parser.py`** — parses Robot Framework `output.xml` (root `<robot>`): arbitrarily nested `<suite>` trees flatten into ` > `-joined suite names; PASS/FAIL/SKIP/NOT RUN map to PASSED/FAILED/SKIPPED (NOT RUN → SKIPPED); the test `<status>` body becomes the failure message / skip reason; failing keywords aggregate into a coarse stack trace; direct child `<kw>` elements surface as bounded common-shape steps (NOT RUN keywords dropped); tags ingest from both the RF4+ `<tag>` and RF3 `<tags><tag>` shapes; timing handles **both** RF3–6 `starttime`/`endtime` and RF7 `start`+`elapsed` attributes.
- **New `backend/app/services/cucumber_parser.py`** — parses Cucumber JSON (`--format json`; cucumber-jvm/js, behave, SpecFlow): Feature → suite (uri kept as class_name), scenario status **derived** from steps (any failed → FAILED; else undefined/ambiguous → **BROKEN** — a missing/ambiguous step definition is an automation bug, mirroring the TestNG failure-vs-error vocab; else pending/skipped → SKIPPED with reason; steps without result objects → UNKNOWN, never vacuously PASSED); Background steps fold into the following scenario per Gherkin semantics; Scenario Outline example rows disambiguate via the report `id` in full_name; **nanosecond** durations convert to ms; Gherkin keywords carry into common-shape step dicts; feature+scenario tags ingest with `@` stripped.
- Wiring: both formats registered in `_SUPPORTED_FORMATS`, `_detect_format` (Robot `<robot` marker sniffed before the JUnit `<testsuite` check; Cucumber array-root + `"elements"`/`"keyword"` markers sniffed **before** the `.json`→Allure extension fallback that would otherwise swallow every cucumber.json), and the worker `_parse_file_to_results` dispatch — zipped reports get both via the existing tier-2 per-entry detection. Upload modal lists both formats with export hints.
- Regression tests: `backend/tests/services/test_robot_cucumber_parsers.py` (9 tests — nested suites, both RF timing shapes, RF3 tag wrapper, status vocab incl. BROKEN-for-undefined, Background folding, ns→ms, malformed input, detection precedence). Quality-gate baseline for `backend.analysis-router` re-keyed (+8-line shift in `worker/tasks.py`; same two pre-existing tolerated entries, no new violations).
- Why: the architecture README has advertised Robot/Cucumber ingestion that did not exist — first credibility slice of the PMF gap analysis (docs-vs-code truth gap A1).

### 2026-07-02 — User Guide: "Troubleshooting & FAQ" (symptom-first index)

- **New `user-guide/troubleshooting.md`** — a distinct document *type* (symptom→cause), not another subsystem: consolidates the operational gotchas scattered across the workflow guides plus this project's real recurring support patterns, each verified against current code — empty page (project/window/now-toasting-422), count mismatches (aggregation mode vs executions-vs-unique-tests), run-with-no-per-test-rows and `/suites`-empty-while-`/runs`-populated (shard-queue subscription), missing Upload button (`manual_upload` flag / All-Projects), integrations not firing (`AI_OFFLINE_MODE` hard kill-switch → Integration Health → Audit), AI features needing the local-LLM profile, My-Failures Mine-vs-Team scope, bisect needing a green baseline, generated-case faithfulness, and stale-bundle. Cross-links into the workflow guides + observability rather than duplicating. Indexed from the guide README.
- Docs-loop iteration 24 (user-doc track). Architecture track verified complete (size-ranked service survey); this symptom-index was the last remaining high-value user-doc slice.

### 2026-07-02 — User Guide: "Knowledge base & test generation"

- **New `user-guide/knowledge-base.md`** — the user-facing side of the RAG subsystem, verified against the implementation: registering knowledge sources (upload/URL, per-project chunking+indexing, sync-history + freshness endpoints, allowlist/scheme validation), grounded test-case generation in the Test-Management knowledge tab (`KnowledgeGenerationTab` + `KnowledgeSourcePicker`/`GenerationReviewPanel`/`CitationDrawer`) with per-case citations, the human review gate (faithfulness score via Ollama/RAGAS + citations), automatic staleness flagging when a source changes, and the optional-AI-stack/`AI_OFFLINE_MODE` gating (stub fallback offline). Ends with when-it's-worth-it + review tips. Indexed from the guide README.
- Docs-loop iteration 23 (user-doc track; sixth pass).

### 2026-07-02 — Architecture docs: AI_EVALUATION.md (model-ops & the AI pre-release gate)

- **New `architecture/AI_EVALUATION.md`** — the AI-ops/model-governance subsystem (the "is the AI itself good and safe to ship" layer, distinct from AI_QUALITY's runtime honesty), verified against the implementation: golden datasets (`golden_datasets`/`golden_agent_outputs` — classification/root-cause/duplicate/release-decision item sets) + feedback-derived datasets (`ai_eval_service.build_dataset_from_feedback`), evaluation runs + drift (`agent_eval_harness`, `AIEvalRun`, `detect_quality_drift`), the checksummed pre-release gate (`eval_gate_service.build_agent_stack_gate_manifest`/`evaluate_pre_release_gate`/`persist_agent_stack_gate_run` — admin-only endpoint, PASS/FAIL blocks prompt/model/routing changes), the per-track model registry (`model_registry` promote/retire/status with justifying metrics), and cost-as-signal (`agent_cost_service`). One mermaid eval-loop diagram. Indexed from the README; cross-linked to AI_QUALITY/RELEASE_GATE/ai-features.
- Docs-loop iteration 22 (architecture track; sixth pass — found via a fresh service survey after the prior "done" call).

### 2026-07-02 — User Guide: "Compliance & governance"

- **New `user-guide/compliance.md`** — the compliance-pack workflow, verified against the implementation: the `release_compliance_pack` feature flag (503 when off), what a pack captures (the six `_gather_*` sections — release+decision, the point-in-time policy snapshot, decision trail incl. overrides, clusters, defects, audit events), generate/download via the Releases-page `CompliancePackPanel` + durable object storage, the MCP `list_compliance_packs`/`generate_compliance_pack` tools, the deliberate self-guarding resilience (decision-trail/audit sections embed an error note rather than failing the whole pack), the governance surround (Audit Dashboard / Decision Trail / Release Gate), and when-to-generate guidance. Indexed from the guide README.
- Docs-loop iteration 21 (user-doc track; long tail).

### 2026-07-02 — Architecture docs: KNOWLEDGE_RAG.md (the optional RAG subsystem)

- **New `architecture/KNOWLEDGE_RAG.md`** — the ~2,500-line RAG-* subsystem (10 services), previously only listed as a schema domain. Verified against the implementation: the two flows (indexing: `knowledge_source_service` w/ `_validate_url_domain` allowlist → `knowledge_chunking_service.chunk_and_index` into a per-project `knowledge_chunks` Chroma collection → `knowledge_sync_service`; grounded generation RAG-8/9: `rag_retrieval_service.retrieve_chunks` → `rag_generation_service.grounded_generate`/`_build_grounded_prompt`/`_stub_generated_cases` offline fallback → `_build_citations` linking every case to its source chunks → `_persist_cases`/`_map_coverage`), and the four integrity guards (`rag_redaction_service` classification-based redaction, `rag_faithfulness_service.evaluate` via Ollama/RAGAS backend, `rag_staleness_service.mark_cases_stale_for_source`, review/eval services). One mermaid component flow. Design invariants: traceability-over-fluency, per-project tenancy, offline-safe stub path. Indexed from the README; cross-linked to AI_QUALITY/SECURITY/DATABASE_SCHEMA.
- Docs-loop iteration 20 (architecture track; fifth pass — long tail).

### 2026-07-02 — User Guide: "Defects & promotion"

- **New `user-guide/defects.md`** — the defect lifecycle, verified against the implementation: the /defects queue + KPIs (open defects, mean-time-to-resolve, escape rate, auto-link-rule-misses, last-sync — labels from `DefectsPage.tsx`), promoting a cluster (`defect_promotion_service.promote_cluster` — evidence bundle carried, `_composite_to_severity` bands CRITICAL≥70/HIGH≥50/MEDIUM≥30/LOW, owner resolution from assignment memory) and its tie to the `DEFECT_CREATED` triage status, issue-tracker auto-linking with the explicit `AI_OFFLINE_MODE` hard-kill-switch-above-integration-flags note, and duplicate detection (`compute_dup_fingerprint` normalization). Ends with a promotion routine. Indexed from the guide README.
- Docs-loop iteration 19 (user-doc track; fifth pass — into the specialized long tail).

### 2026-07-02 — Architecture docs: OBSERVABILITY.md — docs loop fourth pass concludes

- **New `architecture/OBSERVABILITY.md`** — the three signals plus the unusual fourth, verified against the implementation: domain-shaped Prometheus metrics (`core/metrics.py` ingestion/upload/AI instruments) with the versioned Grafana dashboards + alert rules under `infra/monitoring/`, opt-in OTEL tracing (`setup_tracing` OTLP HTTP; no-op unset, consistent with offline-first), the structlog kwargs-only convention (with the positional-%s-TypeError-inside-except incident class), **agent decision logs** (`BaseAgent.log_decision` → `agent_stage_results.decision_log` checkpoints + OTEL span events → the Decision Trail UI), and the health surfaces (`/health/details` per-dependency probes + build provenance; MCP `health_check`; Integration Health page).
- **Docs loop fourth pass complete (iterations 16-18, PRs #312-#314):** FRONTEND.md + working-with-runs + this. Cumulative: **18 iterations, PRs #296-#314** — `architecture/` at **11 documents**, `user-guide/` at **11 files** (10 guides + index). Both tracks comprehensive at every tier.

### 2026-07-02 — User Guide: "Working with runs"

- **New `user-guide/working-with-runs.md`** — the run-centric investigation tools, labels verified against `RunsPage.tsx`/`RunComparePage.tsx`: the run list's per-run actions (Compare to previous, **Bisect from last green**, Deep-all-failed), run detail incl. the live-buffer read path (in-progress runs readable immediately) and failure→triage linkage, the compare view (Left-baseline/new-failures/key-differences, suite scoping, the shareable `mode=manual&left=&right=&suite=` URL), bisect-from-green explained as the regression shortcut (baseline = last green, right = latest failed, suite = failing run's; missing button = no green baseline in window), and a five-step red-build routine tying runs/bisect/clusters/flaky/deep-investigate together.
- Docs-loop iteration 17 (user-doc track; fourth pass).

### 2026-07-02 — Architecture docs: FRONTEND.md (SPA architecture)

- **New `architecture/FRONTEND.md`** — the last major runtime component without an architecture doc (its conventions previously lived only in gitignored local agent docs). Verified against the implementation: the pages→hooks→services→single-Axios layering (with the mermaid flow), the shared instance's interceptor policy (single-flight 401 refresh; the 422-toasts/401-404-quiet policy and array-detail flattening in `services/apiErrors.ts`; deploy-target-agnostic empty base URL), the four Zustand stores incl. the shared `timeWindowStore` and the primitive-selector discipline, the six-theme `[data-theme]` token system (`--color-*`/`--status-*(-bg/-bd)`/`--gate-*`) with the palette warn-ratchet, the enforcing CI (four `frontend.*` quality gates, the all-error eslint ratchet set + promotion regressions, strict tsc, rolldown build), and the patterns-worth-copying distilled from the ratchet burn-downs. Indexed from the README.
- Docs-loop iteration 16 (architecture track; fourth pass).

### 2026-07-03 — Design tokens: ExecutiveSummaryPanel migrated to per-theme status/gate tokens

- **`ExecutiveSummaryPanel.tsx` off raw Tailwind palette classes** — the release-readiness badge (GO/CONDITIONAL/NO_GO), the pass-rate / failed-count metric colours, the dominant-failure category legend and progress bar, and the baseline-comparison trend arrows now route through the per-theme `--gate-*`, `--status-*`, and `--color-*` CSS variables instead of hard-coded `emerald/amber/red/orange/sky/violet` palette classes. Every colour was mapped by semantic role (release-gate → `--gate-*`; passed/failed/broken/flaky → matching `--status-*`; category hues → `--status-failed`/`--status-broken`/`--color-cyan`/`--color-purple`/`--status-flaky`), fixing the light-theme legibility defect those raw classes caused. Drops the file's `no-restricted-syntax` palette warnings from 24 → 0.
- **New `ExecutiveSummaryPanel.test.tsx`** — regression guard asserting the rendered markup carries the token classes and contains no raw palette class for any status signal. The palette lint rule stays at `warn` (533 sites remain across the app).

### 2026-07-02 — User Guide: "Dashboards & analytics" — docs loop third pass concludes

- **New `user-guide/dashboards.md`** — the read-side pages with their counting semantics made explicit (the historical cross-page-confusion class): the shared global time window as the golden rule, Overview (honest weighted pass rate, day-granular "last run"), Trends, Coverage (unique-tests-across-window semantics + bulk suite-label hygiene), the Summary Report's **window-vs-latest aggregation modes** (window matches Coverage; latest is deliberately smaller), Value Metrics, and Search (index freshness tell), ending with a discrepancy cheat-sheet. Labels verified against the six pages.
- **Docs loop third pass complete (iterations 13-15, PRs #308-#310):** DEPLOYMENT.md + administration guide + this. Cumulative loop total: 15 iterations, PRs #296-#310 — `architecture/` at 9 documents, `user-guide/` at 10 files (9 guides + index).

### 2026-07-02 — User Guide: "Administration & settings"

- **New `user-guide/administration.md`** — the admin surface organized into three concerns: people & projects (/projects incl. archive + default-QA-lead auto-provision, /users roles and what they gate, profile, SSO), connecting to your world (API keys, integrations/GitHub/webhooks/notifications/digests — all explicitly subordinate to `AI_OFFLINE_MODE` — and Integration Health as the first stop for "the webhook didn't fire"), and operating the instance (AI settings/eval, feature flags incl. cache propagation, the audit dashboard, storage, project-data retention/cleanup, seed data, performance, billing). Ends with a new-instance checklist and a something-isn't-arriving triage order. Covers the ~19 /settings routes that had no documentation. Indexed from the guide README.
- Docs-loop iteration 14 (user-doc track).

### 2026-07-02 — Architecture docs: DEPLOYMENT.md (topologies)

- **New `architecture/DEPLOYMENT.md`** — consolidates the deployment story that was scattered across compose files, k8s overlays, and workflows, verified against the tree: the five compose variants (dev source-build, pinned-image `release` with demo/local-llm profiles + install.sh, dev-lite, gcp-vm, monitoring), the Kustomize base (incl. the workers-subscribe-to-all-shards invariant) with its nine overlays (env tiers, GKE/EKS/AKS, homelab NodePort-30500 agreement, air-gapped openshift-artifactory, self-hosted), the CI/CD pipelines (ci.yml merge gate + latest/sha images; release.yml semver + SBOM/provenance/digests; per-cloud deploys), and environment realities (TLS-interception CA injection, deploy-target-agnostic frontend, default ports). A choosing-a-topology matrix up front. Indexed from the README; cross-linked to SECURITY/INGESTION_SCALE/GETTING_STARTED.
- Docs-loop iteration 13 (architecture track; third pass).

### 2026-07-02 — User Guide: "AI features" — docs loop concludes with both tracks comprehensive

- **New `user-guide/ai-features.md`** — the AI surfaces from the user side, verified against the pages: the Intelligence Hub (cross-run insights + visible Intelligence spend) and Run Intelligence reports, Deep Investigation (tunable clustering threshold, per-cluster confidence, auto-draft defects / auto-create Jira, browsable past investigations), the Agent Pipeline page (queue the standard pipeline per run, Workflow Progress, Executive Summary), Ask AI chat, and the AI settings/evaluation pages — framed by the two governing rules (offline-by-default with graceful LLM→ML→rules degradation; provenance on every verdict) and a works-with-what matrix.
- **Docs loop complete (12 iterations, PRs #296-#306 + this one):** architecture set now covers FLAKY_INTELLIGENCE / RELEASE_GATE / AI_QUALITY / INGESTION_SCALE / SECURITY beyond the original three docs; the user guide covers ingestion, triage, flaky/quarantine, release gates, CLI/SDK/MCP, test management/ownership, and AI features.

### 2026-07-02 — Architecture docs: SECURITY.md (security & tenancy)

- **New `architecture/SECURITY.md`** — the security architecture, verified against the implementation: identity (JWT + single-flight refresh, API keys, token revocation via `core/token_revocation.py`), the `require_*_access` guard family with the verify-the-PROVIDED-id IDOR bug-class it codifies, the **HMAC-signed Redis membership cache** (`_sign_membership_cache` — forged/tampered entries fail HMAC and fall through to Postgres; sequence diagram included), tenancy defence-in-depth (project-scoped queries/fingerprints, per-project Chroma collections + semantic cache, per-project rate buckets), secure-by-default deployment (F1 `.env`/compose defaults + `critical_security_failures` startup fail-fast + dev-login-404, F2 authenticated Redis, F3 signed cache), offline-first as a provable no-egress property (with an honest scoping note on the knowledge-source domain allowlist), audit trail, and the quality-gate ratchets that enforce it all. Indexed from the README.
- Docs-loop iteration 11 (architecture track).

### 2026-07-02 — User Guide: "Test management & ownership"

- **New `user-guide/test-management.md`** — the catalog + routing layer, verified against the implementation: the Test Management page (case types/priorities/lifecycle draft→active→approved/deprecated, the details/history/reviews/comments/unautomated detail tabs, plans, the suites tab incl. owner assignment, review workflow incl. AI Review, duplicate detection, execution-aware counts), the two ownership layers (per-suite owners + /ownership pattern rules), and the exact auto-assignment resolution chain from `failed_test_assignment_service` (TestSuiteOwner → default QA lead → manager → unassigned) with its two guarantees (never overwrites human reassignments — NULL-only writes; isolated step that can't break finalization), plus the default-QA-lead auto-provision and the admin Team-scope consequence. Indexed from the guide README.
- Docs-loop iteration 10 (user-doc track; second pass beyond the initial index).

### 2026-07-02 — Architecture docs: INGESTION_SCALE.md (ingestion under load)

- **New `architecture/INGESTION_SCALE.md`** — the scalability design layered onto the basic ingest flow, verified against the implementation: the two-layer admission gate (`stream.py` — memory backpressure first for a cheap failure path, then the per-project rate limit with server-side project resolution so noisy sessions charge the right bucket), the O(1) LTRIM buffer cap, project-keyed shard queues (`ingestion_routing.py` — local shard derivation, ordering preservation, 1/N re-home on scale-out, the subscribe-to-ALL-shards operational invariant and its classic symptom), the mid-session drainer (per-run lock, real `started_at` resolution, default-suite application), DLQ + auto-recovery + heartbeat reaper, and the AI cost controls (debouncer grouping, per-project daily budget with mode downgrade, logged high-volume sampling). One end-to-end mermaid flowchart mapping every gate. Indexed from the README.
- Docs-loop iteration 9 (architecture track; second pass beyond the initial index).

### 2026-07-02 — User Guide: "CLI, SDKs & MCP" — user-guide roadmap COMPLETE

- **New `user-guide/cli-sdk-mcp.md`** — the three programmatic surfaces, verified against the implementation: the `testlookup` CLI's 11 command groups (from `cli/testlookup_cli/app.py`), the SDKs' shared `testlookup.yaml` config (real discovery order + precedence chain from `client/testlookup.yaml.example`, secrets-in-env guidance), framework notes (TestNG suite-name inheritance, pytest live-vs-junitxml), and the MCP server (49 `@mcp.tool`s counted from `mcp/tools/`, stdio for desktop clients / `--transport sse` on 8002, domain coverage incl. `get_test_step_flips` and dependency-health `health_check`), plus a choosing-a-surface matrix.
- **This completes the user-guide index**: all five planned guides now exist (getting-results-in, triaging-failures, flaky-tests, release-gates, cli-sdk-mcp). Docs-loop iteration 8 (user-doc track; final roadmap slice).

### 2026-07-02 — User Guide: "Flaky tests & quarantine"

- **New `user-guide/flaky-tests.md`** — the flaky workflow from the user side: flaky-as-verdict (regression-vs-flaky distinction, why regressions never get quarantine proposals), the surfaces (Flaky Coach incl. its specific-project requirement, the per-test "Cross-Run Step Flakiness" panel with its insufficient-history/stable states, the run-level roll-up, MCP `get_test_step_flips`), and the reviewed quarantine lifecycle as the /quarantine page presents it (Awaiting review / Active / Released / Rejected-expired; excluded from the gate signal only — tests keep running and recording; every transition audited), plus an operating rhythm and fix-the-step guidance. Indexed from the guide README.
- Docs-loop iteration 7 (user-doc track).

### 2026-07-02 — Architecture docs: AI_QUALITY.md (cache, corrections, evidence integrity)

- **New `architecture/AI_QUALITY.md`** — the AIQ mechanisms that keep the AI layer honest over time, verified against the implementation: the per-project tenant-scoped semantic cache (`semantic_cache.py` signature build / lookup / store / invalidate), the human-correction learning loop (`analysis_corrections.get_corrections_for_fingerprints` one batched project-scoped query + `build_corrected_analysis` short-circuit before rules/ML/LLM with `human_corrected` provenance; `feedback_service` → exact-signature cache invalidation), real-signal evidence grading (`_grade_anomaly_evidence` / `_grade_trace_evidence` replacing the fixed medium/80), and cluster integrity (`_MAX_NEIGHBOR_QUERY=100`, pure `_cluster_from_neighbours`, logged-not-silent truncation). Two mermaid diagrams (correction-loop sequence, precedence flow: correction > cache > routed computation). Indexed from the README; cross-linked to RELEASE_GATE / FLAKY_INTELLIGENCE / the user guide.
- Docs-loop iteration 6 (architecture track).

### 2026-07-02 — User Guide: "Release gates & policies"

- **New `user-guide/release-gates.md`** — the user-side companion to `architecture/RELEASE_GATE.md`: what GO / CONDITIONAL_GO / NO_GO mean, reading the Release Gate page (decision flow, Risk Dimension Breakdown, PDF/share), the plain-language computation model (BLOCK/WARN/INFO rule folding, fail-closed pass-rate band floor, honest recurrence dimension, quarantine exclusion), configuring policies at /policies (thresholds/weights/rules/bands, effective-policy fallback, WARN-then-promote guidance), reasoned+audited overrides that never erase the computed verdict, and gating a CI pipeline via API/CLI. Indexed from the guide README.
- Docs-loop iteration 5 (user-doc track).

### 2026-07-02 — Architecture docs: RELEASE_GATE.md (verdict decision architecture)

- **New `architecture/RELEASE_GATE.md`** — how a run becomes GO / CONDITIONAL_GO / NO_GO, verified against the implementation: the input signals (pass-rate bands via `classify_with_policy`, `criticality_service` weighted risk dimensions incl. the real-recurrence `hist_recurrence`, flaky verdicts + active-quarantine exclusion, failure clusters), the policy layer (`resolve_effective_policy` → `evaluate_policy`, monotonic-downward rule folding), the release council (`assemble_input_snapshot` determinism, `DIMENSION_METADATA` score×weight contributions, `_apply_band_floor` fail-closed floor + the CONDITIONAL↔CONDITIONAL_GO vocabulary seam, `_worse_verdict` composition, reasoned+audited `apply_override`), and the adjacent agent-stack gate (`eval_gate_service` manifests). One mermaid decision-flow diagram; indexed from the README.
- Docs-loop iteration 4 (architecture track).

### 2026-07-02 — User Guide: "Triaging failures"

- **New `user-guide/triaging-failures.md`** — the daily triage loop across its three surfaces (Failure Analysis / My Failures / run detail), verified against the implementation: cluster-first triage, category distribution + uncategorised classification, the evidence-backed regression-vs-flaky verdict, the auto-assignment inbox with the real `TriageStatus` vocabulary (PENDING_REVIEW inbox; REVIEWED_APPROVED / DEFECT_CREATED / WONT_FIX / AUTOMATION_SCRIPT_ISSUE / FLAKY_TEST resolutions per migration 0088), reassignment (suite owner / QA engineer), the Mine-vs-Team scope gotcha (default-QA-lead auto-assignment), and the AI-correction loop (INCORRECT rating + corrected category → patches the record, evicts the semantic cache, applies by fingerprint on future analyses). Hands off to flaky-coach/quarantine and defect promotion. Indexed from the guide README.
- Docs-loop iteration 3 (user-doc track).

### 2026-07-02 — Architecture docs: FLAKY_INTELLIGENCE.md (FLK P1-P6 subsystem)

- **New `architecture/FLAKY_INTELLIGENCE.md`** — the flaky-detection subsystem shipped across FLK P1-P6 post-dates the architecture set (PR #242) and had no architecture doc. Covers, verified against the implementation: the four evidence layers (`flaky_signals` intermittency/signatures, `flaky_statistics` Wilson-CI, `ml/flaky_confidence` feature vector — distinct from the 6-class triage classifier, `flaky_step_flip` over the retained `test_step_runs` history), verdict assembly in `flaky_investigator.build_flaky_verdict` (evidence-weighted `{is_flaky, confidence, likely_cause_code, evidence[]}`), the sentinel's verdict-reconciled recommendation (`_reconcile_recommendation` and the regression-vs-flaky bug class it fixes), the audited quarantine state machine (propose→approve/reject/expire→release), and the step-flip read path (per-test + run roll-up endpoints, MCP `get_test_step_flips`, SWR panels). Three mermaid diagrams: component map, quarantine stateDiagram, step-flip read sequence. Indexed from `architecture/README.md`.
- Docs-loop iteration 2 (architecture track; iteration 1 was the `user-guide/` foundation).

### 2026-07-02 — User Guide track started: guide index + "Getting results in"

- **New tracked `user-guide/` folder** — end-user documentation (QA engineers, SDETs, leads), distinct from operator docs (GETTING_STARTED) and internals (`architecture/`). `README.md` carries the core-concepts glossary (project/run/fingerprint/cluster/flaky/quarantine/release-gate/ownership), a navigation map of the dashboard grouped by area (verified against `App.tsx` routes), and the guide roadmap.
- **First workflow guide: `getting-results-in.md`** — the four ingestion paths, verified against code: dashboard upload (`/runs?upload=1`, `manual_upload` flag), REST (`POST /api/v1/ingest` JSON batch + `/api/v1/ingest/file` multipart, both `202` async, formats junit/testng/allure/cypress/playwright + `auto` detection), the `testlookup` CLI (real command groups from `cli/testlookup_cli/app.py`), and the live-streaming SDKs (session → events → `run_complete`, heartbeat recovery). Includes the post-ingest pipeline (fingerprint → cluster → analyze → assign → gate) and a troubleshooting section drawn from real incident patterns.
- Part of the iterative docs loop (alternating architecture/user-doc slices); next slices fill the planned guides in the index.

### 2026-07-02 — Design-audit palette-token ratchet: SuiteDetailPage tokenized

- **`SuiteDetailPage.tsx` palette classes → theme tokens** — the coverage suite-detail analytics page (KPI cards, the `StatusBadge`, the per-test and recent-run tables, the flaky pill and the missing-detail warning box) carried 26 raw Tailwind palette classes that bypass the per-theme CSS-token system and read poorly on the light themes. Converted each by semantic role: passed/pass-rate-good greens → `--status-passed(-bg/-bd)`; failed/error/last-error reds → `--status-failed(-bg/-bd)`; broken-status, mid pass-rate, and the "per-test rows missing" warning box ambers+oranges → `--status-broken(-bg/-bd)`; skipped-count ambers → `--status-skipped(-bg/-bd)`; the flaky pill → `--status-flaky(-bg/-bd)`. The non-flagged Recharts hex `fill`/`stroke` chart values and the `cyan` avg-duration KPI are outside the restricted set and left unchanged. The file's `no-restricted-syntax` warn count drops from 26 to 0 (tree total 613 → 587).
- **Regression coverage** — added `SuiteDetailPage.palette.test.ts`, a `?raw` source-text invariant (matching the sibling `TestManagementPage.palette` regression, so the production `tsc` build stays clean) asserting the page contains no flagged `(text|bg|border|ring)-(emerald|green|red|amber|yellow|orange|purple|blue)-N` palette class and does render the `var(--status-passed/-failed/-broken/-skipped)` tokens plus the flaky-pill `var(--status-flaky)` fg/bg/bd. Validated: `eslint src/pages/SuiteDetailPage.tsx` (0 warnings), `vitest run` for the page's test, plus full `lint` + `type-check` + `build`.

### 2026-07-02 — Design-audit palette-token ratchet: LiveExecutionPage tokenized

- **`LiveExecutionPage.tsx` palette classes → theme tokens** — the `/live` real-time dashboard (pass-rate helper, WS-status badge, LIVE badge, hero activity icon, pass/fail progress bars, session status/idle and failed-count cells, workflow subway cards, and the pipeline-events feed) carried 30 raw Tailwind palette classes — the highest count of any file in `src/` — that bypass the per-theme CSS-token system and read poorly on the light themes. Converted each by semantic role: pass/live/completed/success greens+emeralds → `--status-passed(-bg/-bd)`; failed/error reds → `--status-failed(-bg/-bd)`; connecting/idle-stale/warning ambers+yellows → `--status-broken(-bg/-bd)`. The non-flagged `violet` release badge, the `#93c5fd`/`rgba(68,147,248,…)` running-state accents, and the JS `style={{}}` subway-connector rgba values are outside the restricted set and left unchanged. The file's `no-restricted-syntax` warn count drops from 30 to 0 (tree total 613 → 583).
- **Regression coverage** — added `LiveExecutionPage.tokens.test.ts`, a `?raw` source-text invariant (matching the sibling `LiveExecutionPage.dedup.test.ts`, so the production `tsc` build stays clean) asserting the page renders `var(--status-passed/-failed/-broken)` tokens (fg + `-bg`/`-bd` variants) and no longer hard-codes the converted `text-/bg-/border-(emerald|red|amber|yellow)-N` palette classes. Validated: `eslint src/pages/LiveExecutionPage.tsx` (0 warnings), `vitest run` for the page's tests (5 passing), plus full `lint` + `type-check` + `build`.

### 2026-07-02 — Design-audit palette-token ratchet: AgentStatusPage tokenized

- **`AgentStatusPage.tsx` palette classes → theme tokens** — the agent-pipeline page (status maps, stage cards, live-run counts, observability tone helpers, alert surfaces, mode badges) carried 34 raw Tailwind palette classes — the highest count of any file in `src/` — that bypass the per-theme CSS-token system and read poorly on the light themes. Converted each by semantic role: completed/passed/OK greens → `--status-passed(-bg/-bd)`; failed/error/over-budget reds → `--status-failed(-bg/-bd)`; partial/degraded/warning/cost ambers → `--status-broken(-bg/-bd)`; skipped-count gold → `--status-skipped`. Mode-badge colours followed the same role mapping (`auto` → passed, `rules` → broken). The non-flagged `indigo` (token/LLM) and `cyan` (evidence/ML) accents are outside the restricted set and left unchanged. The file's `no-restricted-syntax` warn count drops from 34 to 0 (tree total 656 → 622).
- **Regression coverage** — updated the existing `AgentStatusPage.partial.test.tsx` `?raw` source invariant (BUG-004) to assert the `partial` degraded status renders the `--status-broken` token in `STATUS_COLOUR`, `STATUS_BG`, and the `StatusIcon` branch, replacing the old raw `amber-400` assertions so the regression still pins a *visible* degraded state. Validated: `eslint src/pages/AgentStatusPage.tsx` (0 warnings), `vitest run` for the page's tests (12 passing), plus full `lint` + `type-check` + `build`.

### 2026-07-02 — Design-audit palette-token ratchet: TestManagementPage tokenized

- **`TestManagementPage.tsx` palette classes → theme tokens** — the test-management page (test cases, plans, suites, review workflow, defect decisions, diff feed) carried 74 raw Tailwind palette classes — the highest count of any file in `src/` — that bypass the per-theme CSS-token system and read poorly on the light themes. Converted each by semantic role: passed/approved/added greens+emeralds → `--status-passed(-bg/-bd)`; failed/rejected/deleted/error reds → `--status-failed(-bg/-bd)`; blocked/high/broken/warning oranges+ambers → `--status-broken(-bg/-bd)`; medium-priority and skipped-count golds → `--status-skipped(-bg/-bd)`; AI + defect-decision purples → `--color-purple`; acknowledged/info blues → `--color-accent`. The non-flagged `violet` decision accent is outside the restricted set and left unchanged. The file's `no-restricted-syntax` warn count drops from 74 to 0 (tree total 721 → 647).
- **Regression coverage** — added `TestManagementPage.palette.test.ts`, a `?raw` source-text invariant (matching the sibling refs / suite-aggregates regressions, so the production `tsc` build stays clean) asserting the page contains no flagged `(text|bg|border)-(emerald|green|red|amber|yellow|orange|purple|blue)-N` palette class and does render `var(--status-*)`, `var(--color-purple)`, and `var(--color-accent)` tokens. Validated: `eslint src/pages/TestManagementPage.tsx` (0 warnings), `vitest run` for the page's tests, plus full `lint` + `type-check` + `build`.

### 2026-07-02 — Design-audit palette-token ratchet: WorkflowTimeline tokenized

- **`WorkflowTimeline.tsx` palette classes → theme tokens** — the workflow DAG component had 12 raw Tailwind palette classes (`text-emerald-300`/`bg-red-950`/`border-amber-700`/…) that bypass the per-theme CSS-token system and read poorly on the light themes. Converted each by semantic role: completed/cache-hit status and high-confidence badges → `--status-passed(-bg/-bd)`; failed status, stage-error surfaces, and low-confidence badges → `--status-failed(-bg/-bd)`; checkpoint-restored, mid-confidence, and cost badges → `--status-broken(-bg/-bd)`. Non-status accents (cyan/violet/indigo) are outside the restricted set and left unchanged. Also dropped a dead `useEffect` import. The file's `no-restricted-syntax` warn count drops from 12 to 0.
- **Regression coverage** — added a test to `WorkflowTimeline.test.tsx` asserting status/confidence/cost/event/error surfaces render `var(--status-*)` tokens and that no converted `(text|bg|border)-(emerald|red|amber)-N` palette class leaks. Validated: `eslint src/components/workflow/WorkflowTimeline.tsx` (0 warnings), `vitest run` for the component (5 passing), plus full `lint` + `type-check` + `build`.

### 2026-07-02 — no-non-null-assertion ratchet complete: rule promoted to error

- **`@typescript-eslint/no-non-null-assertion` → `error` (`frontend/eslint.config.js`)** — the last TypeScript rule sitting at `warn`. Every `x!` non-null assertion in `src/` had already been burned down to zero over successive ratchet passes (suite-keyed SWR fetchers moved to a `([, id]) => …` tuple-key destructure; `useLatestSuiteCompare` replaced `suiteName!.trim()` with an explicit `if (!suiteName) throw` guard; RightRail/FailureAnalysisPage/SuiteCasesPage/runComparisons cleared in prior passes). With the last site clean, the rule is promoted to `error`, guarding against reintroducing unchecked `!` assertions that silence the compiler's null/undefined analysis and turn a would-be type error into a runtime crash.
- **Regression coverage** — `noNonNullAssertion.promotion.test.ts` pins the config to `error` (and asserts it is not `warn`), mirroring the sibling `noExplicitAny.promotion.test.ts`. Validated: `eslint src` (0 errors), `tsc --noEmit`, and `vite build` all green.

### 2026-07-02 — deps: vite 6→8 + @vitejs/plugin-react 4→6 (coordinated major bump)

- **Coordinated frontend tooling bump** — supersedes Dependabot #263 (vite 8.1.0) and #266 (@vitejs/plugin-react 6.0.3), which each failed CI with a mutual `ERESOLVE`: plugin-react 4 peers vite ≤6 while vite 8 needs plugin-react 6, so neither could land alone. Bumped together with a single regenerated lockfile (vitest 4.1.5 already peers vite ^8; the lock shrinks ~900 lines as vite 8 replaces esbuild/rollup with rolldown).
- **One config migration** — vite 8 bundles with **rolldown**, which only accepts the *function* form of `build.rollupOptions.output.manualChunks` (the object form fails the build with "manualChunks is not a function"). `vite.config.ts` converts the vendor/charts/ui object mapping to an equivalent function.
- **Validated locally** — `tsc --noEmit`, eslint, `vite build` (chunks emit under the same vendor/charts/ui names), and the full vitest suite green with the new toolchain.

### 2026-07-02 — Design-audit Phase 1 quick wins: keyboard-reachable profile menu, honest metric trends, tokenized badges, palette-class lint guard

- **Accessibility fix (audit 1.4, `TopBar.tsx`)** — the profile dropdown opened via CSS `group-hover` only: no click handler, no `aria-expanded`, no keyboard path — **Sign out was unreachable by keyboard or touch**. It is now a real disclosure widget mirroring the notification bell: `<button aria-haspopup="menu" aria-expanded>` trigger, click toggles, outside-click and Escape close, menu carries `role="menu"`/`menuitem`.
- **MetricCard honesty + a11y (audit 1.5, `components/ui/MetricCard.tsx`)** — (1) removed the per-card `role="status" aria-live="polite"`: a dashboard renders 6–8 cards polling via SWR, so every refresh re-announced every value to screen readers. (2) New `positiveDirection?: 'up' | 'down'` prop (default `'up'`): trend color now keys on whether the direction is *good*, not on direction alone — "Failed tests trending down" renders green instead of red. (3) Accent/trend colors moved off raw `emerald/red/amber/purple` classes onto `--status-*` tokens. NOTE: the shared card currently has no production importers (pages hand-roll their own metric cards, e.g. a page-local `MetricCard` in `ValueMetricsPage` — consolidation gap tracked with audit 2.1); the prop readies the primitive for that migration.
- **Badge tokenization (audit 1.2, `index.css`)** — the five `.badge-*` classes sat on raw `emerald/red/amber/orange/purple-900/50` palette colors *directly below the token definitions*, bypassing the theme system (light-theme legibility defects). Rewritten onto `color: var(--status-X); background: var(--status-X-bg); border: var(--status-X-bd)` per the audit's spec (skipped now uses `--status-skipped`, not amber).
- **Palette-class ESLint ratchet (audit 1.3, `eslint.config.js`)** — new `no-restricted-syntax` guard flagging raw Tailwind palette classes (`text-emerald-400`, `bg-red-900/40`, …) in string literals, at **`warn`** (the audit says `error`, but ~640 sites remain — same ratchet pattern as `no-non-null-assertion`: burn down per-file, flip to `error` when the last site is clean). Avatar-color swatches are user data, exempt by intent. TopBar's own raw palette classes (notification icons/badge, sign-out red) were tokenized in passing.
- **Regression coverage** — `TopBar.test.tsx`: menu closed by default + opens on click with `aria-expanded` + Sign out fires logout + Escape closes. `MetricCard.test.tsx`: rising-trend-green default, `positiveDirection="down"` renders falling-Failed green / rising-Failed red, and a no-live-region guard. Full frontend suite + `tsc --noEmit` + eslint green.
- **Visual-smoke caveat:** badge/topbar colors now resolve per-theme; eyeball badges + notification panel in Signal and Lab (light) in the running app — same caveat class as tailwind-v4/recharts-3.

### 2026-07-02 — Release workflow surfaces released image digests + SBOM/provenance verification

- **Productionization (supply-chain, self-host adoption)** — the release workflow (`.github/workflows/release.yml`) already builds every GHCR image with `sbom: true` + `provenance: mode=max`, but the "Release image summary" only listed the moving *tags* — an operator had no way to see the content-addressable `sha256` digest to pin against, so that supply-chain metadata was effectively unreachable. A moving tag can be re-pushed to point at different bits; a digest cannot. Each `docker/build-push-action` step now carries an `id` (`build-backend`/`build-frontend`/`build-mcp`) so its pushed `outputs.digest` is referenceable, and the summary step surfaces the three `${REGISTRY}/${IMAGE_PREFIX}/<image>@<digest>` pin lines (digests passed via `env:`, not inlined into the shell) plus the `docker buildx imagetools inspect … --format '{{json .SBOM}}'` / `.Provenance` recipe to verify the attestation. No change to what is built or pushed — only what the release summary reports.
- **Regression coverage** — `backend/tests/test_release_workflow.py` gains four cases pinning the contract: every build-push step ships `sbom: true` + `provenance: mode=max`, every build-push step has an `id` (without which `outputs.digest` is unreferenceable), the summary surfaces each image's `steps.build-<image>.outputs.digest`, and it shows both the `@<digest>` pin form and the `imagetools inspect` verification command. Full release-workflow suite (14 tests) green; ruff clean.

### 2026-07-01 — self-host adoption: build provenance on /health/details

- **Build provenance surface (`GET /health/details`)** -- the details health report now carries a `build` block (`{revision, built_at}`) so a self-host operator can confirm exactly which commit and build a running container is — matching a pinned image digest back to its source — without shelling into the pod. The values come from two new settings, `BUILD_REVISION` and `BUILD_DATE`, injected at image-build time via new `backend/Dockerfile` ARGs (production stage) that `release.yml` wires from `github.sha` and a UTC build timestamp. Local/dev runs leave the env unset and are reported as `"unknown"` rather than an empty string. A small pure helper `build_provenance()` in `app/routers/health.py` centralises the fallback so the endpoint stays declarative. No behaviour change to the probes or the K8s liveness/readiness contracts. Regression coverage: `backend/tests/test_health_build_provenance.py` pins the default-`unknown` fallback, verbatim pass-through of injected values, and that `/health/details` includes the `build` block (probes stubbed — network-free).

### 2026-07-01 — MCP `health_check` surfaces real dependency health

- **MCP tool fix (`mcp/tools/auth.py`, `health_check`)** -- the tool advertises returning the backend's per-dependency health, but it read the dependency map from a `data.get("dependencies", {})` key that `GET /health/details` never returns (the endpoint nests each probe under `checks`, e.g. `{"postgres": {"status": "ok"}, …}`). So the "Dependencies" section was silently always empty — an operator or agent running `health_check` saw status/version but no postgres/mongo/redis health, defeating the tool's purpose. The formatting is pulled into a pure module-level `_render_health(data)` helper (mirroring `runs._render_step_flips`) that reads `checks`, surfaces each probe's nested `status` (not the raw dict), and adds an `Uptime` line. Two always-wrong output fields are dropped: `LLM Provider` and `Offline Mode` were read from `llm_provider`/`offline_mode` keys the endpoint does not emit, so they rendered a constant `unknown`/`False`; the docstring is corrected to match what is actually returned. Also removes a dead `await api.get("/health/ready")` pre-call whose result was immediately overwritten by the `/health/details` fetch (one wasted request per invocation). No backend change; reuses the existing endpoint. Regression coverage: a static test pins that the tool reads `checks` and not `dependencies`, plus a functional `TestHealthRenderer` suite exercising `_render_health` against a realistic payload (per-dependency status surfaced, nested dicts not leaked, missing/malformed/empty `checks` degrade without raising).

### 2026-06-30 — no-non-null-assertion ratchet: SuiteCasesPage test cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes the lone non-null assertion from `src/pages/SuiteCasesPage.test.tsx`. The bulk-Move test reached the action-bar's button via `bar.querySelector('button')!` — `Element.querySelector` returns `Element | null`, so the bare `!` asserted non-null without checking it. Replaced with an explicit guard that genuinely narrows the union (`const barButton = bar.querySelector('button'); if (barButton === null) throw new Error('Bulk actions bar has no button')`), mirroring the `closest('tr')` guard introduced for the sibling `MyFailuresPage.test.tsx` site in #253. Same failure surface, with a clearer message if the bar ever renders without a button. Semantic no-op. The rule stays at `warn`; the remaining sites all live in the areas covered by the open ratchet PRs (#251 `workflowPresets`, #252 `RunsPage.test`, #253 `MyFailuresPage`, #269 run-compare/onboarding hooks, #270 `RightRail`/`FailureAnalysisPage`, #271 `runComparisons.test`), so it flips to `error` only once those land and the last site is clean. Regression coverage: the existing `SuiteCasesPage.test.tsx` bulk-Move case passes unchanged through the narrowing — the point being the refactor must not alter what it asserts.

### 2026-06-30 — no-non-null-assertion ratchet: runComparisons compare-href test cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes the lone non-null assertion in `src/utils/runComparisons.test.ts`. The "URL-encodes special characters in the suite name" case dereferenced `href!` on the result of `buildCompareWithPreviousHref` (typed `string | null`) before `.toMatch(...)`. Replaced the bare assertion with an explicit `expect(href).not.toBeNull()` plus a narrowing `if (href === null) throw …` guard, so the match runs against a `string`. This mirrors the `expectHref` narrowing approach already used for the sibling `RunsPage.test.tsx` bisect-href cases and slightly strengthens the test (it now asserts non-null rather than assuming it). No behaviour change. The rule stays at `warn` until the remaining sites across other files are cleared.

### 2026-06-29 — no-non-null-assertion ratchet: RightRail + FailureAnalysisPage cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- clears the two remaining production-source non-null assertions outside of areas already covered by open ratchet PRs. In `RightRail.tsx`'s `ShippingThisWeek`, the `.filter(r => r.dueAt && …)` followed by `new Date(r.dueAt!)` is replaced with a narrowing type-predicate filter (`(r): r is DerivedRelease & { dueAt: string } => Boolean(r.dueAt) && r.stage === 'in_progress'`), so the subsequent `new Date(r.dueAt)` needs no assertion. In `FailureAnalysisPage.tsx`, the per-test failure-rate headline dereferenced `flakyMatch!.total_runs` guarded only by `perTestRatePct !== null`; the guard now reads `perTestRatePct !== null && flakyMatch`, which narrows `flakyMatch` to defined (it was already a precondition of `perTestRatePct` being non-null, so no behaviour change). The rule stays at `warn` until the remaining sites (test/e2e files) are cleared. Regression coverage: a new `RightRail.test.tsx` pins that `ShippingThisWeek` keeps in-progress releases with an in-window due date, drops releases with a `null` due date (the narrowed-filter guard), and drops non-in-progress releases.

### 2026-06-29 — no-non-null-assertion ratchet: run-compare/onboarding hooks cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes the 2 non-null assertions in `src/hooks/useRunCompare.ts` and `src/hooks/useOnboardingStatus.ts` (project-wide count 22 → 20). `useOnboardingStatus` keyed its SWR fetch on `projectId ? ['onboarding-status', projectId] : null` then dereferenced `projectId!` inside the fetcher; it now destructures the tuple key (`([, id]: [string, string]) => onboardingService.detectProgress(id)`), the same pattern already used by `useSuites`/`useOwnershipRules`, so the id arrives as a typed `string` instead of an asserted reach back to the outer optional param. `useLatestSuiteCompare` called `compareLatestSuite(suiteName!.trim())` behind a `Boolean(suiteName?.trim())` gate TypeScript can't relate back to the nullable param; it now uses an explicit `if (!suiteName) throw` guard inside the fetcher (mirroring the sibling `useRunCompare` fetcher in the same file) that narrows `suiteName` to `string`. Both are semantic no-ops. The rule stays at `warn` until the remaining 20 sites across other files are cleared. Regression coverage: a new `src/hooks/useRunCompare.test.ts` pins that `useLatestSuiteCompare` fetches with the trimmed suite name, skips the fetch when the suite name is blank/null, and that `useRunCompare` still gates on both ids being present and distinct; the existing `useOnboardingStatus.test.ts` already asserts the fetcher is called with the keyed project id.

### 2026-06-28 — no-non-null-assertion ratchet: MyFailuresPage cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes both non-null assertions in the MyFailuresPage area (project-wide count 22 → 20). In `MyFailuresPage.tsx` the reassign modal rendered the suite-owner choice under `{options?.suite_owner && …}` but its `onSelect` closure dereferenced `options.suite_owner!.user_id` — the `&&` guard narrows the property at render time but TypeScript cannot carry that narrowing into the deferred callback. The fix hoists `const suiteOwner = options?.suite_owner` once and uses it for the guard, the `option` prop, the `checked` compare, and the `onSelect`, so the closure captures an already-narrowed local instead of reaching back through the optional chain. In `MyFailuresPage.test.tsx` the row-click test used `row.closest('tr')!`; `closest` returns `Element | null`, so the assertion is replaced with an explicit `if (!tr) throw` guard that narrows the union (a semantic no-op that surfaces a clearer failure if the row ever lacks a `<tr>` ancestor). No behaviour change. The rule stays at `warn` until the remaining 20 sites across other files are cleared. Regression coverage: the existing `MyFailuresPage.test.tsx` suite (10 cases, including the row-navigation case that exercises the rewritten guard) passes unchanged.

### 2026-06-28 — no-non-null-assertion ratchet: RunsPage bisect-href tests cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes all 10 non-null assertions from `RunsPage.test.tsx` (the largest single remaining concentration; project-wide count 22 → 12). The `buildBisectHref` URL-introspection cases asserted `expect(href).not.toBeNull()` and then dereferenced `href!` on every `.toContain(...)` — but `toBeNull()` does not narrow the `string | null` union for TypeScript, so each access needed a `!`. The fix introduces a small `expectHref()` test helper that builds the href, asserts non-null, and `throw`s on null — narrowing the return to `string` so every `href!` becomes a plain `href` with no behaviour or coverage change. The two intentional null-return cases keep calling `buildBisectHref` directly. The rule stays at `warn` until the remaining sites across other files are cleared. Regression coverage: the existing 7 `buildBisectHref` cases still pass unchanged via the narrowing helper.

### 2026-06-27 — no-non-null-assertion ratchet: buildValueMetricsWorkflow cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes all 4 non-null assertions from `buildValueMetricsWorkflow` in `src/components/workflow/workflowPresets.ts` (project-wide warning count 22 → 18). The `roi_calculation` stage built its `result_data` and `confidence_score` from `metrics!` because the gating condition was `hasValueSignal` — a *separate* boolean (`!!metrics && (…positive signals…)`) whose implication that `metrics` is non-null TypeScript cannot relate back to the `ValueMetrics | null | undefined` param. The fix changes each gating ternary from `hasValueSignal ? … : null` to `metrics && hasValueSignal ? … : null`, which narrows `metrics` to non-null in the true branch so every `metrics!.x` becomes a plain `metrics.x`. This is a semantic no-op (`hasValueSignal` already requires `metrics` to be truthy) but makes the no-signal path explicit instead of asserted. No behaviour change. The rule stays at `warn` until the remaining 18 sites across other files are cleared. Regression coverage: three new `workflowPresets.test.ts` cases pin that a `null` metrics payload and an all-zero payload both leave the roi stage `pending` with `result_data`/`confidence_score` null (never dereferencing `metrics`), and that a payload with a value signal surfaces the three roi fields plus the computed confidence.

### 2026-07-02 — Surface 422 validation errors in the UI (kill the "empty page, no error toast" footgun)

- **Bug fix (UX/diagnosability)** — the shared Axios response interceptor (`services/api.ts`) suppressed toasts for **422** alongside 401/404. A 422 (typically a strict Pydantic enum over a `String(N)` column, or any request-shape mismatch) therefore failed **silently**: SWR yielded `undefined`, the page rendered a blank state, and the user got zero feedback — the documented "empty page, no error toast" footgun that has cost repeated live-probe debugging sessions. 422 responses now toast; 401 (handled by the single-flight token-refresh flow) and 404 (frequently an expected "no data yet") stay quiet.
- **Message extraction fixed for FastAPI 422 bodies** — a 422 `detail` is an **array** of `{loc, msg, type}` items, so the old `detail || message` would have rendered `[object Object]`. New pure helpers in `services/apiErrors.ts`: `shouldToastError(status)` (the visibility policy) and `extractErrorMessage(detail, fallback)` (string details as-is; validation arrays joined as "Validation error: field — msg; …" with the `body` prefix stripped; graceful fallback on empty/malformed detail).
- **Regression coverage** — new `apiErrors.test.ts` (8 tests) pins 422-now-toasts (and 401/404 stay quiet, network errors surface), string/array/empty detail extraction, and the no-`[object Object]` guarantee. Full frontend suite green; `tsc --noEmit` + eslint clean.

### 2026-07-02 — Dashboard honesty: remove fabricated numbers and dead CTAs from the Overview hero

- **UX/trust fix** — the flagship Overview dashboard presented several **invented** figures and **dead** controls as if they were real, which badly undercuts a *test-intelligence* product on its first screen. Removed/corrected:
  - **Fabricated "readiness confidence %"** (`readinessConfidence()` derived a number from pass-rate and clamped it by verdict bucket — e.g. GO → 92–100%). Deleted. The hero meter, the big number, and the reason card now show the **real weighted pass rate** (`avg_pass_rate_7d`), honestly labelled "Pass rate"; the lede copy no longer references a confidence value the backend doesn't produce.
  - **Fabricated workflow-ribbon literals** — hardcoded `$0.31` "cost", `5 evidence items`, and per-stage durations (`1.2s`/`3.4s`/`0.8s`/`1.6s`) — all removed (the dashboard has no per-run cost / evidence-count / stage-timing signal). The per-stage "N evidence" pills are replaced with **real status tokens** derived from the summary (run count, verdict label, baseline window, actions-to-fix); the Readiness stage now shows the real pass rate instead of the invented confidence.
  - **Dead CTAs** — the hero "Run quality workflow" button (empty placeholder `onClick`), the "View evidence" button (no-op `TODO`), the handler-less "Override gate" button (+ its always-`false` `canOverrideGate` / unused `onViewEvidence` props), and the inert "Compact/Detail" ribbon toggle — all removed rather than shipped as buttons that do nothing.
- **Regression coverage** — a new `OverviewPage.test.tsx` case asserts the fabricated literals (`$0.31`, "evidence items", "Readiness confidence") and the dead CTAs ("Run quality workflow", "View evidence", "Override gate") are **gone**, and that the real "Pass rate" label renders in their place. Existing Overview cases (verdict label, Pending-at-zero) still pass. Full frontend suite (432 tests) green; `tsc --noEmit` + eslint clean.
- **Note:** this trims the hero to what the backend actually provides. If any of these are meant to be wired to real endpoints later (a workflow-trigger action, an evidence drawer, a gate-override flow, real per-stage telemetry), they should return behind a feature flag with live data rather than as static fiction.

### 2026-07-02 — UX fixes: "Last run —" timezone bug + cross-page time-window drift

- **Bug fix (dashboard "Last run" timezone)** — the Overview page computed its "Last run" label with `timeAgo(\`${trendData.at(-1).date}T00:00:00Z\`)`, forcing the day-bucketed trend date to **UTC midnight**. For users east/west of UTC, `Date.now() − <that UTC instant>` could go negative near a day boundary, hitting `timeAgo`'s `ms < 0` guard and rendering **"Last run —"** even with a fresh run — the dashboard looked stale for a whole class of users. Replaced the buggy local `timeAgo` with a new shared `formatters.dayTimeAgo`, which parses the date at **local** midnight (no `Z`) and compares by whole calendar days → "today" / "yesterday" / "N d ago". Day-granular output also stops pretending sub-day ("X min ago") precision the day-bucketed trend never had; a future-rounded bucket resolves to "today" rather than being discarded.
- **Bug fix (cross-page time-window drift)** — every page's window picker snaps the global `timeWindowStore` value (default **7** days) to its own allowed set, but `RunsPage` used `[1, 6, 14, 30, 90, 0]` — an anomalous **6** where every other page (Overview/Coverage/Trends/FailureAnalysis/…) uses **7**. So a user on "7 days" elsewhere silently landed on "6 days" on the Runs page (`snapToAllowed(7, [1,6,…]) → 6`), and touching it there wrote 6 back globally, drifting other pages. Changed Runs' `WINDOWS`/`WINDOW_LABELS` `6 → 7` (`"Last 7 days"`), so the shared 7-day window stays 7 everywhere.
- **Regression coverage** — `formatters.test.ts` pins `dayTimeAgo`: today's date → "today" (not "—") regardless of timezone (the bug), yesterday/older by calendar days, a future bucket → "today", and empty/unparseable → "—". `timeWindowStore.test.ts` updated to assert the corrected Runs set keeps `snapToAllowed(7, …) === 7` (no drift). 17 tests across the touched files green; `tsc --noEmit` + eslint clean.

### 2026-07-02 — Root-cause correction learning loop: human reclassifications now stick and stop being repeated

- **Feature (AI quality, #6)** — closes the last open half of the human-feedback loop. The **capture** side already existed (`feedback_service.submit_feedback` writes an `AIFeedback` row and patches the *current* `AIAnalysis` when a QA engineer marks a verdict INCORRECT and supplies the right `failure_category`/`root_cause_summary`), but nothing fed that correction back: the next run recomputed the same logical test from scratch and could repeat the exact mistake, and the semantic analysis cache kept serving the stale wrong verdict. Now:
  - **Apply on re-analysis.** New `services/analysis_corrections.py`: `get_corrections_for_fingerprints` resolves, in ONE batched project-scoped query, the most-recent human correction per `test_fingerprint`; `build_corrected_analysis` (pure) turns it into the analysis-result dict the pipeline would otherwise compute. The analysis agent fetches corrections *fresh* each run (so a just-submitted correction takes effect immediately — not via the cached metadata) and `_analyse_one` applies one by fingerprint **before** the rules/ML/LLM dispatch, short-circuiting the known-wrong path with a high-confidence, `human_corrected`-provenanced verdict that flows through the same post-processing/audit. Best-effort — any lookup failure falls through to normal analysis.
  - **Invalidate the cache on correction.** `semantic_cache.semantic_cache_invalidate` evicts the exact error-signature entry (same `doc_id` scheme as the store); `feedback_service` calls it when a correction is recorded so the stale verdict isn't re-served to this or a similar test.
  - This turns per-user corrections into compounding accuracy (the same principle the flaky-confidence model uses for quarantine decisions), deterministically — no model retraining required.
- **Regression coverage** — `test_analysis_corrections.py`: the pure builder (shape, FLAKY→`is_flaky`, default summary), the batched resolver's most-recent-per-fingerprint dedup + empty/falsy handling (fake DB), `semantic_cache_invalidate` deleting the exact matching `doc_id` (and no-op without a signature), and `feedback_service` invoking invalidation on an INCORRECT correction. The `get_corrections_for_fingerprints` JOIN (AIFeedback→AIAnalysis→TestCase→TestRun) was additionally **validated end-to-end against real Postgres**: most-recent-per-fingerprint across runs, project scoping (no cross-tenant leakage), and CORRECT-rating exclusion. 97 analysis-agent/router/feedback/architectural tests + the quality gate (15 guards) green; the pre-existing `run_triage_agent` line in the analysis-router baseline was re-pinned after the insertion shifted its line number (same allowlisted call, no new bypass).
- **Deferred follow-up:** ML *retraining* of the failure-category classifier from accumulated corrections (the infrastructure — `AIFeedback` + `build_dataset_from_feedback` — already exists; this loop delivers the immediate compounding-accuracy value deterministically first), and the `defect_promotion` recurrence sibling.

### 2026-07-02 — AI intelligence quality: unfragment failure clusters + grade log evidence by real signal

- **Bug fix (failure clustering, cost)** — `embed_and_cluster` looked up each error's nearest neighbours with `n_results=min(len(errors), 5)`, so its greedy star-clustering could absorb at most 4 similar errors per seed. A single root cause hitting many tests (e.g. a 30-test outage with near-identical errors) therefore **fragmented into ≥6 clusters** instead of one — defeating the O(n)→O(k) purpose of clustering and multiplying downstream per-cluster deep-investigation LLM cost. The lookup cap is raised to `_MAX_NEIGHBOR_QUERY = 100` (large enough that realistic same-cause outages cluster into one, bounded so the O(n·cap) distance matrix can't blow up; beyond it, truncation is logged, not silent). The clustering loop is extracted into a pure `_cluster_from_neighbours` helper (which also replaces the prior O(n)-per-neighbour `ids.index()` with an O(1) id→index map).
- **Bug fix (log-evidence weighting, trust)** — `log_intelligence_agent` emitted its distributed-trace and log-anomaly evidence at a **fixed** `strength="medium", contribution=80` regardless of the actual finding — even a "no anomaly detected" null result was recorded as medium-strength/80, which made the whole evidence-weighting machinery (AIQ-P3 confidence aggregation) cosmetic. Two pure graders now map strength+contribution to the real signal: `_grade_anomaly_evidence` reads `anomaly_detected` + the per-level spike `ratio` (no spike → weak/15; ≥6× → strong/85; else medium/60), and `_grade_trace_evidence` reads the trace's ERROR/FATAL step count (error chain → medium/strong scaled by depth; context-only or empty → weak). Both degrade gracefully (weak) when fields are absent (e.g. Splunk disabled).
- **Regression coverage** — `test_ai_quality_clustering_and_log_evidence.py`: a 30-member same-cause set collapses into one cluster with complete neighbours (and *fragments* under a truncated-5 window — pinning why the cap mattered), distinct causes stay separate, and the anomaly/trace graders map real signal to strength/contribution with graceful degradation. The existing `test_agent_contract_outputs.py` success case was updated to feed realistic tool outputs (error-bearing trace + severe spike) and assert high aggregate confidence rather than the old fixed 80. 232 clustering/evidence/contract/agent tests green; ruff clean.
- **Remaining in the AI-quality thread (deferred):** the root-cause-correction learning loop (a feature — capture user reclassifications as labels + retrain, its own effort), and the `defect_promotion._dim_scores_from_analyses` recurrence sibling (needs history plumbing).

### 2026-07-02 — Release-risk scoring: hist_recurrence measures real recurrence, not AI confidence (double-count fixed)

- **Bug fix (release verdict, risk scoring)** — `criticality_service.compute_dimension_scores` computed the `hist_recurrence` dimension as the ratio of *low-confidence* analyses (`confidence_score < 40`), while `diagnosis_conf` is `100 − avg_confidence`. Both rise when the AI is merely *unsure*, so the same AI-uncertainty signal was folded into composite release risk through **two** weighted terms (`RISK_WEIGHT_HIST_RECURRENCE` + `RISK_WEIGHT_DIAGNOSIS_CONF`), systematically inflating risk whenever confidence was low. Worse, `hist_recurrence`'s own definition ("known issues recurring without resolution") described *recurrence*, not confidence — so the "Why this score?" tooltip misrepresented the dimension. `hist_recurrence` now measures **actual recurrence**: the fraction of this run's failing tests that are **not** new regressions (i.e. still failing from a prior run). It reuses data already passed to the function — `regression_tests` holds the new-failure `test_case_id`s (from `AnomalyAgent`); the complement over the analysed failures is the recurrence set — so there is **no new DB query or plumbing**. `diagnosis_conf` is now the *only* dimension reflecting AI uncertainty, and `regression_likely` (new regressions) / `hist_recurrence` (recurring failures) become complementary rather than one being a confidence proxy.
- **Regression coverage** — `test_criticality_service.py` gains: `hist_recurrence` is 0 when all failures are new regressions, rises toward full when none are, and is monotonic in between; and a direct double-count guard — two analyses sets identical except `confidence_score` now yield the **same** `hist_recurrence` (only `diagnosis_conf` differs). Existing release-council / policy / epic-11 suites (245 tests across the touched paths) green; ruff clean. No test pinned the old low-confidence relationship.
- **Deferred (sibling):** `defect_promotion_service._dim_scores_from_analyses` has the same confidence-based `hist_recurrence`, but it's a secondary "approximate" per-cluster scorer that receives only a cluster's `AIAnalysis` list (no regression/recurrence input) — a correct fix there needs history plumbing into defect promotion, so it's left for a follow-up rather than bundled into this release-verdict change.

### 2026-07-02 — Flaky-sentinel verdict soundness: onset detection + recommendation reconciled with the verdict

- **Bug fix (flaky onset)** — `flaky_sentinel_agent` computed the flakiness onset as the *first status flip* in a test's history, which mislabels a single permanent transition as flaky onset: a test that was broken then fixed (`[F,F,F,P,P,P]`) reported the **fix** build as `flaky_since_build`, and a plain regression (`[P,P,P,F,F,F]`) or a lone recent blip (`[P,P,P,P,P,F]`) was likewise treated as an onset. A new pure helper `_detect_flaky_onset` requires the flip to *begin a genuinely oscillating region* (a flip after which the history flips at least once more); when there's no sustained oscillation it returns `None` and the agent reports `flaky_since_build = "unknown"` and skips the build-diff lookup rather than blaming the wrong build.
- **Bug fix (recommendation ↔ verdict contradiction)** — the human-facing `recommendation` was a raw failure-rate ladder (`>0.5 → QUARANTINE`, `>0.25 → INVESTIGATE`, else MONITOR) computed *independently* of the structured `verdict`. So a consistently-failing real bug (high failure rate, low intermittency → `verdict.is_flaky == False`, `likely_cause_code == "likely_regression"`) was told to "QUARANTINE" while its own verdict said it wasn't flaky — a self-contradiction that also risks **hiding a real defect** behind a quarantine. A new pure helper `_reconcile_recommendation(failure_rate, verdict)` derives the recommendation from the verdict: a non-flaky persistent regression is flagged as a bug to *fix* (not quarantine), insufficient evidence → MONITOR, and only a confirmed-flaky test gets the severity-by-rate quarantine ladder. The quarantine *proposal* side-effect (`propose_quarantine`) is now likewise gated on `verdict.is_flaky`, so the sentinel never proposes quarantining a confirmed regression. The `verdict` (ML + Wilson + intermittency) already distinguished these cases (`flaky_investigator.build_flaky_verdict`); this just makes the sentinel's human output and side-effects honour it.
- **Regression coverage** — `test_flaky_sentinel_onset_and_recommendation.py` pins the onset heuristic across fix/regression/blip/oscillation histories and the recommendation↔verdict invariant (a not-flaky verdict never yields a QUARANTINE recommendation at any failure rate). Existing flaky/agent-workflow/coach suites (190 tests) green; ruff clean.

### 2026-07-02 — Tier-1 perf: ingestion flush-storm removed + release-gate/flaky-count indexes (P1/P2/P3)

- **Perf (P1 — ingestion flush storm)** — `ingestion._insert_step` flushed the DB after **every** step node (up to `_MAX_STEP_NODES=2000` per test) inside the single ingestion transaction, solely to populate `TestStep.id` (a Python-side `default=uuid.uuid4` that only fires at flush) for child `parent_step_id` and attachment/step-run FKs. A run with hundreds of stepful tests meant thousands of round-trips. The PK is now assigned up front (`step_id = uuid.uuid4()`), so the whole step tree is buffered and inserted in the caller's single flush/commit — no per-node flush. Insert ordering is safe: nodes are added depth-first (parent before child), which SQLAlchemy preserves for same-mapper INSERTs, and Postgres evaluates the self-referential FK (`parent_step_id → test_steps.id`) at statement end so intra-batch parent references resolve. **Validated end-to-end against real Postgres**: a nested depth-3 tree with attachments persists with correct parent/child/attachment FK linkage under a single flush.
- **Perf (P3 — release-gate defect index)** — added composite index `ix_defects_project_status_severity` on `defects (project_id, resolution_status, severity)`. `count_open_critical_defects` (release council + dashboard readiness, the hot path introduced in the B1 fix) filters exactly these three columns on every /release-gate + /overview render; the table previously had only the single-column `ix_defects_project_id`, leaving status/severity as a heap filter.
- **Perf (P2 — flaky-count covering index)** — added `ix_history_fingerprint_date_status` on `test_case_history (test_fingerprint, created_at, status)`. The windowed flaky-count scan (`_count_flaky_tests`, from the B3 fix) partitions/orders by `(test_fingerprint, created_at)` then filters `status IN ('FAILED','BROKEN')`; extending the existing 2-col index with `status` makes that scan index-only. The 2-col `ix_history_fingerprint_date` is kept (smaller; used by other queries).
- **Migration 0098** (`CONCURRENTLY` + `autocommit_block` + `if_not_exists`/`if_exists`, matching migrations 0082/0090) so the indexes build without a write lock on the high-write `defects`/`test_case_history` tables. ORM `__table_args__` updated to declare both indexes (model↔schema parity). **Validated on real Postgres**: `alembic upgrade head` creates both indexes, `downgrade 0097` removes them; single head remains 0098.
- **Deferred (P5 — summary-report query parallelism):** the review suggested `asyncio.gather`-ing the ~6 independent summary-report queries, but they share one injected `AsyncSession`, and SQLAlchemy forbids concurrent operations on a single session — true parallelism needs separate sessions/connections per coroutine, which departs from the transaction-boundary ratchet (services get one injected session, router owns the commit). Left for a dedicated change rather than shipped as a risky "perf" tweak.
- **Regression coverage** — `test_granular_steps_ingestion.py::test_insert_step_does_not_flush_per_node` pins that `_insert_step` builds the tree with correct FK linkage even when `db.flush()` raises (no per-node flush); the existing granular-step suite (which asserts parent/child/attachment linkage through a no-op-flush fake) continues to pass. `test_migration_0098_indexes.py` pins the revision chain, index specs, CONCURRENTLY/IF-EXISTS safety, and ORM parity (mirrors `test_migration_0090_indexes.py`). Quality gate (15 guards incl. single-alembic-head + downgrade-implemented) green.

### 2026-07-01 — Secure-by-default deployment: locked-down .env defaults, authenticated Redis, tamper-evident authz cache (F1/F2/F3)

- **Security fix (F1 — insecure default deployment mode)** — a naive `cp .env.example .env && docker compose up` (i.e. without running `scripts/gen-dev-env.sh`) ran with `APP_ENV=development`, which disables the startup fail-fast guard (`Settings.critical_security_failures` only fires for production/staging) — so the app happily started with the default, guessable `JWT_SECRET_KEY`, and `DEV_AUTO_LOGIN_ENABLED=true` exposed `POST /api/v1/auth/dev-login`, which mints an **ADMIN** JWT with no credentials. `.env.example` now ships `APP_ENV=production`, `DEV_AUTO_LOGIN_ENABLED=false`, `APP_DEBUG=false` — that path now fails closed (refuses to start on default secrets; dev-login is a 404). `scripts/gen-dev-env.sh` (the real local/demo entry point — `make dev` / `make quickstart`) restores `APP_ENV=development` + `DEV_AUTO_LOGIN_ENABLED=true` for local convenience, alongside the other secrets it already generates. `docker-compose.yml` / `docker-compose.release.yml` / `docker-compose.dev-lite.yml` / `docker-compose.gcp-vm.yml` now default `APP_ENV`/`DEV_AUTO_LOGIN_ENABLED` to the secure values (`${APP_ENV:-production}`, `${DEV_AUTO_LOGIN_ENABLED:-false}`) as a second layer, for a `.env` that exists but omits those keys. The `Settings()` Python class default is unchanged (`development`) — it only governs bare-metal/test contexts that never reach the `main.py` lifespan startup guard (confirmed the integration test harness's `ASGITransport` never triggers FastAPI lifespan), so this stays out of scope and doesn't disturb the documented local pytest flow.
- **Security fix (F2 — unauthenticated, host-exposed Redis)** — Redis is the Celery broker (task injection = code execution), the JWT-revocation store, and the membership authz cache, but shipped with no `--requirepass` and published `6379:6379` to the host in both `docker-compose.yml` and `docker-compose.release.yml` (contradicting `THREAT_MODEL.md`'s claim that Redis has password auth). Added a `REDIS_PASSWORD` env var: when set, the redis service's `command` conditionally appends `--requirepass` (shell `${VAR:+...}`, empty = no auth, backward-compatible for a purely-internal Redis), the healthcheck authenticates with the same password, and `REDIS_URL`/`CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` embed it for the backend/worker/beat/flower services. The published port is now loopback-only (`127.0.0.1:6379:6379`) — internal services already reach Redis by hostname over the compose network. Applied to `docker-compose.yml`, `docker-compose.release.yml`, `docker-compose.dev-lite.yml`, and the `docker-compose.gcp-vm.yml` override (which needed its own `REDIS_URL`/`CELERY_*` update since compose merges override `environment:` lists by key, and its unauthenticated values would otherwise clobber the base file's). `scripts/gen-dev-env.sh` now generates a `REDIS_PASSWORD` and keeps it in sync with the connection URLs, same as it already does for Postgres/Mongo. `backend/app/core/config.py` gained the `REDIS_PASSWORD` field (documentation/tooling; the app itself only needs the password embedded in `REDIS_URL`/`CELERY_*`). **Validated end-to-end against real `docker compose up`**: unauthenticated `redis-cli ping` → `NOAUTH Authentication required.` when a password is set; authenticated ping succeeds; healthcheck passes; the empty-password path still works unauthenticated (backward compat) with a passing healthcheck.
- **Security fix (F3 — membership-cache poisoning)** — `get_accessible_project_ids` read the project-membership authz scope from the `membership:{user_id}` Redis key and returned it **without revalidating against Postgres on a hit**, so anyone able to write Redis (trivial before F2, and still a valid defense-in-depth concern — e.g. a compromised cache instance, a stale/plain legacy value, or any future writable-Redis regression) could set `membership:{their_id}` to arbitrary project UUIDs and read across tenants, defeating the `project_id` WHERE-clause isolation. Cache values are now HMAC-SHA256 signed (keyed by `APP_SECRET_KEY`, bound to the user id so a validly-signed blob can't be replayed under another user's key). A poisoned, forged, cross-user, or legacy/plain (pre-signing) entry fails verification and is treated as a cache miss — the DB stays authoritative and the entry is refreshed in the new signed format — so the cache can never *widen* a user's scope even if Redis is fully writable by an attacker.
- **Regression coverage** — `backend/tests/test_membership_cache_signing.py` (round-trip, tampered payload, forged signature, cross-user replay, legacy plain-list rejection, garbage rejection — all for F3); `backend/tests/test_env_example_secure_defaults.py` (pins `.env.example`'s `APP_ENV=production` / `DEV_AUTO_LOGIN_ENABLED=false` / `APP_DEBUG=false` / `REDIS_PASSWORD` presence — F1/F2); `backend/tests/test_quickstart_env_gen.py` extended with `REDIS_PASSWORD`/`REDIS_URL`/`CELERY_*` sync assertions and a test pinning that `gen-dev-env.sh` restores the local dev-login convenience the secure defaults disable. `test_bl01_tenant_isolation.py` / `tests/core/test_resolve_project_scope.py` / `test_bl02_secret_hardening.py` (pre-existing) all still green — the existing "dev is never blocked" contract in `test_bl02_secret_hardening.py` is preserved since the Settings() class default and `validate_production_secrets()` gating are unchanged; only the shipped `.env`/compose *values* moved to secure defaults.

### 2026-07-01 — Release-gate hard caps now force NO_GO (B4)

- **Bug fix (release gate, correctness)** — a tripped policy hard cap only stepped the pass-rate band down by one notch, and yellow still maps to verdict `GO`, so an otherwise-green run breaching a `max_p0_defects` / `max_flaky_count` / `max_new_failures_24h` cap still shipped `GO`. A "hard cap" that can't block an otherwise-passing run is advisory at best. Now, in `metrics_service.classify_with_policy`, **any** cap breach forces `verdict = NO_GO` (band pinned `red`) regardless of the pass-rate band; the per-cap one-band-step logic is removed. The `downgrades` audit list is unchanged (records exactly which caps fired), so the UI still explains *why* a green pass-rate produced a NO_GO. This flows through both consumers unchanged: `_apply_band_floor` (release council — `_worse_verdict` already takes the stricter of composite vs band) and the dashboard readiness path (`NO_GO → RED`). Combined with the earlier B1 fix (the P0 cap now actually counts open CRITICAL defects), the default zero-tolerance P0 policy now genuinely blocks a release on any open critical defect.
- **Regression coverage** — `test_classify_with_policy.py` updated: the three individual-cap tests now assert `NO_GO`/`red` (renamed `*_forces_no_go`), the stacked-cap and floor cases already expected `NO_GO`, and the disabled-cap (`max_*=0`) and band-edge cases are unaffected (no cap fires). `test_release_council_band_floor.py::test_band_floor_p0_cap_forces_no_go` asserts an open CRITICAL defect over the cap drives the council verdict to `NO_GO` end-to-end (B1 + B4 together). 100 release-gate tests green; ruff clean.

### 2026-07-01 — Empty runs no longer grade PASSED (B5) + flaky count now windowed (B3)

- **Bug fix (run status, B5)** — a finalized run that executed nothing (an empty or parse-failed upload, or a fully-skipped suite) was marked `PASSED` with `pass_rate=0.0`, so it flowed into trends and the release gate as a *green* run. Both the file/finalize path (`ingestion._update_run_aggregates`) and the live/close path (`stream_service.upsert_test_run`) applied `status = FAILED if failed+broken>0 else PASSED`. Fixed via a shared `services/run_status.terminal_run_status(executed, failed, broken)` helper: a run with `executed == 0` now grades **`STOPPED`** — neither PASSED (masquerades as green) nor FAILED (inflates failures / falsely blocks). `STOPPED` is already excluded from the "last green" baseline (`run_diff`) and the FAILED buckets (`audit_dashboard`), and already has a neutral badge in the frontend (`StatusBadge`). Only applied at finalize/close — the live drainer still creates the in-progress row as `IN_PROGRESS` (verified it never calls `upsert_test_run`).
- **Bug fix (flaky count, B3)** — `metrics_service._count_flaky_tests` documented "last 10 runs" but its SQL had **no per-fingerprint window** (it aggregated over *all* `test_case_history` with `HAVING COUNT(*) >= 5`), so a test flaky months ago but stable since could never lose the flaky flag — the dashboard/summary count only ever grew. It also filtered `status = 'FAILED'` only, silently excluding `BROKEN` (the canonical failed set is `{FAILED, BROKEN}`, per `flaky_signals._FAILED_STATUSES`). Fixed with a `ROW_NUMBER() OVER (PARTITION BY test_fingerprint ORDER BY created_at DESC)` window bounded to the last `_FLAKY_WINDOW_RUNS` (10) executions per fingerprint, and the failed filter now covers `('FAILED','BROKEN')`. The `flaky_rate_pct` clamp stays (flaky window ≠ time window, so >100% is still possible). Validated end-to-end against a real Postgres: the window drops a recovered test, and BROKEN-only flakiness is counted.
- **Regression coverage** — `backend/tests/test_run_status.py` (pure `terminal_run_status` truth table: empty→STOPPED, broken→FAILED, all-pass→PASSED) and `backend/tests/test_count_flaky_tests_window.py` (pins the ROW_NUMBER window, `rn <= 10`, `('FAILED','BROKEN')`, and preserved project/effective-suite scoping — the SQL-shape idiom the repo already uses for raw-SQL queries, since the integration harness mocks the session). Full summary/flaky/finalize/stream/status suite (146 tests across the touched files) green; ruff clean.

### 2026-07-01 — Release-gate P0 hard cap fixed (was dead code)

- **Bug fix (release gate, correctness)** — the `max_p0_defects` hard cap never fired. The release council and the dashboard readiness path both counted open defects with `Defect.severity == "P0"`, but the defect-promotion pipeline only ever writes `CRITICAL/HIGH/MEDIUM/LOW` (`defect_promotion_service._composite_to_severity`; the P0→CRITICAL map already existed unused in `analytics_service._SEVERITY_FROM_PRIORITY`). No row ever matched, so `active_defects_p0` was always `0` and a release with unlimited open **CRITICAL** defects was never downgraded — the gate's headline defect blocker failed open (B1). Compounding it, the two paths counted *different* defect sets: the deep `get_release_council` path filtered `severity == "P0"` (→ 0) while `_synthesize_release_council` passed an *all-severities* open-defect total into the same cap input, so the quick-look and deep verdicts for one run could disagree (B2).
- **Fix** — centralised the mapping in `metrics_service.P0_DEFECT_SEVERITY = "CRITICAL"` + a shared `count_open_critical_defects(db, project_id)` helper, and routed every call site through it. `_apply_band_floor` now resolves the open-CRITICAL count *internally* (dropping its `open_defects` parameter) so no caller can pass the wrong severity set again; the metrics readiness path uses the same helper. The synth path keeps its all-severities `open_defects` total only for the human-readable reasoning string, not the cap. No policy/threshold semantics changed — the cap simply now counts the defects it was always meant to.
- **Regression coverage** — new `backend/tests/test_release_gate_p0_cap_severity.py` pins the producer↔consumer severity vocabulary (`_SEVERITY_FROM_PRIORITY["P0"] == _composite_to_severity(80.0) == P0_DEFECT_SEVERITY`, and that the literal `"P0"` is never a stored severity) and asserts the helper's compiled SQL filters `CRITICAL`/`OPEN`, not `'P0'`. `test_release_council_band_floor.py` updated to the new internal-count contract (mocks the defect-count query; the P0-cap test now exercises the real severity path). Full release/defect/metrics suite (125 tests) green.

### 2026-06-27 — no-non-null-assertion ratchet: SummaryReportPage cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes all 11 non-null assertions from `SummaryReportPage.tsx` (the largest single concentration; project-wide count 37 → 26). The KPI/counts section dereferenced `totals!` because the surrounding `!hasData` ternary couldn't narrow the `data?.totals` union — `hasData` was a separate boolean TypeScript can't relate back to `totals`. The fix adds an explicit `totals == null` clause to the empty-state guard (`{!hasData || totals == null ? <EmptyState/> : …}`), so the data branch narrows `totals` to non-null and every `totals!.x` becomes a plain `totals.x`. This is not redundant: the backend can short-circuit to an empty envelope where `totals` is absent entirely (the same shape the all-projects gate alludes to), so the runtime guard is load-bearing. No behaviour change. The rule stays at `warn` until the remaining 26 sites across other files are cleared. Regression coverage: a new `SummaryReportPage.test.tsx` case pins that a null-`totals` envelope renders the no-data empty state without crashing on the dereference path.

### 2026-06-27 — no-non-null-assertion ratchet: useSuites hooks cleared

- **Frontend lint hardening (slice, `@typescript-eslint/no-non-null-assertion`)** -- removes all 4 non-null assertions from `src/hooks/useSuites.ts` (project-wide count 28 → 24). Each of `useSuite`, `useSuiteTestCases`, `useCanonicalRuns`, and `useCanonicalCase` keys its SWR fetch on a conditional tuple (`id ? ['suite', id] : null`) and then dereferenced the id with `id!` inside the fetcher — a non-null assertion justified only by the fact that SWR never calls the fetcher when the key is `null`. The assertions are replaced with the tuple-key destructure the codebase already uses elsewhere (`([, id]: readonly [string, string]) => suitesService.get(id)`, mirroring `useOwnershipRules`/`useAuditDashboard`/`usePolicyEditor`): SWR passes the key array to the fetcher, so the id arrives as a typed `string` element instead of an asserted reach back to the outer optional param. No behaviour change. The rule stays at `warn` until the remaining 24 sites across other files are cleared. Regression coverage: a new `src/hooks/useSuites.test.ts` pins that each fetcher calls its service method with the destructured id and that the fetch is skipped (key `null`) when no id is supplied — the invariant that makes dropping the assertion sound.

### 2026-06-27 — no-explicit-any promoted to error

- **Frontend lint hardening (`@typescript-eslint/no-explicit-any` → `error`)** -- flips the rule from `warn` to `error` in `frontend/eslint.config.js`, so any new untyped `any` in app code now fails CI. The rule already had zero active warnings; the one remaining production source case was `useTableSort`'s generic constraint `T extends Record<string, any>`, which existed only so the hook could index rows by a runtime sort key. The constraint is dropped to a plain `<T>` (interface-typed rows like `ManagedTestCase` still satisfy it, and `sorted` keeps its `T[]` element type), and the dynamic index now goes through a localized `as Record<string, unknown>` view — the sort body already narrows each value with runtime `typeof` checks and a `String()` fallback, so no precision is lost. The only `any` left in the tree is a test-only `File.prototype.arrayBuffer` mock that carries a scoped, justified disable. No behaviour change. Regression coverage: `src/hooks/noExplicitAny.promotion.test.ts` pins the rule to `error` and the `useTableSort` typing via `?raw` source-text invariants (mirroring the sibling `react-hooks` promotion regressions).

### 2026-06-26 — exhaustive-deps cleared → react-hooks ratchet complete

- **Frontend hooks lint hardening (ratchet complete)** -- clears the final two `react-hooks/exhaustive-deps` warnings and flips the rule from `warn` to `error` in `frontend/eslint.config.js`, so any reintroduction now fails CI. This was the last react-hooks v7 rule still at `warn`; with `set-state-in-effect`, `refs`, and `immutability` already promoted, the entire React Compiler rule set is now enforced as errors. Both remaining sites were the same shape — an SWR-derived array recreated as a fresh literal every render and then used as a `useMemo` dependency, defeating the downstream memo: `ReleasesPage`'s `releases = data?.items ?? []` and `TestManagementPage`'s `userList = (users ?? []) as UserSummary[]`. Each was wrapped in its own `useMemo` (the fix the rule itself recommends), matching the `useMemo(() => … ?? [], […])` pattern already present in those files (e.g. `projectMembers`). No behaviour change — the arrays hold the same values, only their identity is now stable across renders. Regression coverage: `src/pages/exhaustiveDeps.promotion.test.ts` pins the rule to `error` and the two memoized derivations via `?raw` source-text invariants (mirroring the sibling `react-hooks/refs` regression).

### 2026-06-26 — Cross-run step-flip report surfaced via MCP

- **New MCP tool `get_test_step_flips`** (`mcp/tools/runs.py`) -- surfaces the existing FLK-P6 cross-run step-flip read (`GET /api/v1/runs/{run_id}/tests/{test_id}/step-flips`) on the MCP surface, so an agent can ask *which step oscillated PASSED↔FAILED across runs* for one test instead of only seeing the latest-run snapshot from `get_test_case(include_steps=True)`. Read-only; reuses the on-main per-test endpoint (no new backend code). A pure `_render_step_flips` helper mirrors the web `StepFlipPanel` semantics — it distinguishes "not enough history" (`runs_analyzed < 2`) from "stable" from a flicker table (step, flips, runs observed, current status) and degrades gracefully on a malformed payload. Brings the MCP tool count to 49 (all names still globally unique). Regression coverage: static checks plus functional tests of the renderer's three states in `mcp/tests/test_mcp_server.py`.

### 2026-06-25 — FLK-P6 slice 5: run-level step-flip roll-up surfaced on Run Intelligence

Extends the cross-run step-flip intelligence (FLK-P6) from per-test (slice 4) to the **whole run**. Slice 4 answered "which step oscillated for *this* test"; this slice answers "which *tests* in this run have a flickering step" — so a QA engineer triaging a run sees the step-level flakiness across it without opening each test.

- `GET /api/v1/runs/{run_id}/step-flips` (new, `backend/app/routers/runs.py`) — READ-ONLY. Resolves the run's `project_id` from the **provided** `run_id` (guarded by `require_run_access` — IDOR ratchet), then returns a run-level roll-up. 404 when the run doesn't exist. No DB writes (router owns the no-op transaction).
- `runs_service.step_flip_report_for_run()` (new) — gathers the run's fingerprint-anchored tests (fingerprints are unique per run) and defers to the existing batched, project-scoped `step_flip_report_by_fingerprint`. Pure read; never N+1 (one resolve + the batched read's two queries). Returns only the tests with at least one flip (sorted by total flips desc, then name) plus `tests_analyzed` / `tests_with_flips` / `total_flips`; a `max_tests` cap bounds the fingerprints fed to the batched read and surfaces a `truncated` flag (no silent cap). Covered by `backend/tests/test_step_flip_for_run.py`.
- `src/components/runs/RunStepFlipCard.tsx` (new) + `useRunStepFlips` SWR hook (`src/hooks/useRuns.ts`) + `runsService.getRunStepFlips` + `RunStepFlips`/`RunStepFlipTest` types — a "Step-level flakiness" card in the Run Intelligence right column (`src/pages/RunIntelligencePage.tsx`). Lists each flickering test with its top oscillating step (flip count, current PASSED/FAILED) linking to that test's cross-run detail, a stable empty state when no step flips, and a truncation note. Lazy SWR fetch, no set-state-in-effect. Covered by `src/components/runs/RunStepFlipCard.test.tsx`.

### 2026-06-25 — CI check: validate Mermaid diagrams render

- **New CI job "Docs — Mermaid diagrams"** -- parses every `` ```mermaid `` block in tracked markdown with the Mermaid grammar (`mermaid.parse()` under jsdom, the same parse GitHub runs before rendering), so a broken diagram fails the build instead of silently shipping an "Unable to render rich display" box. Self-contained validator in `scripts/mermaid-check/` (pinned `mermaid` + `jsdom`, committed lockfile); enumerates files via `git ls-files` so it checks exactly the tracked docs GitHub renders. Run locally with `cd scripts/mermaid-check && npm ci && node validate.mjs`. Currently 18/18 blocks across 37 markdown files parse cleanly. (Caught and motivated by a real "Unable to render" bug in the Celery topology diagram, fixed in the same day's docs.)

### 2026-06-25 — set-state-in-effect cleared everywhere → rule promoted to error

- **Frontend hooks lint hardening (ratchet complete)** -- clears the final 20 `react-hooks/set-state-in-effect` sites across 14 files and flips the rule from `warn` to `error` in `frontend/eslint.config.js`, so any reintroduction now fails CI. Three mechanical patterns were applied: (1) pagination/selection resets and default-selection syncs now run **during render via previous-value tracking** (the React-recommended way to reset state on a dependency change) instead of a cascading effect -- `RunsPage`, `IntelligenceHubPage`, `LiveExecutionPage`, `WorkflowTimeline`, `AgentStatusPage`, `AgentWorkflowPage`, `DecisionTrailDrawer`, `DefectPromotionModal`, `RunIntelligencePage`, `useAnalyticsView`; (2) data-fetch effects moved to **SWR hooks** -- `ProjectMembersTab` and `TestManagementPage` reuse `useProjectMembers` / inline `useSWR` (with optimistic `mutate` for local edits), `AIEvalDashboardPage` swaps its tab-driven `loadX` effects for SWR; (3) two genuine effects that must stay -- a network token re-verification in `ProtectedRoute` and a coordinated one-time deep-link expand+scroll in `TestManagementPage` -- carry scoped `eslint-disable` lines with justification. Regression coverage: new `WorkflowTimeline` test pinning the default-stage-follow behaviour, plus `ProjectMembersTab.test.tsx` updated for the SWR data path. Lint/type-check/build/411 vitest tests all green.

### 2026-06-25 — DigestsPage off set-state-in-effect (SWR hooks)

- **Frontend hooks lint hardening (slice)** -- clears the `react-hooks/set-state-in-effect` warning on `DigestsPage` (63 -> 62) by replacing its single tab-driven `loadData` effect with two SWR hooks (`useDigestData.ts`: `useDigestSubscriptions` / `useDigestSavedViews`). SWR now owns the loading/data/error state declaratively, gating each query on the active tab and re-keying saved views on the selected project; the page refreshes after a create/pause/resume/delete via the hooks' `mutate` instead of an imperative loader. Adds a regression test (`useDigestData.test.ts`). The rule stays at `warn` until the remaining sites are cleared.

### 2026-06-24 — Semver release workflow (versioned GHCR images)

- **Tag-driven release pipeline** -- new `.github/workflows/release.yml` fires on a `v*.*.*` tag push and builds + pushes the three app images (`backend`, `frontend`, `mcp`) to `ghcr.io/anandtopu/testlookup/*` tagged with the full semver and its moving aliases: `v1.2.3` -> `:v1.2.3 :1.2.3 :1.2 :1 :sha-<sha>`. This makes `TESTLOOKUP_VERSION` real -- `docker-compose.release.yml` self-hosters can now pin a published release tag instead of the moving `latest` (still published by the main-push CI). Reuses the proven build/login/SDK-staging steps from `ci.yml`, derives tags via `docker/metadata-action` semver patterns, attaches an SBOM + max provenance per image, and warns when the pushed tag disagrees with the tracked `VERSION` file. Contract pinned by `backend/tests/test_release_workflow.py` (semver-tag trigger, all three images, semver tag derivation, GHCR push, `packages:write`, GITHUB_TOKEN login).

### Why we built this

Engineering teams running automated tests get fragmented artifacts: JUnit XML, Allure outputs, flaky failures, pipeline status. Existing tools help visualise results, but teams still burn hours on manual triage, clustering, root-cause analysis, and release decisions. The problem is worse in regulated or private environments that can't depend on cloud-only AI services.

TestLookup is our answer: a local-first test failure intelligence engine that ingests results, clusters failures, explains probable root causes, and produces release-risk signals -- all offline-capable, and all accessible through a dashboard, REST API, CLI, and MCP server so AI assistants can query test health directly.

### Added

- **No-clone self-host release artifacts** -- `docker-compose.release.yml` pulls pinned pre-built images (`ghcr.io/anandtopu/testlookup/{backend,frontend,mcp}:${TESTLOOKUP_VERSION:-latest}`) instead of building from source, with demo/local-LLM profiles; `install.sh` is a remote one-liner (`curl ... | bash`) that downloads the stack, generates a local `.env`, and brings it up; a `VERSION` file anchors the release tag. Contract pinned by `backend/tests/test_release_artifacts.py` (image-not-build, no host-source bind-mounts, dev/release service parity, `install.sh` bash-syntax + fetch list).
- **Multi-framework ingestion** -- JUnit XML, TestNG, Allure JSON, Cypress, Playwright, pytest
- **Three analysis modes** -- rules (pattern match), ML (scikit-learn HistGradientBoosting), LLM (Ollama ReAct agent), auto (smart fallback chain)
- **Run Intelligence** -- single-pane summary with failure clusters, regression diff, risk score
- **Release gate** -- GO / CONDITIONAL_GO / NO_GO with explainable reasons and QA Lead override audit trail
- **Decision trail** -- per-run "why did the AI do that" drawer with stage filter and full-text search
- **Two-run compare** -- side-by-side diff with classification (new failures, regressions, duration spikes, renamed tests via fuzzy pairing)
- **Flaky quarantine** -- detection, QA Lead approval, active quarantine, nightly recheck, release/re-quarantine state machine
- **Perf regression detection** -- per-test duration baselines (Welford algorithm) with 3-sigma spike detection
- **Feature flags** -- per-project / per-role / rollout-percent gates with audit history
- **MCP server** -- 48 tools, 9 resources, 6 prompt workflows for AI assistant integration
- **CLI** -- 11 command groups, multi-profile auth, table/JSON/YAML output
- **Dashboards** -- 30+ customizable analytics widgets with drag-and-drop layout
- **Live streaming** -- real-time WebSocket dashboard during test execution
- **Jira integration** -- auto-promote failure clusters with 7-dimension severity scoring
- **PII redaction** -- auto-scrub at all system boundaries
- **Email notifications** -- async SMTP with daily/weekly digest subscriptions
- **Observability** -- OpenTelemetry traces, Prometheus metrics (including 7 Tier 0-2 counters), 5 alerting rules
- **Outbound webhooks** -- HMAC-signed event fan-out with retry, DLQ, and replay
- **Sample datasets** -- JUnit, Allure, Cypress, Playwright test result fixtures under `samples/`
- **Benchmark harness** -- classification accuracy + throughput benchmarks with methodology doc
- **Threat model** -- data flow, offline guarantees, auth boundaries, PII scope

### Experimental (flag-off by default)

- Deep investigation multi-agent pipeline (LangGraph)
- RAG knowledge-grounded test case generation
- RAG faithfulness guardrails (Ollama/Ragas pluggable evaluator)
- LLM cost budget with auto-downgrade enforcement
- GitHub Checks integration (PAT-based check-run posting)
- Release compliance pack (SOX/HIPAA/SOC 2 audit ZIP)
- Weekly retro digest (Monday-morning automated retrospective)
- Team value metrics split via service ownership rules
- Continuous fine-tuning pipeline
- Semantic/hybrid search (ChromaDB)

### Added (2026-06-24 — FLK-P6 slice 4: cross-run step-flip surfaced on the test-case detail page)

Surfaces the previously backend-only cross-run step-flip intelligence (FLK-P6) in the UI. Earlier slices retained per-run step outcomes (`test_step_runs`, migration 0097), added the pure `compute_step_flips`, and the project-scoped DB read `step_flip_report_by_fingerprint`; this slice wires a read API and a per-test panel so a QA engineer can see *which step* oscillated PASSED↔FAILED across runs — pointing at one flickering step rather than a whole-test verdict.

- `GET /api/v1/runs/{run_id}/tests/{test_id}/step-flips` (new, `backend/app/routers/runs.py`) — READ-ONLY. Resolves the test's `test_fingerprint` + `project_id` from the **provided** `run_id` (guarded by `require_run_access` — IDOR ratchet), then returns the cross-run step-flip report. 404 when the `test_id` doesn't belong to the run; an empty-window "insufficient history" report otherwise. No DB writes (router owns the no-op transaction).
- `runs_service.step_flip_report_for_test()` (new) — thin per-test wrapper that resolves `(run_id, test_id)` → `(fingerprint, project_id)` and defers to the existing batched, project-scoped `step_flip_report_by_fingerprint`. Pure read; never N+1 (resolve + anchor + step-runs). Covered by `backend/tests/test_step_flip_for_test.py`.
- `src/components/runs/StepFlipPanel.tsx` (new) + `useTestStepFlips` SWR hook (`src/hooks/useRuns.ts`) + `runsService.getTestStepFlips` + `TestStepFlips`/`StepFlipReport` types — a "Cross-Run Step Flakiness" section on the test-case detail page (`src/pages/TestCasePage.tsx`), beneath the existing History/Steps panels. Renders the per-step flip roll-up (flip count, current PASSED/FAILED status, regression vs recovery) with distinct empty states for "no history yet" vs "stable across N runs". Lazy SWR fetch, no set-state-in-effect. Covered by `src/components/runs/StepFlipPanel.test.tsx`.

### Changed (2026-06-24 — Frontend lint: PolicyEditorPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site (23 → 21 warnings):

- `src/pages/PolicyEditorPage.tsx` — replaced the list-mode `loadPolicies` effect and the edit-mode `getPolicy` effect with SWR hooks. The edit-mode case seeds *editable* form fields (name/description/projectId/doc/isDraft) from the loaded policy, so instead of an effect it uses the render-phase "adjust state during render" pattern guarded by a `seededPolicyId` tracker (runs once per loaded policy). Dropped the now-unused local `error` state; load failures derive from the hooks' `isError`. Behaviour unchanged.
- `src/hooks/usePolicyEditor.ts` (new) — `usePolicies(enabled)` (list mode; exposes `mutate` for post-deactivate refresh) and `usePolicy(policyId, enabled)` (edit mode; re-keys on the id). `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics.

### Changed (2026-06-24 — Frontend lint: AuditDashboardPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site (24 → 23 warnings):

- `src/pages/settings/AuditDashboardPage.tsx` — replaced the tab-keyed load-on-mount effects (one fetching audit categories on mount, one driving `events`/`total`/`obs`/`loading` off the active tab and filter selection) with three SWR hooks, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively and re-keys on the filter params (project, category, day window), so the page no longer threads a `loadEvents` callback through an effect dependency array. Behaviour unchanged.
- `src/hooks/useAuditDashboard.ts` (new) — `useAuditCategories()` always fetches (it feeds the events-tab filter dropdown); `useAuditEvents(params, enabled)` is gated by the active events tab and re-fetches when project/category/days change; `useProjectObservability(projectId, enabled)` is gated by the active observability tab plus a selected project, mirroring the old `if (tab === ...)` / `projectId` branches. `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics.

Added `src/hooks/useAuditDashboard.test.ts` (categories fetch and surface data; a failed load reports `isError` with an empty list; events re-key on filters and omit empty project/category params; events and observability skip the fetch when their tab is inactive; observability additionally skips without a selected project). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, `AuditDashboardPage` no longer flags the rule), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining sites are cleared in later slices.

### Changed (2026-06-23 — Frontend lint: IntegrationHealthPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/settings/IntegrationHealthPage.tsx` — replaced the tab-keyed load-on-mount `useEffect(() => { load() }, [load])` (which drove `statuses`/`trends`/`history`/`loading` from inside the effect) with three SWR hooks, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively. The post-probe `load()` call becomes `refreshIntegrationHealth()` (SWR `mutate`), revalidating whichever dataset is mounted exactly as before. Behaviour unchanged.
- `src/hooks/useIntegrationHealth.ts` (new) — `useIntegrationStatus()` always fetches (it feeds the status tab, the history-tab provider dropdown, and the always-rendered workflow timeline); `useHealthTrends(enabled)` and `useProviderHistory(provider, enabled)` are gated by the active tab (and history additionally by a selected provider), mirroring the old `if (tab === ...)` branches. `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior single-shot fetch semantics. `refreshIntegrationHealth()` revalidates all three keys via a global mutate predicate.

Added `src/hooks/useIntegrationHealth.test.ts` (status fetches and surfaces data; a failed load reports `isError` with an empty list; trends/history skip the fetch when their tab is inactive; history additionally skips without a selected provider). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, `IntegrationHealthPage` no longer flags the rule), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining sites are cleared in later slices.

### Changed (2026-06-23 — Deps: recharts 2 → 3)

Bumped `recharts` from `^2.13.3` to `^3.9.0` (supersedes Dependabot #211, which failed `tsc`). recharts 3 tightened the `Tooltip` `formatter` type to `Formatter<ValueType, NameType>`, so the two call sites that annotated the value param as `number` no longer compiled (`DefectDonut.tsx`, `SuiteDetailPage.tsx`). Dropped the manual annotations so recharts' own param types flow through; behaviour is unchanged (the donut tooltip still shows `[value, name]`, the pass-rate area still shows `"<v>% / Pass Rate"`). recharts 3 also restructured its internals (now backed by a redux/immer store instead of lodash/prop-types/react-smooth), which is reflected in the lockfile. Validated locally: `type-check`, `lint` (0 errors), `build`, and the chart/SuiteDetail vitest suites all green.

> **Manual smoke required before relying on this in prod:** recharts 3 changes some default rendering (animations, axis/category defaults, responsive sizing). The build and unit tests pass, but the actual chart visuals (Overview trend, DefectDonut, PassRateGauge, TrendChart, SuiteDetail pass-rate area) should be eyeballed in the running app — same caveat class as the tailwind v4 bump.

### Changed (2026-06-23 — Deps: web-vitals 4 → 5)

Bumped `web-vitals` from `^4.2.4` to `^5.3.0` (supersedes Dependabot #219, which failed type-check on the removed export). v5 retired FID (First Input Delay) in favour of INP (Interaction to Next Paint) and removed the `onFID` export, so `src/hooks/useWebVitals.ts` no longer registers it — the remaining five Core Web Vitals (CLS, LCP, FCP, TTFB, INP) are unchanged. Added `src/hooks/useWebVitals.test.ts` to guard the registered metric set. Validated locally: `type-check`, `lint` (0 errors), `build`, and the new vitest suite all green.

### Changed (2026-06-23 — Frontend lint: ProfilePage off set-state-in-effect (render-phase sync, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/set-state-in-effect` is cleared one site per PR by real refactors (not disables) before the rule is promoted to `error`.

- **`settings/ProfilePage`** — replaced the form-sync `useEffect` (which re-seeded `fullName`/`avatarColor` from the auth-store `user` whenever it changed) with React's documented "adjust state during render" pattern. The component now tracks the last-synced `full_name`/`avatar_color` and resets the editable fields during render when the canonical user changes (after a save here, a save elsewhere, or re-login), with no effect. Local edits are preserved because the user object is unchanged while typing. Regression test `frontend/src/pages/settings/ProfilePage.test.tsx` covers initial seeding, the re-seed on user change, and the empty-name fallback.

### Changed (2026-06-23 — Frontend lint: SSOSettingsPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/settings/SSOSettingsPage.tsx` — replaced the tab-keyed load-on-mount `useEffect(() => { loadData() }, [loadData])` (which drove `configs`/`scimTokens`/`events`/`syncStatus`/`loading`/`error` from inside the effect) with a new SWR hook `useSSOTabData(tab)`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data/error state declaratively, keyed on the active tab so switching tabs fetches exactly the slice the old effect did. The post-mutation `loadData()` calls (after create/toggle/delete config and create/revoke SCIM token) become `refresh()` (SWR `mutate`), revalidating the active tab exactly as before; mutation errors still set a local `error` that is merged with the load error for the inline banner. Behaviour unchanged.
- `src/hooks/useSSOTabData.ts` (new) — `useSWR` keyed on `['sso-tab', tab]`, mirroring the old `tab` dependency. The fetcher switches on tab and populates only that tab's slice (the others stay empty, matching the original per-tab render guards); `revalidateOnFocus: false` and `shouldRetryOnError: false` preserve the prior fetch semantics, and a failed load is surfaced as a string `error` for the banner the old `catch` raised.

Added `src/hooks/useSSOTabData.test.ts` (config-tab fetches only the config slice; the events tab unwraps the `{ total, items }` envelope; a failed load surfaces a string error with a null `syncStatus`; `refresh` revalidates). Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, the rule's count drops 27 → 26 and `SSOSettingsPage` no longer flags it), `type-check`, `build`, and the new vitest suite all green. The rule stays at `warn` until the remaining 26 sites are cleared in later slices.

### Added (2026-06-22 — Frontend: ReassignModal off set-state-in-effect via SWR hook)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/set-state-in-effect` is cleared one site per PR by real refactors (not disables) before the rule is promoted to `error`.

- **`MyFailuresPage` ReassignModal** — replaced the load-on-mount `useEffect` (which drove `setOptions`/`setLoading`/`setError` and a derived default selection) with a new SWR hook `useReassignOptions(testCaseId)`. SWR now owns loading/data/error declaratively; the default assignee (suite owner → first QA Engineer → none) is derived during render with an explicit pick taking precedence, so the picker behaves identically without driving state from an effect. Regression test `frontend/src/hooks/useReassignOptions.test.ts` covers the fetch, the null-id skip, and the error path.

### Changed (2026-06-22 — Frontend lint: OnboardingPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn`; the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/OnboardingPage.tsx` — replaced the load-on-mount `useEffect(() => { … onboardingService.detectProgress(projectId).then(setStatus)… }, [projectId])` (which drove `status`/`loading` from inside the effect) with a new SWR hook `useOnboardingStatus`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the loading/data state declaratively, so the page no longer drives state from an effect. The post-skip `setStatus(updated)` becomes `mutate(updated, { revalidate: false })` (applies the server response to the SWR cache without a refetch, exactly as before).
- `src/hooks/useOnboardingStatus.ts` (new) — `useSWR` keyed on `['onboarding-status', projectId]`, mirroring the old `projectId` dependency. The key is `null` (no fetch) when there is no resolved project — exactly the old `if (!projectId) { setStatus(null); setLoading(false); return }` guard, where the all-projects view (`null` projectId) never fetched and showed the 0%/no-steps workspace view. `status` is `null` while loading or before a project is selected (preserving the prior `useState<OnboardingStatus | null>(null)` semantics); `shouldRetryOnError: false` surfaces a failed load immediately, toasting the same "Failed to load onboarding status" the old `.catch` raised. Behaviour unchanged.

Added `src/hooks/useOnboardingStatus.test.ts` (project-scoped fetch surfacing status; the no-project case skipping the fetch entirely; the error path toasting and leaving `status` null); each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, the rule's count drops 32 → 31 and `OnboardingPage` no longer flags it), `type-check`, `build`, and the full vitest suite all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.

### Changed (2026-06-21 — Frontend lint: OwnershipEditorPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (31 sites remain after this slice); the sites are cleared in reviewable, one-page-per-PR slices by real refactors (not disables) before the rule is promoted to `error`. This slice clears one more site:

- `src/pages/OwnershipEditorPage.tsx` — replaced the load-on-mount `useEffect(() => { loadRules() }, [loadRules])` (the `loadRules` callback drove `rules`/`loading`/`error` state synchronously) with a new SWR hook `useOwnershipRules`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect; the mutation handlers (create/toggle/delete) call the hook's `refresh()` (SWR `mutate`) instead of re-invoking `loadRules()`.
- `src/hooks/useOwnershipRules.ts` (new) — `useSWR` keyed on `['ownership-rules', projectId]`, mirroring the old effect's `projectId` dependency. The key is `null` (no fetch) when there is no resolved project — matching the old `if (!projectId) return` guard, where the all-projects view never fetched and showed the "select a project" empty state. `shouldRetryOnError: false` surfaces a failed load immediately as `isError` (rendered as the same red "Failed to load ownership rules" banner), and `rules` is `[]` while loading or on error, preserving the prior `useState([])` semantics. Behaviour unchanged.

Added `src/hooks/useOwnershipRules.test.ts` (regression guard per repo convention): project-scoped fetch surfacing rules, the no-project case skipping the fetch entirely, and the error path reporting `isError` with an empty rules list. Each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, 73→72 warnings; the rule's count drops 32→31 and `OwnershipEditorPage` no longer flags it), `type-check`, `build`, and the full vitest suite (351 passed) all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.

### Changed (2026-06-21 — Frontend lint: SuiteDetailPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (32 sites remain after the PerformancePage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/SuiteDetailPage.tsx` — replaced the `(suiteName, activeProjectId, days)`-keyed load effect `useEffect(() => { setTrendLoading(true); getSuiteTrend(suiteName, pid, days).then(res => setTrendPoints(res.points || [])).catch(() => setTrendPoints([]))… }, [suiteName, activeProjectId, days])` with a new SWR hook `useSuiteTrend`, matching the codebase's "pages fetch via SWR hooks" convention (the page already reads `useSuiteDetail`/`useSuites`). SWR now owns loading/data state declaratively, so the page no longer drives state from an effect. Dropped the now-unused inline `SuiteTrendPoint` type, `useState`/`useEffect`, and `testManagementService` imports.
- `src/hooks/useSuiteTrend.ts` (new) — `useSWR` keyed on `['suite-trend', activeProjectId, suiteName, days]`, mirroring the old effect's dependency array. The key is null only when there is no `suiteName` (so no fetch then, exactly as the old `if (!suiteName) { setTrendPoints([]); return }` guard); the "all projects" view maps `ALL_PROJECTS_ID` to a `null` project id as the old effect did. `shouldRetryOnError: false` makes a failed load surface as `[]` immediately, matching the old `.catch(() => setTrendPoints([]))`. `points` is `[]` while loading or on error (preserving the old `useState<SuiteTrendPoint[]>([])` semantics). Behaviour unchanged.

Added `src/hooks/useSuiteTrend.test.ts` (project-scoped fetch surfacing points, all-projects view mapping to a `null` project id, no-suite-name skipping the fetch, and the error path surfacing `[]`); each render uses a fresh SWR cache so keys don't bleed across tests. Validated: `npm run lint` (0 errors, 73→72 warnings; the rule's count drops 32→31 and `SuiteDetailPage` no longer flags it), `type-check`, `build`, and the full vitest suite (352 passed) all green. The rule stays at `warn` until the remaining 31 sites are cleared in later slices.

### Changed (2026-06-22 — Frontend lint: DeepInvestigationPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks **v7 (React Compiler)** rule set: `react-hooks/set-state-in-effect` is the last named rule still at `warn`. Sites are cleared one page per slice by real refactors (not disables) before the rule is promoted to `error`.

- **`DeepInvestigationPage`** — replaced the load-on-mount `useEffect` that fetched WF-1 pipeline status into a `useState` (`getPipelineStatus(runId, 'deep')` with an `alive` guard) with a new SWR hook **`usePipelineStatus`** in `useDeepInvestigation.ts`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns the fetch declaratively; the key is `null` (no fetch) when there is no run — exactly the old `if (!runId) { setPipelineStatus(null); return }` guard — and `revalidateOnFocus` is left at the SWR default to honour the page's "refresh on focus" intent the bare effect never actually delivered. `shouldRetryOnError: false` surfaces a failed load as `null`, matching the old `.catch`. Behaviour unchanged.
- **Regression test** `src/hooks/useDeepInvestigation.test.ts` covers the run-scoped fetch, the no-run case skipping the fetch, and the error path leaving data undefined (rendered as `null`).
- `npm run lint` 0 errors; `set-state-in-effect` count drops **32 → 31**; type-check, build, and full vitest (358) green. The rule stays at `warn` until the remaining 31 sites are cleared.

### Added (2026-06-21 — Adoption: `make smoke` health verifier + fixed demo health-wait)

Slice 4 of frictionless self-host adoption — give newcomers confidence the stack actually came up, and fix a real hang.

- **Fixed a `make demo` hang**: it waited on `curl http://localhost:8000/health`, but the bare `GET /health` shim was retired — the probes are `/health/live` and `/health/ready` (the Compose healthcheck already uses `/health/live`). The retired path 404s, so `curl -sf` never succeeded and the demo looped forever after "Waiting for backend health check." Now it waits on `/health/ready`.
- **New `scripts/smoke.py`** (stdlib only — no install) + **`make smoke`**: probes backend readiness (`/health/ready`), liveness (`/health/live`), the API schema (`/openapi.json`), and the frontend, then prints a clear PASS/FAIL report and exits non-zero if any core check is down (usable in CI / setup scripts). Dependency details are informational. Output is ASCII-only so it can't crash a Windows (cp1252) console. Base URLs override via `TL_API` / `TL_WEB`.
- **GETTING_STARTED.md**: Step 1 now uses `make quickstart` (dropping the stale "`cp .env.example` — defaults work for local dev" line, which was the broken path slice 1 fixed) and adds a `make smoke` verification step.
- **New `backend/tests/test_smoke_script.py`** (5 tests) — pins the pure `summarize()` decision core (core failure ⇒ non-pass; informational failure ⇒ still pass; empty ⇒ ok) and that `probe()` never raises on an unreachable host. ruff + quality-gate green.

### Added (2026-06-21 — Adoption: first-run getting-started guide on the dashboard)

Slice 3 of frictionless self-host adoption. A brand-new project (or a fresh instance) used to land on a zeroed-out dashboard with no obvious next step. Now, when there are no executions in the window and no recent runs, the Overview shows a dismissible **getting-started guide** that turns the empty state into a clear path to first value.

- **New `frontend/src/components/onboarding/FirstRunGuide.tsx`** — a presentational, dismissible card with three copy-to-clipboard steps (1: `make quickstart` to load the demo; 2: `testlookup upload results.xml` / ingest your own results; 3: explore) plus quick links to Failure analysis, Flaky coach, the Release gate, and the getting-started docs. Copy uses the shared `utils/clipboard` helper; dismissal persists per-browser via `localStorage` (`FIRST_RUN_DISMISS_KEY`).
- **`OverviewPage.tsx`** — renders the guide at the top when `!summaryLoading && total_executions === 0 && recentRunItems.length === 0` and it hasn't been dismissed; it disappears automatically once the first run lands (and the demo seed from slice 2 means demo users never see it).
- **New `frontend/src/components/onboarding/FirstRunGuide.test.tsx`** (7 tests) — steps/commands present, first-insight links wired, project name shown, clipboard copy, dismiss handler, no-dismiss-without-handler, stable key.

Validated locally: `vitest` (7 passed), `tsc --noEmit`, `eslint`, and `vite build` all green. (The live Overview render on a fresh instance is best confirmed with a `make quickstart` smoke — the component is fully unit-tested and the page integration type-checks/builds.)

### Added (2026-06-21 — Adoption: rich demo dataset so first-run views are populated)

Slice 2 of frictionless self-host adoption. The seed previously created only *authored* test cases/plans/releases, so a fresh install's dashboards, flaky-coach, trends, and failures pages were empty until real runs arrived (and a single upload can't show flakiness or trends). Now a `make quickstart` / `make seed-data` shows a populated, compelling app out of the box.

- **New `backend/app/services/demo_dataset.py`** — pure, deterministic generator (`generate_demo_runs(now=…)`) producing ~14 synthetic runs over ~30 days that embed the headline patterns: stable tests, a **flaky** test (alternating pass/fail with varied environmental error signatures), a **regression** (clean for most of the window then failing in the latest runs), a **product bug** (fails every run with one assertion), and a **perf** regression (duration spikes in recent runs). No DB / no wall-clock — the caller passes `now`.
- **`scripts/seed_dev_data.py`** — new best-effort `_seed_execution_history` runs AFTER the core seed commits, in isolated sessions, feeding the generated runs through the REAL ingestion pipeline (`create_run_from_payload` → `ingest_test_results` → `finalize_run`) so every derived table (history, fingerprints, canonical cases, suites, aggregates) is correct, then backdates the run + its rows so the history spans the trend window. Any failure here is swallowed per-run and can never break the user/project/case seed.
- **New `backend/tests/test_demo_dataset.py`** (14 tests) — pins valid `IngestPayload`/`IngestTestResult` shape, the ~30-day date spread, and that each pattern is detectable downstream (flaky alternates ≥3/≥3 with varied errors; regression is clean→failing; product bug always fails; stable always passes; perf duration spikes; realistic per-run pass rate; stable test identity; determinism). ruff + quality-gate green.

(End-to-end ingestion of the seed runs needs a running stack — validate with a `make quickstart` smoke; the generator contract is pinned by the tests.)

### Added (2026-06-21 — Adoption: one-command zero-config quickstart (`make quickstart`))

First slice of the "frictionless self-host adoption" track. Removes the #1 first-run barrier: previously a newcomer had to `cp .env.example .env` and hand-generate **8 secrets** via `openssl`, and `.env.example` shipped literal placeholders where `DATABASE_URL`/`MONGO_URI` embedded *different* placeholder passwords than `POSTGRES_PASSWORD`/`MONGO_PASSWORD` — so a bare copy + `make demo` brought up containers whose backend couldn't authenticate to Postgres (Compose hard-fails on empty `${POSTGRES_PASSWORD:?…}` etc.).

- **New `scripts/gen-dev-env.sh`** — copies `.env.example`, then fills every required secret (`POSTGRES_PASSWORD`, `MONGO_PASSWORD`, `MINIO_ACCESS_KEY/SECRET_KEY`, `APP_SECRET_KEY`, `JWT_SECRET_KEY`, `WEBHOOK_SECRET`, `FLOWER_PASSWORD`, `GF_SECURITY_ADMIN_PASSWORD`) with a freshly generated random value and rewrites `DATABASE_URL` / `MONGO_URI` so their passwords stay in sync. Secret source falls back openssl → python `secrets` → `/dev/urandom`. The file is git-ignored and stamped with a clear LOCAL/DEMO-only banner; the script no-ops if `.env` already exists (`--force` to regenerate).
- **Makefile**: the `.env` bootstrap target now runs the generator (so `make dev` / `make demo` start on the first try with zero manual editing), with a plain-copy fallback if `bash` is unavailable. New **`make quickstart`** target = generate `.env` + run the demo.
- **README**: Quick Start now leads with the one-command `make quickstart`; the secrets section is reframed as production-only (local/demo secrets are auto-generated); removed the stale "demo coming in v0.1.0" note (the `demo` target already exists).

### Changed (2026-06-20 — Frontend lint: PerformancePage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (33 sites remain after the SeedDataPage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/settings/PerformancePage.tsx` — replaced the load-on-mount `useEffect(() => { setLoading(true); Promise.all([getPerformanceBudgets(), getSearchConfig()]).then(([b, c]) => { setBudgets(b); setConfig(c) })… }, [])` with a new SWR hook `usePerformanceSettings`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect.
- `src/hooks/usePerformanceSettings.ts` (new) — `useSWR` keyed on the constant `'performance-settings'`; both static, read-only endpoints are fetched in parallel under one key, exactly as the old `Promise.all`. `shouldRetryOnError: false` makes a failed load surface as empty immediately, matching the old `.catch(() => {})` that swallowed the error. `budgets`/`config` are `null` while loading or on error (preserving the old `useState<… | null>(null)` semantics); each tab body still renders only when its data is present. Behaviour unchanged.

Added `src/hooks/usePerformanceSettings.test.ts` (parallel fetch surfacing both budgets and config; the error path leaving both `null` and reporting `isError`); each render uses a fresh SWR cache so the constant key doesn't bleed across tests. Validated: `npm run lint` (0 errors, 74→73 warnings; the rule's count drops 33→32 and `PerformancePage` no longer flags it), `type-check`, `build`, and the full vitest suite (348 passed) all green. The rule stays at `warn` until the remaining 32 sites are cleared in later slices.

### Changed (2026-06-20 — Frontend lint: SeedDataPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (34 sites remain after the ValueMetricsPage slice); the sites are cleared in reviewable slices before the rule is promoted to `error`. This slice clears one more site by a real refactor (not a disable):

- `src/pages/settings/SeedDataPage.tsx` — replaced the load-on-mount `useEffect(() => fetchStatus(), [fetchStatus])` (whose `fetchStatus` called `setSeeded`/`setLoading`) with a new SWR hook `useSeedStatus`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data/error state declaratively, so the page no longer drives state from an effect. The post-mutation `await fetchStatus()` (after load/reset/delete) becomes `await refresh()` (SWR `mutate`).
- `src/hooks/useSeedStatus.ts` (new) — `useSWR` keyed on the constant `'dev-seed-status'` with `shouldRetryOnError: false` so a failed check surfaces immediately like the old `.catch`. `seeded` is `null` while loading or on error (preserving the old `useState<boolean | null>(null)` semantics); the page distinguishes the error case via `isError`. Behaviour unchanged.

Added `src/hooks/useSeedStatus.test.ts` (seeded=true, seeded=false, and the error path surfacing `isError` + `seeded=null`); each render uses a fresh SWR cache so the constant key doesn't bleed across tests. Validated: `npm run lint` (0 errors, 75→74 warnings; the rule's count drops 34→33 and `SeedDataPage` no longer flags it), `type-check`, `build`, and the full vitest suite (346 passed) all green. The rule stays at `warn` until the remaining 33 sites are cleared in later slices.

### Changed (2026-06-19 — Frontend lint: ValueMetricsPage off set-state-in-effect (SWR migration, slice toward promotion))

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set. `react-hooks/set-state-in-effect` is the last named rule still at `warn` (it flags 35 sites — far more than the `refs`/`immutability` flips, which each touched a single component — so it is cleared in reviewable slices before the rule is promoted to `error`). This slice clears one site by a real refactor (not a disable):

- `src/pages/ValueMetricsPage.tsx` — replaced the load-on-mount `useEffect(() => { setLoading(true); valueMetricsService.get(projectId, days).then(setMetrics)… })` with a new SWR hook `useValueMetrics`, matching the codebase's "pages fetch via SWR hooks" convention. SWR now owns loading/data state declaratively, so the page no longer drives state from an effect.
- `src/hooks/useValueMetrics.ts` (new) — `useSWR` keyed on `['value-metrics', projectId ?? '__all__', days]`, mirroring the old effect's `[projectId, days]` dependency array. The key is never null, so the page still always fetches (including all-projects mode, where the service omits the `project_id` filter); `onError` preserves the prior toast. Behaviour unchanged.

Added `src/hooks/useValueMetrics.test.ts` (project-scoped fetch, all-projects fetch with no filter, error surfaced + toast). Validated: `npm run lint` (0 errors, 76→75 warnings; the rule's count drops 35→34 and `ValueMetricsPage` no longer flags it), `type-check`, `build`, and the full vitest suite (343 passed) all green. The rule stays at `warn` until the remaining 34 sites are cleared in later slices.

### Added (2026-06-19 — Flaky-Test Intelligence: cross-run step-flip DB read (FLK-P6, slice 3 — assemble))

Picks up the read that slice 2 deferred: it pulls the retained per-run step outcomes from `test_step_runs` (#199), groups them into the oldest→newest per-run window `compute_step_flips` (#200) expects, and returns the step-flip report — so the signal can finally be assembled from real history. New `runs_service.step_flip_report_by_fingerprint(db, project_id, fingerprints, *, since=None, max_runs=25) -> dict[fingerprint, StepFlipReport]`, a sibling of the existing `failing_step_detail_by_fingerprint` (which reads the latest-run snapshot):

- **Two batched queries, never N+1**: resolve the fingerprints to canonical ids (project-scoped via the canonical anchor), then one `test_step_runs JOIN test_runs` read ordered `(canonical, run created_at, run id, ordinal)` — oldest→newest, the order `compute_step_flips` wants — with deterministic run-id/ordinal tiebreaks when `created_at` collides for runs ingested together.
- Folds the flat, ordered rows into per-canonical per-run windows (first-seen run wins ordering; steps accumulate in ordinal order), capping to the most recent `max_runs` runs and honouring an optional `since` bound on `TestRun.created_at`. A resolved canonical with `<2` runs of history maps to an "insufficient history" report (caller distinguishes "no flip" from "no history"); a fingerprint with no anchor is absent. Pure read — the caller's transaction is never mutated.
- **Deferred to a later slice**: the surfacing (Flaky Coach response fields / agent verdict / UI). This slice is the read those will call — no migration, no ingestion/router change.
- New `backend/tests/test_step_flip_read.py` (10 cases, DB stubbed by SQL-dispatch fake) — oscillation flagged as a flip, stable-step no-flip, single-run/no-history insufficient-history reports, project scoping, the two-query batching, `max_runs` capping (drops early oscillation), `since` filtering, and per-ordinal step grouping within a run. Full backend suite (3421 passed) + 15 quality gates + ruff green.

### Added (2026-06-18 — Flaky-Test Intelligence: cross-run step-flip computation (FLK-P6, slice 2 — compute))

Builds on slice 1's `test_step_runs` retention (#199) to compute the signal FLK-P5 explicitly deferred ("left for future work" until per-run step history existed): **which step flipped between runs, how often, and in which direction**. A step that oscillates PASSED↔FAILED across runs reads as *step-level flakiness* — fix/quarantine that one step — rather than a whole-test verdict. New pure, no-DB, never-raise module `backend/app/services/flaky_step_flip.py`, a sibling of `flaky_signals`/`flaky_step_analysis`:

- `compute_step_flips(runs) -> StepFlipReport` over a per-run step-outcome window ordered oldest→newest (the shape `test_step_runs` yields). Steps are matched across runs by `ordinal` (the table's grain and the key FLK-P5 attribution already uses); a flip is a PASSED↔FAILED change between two consecutive runs where the step had a pass/fail outcome. `BROKEN` normalises to `FAILED`; `SKIPPED`/`UNKNOWN` carry no pass-vs-fail signal and are bridged (not counted as a third state that would manufacture spurious flips). Each transition is classified `regression` (PASSED→FAILED) or `recovery` (FAILED→PASSED); per-step roll-ups (`flip_count`, `runs_observed`, `last_status`) are sorted most-flipping-first.
- Defensive by construction: a `<2`-run window reports "insufficient history" (a flip is undefined with one run), malformed rows degrade to a neutral contribution, and the function never raises — safe under `AI_OFFLINE_MODE`.
- **Deferred to a later slice**: the DB read that assembles the per-run window from `test_step_runs` (ordered by run start) and the surfacing (Flaky Coach / agent verdict). This slice is the pure computation those will call — zero blast radius, no migration, no ingestion/router change.
- New `backend/tests/test_flaky_step_flip.py` (14 cases) — pass→fail/regression, fail→pass/recovery, multi-transition oscillation, stable-step no-flip, BROKEN/enum-prefix normalisation, SKIPPED bridging, independent-ordinal tracking, flip-count ordering, latest-name display, the `<2`-run guard, and the never-raise invariant. Full backend suite + 15 quality gates + ruff green.

### Added (2026-06-17 — Flaky-Test Intelligence: per-run step-outcome retention (FLK-P6, slice 1 — capture))

Foundation for cross-run step-flip analysis, fulfilling the schema change FLK-P5 flagged as "left for future work". `test_steps` is a LATEST-RUN-ONLY snapshot (one per `canonical_test_cases`, delete+reinsert on every ingest), so a step-flip — "PASSED in run N-1, FAILED in run N" — cannot be computed from it. This slice starts RETAINING per-run step outcomes without touching the snapshot or any of its readers (zero blast radius):

- **New table `test_step_runs`** (Alembic `0097`, down_revision `0096`, real downgrade) — one compact, flat row per `(canonical_test_case_id, source_test_run_id, ordinal)`. Deliberately minimal: only the step identity (`ordinal`/`depth`/`name`/`keyword`) and the per-run signal (`status`/`duration_ms`). The heavy, PII-bearing columns (assertion message/trace, expected/actual, parameters, attachments) stay ONLY on the latest-run `test_steps` snapshot and are **not** duplicated per run. `source_test_run_id` is **CASCADE** (the row *is* about that run — deleting the run deletes its step history), unlike the snapshot's SET NULL provenance pointer. A unique constraint on `(canonical_test_case_id, source_test_run_id, ordinal)` encodes the idempotency invariant.
- **Ingestion writes the history alongside the snapshot** (`_persist_step_snapshot` / `_insert_step` in `backend/app/services/ingestion.py`), idempotent per `(canonical, run)`: it deletes only **this run's** rows then reinserts them, so re-ingesting a run overwrites its own rows while prior runs' history is retained. Stays inside the ingestion-pipeline transaction — the router still owns the commit (no service-level commit).
- **Deferred to slice 2**: the cross-run step-flip computation + surfacing. Capture must ship first — step-flip is undefined until ≥2 runs of history have accumulated post-deploy.
- New `backend/tests/test_step_run_retention.py` — proves cross-run retention makes a PASSED→FAILED flip observable, per-`(canonical, run)` idempotency on re-ingest, the latest-run snapshot is unchanged (no regression), and the model/migration contract (compact columns, run-CASCADE, real downgrade). Full backend suite (3397 passed) + 15 quality gates + ruff green.

### Changed (2026-06-16 — Frontend lint: promote react-hooks/refs warn → error)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/refs` moves from `warn` to `error` in `frontend/eslint.config.js`. All 18 flagged violations were in a single component and fixed by refactor (not disables):
- `src/pages/TestManagementPage.tsx` — the `CasesFilterBar` component took its props as an undestructured `p` object whose `searchInputRef: RefObject` member made the rule treat *every* `p.*` read as a ref-read-during-render (18 false positives across the search input, checkbox, filter chips, and saved-view buttons). Destructured the props at the parameter so the ref is a named binding forwarded straight to the DOM `ref=` (which the rule allows); the remaining props become plain locals. Behaviour unchanged.

Added `src/pages/TestManagementPage.refs.test.ts` (source-text invariants via `?raw`, matching the sibling suite-aggregates regression) asserting the rule stays at `error` and `CasesFilterBar` keeps its destructured signature and named-binding `ref=`. Validated: `npm run lint` (0 errors, 94→76 warnings), `type-check`, `build`, and the new test all green.

### Changed (2026-06-16 — Frontend lint: promote react-hooks/immutability warn → error)

Continues the phased adoption of the eslint-plugin-react-hooks v7 (React Compiler) rule set: `react-hooks/immutability` moves from `warn` to `error` in `frontend/eslint.config.js`, with its four flagged violations fixed by refactor (not disables):
- `src/hooks/useLiveExecution.ts` — the WebSocket reconnect timer called `setTimeout(connect, …)` from inside `connect`'s own `onclose`, a forward self-reference the rule rejects (it closes over a stale `connect`). Routed the reconnect through a new `connectRef` kept in sync with the latest `connect` via an effect, so the timer always invokes the current closure.
- `src/hooks/useProjectChange.ts` — the wrapper-hook ref `previousProjectId` was mutated in effects but not named with the `Ref` suffix the rule keys on to recognise an intentional mutable ref. Renamed to `previousProjectIdRef` throughout; behaviour unchanged.

Added `src/hooks/useProjectChange.test.ts` asserting the previous-project tracker still: doesn't fire on first render, fires once per change while enabled, and silently absorbs changes while disabled without replaying them on re-enable. Validated: `npm run lint` (0 errors, immutability warnings gone), `type-check`, `build`, and the hook tests all green.

### Fixed (2026-06-13 — AI-Agent Quality: RegressionWatchman confidence coercion OverflowError (AIQ-P1 cleanup follow-up))

Follow-up to the AIQ-P1 cleanup. `_summarize_classification` in `backend/app/agents/regression_watchman.py` coerced each classification `confidence` inside `try/except (TypeError, ValueError)`, which did **not** catch `OverflowError` from `int(float("inf"))` — and `float("inf")` is reachable because Python's `json.loads` accepts the `Infinity` token, so a degraded LLM payload could still escape the guarded helper and fail the pipeline run. Added `OverflowError` to the except clause (the awkward value now coerces to `0`, like the other non-numeric cases). Extended `test_summarize_classification_defensive_coercion` in `backend/tests/test_agent_contract_outputs.py` with an `inf` case asserting it coerces rather than raises. No behavior change for well-formed input; contract suites green (13 passed).

### Added (2026-06-15 — Flaky-Test Intelligence: granular step-level surgical attribution (FLK-P5))

Final phase of the Flaky-Test Intelligence (FLK) initiative. Where FLK-P1..P4 reason at the *test* level, FLK-P5 drills into the granular `test_steps` snapshot to answer **which step / assertion is the failure** — so the recommendation is a *surgical fix of one step* instead of quarantining the whole test. New pure, no-DB, never-raise module `backend/app/services/flaky_step_analysis.py`:

- `build_step_attribution(first_failing_step, total_steps, failing_step_count) -> StepFailureAttribution` — pinpoints the lowest-ordinal failing step, summarises the assertion (`expected X, got Y`, or the message), classifies assertion-vs-error failures, SHA-**fingerprints the step location** (denoised name + assertion trace, so a consistent step reads as deterministic), and emits a surgical-fix recommendation. Assertion text is redacted + truncated at the boundary.
- **Data-model honesty**: `test_steps` is a LATEST-RUN-ONLY snapshot (one per `canonical_test_cases`, delete+reinsert on ingest), so cross-run step-flip history is not retained. FLK-P5 therefore attributes the failure in the most recent snapshot (the actionable "what to fix now") and fingerprints *where* it fails; true cross-run step-flip would require per-run step retention (a schema change left for future work). Test-level stack-trace fingerprinting already shipped in FLK-P1/P4.
- New batched read `runs_service.failing_step_detail_by_fingerprint(db, project_id, fingerprints)` — resolves fingerprints to their project-scoped canonical anchors and folds the snapshot into `{first_failing, total_steps, failing_step_count}` per fingerprint (two batched queries, no N+1).
- **Surfaced** on `/flaky-coach` (`FlakyCoachEntry.failing_step` + `failing_step_detail`, read-time, **no migration**, best-effort) and in the FLK-P4 agent verdict (`flaky_sentinel_agent` attaches a `step_attribution` block per finding). The Flaky Coach page renders the failing step + surgical recommendation.
- New `backend/tests/test_flaky_step_analysis.py` (assertion summary, surgical phrasing, stable noise-free fingerprint, redaction, never-raise); `test_flaky_signals.py` updated for the read-time step query. Full backend suite + 15 quality gates + architectural ratchets green.

### Added (2026-06-14 — Flaky-Test Intelligence: agentic flaky investigator + likely-cause (FLK-P4))

Fourth phase of the Flaky-Test Intelligence (FLK) initiative. The `flaky_sentinel_agent` is upgraded from a lifecycle reporter into a multi-step **investigator that confirms _and explains_ each flaky verdict**, emitting a structured `{is_flaky, confidence, likely_cause, evidence[]}` per AIQ-P1/P3. The reasoning lives in a new pure, no-DB, never-raise module `backend/app/services/flaky_investigator.py` so it is unit-testable and reusable by the read-time surfaces:

- `cluster_failures(records)` — groups the recent window's FAILED rows by error signature + stack fingerprint (FLK-P1 helpers, now exposed as public `error_signature` / `stack_fingerprint`) into an explainable cluster summary (distinct signatures/stacks, dominant error, examples).
- `determine_likely_cause(signals, ml_confidence=None)` — maps the FLK-P1 intermittency signals (+ optional FLK-P3 ML confidence) to a `(human_text, stable_code)` cause: `in_run_retry` · `environmental` · `race_condition` · `likely_regression` · `intermittent` · `low_volatility` · `insufficient_data`. A confident "not a flake" from the ML model overrides the heuristics.
- `build_flaky_verdict(...)` — assembles the structured verdict; `confidence` is the **AIQ-P3 evidence-weighted aggregate** (`aggregate_confidence` over `EvidenceRef`s for volatility, error/stack clustering, in-run retries, the ML score, the Wilson interval, and build changes) so it carries an auditable breakdown and the "high confidence needs strong evidence" cap. `is_flaky` is false for a confirmed regression / insufficient data.
- The agent now joins the per-run `TestCase` meta into its history window, computes signals + Wilson CI + ML confidence + clusters, and attaches the verdict (plus flat `is_flaky` / `likely_cause`) to each finding. The quarantine state machine and proposal path are unchanged.
- **Surfaced on `/flaky-coach`** (`FlakyCoachEntry.flaky_likely_cause` computed at read time from signals + the persisted ML confidence — **no migration**) and on **`/failures`** (`analytics_service.flaky_tests` attaches `likely_cause` via one bounded, tenant-scoped windowed query; best-effort so the list always renders). Both frontends render the likely cause.
- New `backend/tests/test_flaky_investigator.py` (clustering, every likely-cause branch incl. the ML-skeptic override, verdict evidence/confidence cap, never-raise); `test_flaky_failures_consistency.py` updated for the enrichment query. Full backend suite + 15 quality gates + architectural ratchets green.

### Added (2026-06-14 — Flaky-Test Intelligence: ML flakiness-confidence classifier (FLK-P3))

Third phase of the Flaky-Test Intelligence (FLK) initiative. Adds a focused ML model that scores **`is_flaky_confidence` ∈ [0, 1]** for each flaky verdict, learned from the team's own quarantine approve/reject decisions — it complements (does not replace) the 6-class triage classifier and the deterministic Wilson/intermittency signals. New module `backend/app/services/ml/flaky_confidence.py` deliberately mirrors the existing ML infra (`ml/classifier.py` + `ml/trainer.py`): versioned `flaky_confidence_v*.joblib` artifacts under `ML_MODEL_DIR`, a module-level cache with periodic hot-swap, a persisted feature contract validated before a model is trusted, and **graceful degradation** — absent `scikit-learn`/`joblib` or an absent model makes `predict()` return `None` (never raises), so the leaderboard falls back to the deterministic signals.

- **Features** (8, train/serve-parity via one pure `build_flaky_feature_vector`): `failure_rate`, `status_volatility`, `flip_count`, `error_signature_diversity`, `failure_rate_trend` (recent-half minus older-half failure rate), `run_interval_variance` (coefficient of variation of inter-run gaps), `in_run_retry_rate`, `stack_trace_diversity`. Most are reused from FLK-P1's `IntermittencySignals`; the trend + interval-variance are computed here.
- **Ground truth**: `FlakyQuarantineRequest` outcomes — APPROVED/QUARANTINED/RE_QUARANTINED = confirmed flake (1), REJECTED = confirmed non-flake (0); undecided states excluded. Binary `HistGradientBoostingClassifier`; **AUC** is the deploy gate (accuracy is misleading on the imbalanced flake set).
- **Training**: `train_flaky_confidence_model()` + a nightly Celery beat `nightly-flaky-confidence-training` (03:30 UTC). No-op-safe (`insufficient_data` / `insufficient_class_diversity` / `error` status strings) until enough labeled decisions exist. The model is a LOCAL scikit-learn artifact — no outbound calls — so it is unaffected by `AI_OFFLINE_MODE`.
- Migration **0096** (`down_revision = 0095`, real `downgrade()`) adds nullable `flaky_coach_results.is_flaky_confidence`. `refresh_flaky_coach` predicts + persists it and appends an advisory stabilization line (model-confirmed ≥70% / skeptical ≤30% / uncertain); `get_flaky_coach` surfaces it on a new optional `FlakyCoachEntry` field; the leaderboard adds it as a `NULLS LAST` ranking key between `impact_score` and the Wilson lower bound (surface high-confidence flakes first). The quarantine state machine is untouched.
- Frontend: `FlakyCoachEntry` type + Flaky Coach page render the ML confidence (colour-coded likely-flake / likely-real-failure / uncertain) in the expanded detail.
- New `backend/tests/test_flaky_confidence.py`: feature-vector correctness + never-raise, inference graceful degradation (no model / broken model / single-class), a trained-sklearn round-trip asserting separation, training degrade path, and feature parity with `compute_intermittency_signals`. `test_flaky_signals.py` extended to surface the persisted field.

### Added (2026-06-14 — Flaky-Test Intelligence: statistical confidence interval on flaky verdicts (FLK-P2))

Second phase of the Flaky-Test Intelligence (FLK) initiative. Every auto-detected flaky verdict now carries a **statistical-confidence band** on its failure ratio so the leaderboard can distinguish a well-evidenced flake (e.g. `30/100`) from a thin one (`3/10`) that share the same point estimate. A new pure, no-DB, never-raise module `backend/app/services/flaky_statistics.py` computes the **closed-form Wilson score 95% interval** (`wilson_failure_confidence(failures, total) -> FailureRatioConfidence`) — no `scipy` dependency. Wilson is preferred over the normal (Wald) approximation because it stays inside `[0, 1]` and is well-behaved for the small-`n` / extreme-`p̂` regime flaky tests live in. Degenerate inputs (`total<=0`, `failures>total`, non-numeric, non-finite `z`) degrade to a neutral band rather than raising.

- Migration **0095** (`down_revision = 0094`, real `downgrade()`) adds two nullable `Float` columns to `flaky_coach_results`: `flaky_confidence_low` / `flaky_confidence_high`. Nullable because historical cached rows and manual-triage leaderboard entries carry no statistical interval until the next `refresh_flaky_coach` recompute.
- `refresh_flaky_coach` computes and persists the interval; `get_flaky_coach` surfaces it on new optional `FlakyCoachEntry` fields (`flaky_confidence_low/high`).
- **Ranking by statistical strength**: `impact_score` already scales with `log2(total_runs)` (so `30/100` already outranks `3/10`); the Wilson lower bound is added as a deterministic `NULLS LAST` secondary sort key so equal-impact rows order by statistical strength.
- Frontend: `FlakyCoachEntry` type gains the FLK-P1/P2 optional fields and the Flaky Coach page renders the **"Failure rate 95% CI (Wilson)"** band in the expanded test detail.
- **No new service commit**, **no outbound calls** — `AI_OFFLINE_MODE` semantics untouched. New `backend/tests/test_flaky_statistics.py` (closed-form correctness vs an independent reference, the `30/100 > 3/10` lower-bound ranking property, interval-narrows-with-sample-size monotonicity, extremes at `p̂=0`/`p̂=1`, and never-raise on degenerate input); `test_flaky_signals.py` extended to assert the persisted band is surfaced.

### Added (2026-06-13 — Flaky-Test Intelligence: intermittency + error-signature analysis (FLK-P1))

First phase of the Flaky-Test Intelligence (FLK) initiative. Flaky verdicts now carry **intermittency signals** that discriminate a *high-volatility flake* (flips pass↔fail with many distinct errors / in-run framework retries) from a *low-volatility regression* (fails persistently with a single repeated error — a real bug that should **not** be quarantined on the flake track). A new pure, no-DB, never-raise scorer `backend/app/services/flaky_signals.py` defines `compute_intermittency_signals(records) -> IntermittencySignals` over a per-run window:

- **status_volatility** = `flips / (runs - 1)` (adjacent pass↔fail changes)
- **error_signature_diversity** = unique denoised error-prefix signatures / fail_count
- **stack_trace_diversity** = unique stack-trace fingerprints / fail_count (many unique ⇒ environmental/race; one ⇒ deterministic bug)
- **in_run_retry_rate** = fraction of runs the framework recorded an in-run retry / flaky flag (granular `retry_count` / `is_flaky_run` from PR #169 — a strong in-run flake signal)
- **intermittency_label** ∈ `intermittent_flaky` · `environmental_flaky` · `low_volatility_flaky` · `persistent_regression` · `insufficient_data`

`refresh_flaky_coach` joins the granular `TestCase` meta (`error_message`, `stack_trace`, `retry_count`, `is_flaky_run`) into its existing windowed query and uses the label to refine the advisory verdict: a `persistent_regression` that would otherwise be `QUARANTINE` is downgraded to `INVESTIGATE` (the quarantine **state machine is untouched** — only the advisory recommendation string changes), and signal-aware stabilization lines are appended. `get_flaky_coach` scores intermittency at read time for the bounded leaderboard set (one extra batched query) and surfaces the numeric signals on new optional `FlakyCoachEntry` fields, so `/flaky-coach` shows the new signal. **No migration** (read-time compute + verdict refinement only), **no new service commit**, **no outbound calls** — `AI_OFFLINE_MODE` semantics untouched. New `backend/tests/test_flaky_signals.py` (16 tests) covers discrimination, granular retry/stack signals, noise normalisation, never-raise on malformed input, and the service-level refinement + surfacing.

### Fixed (2026-06-13 — AIQ-P1 cleanup: RegressionWatchman never-raise + contract field-drop parity)

External review of the merged AIQ-P1 contract work surfaced two empirically-reproduced defects in `backend/app/agents/regression_watchman.py` and `backend/app/models/agent_contracts.py`, both now fixed:

- **MAJOR — success path could raise (never-raise regression).** `RegressionWatchman.run()`'s non-error branch computed `confidence`/`evidence_refs` inline over `classification.values()`. A classification merged back from a partially-validated LLM payload can hold a **non-dict value** (`v.get` → `AttributeError`) or a **non-numeric `confidence`** such as `'high'` (`int('high')` → `ValueError`), which raised into the graph node wrapper and **failed the whole pipeline run** — the exact failure the contract layer exists to prevent (live-LLM only; offline was already safe). Confidence/evidence derivation moved into a guarded `_summarize_classification(...)` helper that skips non-dict values and wraps each `int(...)` (default `0`; empty → `100` as before), so the success path can **never** raise.
- **MINOR — silent field drop.** `LogIntelligenceAgentOutput` and `RegressionWatchmanAgentOutput` lacked `model_config = ConfigDict(extra='allow')` (only `RunCompareAgentOutput` had it), so an undeclared payload key was silently dropped on `model_validate`. Both now set `extra='allow'` for parity, preserving undeclared nested keys.

Regression tests added in `backend/tests/test_agent_contract_outputs.py`: a `run()`-level test feeding a malformed classification with **both** a non-dict value and a non-numeric `confidence` (asserts no raise + valid contract), a direct helper-coercion test, and undeclared-key-survival tests for both contracts. AIQ-P1 ratchet + behavioral suites green; 15/15 quality-gate guards and architectural ratchets green.

### Added (2026-06-12 — AI-Agent Quality: report-quality eval harness for agent outputs (AIQ-P5))

Fifth phase of the AI-Agent Quality (AIQ) initiative. AIQ-P5 adds a **pure-local, offline-safe, never-raise** scorer for **RECORDED** agent outputs plus a golden-set **CI gate** that fails the build if an agent change regresses report quality. A new module `backend/app/services/agent_eval_harness.py` defines `evaluate_agent_outputs(samples) -> AgentEvalReport`, scoring 5 report-quality metrics over a list of `AgentEvalSample`: **coherence** (per-sample invariants), **completeness** (evidence present when `confidence >= 60`), **actionability** (a fix/action present when the verdict is non-flaky), **accuracy** (`verdict == ground truth`), and **calibration** — Brier score `mean((conf/100 - outcome)^2)` and ECE `sum over 10 bins of (|S_b|/N)*|acc_b - conf_b|`. The report carries `per_metric_pass` + an `overall passed`, gated by `PASS_THRESHOLDS` (`coherence >= 0.90`, `completeness >= 0.90`, `actionability >= 0.85`, `accuracy >= 0.70`, `brier <= 0.20`, `ece <= 0.15`) with `MIN_SAMPLES = 5` (insufficient data fails). The scorer **never raises**, makes **no DB**, and has **no outbound/LLM imports** (offline by construction). A golden set `backend/app/services/golden_agent_outputs.py` records agent outputs + ground truth (passes all thresholds, accuracy `0.889`) plus negative fixtures and an always-wrong under-confident fixture. Two additive helpers: `compute_agent_report_quality(samples) -> dict` (`backend/app/services/ai_eval_service.py`) and `evaluate_agent_report_quality_rules(report) -> list` of gate rule dicts (6 metric rules + an overall `agent_report_quality`, `backend/app/services/eval_gate_service.py`). The CI gate `backend/tests/test_architectural_agent_eval_harness.py` runs in the existing **backend-test** pytest job and fails CI if an agent change regresses calibration / evidence / actions / accuracy on the golden set, alongside the `backend/tests/services/test_agent_eval_harness.py` unit suite. Design note: an adversarial review found a calibration-only gate let an **under-confident, always-wrong** agent pass, so the **accuracy** metric was added to close that bypass. **No migration, no outbound calls, offline by construction** — `AI_OFFLINE_MODE` semantics untouched.

### Added (2026-06-12 — AI-Agent Quality: gap-detection + report-refinement agents (AIQ-P4))

Fourth phase of the AI-Agent Quality (AIQ) initiative. AIQ-P4 adds two **pure-local, offline-safe, never-raise** reconciliation agents that audit and reconcile the deep-workflow output, both wired **OPTIONALLY** behind a new default-off flag so there is **zero runtime impact until enabled**. A new module `backend/app/agents/gap_detection_agent.py` defines **GapDetectionAgent**: for every failed test it audits whether the test was analyzed / skipped / errored and enforces the referential-integrity invariant `analyzed_count + skipped_count + errored_count == failed_count`, emitting a validated `GapDetectionAgentOutput` whose `gap_report` carries `failed_count` / `analyzed_count` / `skipped_count` / `errored_count`, `coverage_ratio` (forced to `1.0` when there are no failures), `integrity_ok` (self-recomputed by a `@model_validator`), `inconclusive_count` / `no_evidence_count`, and a `gaps[]` list of `GapItem{test_id, reason ∈ unanalyzed|errored|inconclusive|no_evidence|low_confidence, detail (structural tokens only), bucket ∈ analyzed|skipped|errored}`. A second module `backend/app/agents/report_refinement_agent.py` defines **ReportRefinementAgent**: it reconciles the parallel anomaly / analysis / cluster signals — **deduping** a test analyzed by ≥2 routes (deterministic precedence `analysis > anomaly > cluster`), **detecting contradictions** (e.g. `flaky_vs_regression` when analysis says `is_flaky` but the test is a fresh regression), and **resolving** them (`prefer_analysis` | `prefer_anomaly` | `merge` | `flag_for_review`) — and emits a validated `ReportRefinementAgentOutput` whose `refined_report` carries `dedup_count`, `contradictions[]` (`Contradiction{test_id, type, routes, resolution, detail}`), `contradictions_resolved` / `unresolved_count` (self-recomputed from the contradiction list by a `@model_validator`), `multi_route_test_ids`, and `reconciled_tests`. Both stages are wired into the **DEEP** workflow graph (`backend/app/agents/workflow.py`) behind the new config flag **`AIQ_GAP_REFINEMENT_ENABLED`** (default `False`, `backend/app/core/config.py`): the **graph topology is identical whether the flag is on or off** — when off the nodes early-return a skip delta (`skipped_stages`), and when on the chain runs `summary → (triage) → gap_detection → report_refinement → flaky_sentinel → test_health → release_risk → END`. Both stages are added to `DEEP_OPTIONAL_STAGES` and to the agent_planner's `_DEEP_STAGES` (`backend/app/services/agent_planner.py`, flag-gated). New Pydantic v2 models in `backend/app/models/agent_contracts.py`: `GapReport`, `RefinedReport` (plain nested models with self-recomputing `@model_validator`s), `GapDetectionAgentOutput`, `ReportRefinementAgentOutput`, `GapItem`, `Contradiction`, and the enums `GapReason` / `ContradictionType` / `ResolutionStrategy`; the contracted-output-model ratchet floor is raised `12 → 14`. The offline gate is honored (`AI_OFFLINE_MODE=True` default): both agents are deterministic in-memory reconciliation with **no LLM, network, or DB calls**, and any malformed / non-dict input degrades to a fallback contract — they **never raise**. **No migration (pure in-memory, no new DB columns), no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_gap_detection_agent.py`, `backend/tests/test_report_refinement_agent.py`, and `backend/tests/test_aiq_p4_workflow_wiring.py` (30 tests: integrity invariant, dedup precedence, contradiction taxonomy/resolution, never-raise, flag-on/flag-off topology, and skip-delta wiring).

### Added (2026-06-12 — AI-Agent Quality: structured evidence + confidence scoring (AIQ-P3))

Third phase of the AI-Agent Quality (AIQ) initiative. AIQ-P3 adds a **pure-local, additive evidence + confidence-scoring layer** so an agent's confidence is a weighted, capped function of named evidence rather than a hand-tuned constant — and the rationale is surfaced for audit. A new shared module `backend/app/agents/evidence.py` provides an `EvidenceRef` Pydantic v2 model (`source`, `ref_id`, `excerpt`, `strength` ∈ weak|medium|strong, `contribution` 0-100) and an `aggregate_confidence(refs) -> (final, breakdown)` helper. All `EvidenceRef` coercion lives in `field_validator(mode="before")` (None→"" strings, lowercased/fallback strength, contribution clamped 0-100, **excerpt redacted via `redact_text` then truncated to 240 chars + "…"** at the model boundary) so a malformed field degrades to its default and the model **never raises**. `aggregate_confidence` computes a strength-weighted mean (weak=1, medium=2, strong=3), rounds and clamps to [0, 100], then applies the **cap rule: any confidence >70 requires ≥1 strong OR ≥2 medium sources**, else it is capped to 70 with `cap_applied`/`cap_reason` recorded; empty/all-invalid input yields `(0, zero-breakdown)` and non-`EvidenceRef` items are filtered. `AgentContractMetadata` gains an optional `confidence_breakdown: Optional[dict]`, and `validate_agent_contract(...)` gains a keyword-only `structured_evidence=None`: when supplied it derives `confidence_score` from the aggregate (an **explicit `confidence` kwarg still wins**), auto-populates legacy `evidence_refs` from `as_legacy_dict()` when the caller passed none, and stamps the `confidence_breakdown` — all inside the existing try/except so it **never raises** (the `aggregate_confidence` import is lazy to avoid a `models → agents` cycle). Two adopters move onto structured evidence: **LogIntelligenceAgent** emits `distributed_trace` / `log_anomaly` medium refs (contribution 80, so both signals present preserve the prior confidence of 80 while a single source honestly caps to 70) and lets the contract own the score; **ReleaseRiskAgent** emits a `score_model` strong ref (contribution `100 − risk_score`) plus a weak `consistency_report` ref — existing `decision_reason`, consistency suffix, `log_decision`, and `BaseAgent` structure unchanged. **No behavior change to the analytic payloads, no migration, no outbound calls, no new service commits, no print/logging side effects** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_evidence_confidence.py` (unit + property coverage of the cap, redaction/truncation, weighted mean, and never-raise invariants) and two additive ratchets in `backend/tests/test_architectural_agent_contracts.py` (`confidence_breakdown` field presence + the cap invariant), with `evidence.py` added to `INFRA_ALLOWLIST`.

### Added (2026-06-12 — AI-Agent Quality: self-critique / verification pass (AIQ-P2))

Second phase of the AI-Agent Quality (AIQ) initiative. AIQ-P2 adds a **pure-local, additive self-critique layer** so the analytic agents catch their own internal contradictions instead of emitting confident-but-incoherent output. A new shared module `backend/app/agents/consistency.py` (`ConsistencyCheck` / `ConsistencyReport` Pydantic v2 models, `log_consistency_failures`, and three checkers) is wired into **SummaryAgent**, **ReleaseRiskAgent**, and **AnalysisAgent** on the success path immediately before `validate_agent_contract(...)` — so every consistency result flows into the existing `agent_contracts` metadata (`decision_reason` gains a `; consistency_ok` / `; consistency_check_failed:<names>` suffix and one extra `consistency_report` evidence ref) **without changing any agent's output shape**. Failures are logged as a single structlog `consistency_check_failed` event per failed check (the event name lives in `consistency.py` only — a new ratchet enforces this) and are **never silently dropped**; the underlying analysis payload is read-only to the checker and is never mutated. The checks are **pure functions** of already-computed data: no outbound calls, no DB, no LLM, no migration, and — by hard invariant — **they never raise on any input shape** (all list-typed fields are isinstance-guarded; non-data is treated as "nothing to evaluate ⇒ passed"). **SummaryAgent** verifies referential integrity of every cited test id (cited ids must exist in `failed_test_ids ∪ analyses` keys, compared as strings so int/UUID keys don't false-positive; an empty analyzed universe with cited ids is itself flagged) plus cross-layer coherence (exec-summary ⇄ incident-view ⇄ release-impact ⇄ criticality ⇄ action-plan). **ReleaseRiskAgent** verifies the narrative never contradicts the deterministic score band (`<20 GO`, `20–55 CONDITIONAL_GO`, `≥55 NO_GO`, matching `score_to_recommendation`) using **negation-aware** phrase matching so a correct "not low risk" NO_GO is not flagged, and downgrades a *more-conservative* recommendation (e.g. a pass-rate-floored NO_GO at a low score) or a `policy_id` override from error to warning. **AnalysisAgent** gains expanded confidence-correction rules in `_validate_confidence` (UNKNOWN-category caps, error⇒zero-confidence floor, raw-confidence-vs-evidence cap, flaky-contradicted-by-history) that only ever *lower or correct* values and record an auditable adjustment, plus a stage-level coverage/flaky-floor consistency check. **No behavior change to the analytic payloads, no migration, no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New tests `backend/tests/test_agent_consistency.py` (DB-free behavioral coverage incl. the never-raise, int-key, negation, and conservative-override cases) and a new ratchet `test_target_agents_run_consistency_checks` in `backend/tests/test_architectural_agent_contracts.py`.

### Added (2026-06-12 — AI-Agent Quality: structured agent contracts (AIQ-P1))

First phase of the AI-Agent Quality (AIQ) initiative — making the analytic agents (test reporting, gap-finding, report analysis, engineering-intelligence) a **solid, auditable** signal. AIQ-P1 ratchets the **structured agent-contract** discipline: every analytic agent under `backend/app/agents/` must wrap its output through `validate_agent_contract(...)`, which stamps audit-friendly metadata (`confidence_score` clamped 0-100, `evidence_count`, `decision_reason`, `evidence_refs`, `fallback_used`, `generated_at`) under the output's `agent_contracts` key **without changing the agent's own output shape** (the metadata is additive; validation failure logs a structlog warning and returns the original payload stamped with `contract_validation_error` — it never raises, so the deterministic fallback is guaranteed). Three previously-unwrapped agents are brought under contract — **RunCompareAgent** (both the parsed-success and exception-fallback returns; `RunCompareAgentOutput` uses `extra="allow"` so arbitrary LLM keys pass through untouched), **LogIntelligenceAgent** (tiered confidence + per-source evidence refs derived from trace/anomaly success), and **RegressionWatchman** (`run()` success + error returns; the standalone `_classify`/`run_regression_watchman` path is deliberately left raw). `AgentContractMetadata` gains the explicit `confidence_score`/`evidence_count` fields (derived inside `validate_agent_contract` from the existing `confidence`/`evidence_refs` kwargs, so **none of the 9 prior callers change**) plus a Pydantic v2 `field_validator` clamping `confidence_score` to 0-100. **No behavior change, no migration, no outbound calls, no new service commits** — `AI_OFFLINE_MODE` semantics untouched. New ratchets: `backend/tests/test_architectural_agent_contracts.py` (AST scan asserting every non-infra agent calls `validate_agent_contract`, the required metadata fields exist, and the contracted-output-model count holds at its ≥12 floor — a downward-ratchet guard) and `backend/tests/test_agent_contract_outputs.py` (runtime coverage of the LogIntelligence success/fallback wrapping and the RegressionWatchman contracted output shape).

### Added (2026-06-10 — Granular steps in MCP + CLI (Phase 6))

Phases 1-5 captured and surfaced the LATEST-RUN-ONLY granular step/assertion snapshot across the UI and REST API, but the **MCP server** and **CLI** — the surfaces an AI assistant or a terminal user actually drives — still stopped at the top-line test outcome, so they couldn't answer *where* a test broke without a separate API round-trip. Phase 6 closes that gap and **completes the initiative**. It is purely **additive and best-effort** over the existing read endpoint `GET /runs/{run_id}/tests/{test_id}/steps` (no migration, no backend change, no new write): every new path fetches steps lazily and **degrades silently** when the snapshot is absent (older runs have none), so no existing tool/command output changes when steps aren't present. (1) MCP `get_test_case` gains an opt-in `include_steps` flag that renders the nested step tree as a readable markdown table (step #/indent, name, status, duration, truncated assertion) — default output unchanged. (2) MCP `trigger_ai_analysis` accepts an optional `run_id` and, when given, adds the first FAILED/BROKEN step to the Evidence References as `step N failed: <assertion>` so the agent's root-cause output points at the failing assertion. (3) MCP `search_tests` annotates a result's subtitle with `matched on step text` when the query isn't visible in the test name/suite/error (the Phase 3 keyword index also matches step text), explaining why the hit surfaced. (4) CLI `tests get` gains `--show-steps`, summarising step count + first/last step + the failing step name & assertion. MCP tool **names are unchanged** (a separate branch owns the tool-name collision work). Regression: `mcp/tests/test_mcp_server.py` adds static coverage for each surface (include_steps option + step table, `step N failed` evidence helper, step-match subtitle note).

### Fixed (2026-06-10 — Granular initiative: stale model stub in batch5 runs_service test)

`tests/services/test_batch5_all_projects.py::test_list_project_runs_no_filter_builds_correct_query` stubs `app.models.postgres` with a hand-built `SimpleNamespace` and force-reimports `app.services.runs_service`. Phase 5 added `CanonicalTestCase` to the module-top `from app.models.postgres import (...)` in `runs_service.py` (for the batched canonical step helpers), but the stub — already updated for the Phase 1 `TestStep`/`TestAttachment` imports — was not extended, so the forced reimport raised `ImportError: cannot import name 'CanonicalTestCase'`. The test failed in isolation and in the full suite (not ordering pollution). Fix: rather than enumerate yet another import name (the stub had already broken twice this way), make the fake module **auto-provide** any import-only model via `__getattr__`, so future `from app.models.postgres import X` additions can't re-break the test; only models whose attributes are read at import time (`TestRun.primary_suite_name`) stay explicit. Also dropped the dead `FakeDB`/`FakeResult`/`captured_queries` scaffolding the test never exercised and corrected its docstring to match what it actually asserts (the optional `project_id` signature). Test-only; no product-code change. Surfaced by a full-suite run during crash-recovery re-verification of the granular initiative; stub hardening added after multipass-review of the original fix.


### Added (2026-06-10 — Granular steps in reports/coverage/my-failures (Phase 5))

Phases 1-3 captured a granular step/assertion snapshot per logical test (LATEST-RUN-ONLY, anchored to `canonical_test_cases`), but that detail lived only on the test-case page — the report, analytics, and triage surfaces still showed only the top-line outcome, so an engineer still had to open the test to learn *where* a test broke. Phase 5 surfaces that signal across those high-traffic views. It is pure **enrichment**: every new field is an **additive, OPTIONAL** field on an existing Pydantic v2 response schema (defaults to `None`), so existing consumers are unaffected; there is **no migration** and **no new ingestion write** — these are reads, the service stages/returns and the router owns any commit. All step reads go through **batched** helpers keyed by a *set* of canonical ids / fingerprints (`runs_service.first_failed_step_by_canonical`, `first_failed_step_by_fingerprint`, `step_success_by_canonical`, plus the report service's own batched canonical+steps lookup) so no surface ever N+1s over individual tests. Suite grouping reuses `_effective_suite_sql()` so step labels line up with the existing breakdown, and fingerprint/canonical reads stay project-scoped via the snapshot's project-bound anchor (no cross-tenant leak — multi-tenant/unscoped analytics views leave the field `None` rather than resolve a snapshot).

- **my-failures `last_failure_step`.** Each `MyFailureItem` carries the name of the first FAILED/BROKEN step from the test's latest-run snapshot, batched once per page over the rows' canonical ids; rendered as a "failed at:" line on each inbox row when present.
- **analytics FAILURE LOCATION.** `top_failing_tests` attaches `failure_step` (first FAILED/BROKEN step name) to each top-failing test, resolved by `test_fingerprint` within the in-scope project and surfaced on the failure-analysis "what's failing" card.
- **summary-report step success-rate.** Each `SummarySuiteRow` gains optional `step_success_rate` / `passed_steps` / `total_steps` (share of captured steps that passed, per effective suite); the Summary Report suite table renders a "Step %" column only when at least one suite has step data. Each top-failing test also carries `failure_step` + an ordered `step_breakdown`.
- **PDF step breakdown.** `summary_report_pdf` renders an engineering "failure step breakdown" section for failing tests that have a snapshot — the ordered step list with status (failed/broken cells highlighted) and the failing step's assertion message — rendered over a bounded window centred on the failure location (capped per test, elided-count footer) so a 2000-step test can't blow up the flowable. `reportlab` is not installed locally, so the render-path test is import-guarded/skipped locally and runs in CI.

Regression suites: `backend/tests/test_granular_reports.py` (the batched step-read helpers + the no-N+1 / batched guarantee asserted by counting `execute` calls + project-scoping of the fingerprint helper), `backend/tests/test_my_failures_router.py` (`last_failure_step` enrichment + absence default), `backend/tests/test_summary_report_service.py` + `test_summary_report_router.py` (per-suite step success-rate + per-test failure-step/step-breakdown), `backend/tests/test_summary_report_pdf.py` (render-guarded engineering step section), `backend/tests/regression/test_summary_report_flaky_and_effective_suite.py` (effective-suite grouping still holds with the enrichment).

### Added (2026-06-10 — Duplicate test-case detection (Phase 4))

Phase 4 is the feature's end goal: a tiered, **offline-first, per-project** detector that finds near-duplicate **authored** test cases (`managed_test_cases` — never the execution `test_cases`) so a QA lead can review and merge them instead of re-discovering the same coverage. Detection runs in three tiers, all stdlib/local — no new dependency (`rapidfuzz` deliberately avoided): **Tier 0** is a normalised content fingerprint (sha256 over lowercased/whitespace-collapsed title+objective+steps+expected) that buckets exact matches in one equality scan; **Tier 1** is the structural tier — stdlib `difflib.SequenceMatcher` plus a pure-Python token-set/Jaccard ratio, made word-aware so opposite-sense titles (`valid`/`invalid`, `200`/`404`, `login`/`logout`) are demoted by a discriminating-token band cap instead of inflating on spurious character overlap, with an order-insensitive step-set so reordered-but-identical steps still match; **Tier 2** is an optional semantic pass over only the already-blocked cases, gated on a **local** ChromaDB ONNX embedder (never a cloud embedding function, satisfying the `AI_OFFLINE_MODE=True` default) and skipped silently on any import/client error. To stay off the O(n²) all-pairs path, cases are only compared when they share a **blocking key** (same suite, an overlapping tag, or a shared *rare* title token); blocks, the total pair budget, the per-project case count, and the semantic case count are each capped, and a capped run is flagged `sampled` with a human-readable `note`. Every candidate is **explainable**: it carries per-component scores, a human-readable `reason` (what the pair shares + how it differs), the `method` (fingerprint|structural|semantic) and a `band` (exact|strong|possible). Candidates are idempotent per `(project_id, case_a_id, case_b_id)` with canonical ordering (`case_a_id < case_b_id`, enforced by a DB CHECK + unique constraint), and a dismissed pair stays dismissed across re-runs via a `dismissed_duplicate_pairs` suppression table. Surfaced through `GET/POST /api/v1/projects/{project_id}/duplicate-candidates` (+ `/detect`, `/{id}/dismiss`, `/{id}/merge`), each guarded by `require_project_access()` verifying the **provided** `project_id` (IDOR ratchet); the detection **service stages only** (zero commits — the transaction-boundary ratchet stays green) while the router and the nightly Celery beat task own the commit, the beat task fanning out one sub-task per project so a single slow project can't starve the sweep under the task time limit. A new **Duplicates tab** on Test Management renders the banded review queue with the score chips, the reason, and detect/dismiss/merge actions. **MERGE IS NON-DESTRUCTIVE this phase** (pending product sign-off): resolving a pair only flips the candidate `status` to `merged` and may soft-deprecate the losing case (status flag + `is_stale`) — it never deletes a case, redirects `test_fingerprint`, or destroys data. Migration `0094` adds `managed_test_cases.dup_fingerprint` (indexed, lazily populated — no backfill) plus the `duplicate_test_case_candidates` and `dismissed_duplicate_pairs` tables. Regression suites: `backend/tests/test_duplicate_detection.py` (Tier 0-2 scoring, blocking, per-project scope, dismissal suppression, non-destructive merge), `backend/tests/test_duplicates_router.py` (IDOR, detect/dismiss/merge/list HTTP behaviour, commit ownership), `backend/tests/test_duplicate_detection_task_fanout.py` (beat fan-out + per-project commit).

### Added (2026-06-09 — Granular steps for Playwright/Cypress/TestNG + step search (Phase 3))

Phase 3 extends the granular step snapshot to the three remaining parsers (Playwright, Cypress, TestNG) so they emit the **same common step dict** Allure/pytest already produce and flow through the unchanged Phase 1 `_persist_step_snapshot`/`_insert_step` persistence — **no new migration, no new persistence code**, inheriting the shared depth/node caps + PII redaction for free. TestNG synthesizes coarse pseudo-steps preserving the `<failure>`→FAILED / `<error>`→BROKEN distinction (+ reporter/system-out log pseudo-steps) and now surfaces `stack_trace`; Cypress emits a single assertion step carrying its unique structured `expected`/`actual` chai diff; Playwright emits the native nested step tree (status from per-step `error` presence) + a synthetic step per remaining `errors[]` entry and carries `retry_count`/`is_flaky_run`/`stack_trace`. The `RunDetailPage` test-row list shows a `step_count` badge so a test's granularity is visible without opening the steps panel. It also surfaces step text in search: the keyword path matches a project-scoped correlated `EXISTS` over the test's own canonical steps, and the semantic embedding document now concatenates step names + assertion messages. Regression suites: `test_granular_steps_ingestion.py` (TestNG failure/error mapping), `tests/services/test_cypress_playwright_parsers.py` (Cypress expected/actual, Playwright steps/errors/flaky), `tests/regression/test_search_step_text_indexing.py` (keyword EXISTS + project scope + semantic doc step text).

- **Search surfacing — gate (c) accepted deviations (documented, not silent).** The keyword step-match is a project-scoped correlated `EXISTS` on the test's own canonical (NULL canonical → no match) under the always-`project_id`-bound outer query — offline-safe (pure SQL, no embeddings). For the **semantic** path, two pre-existing architecture choices in `semantic_search.py` are explicitly recorded rather than left implicit, since Phase 3 only widens the embedded document (it does not change the collection or embedder): (1) **single shared ChromaDB collection** isolated by a query-time `project_id` metadata filter + a Postgres-layer re-filter (defence-in-depth), rather than a physical per-project collection — no row is ever returned outside the caller's `project_id`/`allowed_project_ids`, so the rule is satisfied in effect; the physical per-project shard is deferred because it would also have to re-shape the cross-project full-reindex batching and the multi-project `$in` query fan-out. (2) **Offline-safe by construction** — the semantic path is opt-in (`search_type=semantic`/`hybrid`; router defaults to keyword) and falls back to keyword on any ChromaDB error; `_get_or_create_collection` passes **no** explicit `embedding_function`, so ChromaDB's bundled **local ONNX all-MiniLM** model is used (never a cloud API), which is why no `AI_OFFLINE_MODE` early-return is needed under the `AI_OFFLINE_MODE=True` default. Both deviations are now captured in the module + `_get_or_create_collection` docstrings; a future cloud `embedding_function` MUST be gated on `AI_OFFLINE_MODE` + a local-embedder check.

### Added (2026-06-09 — Granular test-case steps: Allure + pytest capture (Phase 1))

Test results showed only the top-line outcome per test — to see *where* a test failed (which step, which assertion, which screenshot) users had to leave TestLookup and open the raw Allure/pytest report. Phase 1 of the granular initiative captures a step/attachment **snapshot** at ingest and surfaces it on the test-case page.

- **Latest-run-only snapshot model (migration 0093).** Two new tables — `test_steps` (ordered, self-referencing nested step tree: name/keyword/status/duration/assertion message+trace/expected+actual/parameters) and `test_attachments` (index-only metadata — `source_ref`+`media_type`, no byte-proxying in Phase 1). Both anchor to the project-scoped `canonical_test_cases` identity (one snapshot per `(project_id, test_fingerprint)`, `ON DELETE CASCADE`), **not** the per-run `test_cases` rows, so there is exactly one snapshot per logical test. `source_test_run_id` (`SET NULL`) records which run produced it. On each new run for the same canonical test, ingestion does a **delete-then-insert overwrite** so the snapshot always reflects the latest run — no per-step time-series, smallest footprint. Also added four nullable per-run metadata columns to `test_cases` (`retry_count`, `is_flaky_run`, `stack_trace`, `step_count`) populated by the parsers; no backfill of history (going-forward only).
- **Allure + pytest step extraction.** The Allure parser recursively flattens the arbitrarily-nested `steps` tree (before/after fixtures + sub-steps) into the common step dict, mapping each node's status into the strict `PASSED/FAILED/SKIPPED/BROKEN/UNKNOWN` vocab (a value outside it would silently 422 the read endpoint) and indexing per-step + test-level attachments. Untrusted-input hardening: depth (`_MAX_STEP_DEPTH=20`) and node-budget (`_MAX_STEP_NODES=2000`) caps so a pathological customer `-result.json` can't `RecursionError` the worker or materialise unbounded rows mid-transaction. The pytest parser synthesizes one pseudo-step per execution phase (setup/call/teardown) from `--json-report`, each carrying its own outcome/duration/trace.
- **Staged inside the ingestion transaction (no new service commit).** `_persist_step_snapshot` / `_insert_step` do the delete-then-insert via `db.add`/`db.flush` and return; the ingestion router still owns the single `await db.commit()`, so the transaction-boundary ratchet is unaffected. Idempotent on re-ingest of the same run (the delete clears any prior rows first).
- **Read endpoint `GET /api/v1/runs/{run_id}/tests/{test_id}/steps`** — lazy/separate from the test-case detail payload (detail endpoint unchanged; clients fetch on demand). Guarded by `require_run_access()` which verifies the **provided** `run_id` (IDOR ratchet). `runs_service.get_test_steps_tree` resolves the canonical snapshot and returns the ordered, nested step tree with per-step + test-level attachments.
- **TestCase Steps UI panel.** New `components/runs/TestStepsPanel.tsx` (SWR via `useTestSteps`) renders the nested step timeline on `TestCasePage` — status badges, durations, expand/collapse, failed-step assertion traces, and attachment refs.
- Tests: `backend/tests/test_granular_steps_ingestion.py` (13 tests — Allure recursive flattening, pytest phase pseudo-steps, depth/node caps, latest-run delete-then-insert + same-run idempotency, the endpoint's `require_run_access` IDOR guard, migration 0093 columns/downgrade, and ORM snapshot relationships).

### Added (2026-06-09 — Test-case history, flakiness & metadata panel (Phase 2))

The test-case page showed only the current run's outcome plus the Phase 1 step snapshot — to judge whether a failure was a one-off, a flake, or a hard regression, and to find the owner/suite/age of the test, users had to cross-reference `/failures`, `/flaky-coach`, and the catalog by hand. Phase 2 folds that signal onto the test-case detail itself. **Read-only — no migration, no new ingestion writes, no new service commit;** it reuses the existing machinery rather than recomputing anything.

- **Read endpoint `GET /api/v1/runs/{run_id}/tests/{test_id}/history`** — lazy/separate from the test-case detail payload (detail endpoint unchanged; clients fetch on demand). Guarded by `require_run_access()` which verifies the **provided** `run_id` (IDOR ratchet). It resolves the test's `test_fingerprint` + `project_id` from that run, then returns a project-scoped `{history, flakiness, metadata}`. Because `test_fingerprint` is **not** project-salted, every history/flakiness query JOINs `test_runs` and filters on `project_id` — a same-fingerprint test in another tenant can never leak into the timeline.
- **Reuses existing machinery, invents no new formula.** Timeline = the windowed `ROW_NUMBER() PARTITION BY test_fingerprint ORDER BY created_at DESC` pattern over `test_case_history` already used by `test_health_coach_service.refresh_flaky_coach`, capped to a 50-run display depth inside the same 30-day window the flakiness value honours. Flakiness `failure_rate`/`failure_rate_pct` (FAILED+BROKEN over total in-window) match `analytics_service.flaky_tests`, and `is_flaky` gates on that detector's identical `≥3 runs` + `0.05–0.95` ratio band so the panel agrees with `/failures`; `classification` + `impact_score` reuse `test_health_coach_service` thresholds. Metadata surfaces owner (`TestCase.owner`/`assigned_to_user_id`), the **effective** suite via `_effective_suite_sql()` (both `tc.suite_name` and `tr.primary_suite_name`), per-test `severity`/`feature` (the existing per-test criticality fields), and first/last-seen run labels + catalog timestamps from `canonical_test_cases`.
- **No transaction-boundary or migration impact.** The new `services/test_case_history_service.py` only reads (`db.execute(select(...))`); the router owns the no-op transaction. No allowlist/cap bump, no schema change.
- **TestCase History & Flakiness UI panel.** New `components/runs/TestHistoryPanel.tsx` (SWR via `useTestCaseHistory`, single Axios base via `runsService.getTestHistory`) renders the pass/fail dot timeline, the flakiness badges (classification / fail-% / impact when flaky) with pass/fail/run counts, and the metadata block (suite, owner, severity, feature, first/last seen, created/updated) beside the Phase 1 Steps panel on `TestCasePage`.
- Tests: `backend/tests/test_granular_history.py` (8 tests — cross-project no-leak scoping, flakiness matching the analytics formula, 30-day-window exclusion of stale rows, the `0.05–0.95` flaky band, empty-history HEALTHY block, metadata first/last-seen labels, wrong-run resolver 404, and the endpoint's `require_run_access` IDOR guard).

### Tested (2026-06-08 — /releases delete: cascade contract regression)

Pinned the cascade contract behind the `/releases` **Delete** button (backend `DELETE /api/v1/releases/{id}` → `release_service.delete_release` → bare `db.delete(release)`). Audit confirmed the end-to-end delete path already exists (ADMIN-gated, project-scoped endpoint; UI button + confirm + toast + SWR refetch), so no behaviour change — added the missing guard tests:
- `Release.phases` and `Release.test_run_links` must stay `delete-orphan` (dropping it would 500 any delete of a release that has phases or linked runs via an FK `IntegrityError`).
- Deleting a release must **not** cascade into `TestRun` — `Release` holds no relationship to `TestRun`, and the link's `test_run_id`/`release_id` FKs are `ON DELETE CASCADE` (run-removal clears the link, never the reverse). Runs survive release deletion.
- `backend/tests/regression/test_release_delete_cascade.py` (4 tests, mapper-introspection — no DB needed).

### Fixed (2026-06-08 — /failures vs /flaky-coach disagreed on what's flaky)

`/failures` rendered *"Not a flake — flake detector found zero intermittents. Treat as a hard regression, not a re-run candidate."* for projects where `/flaky-coach` **did** list flaky tests. Root cause: two endpoints with two different flaky definitions. `/flaky-coach` (`test_health_coach_service`) has merged human-triaged `FLAKY_TEST` rows (from `/my-failures`) on top of auto-detection since 2026-05-18; `/failures` (`analytics_service.flaky_tests`) was auto-detect **only** (intermittent `test_case_history`, ≥3 runs, both pass+fail) and ignored manual triage entirely — so `flakyCount` came back 0 and the page declared a hard regression, contradicting `/flaky-coach`.

- `analytics_service.flaky_tests` now merges the same manually-triaged `FLAKY_TEST` fingerprints (same tenant + suite scoping; deduped by fingerprint with auto winning since it carries a real ratio; capped at `limit`). Each row is tagged `source` (`auto`/`manual`); manual entries use `failure_rate_pct=100` as the "human-flagged" marker, mirroring flaky-coach's `failure_rate=1.0`. Additive field — existing consumers (dashboard widgets) are unaffected.
- Frontend `FlakyTestItem` type gains optional `source`/`class_name` so the UI can render manual entries distinctly.
- **Multi-pass adversarial review fixes (UX):** the `/failures` Flakiness card no longer (a) renders manual entries as a misleading "100% flake" — they show a blue **"Flagged"** badge ("Manually triaged as flaky on /my-failures"); (b) tanks the *Flake-free* dimension score to 0 because of the 100 marker — manual entries are excluded from the measured score while still counting toward `flakyCount`/verdict; (c) describes manual entries as "intermittent pass/fail patterns" — the lede now describes each source present. Backend merge also skips NULL/empty fingerprints defensively.
- Tests: `backend/tests/regression/test_flaky_failures_consistency.py` (merge, auto-wins dedup, limit-skips-manual, empty-stays-empty, combined-limit, **tenant-scope pass-through, suite-filter pass-through, null-fingerprint skip**) + a `FailureAnalysisPage.test.tsx` case (verdict flips off "hard regression"; renders "Flagged" not "100% flake") + updated the existing `flaky_tests` service test for the new second query.
- **Known follow-ups (documented, not in this change):** (1) the auto-detector *band* still differs — `analytics_service` excludes <5%/>95% rates as deterministic while `test_health_coach_service` includes them; this only diverges at extreme tails needing many runs, and at >95% the `/failures` "hard regression" wording is arguably the more correct one, so aligning the cached quarantine detector is deferred for product sign-off. (2) `/flaky-coach` reads a project-scoped `FlakyCoachResult` cache and its endpoint takes no `suite_name`, so it can't be suite-filtered like `/failures` — a separate enhancement.

### Added (2026-06-08 — /agents: run/suite context on each pipeline card)

Each AI-pipeline card on `/agents` now shows **which run/suite it analysed** — previously it showed only a workflow type, so users couldn't tell what a pipeline was for. The `GET /api/v1/agents/pipelines` (+ `/pipelines/{id}`) responses gained `build_number`, `run_seq`, and `suite_name`, attached from the owning `TestRun` on the read path (`_attach_run_context`, in-place like `_apply_effective_status`); `run_seq` reuses the shared per-(project, primary_suite_name) "Run #N" numbering (`runs_service.fetch_run_seq_map`) so the label matches `/runs` and `/live`. The card renders `Run #N · <suite>`, falling back to `Build <n>` then a short run-id when `run_seq`/suite are absent (legacy rows stay `null`, never error). The TestRun lookup is bounded to the already-tenant-scoped pipeline ids. Tests: backend `_attach_run_context` (attach, legacy-null, run_seq-independent-of-suite, empty-no-query, TestRun-without-run_seq) + a schema-serialization test proving the non-mapped ORM attrs flow through `AgentPipelineResponse` (`from_attributes`) + two `AgentStatusPage` card-render cases. A 4-lens adversarial review (correctness / security / UX / coverage) found **no code defects** — security confirmed the context lookup is tenant-safe (bounded to scoped ids; `get_pipeline` checks access first); the only gaps were the extra tests now added (endpoint-level integration tests remain a CI follow-up).

### Changed (2026-06-08 — /agents: AI report expanded by default)

The `/agents` (AI Pipelines) page now shows the **AI report by default** once a pipeline run is selected — it's the headline output, so users no longer have to click "View AI report" to see it. `showSummary` defaults to `true` (and stays expanded when switching between runs); the toggle still collapses it ("Hide report"). The report is still lazy-fetched, now triggered as soon as a pipeline is picked. Tests: a new `AgentStatusPage.test.tsx` case asserts the report content renders without a click and the toggle reads "Hide report"; updated three existing tests that previously had to click "View AI report". (Per-run/suite *context* on each pipeline card is a follow-up in the same branch.)

### Changed (2026-06-08 — run identifiers show when the run was generated)

"Run #N" repeats per (project, suite) and across projects, so the same label can point at many different executions — on `/live` ("Run #1") and elsewhere it was impossible to tell same-numbered rows apart. Added a shared, null-safe `formatRunWhen(iso)` helper (`utils/formatters.ts`, compact `MMM dd, HH:mm`; returns `''` for missing/invalid timestamps so legacy rows never render "Invalid Date"). Wired into the `/live` sessions table, the `/agents` **live-run card**, and `/my-failures` — each shows the run's start time **inline** beside Run #N (consistent compact format). On `/deep-investigation` the run timestamp was upgraded from date-only to date+time (same compact helper) so same-day runs are distinguishable. The other run-label sites already render a run datetime in their own established layout and were left as-is to avoid regressing them: `/runs` and `/intelligence` use a dedicated full `toLocaleString` **date column** (kept verbose by design — a different UI element from the inline run-label treatment), run-compare embeds `created` in its option, and `/intelligence`'s activity feed uses a relative `fromNow`. `run_seq`/`build_number`/UUID routes are untouched everywhere (display-only).

A 4-lens adversarial review (correctness / security / UX / coverage) found **no functional defects** (the helper's null/invalid guards, field mappings, and frontend-only scope all verified). Its UX findings were applied: `/my-failures` moved from a hover-only tooltip to an inline datetime (matching `/live`); the `/agents` live-card timestamp got `whitespace-nowrap`/`shrink-0` to avoid awkward flex-wrap; double `formatRunWhen` calls were collapsed to a single computed value. Tests: `utils/formatters.test.ts` (format, ISO, null/empty/invalid → '') + a `MyFailuresPage` render test asserting the inline run datetime.

### Fixed (2026-06-07 — Manual upload: MRU-14 review)

Review found 2 high + 1 low; fixed before they shipped:
- **Auto-detect no longer misses real CI reports:** the pytest sniffer required `"summary"`, which sits *after* pytest-json-report's unbounded `environment` block and is pushed past the 4 KB detection window on package-heavy CI images (→ silently mis-parsed as Allure → empty run). Now keys on `exitcode`+`root` alone (both top-of-doc + unique).
- **No more cross-file fingerprint collisions:** function/class tests folded the file only into `suite_name`, but the dedup fingerprint is `class_name::test_name` — so `test_a.py::test_smoke` and `test_b.py::test_smoke` collided and one silently overwrote the other. The file is now folded into `class_name` (matching the playwright/cypress parsers); `suite_name` stays the bare file for grouping.
- Parametrized nodeids with `::` inside the `[...]` id (`test_q[a::b]`) split correctly. Tests added for all three.

### Added (2026-06-07 — Manual upload: pytest-json-report parser (MRU-14))

- **Native `pytest --json-report` support.** A new `pytest_parser` ingests the pytest-json-report plugin's JSON: each `tests[]` entry's `nodeid` → suite (file) + class + name, `outcome` → status (passed→PASSED, failed→FAILED, error→BROKEN, skipped/xfailed→SKIPPED, xpassed→PASSED), duration = setup+call+teardown (s→ms), and the failing phase's `longrepr` → error message. Auto-detected by the distinctive top-level `exitcode`+`root`+`summary` markers; also selectable as "pytest JSON" in the modal and accepted via `format=pytest`. (pytest's JUnit XML continues to work via the JUnit parser.) Works inside a zip too (tier-2). Tests: parser status/nodeid/duration mapping, malformed→empty, and format detection.

### Fixed (2026-06-07 — Manual upload: MRU-15/16/17 review)

- **Failure metrics no longer undercount:** the retry-exhausted terminal (outer task handler) set a FAILED status but emitted no metric (the `_emit_failed` closure is scoped to the inner coroutine). It now emits `uploads_total{state=failed}` + `upload_failures_total{code=infra_error}` (distinct from the parse-time `ingest_error`), so the counters balance the terminal-status writes.
- Clarified `upload_processing_seconds` help (success-only by design) and added the backend tests the prior entry referenced (migration 0092 chain/downgrade + metrics label sets).

### Added (2026-06-07 — Manual upload: metrics, in-app help, rollout flag (MRU-15/16/17))

- **MRU-17 — rollout flag.** The upload UI (the `/runs` "Upload report" button, the sidebar "Upload Report" item, and the `/runs?upload=1` deep-link) is gated behind a new `manual_upload` feature flag (migration 0092, **default OFF**) — an ADMIN enables it per environment/project/role from Settings › Feature Flags, mirroring `cypress_ingest`/`playwright_ingest`. A new `useFeatureEnabled(key)` hook resolves the flag for the active project. The `POST /api/v1/ingest/file` endpoint itself is not gated (API/CI clients unaffected).
- **MRU-15 — metrics.** Prometheus counters/histogram for uploads: `testlookup_uploads_total{state,format}`, `testlookup_upload_failures_total{code}`, and `testlookup_upload_processing_seconds`, emitted at each worker terminal (succeeded / parse_error / empty_report / ingest_error / zip safety codes).
- **MRU-16 — in-app help.** The upload modal has a "Supported formats & how to export" expandable listing each framework's export (JUnit/TestNG XML, Allure JSON or zip, Playwright `--reporter=json`, Cypress Mochawesome, multi-file). (Kept in-app since `docs/` is gitignored.)
- Tests: Sidebar flag-gate (hidden off / shown on); migration 0092 chains + downgrade; metrics import.

### Added (2026-06-07 — Manual upload: raw archival + skip-AI toggle (MRU-9 / MRU-8))

- **MRU-9 — Raw uploaded files are archived** (byte-exact) to the storage backend under `uploads/{project_id}/{run_id}/{filename}` for audit/replay; the key is linked onto the run's `minio_prefix`. Best-effort — a storage hiccup never fails the ingest.
- **MRU-8 — "Skip AI analysis" toggle** in the upload modal. When checked, `finalize_run(run_ai=False)` skips the agent-pipeline enqueue (faster ingest, no LLM cost) while everything else (clustering inputs, owners, aggregates) still runs; the user can trigger AI later from the run page. `run_ai` defaults to true and threads endpoint → task → `finalize_run` (the SDK-batch and webhook paths are unaffected — they keep the default).
- Tests: router asserts `run_ai` flows + the raw file is archived (`put_object` called, key passed); service asserts the `run_ai` field; modal toggle wired.

### Added (2026-06-07 — Manual upload: multi-file selection (MRU-13))

- **Upload several report files at once.** The modal now accepts multiple files; when more than one is selected they're **zipped client-side** (via `fflate`) into a single `reports-bundle.zip` and sent through the existing archive path (the backend tier-2 detects + parses each entry), so "N JUnit XMLs" or a mixed set ingest as one run. A single file still uploads as-is. Duplicate filenames in a bundle are de-duplicated; total selection is size-capped client-side. Test: selecting 2 files produces one `application/zip` upload.

### Fixed (2026-06-06 — Manual upload: MRU-12 review hardening)

Multi-pass review (3 lenses, adversarially verified) of the zip wiring found 9 issues; the high + quick wins are fixed:

- **Closed a feature-flag bypass** (was: high — all 3 review highs). Zipping a Cypress/Playwright report skipped the admin-gated `cypress_ingest`/`playwright_ingest` flag (the router only gated single files). The router now resolves the disabled gated formats and passes `disabled_formats` to the worker, which **skips matching tier-2 entries** — a zip can't re-enable a disabled parser. Covered by a test (gated → skipped, allowed → parsed) and a parametrized router test (`format=cypress` + zip → `archive`, never 503).
- **Fixed a latent crash** — several worker `logger.warning(..., key=val)` calls used structlog-style kwargs, but `worker/tasks.py`'s `logger` is **stdlib** (`%s` positional), so the parse-error/archive paths would have raised `TypeError` mid-handler. Converted to `%s` style.
- Freed the compressed `raw` bytes after extraction (memory peak); added tests for noise-only zip → empty, and `UnsafeZipError` propagating through the `archive` dispatch.

### Added (2026-06-06 — Manual upload: Allure-zip ingestion wired (MRU-12))

- **Upload an Allure results ZIP (or a zip of several reports) from the UI.** The endpoint now detects a zip by `PK` magic / `.zip` **before** the utf-8 decode (binary-safe), base64-encodes it for the Celery JSON transport, and dispatches `file_format="archive"`. The worker's `_parse_archive_to_results` safe-extracts in memory (the MRU-11 `safe_extract_zip` with config-driven limits) then dispatches **tier-1** (any `*-result.json` → `parse_allure_zip`) or **tier-2** (heterogeneous, e.g. N JUnit XMLs → per-entry detect + existing parsers), filtering `__MACOSX`/dotfile noise. A zip-safety violation surfaces as the specific `error.code` (`zip_bomb`/`unsafe_path`/…) in the upload status, not a generic failure.
- Archive limits are configurable: `MAX_ARCHIVE_UNCOMPRESSED_BYTES` (200 MB), `MAX_ARCHIVE_ENTRIES` (5 000), `MAX_ARCHIVE_ENTRY_BYTES` (50 MB), `MAX_ARCHIVE_RATIO` (100×). The modal now accepts `.zip`. Tests: `test_archive_upload.py` (tier-1/tier-2 dispatch, noise filtering, zip-bomb propagation) + a router test asserting zip → `archive` + base64. Green on py3.11.

### Added (2026-06-06 — Manual upload: Allure-zip spike PoC (MRU-11))

- **Spike for Allure ZIP upload resolved (GO)** with a proof-of-concept (not yet wired to the endpoint — that's MRU-12). `app/services/safe_archive.py` adds a hardened, in-memory `safe_extract_zip` that enforces concrete limits — 200 MB uncompressed total, 5 000 entries, 50 MB/entry, 100× ratio — and rejects path traversal/absolute/UNC, symlinks, and nested archives, streaming each entry with a running byte cap (never trusting `ZipInfo.file_size`); each violation raises `UnsafeZipError(code)` (`zip_bomb`/`zip_too_large`/`too_many_entries`/`unsafe_path`/`nested_zip`/`bad_zip`).
- `allure_parser.parse_allure_zip(files, run_id, s3_prefix)` reuses `parse_allure_result` per `*-result.json`, backfills `suite_name` from `*-container.json` hierarchy (only when no suite label), and collapses retries by `historyId` to the latest attempt with `is_flaky`/`retry_count`. Decision note + concrete limits table recorded in `docs/PRD-manual-report-upload.md` §9.1. Tests: `test_allure_zip_spike.py` (12 — parse + every modelled attack). Green on py3.11.

### Fixed (2026-06-06 — Manual upload: MRU-5/6 review hardening)

Multi-pass review (4 lenses, adversarially verified) of the status/parse-error slice found 15 issues; the highs/mediums + quick wins are fixed:

- **Success count is no longer always "0 passed · 0 failed"** (was: high). The per-status summary compared UPPERCASE while the JUnit/TestNG/Allure parsers emit lowercase — fixed with a case-insensitive `_summarize_upload` helper. `total` now reflects rows **actually ingested** (not parsed), and an all-rows-failed ingest reports `failed` instead of "succeeded, 0 tests".
- **The status endpoint's IDOR/auth surface is now tested** (was: high coverage gap) — 404 unknown, 403 cross-project, 200 member (+ asserts `project_id` isn't leaked in the response).
- **No spurious 403 on the status poll** — the worker is now enqueued with the canonical UUID so its status writes match the seeded `pending` record and the project-scoped-key check.
- **`get_status` is now best-effort** (symmetric with `set_status`): a Redis outage degrades to a clean 404/"still processing" instead of a 500; non-dict payloads return None.
- **Frontend poll** now treats a 403 as a real error (stops, surfaces it) instead of masking it as success after timeout, and the ~60s timeout fallback renders a neutral "still processing" (clock) state rather than a green check.
- Annotation fix on the new endpoint (`tuple[User, uuid.UUID | None]`). Tests added: casing/total summary, malformed-Allure-raises, endpoint 404/403/200.

### Added (2026-06-06 — Manual upload: async status + parse-error feedback)

- **Upload no longer fails silently** (PRD MRU-5/6). A new Redis-backed status record (`app/services/upload_status.py`, 24h TTL) is updated by the `ingest_uploaded_file` task at each stage (`pending → parsing → ingesting → succeeded | failed`) and exposed via **`GET /api/v1/ingest/uploads/{task_id}`** (project-scoped — 404 if unknown/expired, 403 if the caller can't access the run's project, so a guessed task_id can't leak another tenant's run).
- **Parse failures and empty reports are now surfaced, not swallowed.** A parser exception → `failed` with `error.code=parse_error`; a valid-but-zero-tests file → `failed` with `empty_report`; malformed Allure JSON now raises instead of silently producing an empty run. Parse/empty failures are **not retried** (the file won't parse on retry); only transient infra errors retry, surfacing `ingest_error` once exhausted.
- **The upload modal polls the status** after the 202 and shows a real outcome: a "Processing…" spinner, then **"Processed N tests (P passed, F failed)"** with **View run**, or the parse error inline with retry. Falls back to "still processing" after ~60s.
- Tests: `test_upload_status.py` (roundtrip, TTL, missing→None, error-swallowing) + modal polling success/parse-failure. All green on the py3.11 venv.

### Fixed (2026-06-06 — Manual upload: review-driven hardening + collision policy)

A multi-pass review (5 lenses, adversarially verified) of the upload feature surfaced 16 confirmed issues; the highs/mediums + quick wins are fixed here (pulling MRU-7's collision policy forward):

- **Uploads no longer merge into an unrelated run on a build-label collision** (was: high — silent data corruption). `create_run_from_payload` gained `reuse_existing` (default True keeps SDK/CI retry-idempotency); the manual-upload task passes `reuse_existing=False`, so an upload **always creates a fresh run**, auto-suffixing the build label (`-2`, `-3`, … then a random token) via `_unique_build_number` when it collides. This also makes the 202 `run_id` authoritative, fixing the **"View run" → 404** where a merged run discarded the response id.
- **SDK/CI reuse no longer risks clobbering `ingestion_source`** — the reuse branch returns the existing run untouched (pinned by a new test: a `live` run reused by an `sdk` ingest stays `live`).
- **Default build label is now millisecond + random** (was per-second), so back-to-back / concurrent blank-label uploads don't collide.
- **Modal: a rejected file now clears any prior valid selection** (was: could submit a stale file under a new file's error) and resets the input; **422 `detail` arrays no longer render as `[object Object]`** (string-guarded); **Space on the dropzone no longer scrolls the modal** (`preventDefault`).
- Tests: backend collision/reuse/suffix cases + `UploadReportModal.test.tsx` (gating, validation rejection + clear, success → `onSuccess`). All green on the py3.11 venv.

### Added (2026-06-06 — Manual report upload UI)

- **Upload a test report from the UI** (PRD MRU-4). New `UploadReportModal` + `reportUploadService` wire the existing `POST /api/v1/ingest/file` endpoint to a drag-and-drop modal: pick a JUnit/TestNG `.xml` or Allure/Playwright/Cypress `.json` file, choose a format (default auto-detect), optionally set build label / release / branch / commit, and upload with a live progress bar. On accept (202) the user is deep-linked to the new run; uploaded runs carry `ingestion_source='upload'` and show the "Uploaded" badge. Client-side guards: 50 MB cap (matches backend), extension allow-list, and All-Projects mode disables upload (a run must target one project). Entry points: an "Upload report" button on `/runs` and an "Upload Report" item in the Testing sidebar group (`/runs?upload=1` deep-link). Gated to QA-engineer+. Errors (incl. a 503 for a disabled Cypress/Playwright feature flag) surface inline. Async parse status feedback is the next slice (MRU-5).

### Added (2026-06-06 — Run source tracking for manual report upload)

- **`TestRun.ingestion_source`** (`live | sdk | upload | file | unknown`) records how a run's results entered TestLookup (migration `0091`, new `IngestionSource` enum). It's set at every run-creation site — live-stream stub/upsert/drainer/persist → `live`, SDK batch (`/api/v1/ingest`) → `sdk`, manual file upload (`/api/v1/ingest/file`) → `upload`, MinIO/sentinel webhook → `file` — and `create_run_from_payload` now threads a caller-supplied `ingestion_source`. Existing rows are backfilled by a best-effort heuristic (event_archive/live_stream → `live`; trigger_source `api` → `sdk`; minio_prefix → `file`; else `unknown`). The column is `NOT NULL` with `server_default='unknown'`; downgrade drops it.
- **API + UI expose the source:** `TestRunSummary` carries `ingestion_source`, and the `/runs` table renders an **"Uploaded"** badge for `ingestion_source='upload'`. This is the first slice of the Manual Test Report Upload feature (see `docs/PRD-manual-report-upload.md`, tickets MRU-1/MRU-2/MRU-3); the backend file-ingestion path + parsers already existed and are unchanged.

### Added (2026-04-25/26 — Phase OS-Deploy)

- **Multi-cloud Kubernetes overlays** -- `k8s/overlays/{aws-eks,gcp-gke,azure-aks,self-hosted}/` cover the four major deployment targets. All inherit the existing `prod` overlay so HPA tuning and CORS config stay shared; each only patches what's actually cloud-specific (Ingress class, StorageClass, image registry).
- **Single-command multi-cloud deploy script** -- `scripts/deploy-k8s.sh --cloud=<aws|gcp|azure|self-hosted> [--registry --image-tag --dry-run]` validates kubectl context, pre-checks the `testlookup-secrets` Secret with a copy-paste-ready create command, supports image rewrite via `kustomize edit set image`, and blocks on backend + frontend rollout.
- **Deployment guides** -- `docs/deployment/README.md` (index + decision matrix) plus `aws-eks.md`, `gcp-gke.md`, `azure-aks.md`, `self-hosted-k8s.md` -- end-to-end walkthroughs covering cluster bootstrap, registry push, managed-services choice, secrets, deploy, DNS, verify, cleanup, and a per-cloud common-issues section.
- **WebSocket authentication handshake** -- `/ws/live/{project_id}` now requires `{"type":"auth","token":"<JWT>"}` within 10 seconds of connect. Uses the same `decode_token` + `get_accessible_project_ids` chain as REST, so WS and HTTP share one membership truth.
- **Per-connection audit trail** -- `ws_connect` and `ws_disconnect` events flow into the existing `access_audit_logs` table with reason codes (`client_disconnect`, `token_expired`, `refresh_failed`, etc.). Audit failures never break the WebSocket.
- **In-place WS token refresh** -- clients can send `{"type":"refresh","token":"<new>"}` to extend the session without dropping the connection. Server replies `{"type":"refreshed","exp":<epoch>}`. Avoids the 5-second reconnect gap on token rotation.
- **Project-existence guard on entity creation** -- `create_managed_test_case` and `create_test_plan` now verify the project exists before insert and return a clean `404` instead of letting `asyncpg.ForeignKeyViolationError` bubble up as an opaque `500`. Pattern can be applied to other entity-create services.

### Changed (2026-04-25/26 — Phase OS-Deploy)

- **Empty-state synthetic workflow stages now render as `'skipped'`** with a `skipped_reason` (was `'pending'`, which looked active). Affects `/runs`, `/intelligence`, `/failures`, `/coverage`, `/overview`. Also fixed `coverage_risk` misuse of `'running'` (showed a spinner when failing suites existed even though no work was running).
- **Frontend project store self-heals** -- `refreshProjects()` validates the persisted `activeProjectId` against the fresh project list and clears stale IDs. Prevents cross-deployment localStorage from making every mutation 500.
- **Vite dev-server proxy is in-container friendly** -- `/api`, `/webhooks`, `/ws` now read `process.env.VITE_PROXY_TARGET` (defaults to `localhost:8000`). Docker Compose sets it to `http://backend:8000` so the browser-host can reach the in-network backend.
- **Frontend bundle uses same-origin URLs in dev** -- `VITE_API_BASE_URL=` (empty) so the bundle uses relative paths through the working Vite proxy. Eliminates cross-origin CORS/CSP issues.
- **`Makefile` pins `SHELL := bash`** -- prevents `make` from silently falling back to `cmd.exe` on Windows, which can't parse `until`/`do`/`done` in the demo target's health-wait loop.
- **`live.router` moved to `PUBLIC_ROUTERS`** -- the WebSocket scope can't run HTTP-only `OAuth2PasswordBearer`, so the auto-applied protected-router auth crashed every connect. WS now does its own auth via the handshake; the router's HTTP route still has its own `verify_webhook_secret`.
- **Frontend live-execution hook holds token in a ref** -- token rotation no longer recreates the connect callback (which would tear down the socket). New effect sends a `refresh` frame to the open WS instead.
- **`CLAUDE.md` cleanup** -- fixed stale MCP/CLI counts (24 → 48 tools), slimmed Coding Conventions to 9 cross-cutting rules (was 24+7 bullets duplicated from subdir CLAUDE.md files), trimmed Known Pitfalls 31 → 14, replaced static feature-flag table with pointer to live inventory. 324 → 267 lines.
- **Homelab K3s overlay** -- now ships `netpol-homelab.yaml` (allows colocated data stores + Traefik ingress) and `frontend-nginx-configmap.yaml` (pre-rendered nginx config, mounted via subPath, with `command: [nginx, -g, "daemon off;"]` to bypass `/docker-entrypoint.sh`).

### Fixed (2026-06-06 — CI test-ordering pollution from the PR #164 regression tests)

- **CORRECTION to the entry below:** the 13 failures previously written off as "stale clobber" were **not stale** — they fail only in the **full-suite run** (`pytest tests/`), not in isolation, which is why the earlier isolated reproduction looked green. Root cause: two of the PR #164 regression tests leak global state into the rest of the suite. Both product code paths are correct; the fixes are test-isolation only. Reproduced + verified on a Python 3.11 venv (full suite minus integration): **3000 passed, the 13 dump failures gone.**
- **`tests/regression/test_chromadb_telemetry_disabled.py` left `app.core.config` reloaded.** It calls `importlib.reload(config)` to re-exercise the import-time telemetry guard, which rebinds the module's `settings`/`get_settings` to brand-new objects. Every module that already did `from app.core.config import settings` keeps the ORIGINAL instance, so downstream tests that `monkeypatch.setattr(config.settings, <flag>, ...)` patch a dead object while the app reads defaults — silently breaking ~12 "feature-disabled / offline" assertions (ingestion buffer-cap/routing/rate-limit/backpressure, ai_pipeline_debouncer, github_checks ×3, webhook, integration-probe). Fix: an autouse fixture snapshots and restores `config.settings`/`config.get_settings` so the reload can't escape the test.
- **`tests/test_db_postgres_lazy_engine.py` tripped over a `monkeypatch.setattr` leak.** `engine`/`AsyncSessionLocal` are served by PEP 562 `__getattr__` (not real attributes). Pytest's `monkeypatch.setattr` (used across ~20 files to inject a fake session factory) restores by *setattr*, not delattr, leaving a **real** stale attribute behind that shadows the lazy hook; combined with the BUG-003 `dispose_engine_for_loop` engine rebuild, `pg.AsyncSessionLocal` then no longer matched the live `get_session_factory()`. Fix: the two lazy-hook tests now drop any leaked `engine`/`AsyncSessionLocal` from `pg.__dict__` before asserting, so they exercise the hook itself.
- **`tests/services/test_audit_log_service.py` depended on a fragile fd-capture of structlog output.** Two tests asserted a WARNING via `capfd` (stdout file-descriptor). `configure_logging()` (triggered by app startup elsewhere in the suite) routes structlog through the stdlib `LoggerFactory` and binds a `StreamHandler` to whichever `sys.stdout` was current when it first ran (with `cache_logger_on_first_use=True`), so depending on ordering the line lands on a stale stream `capfd` never sees. Fix: spy the service logger directly (`patch("app.services.audit_log_service.logger")`) and assert the warning event name — independent of structlog config, streams, and ordering. (These two passed in CI but failed in the local full-suite run with integration excluded; now order-independent.)

### Fixed (2026-06-06 — CI test failures: stale dump triage + 2 real fixes)

- **Triaged a CI `pytest` dump reporting 15 failures.** Reproduced the full set on a Python 3.11.9 venv (matching CI). **13 were stale** — they came from the documented RAG/knowledge service-clobber state and pass on current `main` once the services were restored (the entire "feature-disable / offline-off" cluster: ingestion buffer-cap/routing/rate-limit/backpressure, ai_pipeline_debouncer, github_checks ×3, webhook, plus the lazy-engine and integration-probe tests). **2 were real** and are fixed below; both were self-inflicted by the 2026-06-06 autonomous-loop fixes (PR #164).
- **`test_run_async_no_event_loop_closed_error_on_repeated_calls` (BUG-003 regression test) asserted `loop.is_closed()` after the loops were already closed.** `_run_async` closes each event loop once its coroutine completes, so checking the captured loop objects *after* the call block always read `True`. Fixed to capture the closed-state **at dispose time** (inside the patched `dispose_engine_for_loop`), matching the sibling test — proving dispose runs before close without depending on post-hoc loop state. Product code unchanged (the BUG-003 fix is correct).
- **`test_chroma_dedup_collection_is_per_project` patched the wrong seam after the BUG-001 refactor.** The test patched `sys.modules["chromadb"]`, but `_find_duplicate_semantic` now builds its client via `app.db.chroma.get_chroma_client()` (which binds `chromadb` at its own import), so the patch never intercepted the call and the captured collection name was empty. Fixed to patch `app.db.chroma.get_chroma_client`. The product code still correctly namespaces the collection per project (`open_defects_{project_id}`).
- **Quality gate (`scripts/quality_gate.py`) refreshed two stale line-number baselines** (`backend.analysis-router`, `backend.structlog-positional-args`) that drifted after the service restore; gate now passes 15/15.

### Fixed (2026-06-06 — BUG-001 ChromaDB telemetry log spam)

- **ChromaDB anonymous telemetry flooded the AI-worker logs** with `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given` (~7x per pipeline). The prior attempt — `os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")` plus the k8s configmap `ANONYMIZED_TELEMETRY: "False"` — was **insufficient**: the env var was verified present in the worker pod (`printenv` → `False`) yet chromadb 0.5.20's `HttpClient` ignores it and still emits the telemetry error. Authoritative fix: a shared `app/db/chroma.get_chroma_client()` helper that passes an explicit `Settings(anonymized_telemetry=False)` to every client. All 8 `chromadb.HttpClient(...)` call sites (agent_memory_service, semantic_cache, semantic_search, knowledge_chunking_service, defect_promotion_service, conversation agent, embed_and_cluster tool) now route through it. The env `setdefault` is retained in `config.py` as a harmless additional safeguard. Regression test: `tests/regression/test_chromadb_client_telemetry_off.py`.

### Fixed (2026-04-25/26 — Phase OS-Deploy)

- **WebSocket connection failed immediately after handshake** -- `OAuth2PasswordBearer.__call__() missing 1 required positional argument: 'request'` from chained dependencies. Root cause: HTTP-only auth dep applied to a WebSocket route at the router level.
- **`POST /api/v1/test-management/cases` returned 500** for stale `project_id` from cross-deployment localStorage -- now a clean 404 plus the frontend self-heals to prevent recurrence.
- **`GET /api/v1/analytics/defects` returned 500** -- earlier image had stale `tr.release_name` SQL; the source already had the fix (`r.name AS release_name` joining `releases r`), but the deployed image predated it. Documented the fix-and-redeploy flow in `homelabsetup/DEPLOY_TESTLOOKUP.md` with the K3s `:latest` cache trap and the `docker save | k3s ctr images import` workaround.
- **Frontend `nginx:alpine` failed to start** with `mkdir(/var/cache/nginx/client_temp) Permission denied` -- the homelab overlay needed `runAsUser: 0` AND default capabilities (dropping all caps blocks `chown` even as root). Fixed via JSON-patch `op: replace` on the full securityContext.
- **Backend `Connection refused` to colocated PostgreSQL** in homelab K3s -- `default-deny-all` NetworkPolicy blocked ingress to data stores. Now patched via `netpol-homelab.yaml` with `allow-data-stores`.
- **Traefik couldn't reach frontend on K3s** -- base `allow-frontend` NetworkPolicy referenced `ingress-nginx` namespace, but K3s uses Traefik in `kube-system`. Added `allow-traefik-ingress` to the homelab overlay.
- **Local Compose frontend showed "500 Internal Server Error" on every page** -- bundle was hardcoded to `http://localhost:8000` and CSP blocked it; some requests fell back to the Vite proxy at `localhost:3000`, which couldn't reach `localhost:8000` from inside the container. Now both paths converge: empty `VITE_API_BASE_URL` → same-origin → Vite proxy → `backend:8000`.

### Security (2026-04-25/26 — Phase OS-Deploy)

- WebSocket connections now require an authenticated user with project membership -- previously the `/ws/live/{project_id}` endpoint accepted anonymous connections and the in-flow auth message was never validated.
- WebSocket sessions enforce JWT expiry mid-connection (5-second grace) -- long-lived sockets no longer outlive their access token.
- Per-connection audit trail (`access_audit_logs`) lets compliance reports answer "who connected to which project channel, when, and why did it disconnect."

### Performance (2026-06-03 — Phase AUTO, autonomous loop)

Changes below are produced by the autonomous hourly performance loop (one focused, reviewed
branch per fix; see the per-entry branch for the full diff + regression test).

- **`agent_cost_service.check_alerts` N+1 removed** (`auto/perf-20260603-0745`) — the
  consecutive-failure check fired one `COUNT` query per failed stage in a pipeline; it now
  batches all per-stage 24h failure counts into a single `GROUP BY` query. Constant DB
  round-trips regardless of failed-stage count; alert output unchanged. Pinned by
  `tests/regression/test_agent_cost_check_alerts_no_n_plus_1.py`.
- **MinIO/sentinel ingest N+1 removed** (`auto/perf-20260603-0830`) — `ingestion.process_sentinel`
  upserted parsed cases without prefetching, so `_upsert_test_case` issued one SELECT per case
  (a 1000-test upload = 1000 extra round trips). It now prefetches existing rows in a single
  batched query and passes `existing`/`fingerprint` through, mirroring
  `ingestion_pipeline.ingest_test_results`. Pinned by
  `tests/regression/test_ingestion_sentinel_prefetch_no_n_plus_1.py`.
- **`run_diff_service.get_baseline_diff` redundant query removed** (`auto/perf-20260603-1531`) —
  it ran two SELECTs against `test_cases` with identical WHERE clauses for the baseline run's
  failures (one projecting `test_fingerprint`, one projecting `fingerprint`+`name`). Now fetches
  both columns once and reuses the rows for new-failure detection and resolved-failures — one
  fewer round trip per baseline diff; output unchanged. Pinned by
  `tests/regression/test_run_diff_baseline_single_fetch.py`.
- **`ai_eval_service.compute_agreement_rate` 3 COUNTs → 1** (`auto/perf-20260603-1830`) — it ran
  three sequential COUNT queries over the same `created_at >= cutoff` window (total / correct /
  partially_correct). Now one aggregate query with conditional counts
  (`count(case((rating == X, 1)))`); 3 round trips → 1, output identical. Pinned by
  `tests/regression/test_ai_eval_agreement_single_query.py`.
- **`release_service.update_phase` all-phases-done check → COUNT** (`auto/perf-20260603-1837`) —
  it fetched every `ReleasePhase` row for the release and scanned in Python
  (`all(p.status in ('completed','skipped'))`) on each phase update. Now a single COUNT of
  NOT-done phases (`status NOT IN ('completed','skipped') OR status IS NULL`) — `all_done` is
  True iff the count is 0. O(N) row fetch → O(1) aggregate; behavior identical incl. the
  NULL-status-is-incomplete and zero-phases (`all([]) is True`) edges. Pinned by
  `tests/regression/test_release_phase_all_done_count.py`.
- **`audit_dashboard_service.get_tenant_observability` merges failed-runs COUNT** (`auto/perf-20260603-2018`)
  — it ran a separate `COUNT WHERE status='FAILED'` in addition to the total/avg/sum aggregate
  over the same `(project_id, created_at >= cutoff)` window. The FAILED count is now a conditional
  `count(...).filter(status == 'FAILED')` in that same query; the function drops from 5 DB round
  trips to 4 with identical output (FILTER excludes NULL status just as the `status == 'FAILED'`
  WHERE did). Pinned by `tests/regression/test_audit_observability_single_runs_query.py`.
- **`test_management_service.recompute_plan_counts` aggregates in SQL** (`auto/perf-20260603-2120`)
  — it materialised every `TestPlanItem` row and ran five Python passes (len + 4 conditional
  sums). Now one aggregate query with conditional counts. ``executed`` is derived as
  ``total - count(status == 'not_run')`` (not `count(status != 'not_run')`) so a NULL
  `execution_status` counts as executed, exactly matching the original
  ``status not in ('not_run',)`` (the column is nullable); passed/failed/blocked use `== X`,
  which excludes NULL in both. Constant one round trip, no row materialisation. Pinned by
  `tests/regression/test_plan_counts_aggregate_query.py`.
- **`retro_digest_service._count_new_regressions` counts in SQL** (`auto/perf-20260603-2212`)
  — the current-week regression count fetched the distinct fingerprints
  (`SELECT DISTINCT test_fingerprint`) and did `len({row[0] ... if row[0]})` in Python. Now a
  `COUNT(DISTINCT test_fingerprint)` consumed via `.scalar()` — no row transfer / Python dedup.
  Identical result: the IN-list (`prev_passed_fps`) already holds only truthy fingerprints and
  COUNT(DISTINCT) skips NULL, so the `if row[0]` filter was redundant. Pinned by
  `tests/regression/test_retro_count_new_regressions_scalar.py`.
- **`feedback_service` total/unexported COUNTs collapsed** (`auto/perf-20260603-2300`) —
  `get_feedback_stats` ran 3 COUNTs on AIFeedback (group-by ratings, total, unexported) and
  `get_training_status` ran 2 (unexported, total). The total + unexported pair is now one
  aggregate query with a conditional `count(...).filter(exported.is_(False))`: get_feedback_stats
  3→2 round trips, get_training_status 2→1. Identical output — `FILTER (exported IS FALSE)`
  matches the prior `WHERE exported.is_(False)` (NULL excluded by both). Pinned by
  `tests/regression/test_feedback_stats_single_aggregate.py`.

### Performance (2026-06-03 — query batching, human-directed)

- **`perf_regression_service.refresh_baselines` per-row baseline SELECT batched**
  (`perf/refresh-baselines-batch`) — the nightly sweep called `record_observation` per swept
  `TestCase` row, each issuing a `SELECT PerfBaseline WHERE (project_id, test_fingerprint)`
  (`1 + N` queries, up to `_REFRESH_BATCH_SIZE` rows). It now prefetches the batch's baselines in
  one `IN`×`IN` query and passes each through `record_observation(existing=...)`, caching the
  returned (new or existing) baseline per pair. Behavior-preserving incl. the critical
  repeated-`(project, fingerprint)` case: multiple rows for the same test still accumulate into
  ONE baseline via Welford (the cache stands in for the old autoflush-visible just-created row),
  so no duplicate row / `uq_perf_baseline_fingerprint` violation. Plain `IN` (no window /
  composite-IN). Pinned by `tests/regression/test_refresh_baselines_batch.py` (repeated-fp → one
  baseline n=2; existing reused not recreated; sweep + one prefetch only).
- **Two perf indexes added (migration 0090)** (`perf/index-agent-stage-results`) —
  `ix_agent_stage_results_stage_status` on `agent_stage_results (stage_name, status)` (backs the
  24h repeated-failure count in `agent_cost_service.check_alerts`) and `ix_test_cases_run_suite`
  on `test_cases (test_run_id, suite_name)` (backs run-scoped suite-breakdown reads —
  coverage/summary/suite_history — which previously had only a GIN trigram index on `suite_name`).
  Both built `CREATE INDEX CONCURRENTLY` + `IF NOT EXISTS` (the migration-0082 pattern) so no
  write lock on the hot `test_cases` table. The analyst's headline `agent_stage_results
  (pipeline_run_id)` finding was a false positive — that index already exists (`ix_stage_results_pipeline`,
  migration 0004); it was just absent from the ORM `__table_args__`, now declared for parity.
  Pinned by `tests/test_migration_0090_indexes.py`.
- **`suite_sync_service` per-suite query fan-out collapsed** (`perf/suite-sync-batch-membership`)
  — `sync_suite_membership` called `_sync_one_suite` per suite, and each call issued 3 SELECTs
  (managed cases, existing memberships, `<suite>-deleted` bucket), i.e. `1 + 3N` queries on the
  ingestion critical path. The three lookups are now batched across all suites in one query each
  (`4` total, constant in suite count) and sliced per suite; `_sync_one_suite` performs no DB
  reads. Behavior-preserving — suites are processed independently (disjoint `suite_name` /
  `<suite>-deleted` rows), so prefetching the whole set up front matches the prior sequential
  fetches. Pinned by `tests/regression/test_suite_sync_batched_queries.py` (constant 4 round
  trips for N suites + add/unchanged/delete behavior across a multi-suite run).
- **`flaky_quarantine_service.run_recheck_cycle` per-row count batched** (`perf/run-recheck-batch-counts`)
  — the recheck beat task issued one grouped `COUNT` query per `RECHECK_SCHEDULED` row (N
  queries) to compute each test's post-quarantine flip rate. The counts are now computed in a
  single grouped query that OR-chains per-`(project, fingerprint, since)` conditions and groups
  by `(project, fingerprint, status)`, mapping results back per row. Each row's individual
  `since` cutoff and the per-project scope are preserved; the partial unique index
  `ux_fqr_live_per_fingerprint` guarantees `(project, fingerprint)` is unique among live rows so
  the mapping is exact. Shorter DB-session hold on the beat worker. Pinned by
  `tests/regression/test_flaky_quarantine_recheck_project_scope.py` (one batched count for N
  rows; release/re-quarantine/insufficient decisions unchanged; query still project-scoped).
- **`test_health_coach_service.refresh_flaky_coach` per-fingerprint N+1 batched** (`perf/flaky-coach-batch`)
  — the flaky-coach refresh (request path: `POST /flaky-coach/refresh`, plus a scheduled task)
  ran two queries per candidate fingerprint (`status_q` top-30 history + `tc_q` latest name), i.e.
  `1 + 1 + 2N`. The per-fingerprint lookups are now two batched queries: a windowed
  `ROW_NUMBER() OVER (PARTITION BY test_fingerprint ORDER BY created_at DESC) <= 30` (preserves
  the per-row top-30 that drives `failure_rate`, `flaky_since`/`last_failure_at`, and
  `status_history[:10]`) and a `DISTINCT ON (test_fingerprint)` for the latest name/suite. Round
  trips drop to a constant `4`; classification unchanged. Both keep the project scope. Pinned by
  `tests/regression/test_flaky_coach_batched_queries.py` (constant 4 round trips + failure-rate /
  status-history-order / flaky-since / both-pass-and-fail-filter behavior).

### Security (2026-06-03)

- **Tier-3 hardening — auth refresh rate-limit, allowlist validation, CORS wildcard warning**
  (`sec/tier3-hardening`, audit items S7/S8/S11) — three small defense-in-depth fixes:
  - **S7:** added `/api/v1/auth/refresh` to the `_AUTH_RATE_LIMITS` middleware (30/min prod) — the
    token-mint endpoint was previously unthrottled (refresh-token grinding / token amplification).
    `/login` (10/min) and `/register` (5/min) were already covered.
  - **S8:** `KnowledgeDomainAllowlistUpdate.domains` now validates each entry is a real FQDN —
    rejects wildcards (`*`), schemes, ports, paths, and IP literals. The allowlist is the domain
    gate that complements the S1 SSRF guard, and the `hostname == d or endswith('.'+d)` matcher
    never honoured those forms anyway, so rejecting them is behaviour-preserving. Empty list still
    clears the allowlist (permissive), preserving existing semantics.
  - **S11:** `Settings.validate_production_secrets()` now emits a startup WARNING when
    `CORS_ORIGINS` contains a wildcard in production/staging (credentialed any-origin access).
    `CORS_ORIGINS` never defaults to `*`, so this only fires on explicit operator misconfig.
  - **Verified no-change:** S9 (ingest `status` already constrained by a regex pattern) and S10
    (debug router already ADMIN-gated, no secret-bearing Celery args).
  Regression: `tests/regression/test_tier3_hardening.py`.
- **SSRF guard on the URL knowledge connector** (`sec/url-connector-ssrf-guard`, audit item S1) —
  `connectors/url_connector.fetch_content` validated only the URL *scheme* and an opt-in
  knowledge-source domain allowlist; neither blocks a host that resolves to a private / loopback /
  link-local / cloud-metadata address. A QA_ENGINEER+ creating an `external_url` knowledge source
  could point sync at `http://169.254.169.254/...` (cloud metadata), `http://127.0.0.1:6379`
  (Redis), or any RFC1918 host — a server-side fetch + response-exfiltration SSRF. Extracted the
  existing webhook SSRF check into a shared `services/url_safety.is_safe_public_url` (resolve-then-
  classify; unresolvable hosts allowed since they aren't reachable) and applied it in the connector
  on the **initial URL and on every redirect hop** — auto-redirect following is now disabled and
  the chain is walked by hand so a public host can't 30x the fetch into a private target. Redirect
  cap unchanged (5). `webhook_service` now imports the shared guard (behaviour identical; the
  `_is_safe_public_url` name is retained as an alias). Regression pins:
  `tests/regression/test_url_connector_ssrf_guard.py` (private/metadata/loopback initial target
  blocked with no GET; public→private redirect blocked on the hop; scheme block still first;
  public single-hop still fetches).
- **Tenant-scoped `GET /api/v1/agents/active-runs`** (`sec/audit-remediation-2026-06`, audit item
  S2 — IDOR) — the active-runs *list* endpoint had only `Depends(get_current_active_user)` and
  returned `RedisLiveRunState.get_all_active()` verbatim, so any authenticated user (even a VIEWER
  in one project) saw every other project's live runs: slug, build number, pass/fail counts,
  timing, `project_id`. The `/active-runs/{run_id}` sibling already gated on `require_run_access`;
  the list did not. Now filtered by `get_accessible_project_ids` (ADMIN → unrestricted) using the
  `project_id` already on each Redis state row — in-memory, no DB round trip; rows without a
  resolvable project are dropped for non-admins. Regression:
  `tests/regression/test_active_runs_idor.py`.
- **PII redaction in the reasoning-track fine-tune export** (`sec/audit-remediation-2026-06`, audit
  item S3) — `training/exporter._export_reasoning` copied raw Mongo ReAct traces (`prompt` +
  `intermediate_steps` + `analysis`) straight into the MinIO fine-tune corpus with no redaction;
  those traces carry test names, stack traces, env URLs, emails, and secrets. Every free-text field
  on a reasoning example now passes through `privacy_service.sanitize_for_persistence` (the
  `[REDACTED]` boundary) — the prompt, each ReAct step in `_format_reasoning_chain`, and the
  serialized analysis payload. Idempotent; structured labels (e.g. `PRODUCT_BUG`) are preserved.
  Regression: `tests/regression/test_training_exporter_pii_redaction.py`.
- **Deferred — S6 (connector base-URL SSRF):** verified and intentionally NOT applied. Jira /
  Confluence base URLs come from `JIRA_DOMAIN` / `CONFLUENCE_DOMAIN`, settable only by ADMIN
  (`PUT /api/v1/settings/integrations`, `require_role(ADMIN)`) — an ADMIN trust boundary, so SSRF
  exploitability is negligible. More importantly, a private-range block would break legitimate
  on-prem Jira/Confluence **Server** deployments (which routinely run on private IPs). Left as a
  documented non-action rather than a regression.
- **Input-length caps on persisted request models** (`sec/input-length-caps`, audit item S4 — OWASP
  A03) — several `*Create`/`*Update` Pydantic models accepted arbitrarily long strings for fields
  written to the DB, so a QA_ENGINEER+ could POST a multi-MB/GB value (test-case body, plan /
  strategy free-text, descriptions) and exhaust memory / DB write capacity; `UserCreate.password`
  was the sharpest — an unbounded password is hashed on the bcrypt path (CPU/memory DoS). Added
  `Field(max_length=...)`: long-form `Text`-backed fields use a shared `MAX_LONG_TEXT` (50 000 —
  generous, so realistic content is never rejected); `String(N)`-backed fields match `N` exactly
  (an over-long value now returns a clean 422 instead of a DB-overflow 500); `password` capped at
  128. Covers `ManagedTestCase`, `TestPlan`, `TestStrategy`, `TestSuite`, `TestCaseComment`,
  `ChatSession`, `QualityGate`, `ReleaseGatePolicy`, `SavedView`, `AIEvalDataset`, `KnowledgeSource`,
  `UserCreate`. Only `max_length` was added (no new `min_length`), so the change is
  behaviour-preserving. Regression: `tests/regression/test_input_length_caps.py`.

### Fixed (2026-06-04 — RAG/knowledge service stubs restore)

- **`rag_generation_service` + `knowledge_sync_service` clobbered to stubs — RAG generation
  and knowledge sync disabled, 47 backend tests red** (`fix/restore-rag-knowledge-services`) —
  commit `51dd4bc` ("Fix structlog logger calls to use keyword args") replaced both full
  modules with ~20-line placeholder stubs (rag 383→21 lines, knowledge_sync 424→35),
  deleting `grounded_generate`, `_build_grounded_prompt`, `_stub_generated_cases`,
  `_build_citations`, `_call_llm_generate`, `_map_coverage`, `MAX_CITATIONS_PER_CASE`,
  `GroundedGenerationResult` (rag) and `get_connector`, `compute_staleness`,
  `_effective_threshold` (knowledge_sync). Same incident class as the `secret_service`
  clobber below. Restored the full modules from their last-good commits (`0804a22` /
  `e0dc759`) and re-applied the intended structlog `%s`→kwargs cleanup the bad commit was
  supposed to do (9 positional calls across the two files, plus 4 in
  `knowledge_chunking_service.py:305/307/327/349` that were also raising
  `BoundLoggerBase._proxy_to_logger()` TypeErrors). Reconciled five stale tests against
  evolved code: secret-mask format now `****{last2}` (hardened in `e0dc759`/`e0d4fea`,
  secrets-at-rest review — tests updated, impl unchanged); `get_pipeline_timeline()` param
  `_`→`current_user` + new IDOR access gate; `_upsert_test_case` history-dedup probe changed
  the prefetch-path execute count (1 history vs 2 for the legacy lookup+history path);
  suite-trend routing pin granted project access for its random `project_id` (post-IDOR gate).
  Regression coverage: `test_rag_services.py` + `test_rag_generation.py` +
  `test_knowledge_sync_offline_gate.py` (158 pass locally).

### Fixed (2026-06-03)

- **`secret_service` clobbered to a stub — whole-app import break (CI exit 2)** (`fix/secret-service-restore`) —
  commit `e6c0348` ("Align secret masking implementation with tests") replaced the entire
  `services/secret_service.py` module with a masking-only stub, deleting `store_secret`,
  `read_secret`, `has_secret`, `get_masked`, `extract_secrets_from_config`,
  `strip_secrets_from_config`, `is_secret_field`, `SECRET_FIELDS`, and the Fernet encryption
  helpers, and renaming `mask_value` → `mask_secret`/`mask_api_key`. But `routers/app_settings.py`
  imports those names at module top, and `bootstrap.py` imports `app_settings`, so the **whole app
  failed to import** — pytest collection errored on `test_architectural_authorization`,
  `test_llm_connectivity`, `test_route_ordering` (CI exit 2) and the app couldn't start. (The stub's
  `mask_secret`/`mask_api_key` were dead code — no caller or test referenced them; the tests import
  `mask_value`.) Restored the full module (the pre-clobber `e0d4fea` version), which already
  satisfies the `mask_value` masking tests. Other callers restored too: `ai_config_resolver`,
  `github_checks_service`, `webhook_service`. Regression:
  `tests/regression/test_secret_service_public_api.py` pins the public surface + `mask_value` +
  encrypt/decrypt round-trip so a future stub breaks loudly instead of taking down the import graph.
- **Stale commit-allowlist caps (failing CI gate)** (`fix/stale-commit-allowlist-caps`) —
  `test_commit_allowlist_caps_are_accurate` was red on `main`: `knowledge_sync_service` (cap=6)
  and `rag_generation_service` (cap=1) had been converted to stage-only (0 service-level
  `commit()` calls) without updating the architectural allowlist. Removed both now-zero entries
  (a 0-commit service needs no allowlist entry; the `count==0` files are already skipped by the
  "commits must be allowlisted" guard). Test-only change; the four
  `test_architectural_transaction_boundaries` tests pass again.
- **Incomplete `app.core.deps` test stub (failing CI gate)** (`fix/release-phases-deps-stub`) —
  `test_release_phases.py` and `test_project_and_release.py` stub `app.core.deps` in `sys.modules`
  but the stub omitted `require_release_access` and `require_run_access`, which
  `routers/releases.py` imports — so every test in those files that imported the router failed at
  collection with `ImportError: cannot import name 'require_release_access'` (19 failures across
  the two files). Added both names to each stub. Test-only; both files now pass (33 + 22).

## [0.0.1] - 2026-04-15

### Added

- Repository hygiene: SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, ROADMAP.md, LICENSE (Apache 2.0)
- Fixed quickstart: three labelled run modes (core / full / demo)
- Cleaned generated artifacts from the tree
- Removed placeholder root `pyproject.toml`
