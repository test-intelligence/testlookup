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
# Inside the backend container (the stack must have run at least one pipeline):
make benchmark-pipeline

# Or directly, against any reachable database:
python benchmarks/pipeline/collect.py \
    --days 30 \
    --workflow-type deep \
    --output benchmarks/results/pipeline_baseline.json
```

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

## What it does not measure yet

Declared in the output's `not_measured` block rather than silently absent:

- **`citation_coverage`** — citations live on the persisted summary document,
  not on `agent_stage_results`. Blocked on finding F-3 / requirement G.2.
- **`unsupported_claim_rate`** — nothing classifies a claim as supported today.
  Blocked on requirement G.3 (narrative critic).
- **`failure_category_macro_f1`** — needs labelled outcomes joined to analyses;
  `ai_eval_service` computes this on demand but nothing schedules it. Blocked
  on requirement I.1 (nightly evaluation).

## Related

- `benchmarks/METHODOLOGY.md` — classification and throughput benchmarks
- `architecture/AI_EVALUATION.md` — the eval gate this baseline feeds
- `backend/tests/test_pipeline_baseline_aggregate.py` — the aggregator's tests
