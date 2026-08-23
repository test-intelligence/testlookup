# AI pipeline baseline

Answers one question: **what does a pipeline run cost today, by run size?**

Every performance and quality target in `architecture/AI_QUALITY.md` and the AI
review is currently an estimate. Nothing measured them, so no change to the AI
layer could be shown to have helped. This harness produces the number those
targets are compared against.

## What it is

An **aggregator, not an instrument.** The pipeline has recorded per-stage
tokens, cost, LLM-call counts, fallback flags and timings in
`agent_stage_results` since Phase 6, and end-to-end timings in
`agent_pipeline_runs`. No baseline existed because nothing ever rolled them up.

```
agent_pipeline_runs ─┐
                     ├─► collect.py ─► aggregate.py ─► pipeline_baseline.json
agent_stage_results ─┘     (SQL)         (pure)
```

`aggregate.py` is pure — no database, no HTTP, no `app` imports — so it is unit
tested without a stack. `collect.py` is a thin SQL shell over asyncpg, so it
runs against any environment holding pipeline data: a homelab, a dev compose
stack, or a restored dump.

## Running

```bash
# Inside the backend container (the stack must have run at least one pipeline).
# Grounding metrics come along automatically: --mongo-uri defaults from
# MONGO_URI, which the backend container already sets.
make benchmark-pipeline

# Or directly, against any reachable database:
python benchmarks/pipeline/collect.py \
    --days 30 \
    --workflow-type deep \
    --output benchmarks/results/pipeline_baseline.json
```

### `--limit` silently caps the window

The default is **500**. A run reporting `runs_observed: 500` with
`window.limit: 500` did **not** read the window you asked for -- it read the
most recent 500 runs and stopped. Compare the two before quoting a span: the
first read of 2026-08-23 looked like 30 days and covered 15. Pass a `--limit`
above the row count you expect.

### Running it against a Kubernetes deployment

`make benchmark-pipeline` shells out to `docker compose exec backend`, which a
k3s/OpenShift deployment does not have. Two things are in the way:

1. **`benchmarks/` is not in the backend image.** The Dockerfile build context
   is `backend/`, so the harness must be copied in first:

   ```bash
   kubectl exec -n <ns> <pod> -- mkdir -p /app/benchmarks/results
   kubectl cp benchmarks/pipeline "<ns>/<pod>:/app/benchmarks/pipeline"
   ```

2. **`DOCKER_COMPOSE` is overridable** (`?=` in the Makefile, so the environment
   wins). Point it at a shim that turns `exec backend <cmd>` into
   `kubectl exec -n <ns> <pod> -- <cmd>` and the real target runs unmodified.
   Pin the shim to **one** pod -- a multi-replica deployment will otherwise exec
   into a pod with no harness in it.

Run it in the pod rather than from a laptop: the database credentials never
leave the cluster, and `MONGO_URI` / `DATABASE_URL` are already set there.

## What it measures

| Metric | Grouping | Source |
|---|---|---|
| End-to-end latency p50/p95/max | run-size band | `agent_pipeline_runs.started_at/completed_at` |
| Tokens per run | run-size band | sum of `agent_stage_results.total_tokens` |
| Cost per run | run-size band | sum of `agent_stage_results.cost_usd` |
| LLM calls per run | run-size band | sum of `llm_calls_count` |
| Degraded-run rate | run-size band | stage `status` + `execution_path` |
| Budget-skipped rate | run-size band | `result_data.budget_skipped` |
| Stage latency p50/p95 | stage | `agent_stage_results.started_at/completed_at` |
| Fallback rate + reasons | stage | `fallback_used` / `fallback_reason` |
| Parse failures per LLM call | stage | `decision_log` schema-validation entries |
| Engine mix | stage | `analysis_mode` |
| Claim evidence coverage | report | `decision_reports.decision_intelligence.claims` |
| Shared-evidence-bundle rate | report | evidence signature per claim |
| Narrative citation rate | summary | `run_summaries.layer3_evidence_pack.citations` |

The last three need `--mongo-uri` (set from `MONGO_URI` by default). Without it
the collector still runs; those metrics are declared unmeasured rather than
reported as zero.

**Run-size bands** are `green` (0 failures), `small` (1-9), `medium` (10-49),
`large` (50+). Cost is driven by the size of the failure set, so a single
global average hides the only dimension that matters. The failure count comes
from the analysis stage's own `result_data` — `analysed + budget_skipped` — so
tests the wall-clock budget never reached still count as workload the pipeline
faced.

## Three rules it holds to

**It never invents a number.** An absent measurement is reported as `null`, not
`0`. "No data" and "zero" are different answers, and a baseline that conflates
them is worse than none — later work gets compared against a fiction.

**It never raises.** Malformed rows are dropped, not fatal. A harness that dies
on one bad row measures nothing, which is the failure mode it exists to end.

**It refuses to write an empty baseline.** A window with no runs is an *error*,
not a file of nulls. Once committed, a null-filled baseline is indistinguishable
from a measured one.

## Reading the output honestly

`sufficient_samples` is `false` below 5 runs (per band and overall). Percentiles
are still emitted, because suppressing them hides the sample size too — but a
p95 over three runs is arithmetic, not evidence, and the terminal output says so.

**A deliberate skip is not degradation.** All-green fast-path skips, planner
skips and confidence-router skips are the pipeline routing correctly. Only
failures, wall-clock-budget skips, and skips with no recorded `execution_path`
count toward `degraded_rate`. The first draft of this harness counted every
skip and reported healthy green runs as 100% degraded.

## Grounding: two mechanisms that fail differently

Measured separately, because conflating them hides both.

**Typed claims** in the decision report carry a server-built `evidence` list, so
the question is not *whether* a claim cites evidence — nearly all do — but
whether that evidence is **claim-specific** or one bundle stamped onto every
claim. `shared_evidence_bundle_rate` counts reports where every claim carries a
byte-identical evidence list. A high rate means the claim-evidence drawer
presents bundle-level provenance as claim-level (finding **F-16**). Reports with
a single claim are excluded from that denominator — one claim has nothing to
share with, and counting it would flatter the rate.

**The narrative** gets citations only from a verbatim 40-character match against
evidence excerpts, applied to layer 3 alone. `citation_rate` measures how often
that fires; `uncitable_layers` reports the structural half — three of four
layers cannot carry a citation however well the model behaves (finding **F-3**).
That list is emitted even for an empty corpus, because it is a property of the
code rather than an observation about runs.

Only **published** reports count. A rejected or superseded attempt is not what a
reader was shown.

## What it still does not measure

Declared in the output's `not_measured` block rather than silently absent:

- **`unsupported_claim_rate`** — coverage says whether a claim *cites* evidence,
  not whether that evidence *supports* it. Nothing classifies support today.
  Blocked on requirement G.3 (narrative critic).
- **`failure_category_macro_f1`** — needs labelled outcomes joined to analyses;
  `ai_eval_service` computes this on demand but nothing schedules it. Blocked
  on requirement I.1 (nightly evaluation).

When run without `--mongo-uri`, the grounding metrics join that list with
`blocked_on: collector input` — "we did not look" is reported as its own
answer, distinct from "there was nothing to find".


## The committed baselines

Two files, because a single one would blend code versions.

| File | Window | What it is for |
|---|---|---|
| `results/pipeline_baseline.json` | all-time | the corpus as it stands, dominated by historical runs |
| `results/pipeline_baseline_post_fixes.json` | after the 2026-08-22 fixes | **the reference point for future comparison** |

**Compare against the post-fix file, not the all-time one.** The all-time corpus
spans two code versions on the same calendar day: the AI-layer fixes of
2026-08-22 landed mid-day, so runs before and after them sit in the same buckets
with nothing distinguishing them. The `temporal` block splits by *day*, which
cannot see a deploy boundary inside one.

This is a real limitation of the harness, not of the data. Measured deltas
across that boundary (all confirmed against deployed runs):

| Metric | Before | After |
|---|---|---|
| Summary parse failures | 7.16% | **1.09%** |
| Narrative citations | 0% | **28%**, zero fabricated ids |
| Analyses carrying evidence | ~0% | **58%** |
| Reports sharing one evidence bundle | 100% | **0%** |

A future change should regenerate the post-fix file and compare to *it*. Reading
the all-time file as "current state" is the same error as reading a degraded
rate that one bad hour produced.

## Related

- `benchmarks/METHODOLOGY.md` — classification and throughput benchmarks
- `architecture/AI_EVALUATION.md` — the eval gate this baseline feeds
- `backend/tests/test_pipeline_baseline_aggregate.py` — the aggregator's tests
