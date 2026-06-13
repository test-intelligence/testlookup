# AI-Agent Quality (AIQ) & Flaky-Test Intelligence (FLK) — Feature Doc

Living feature documentation for the `feat/ai-agents-and-flaky-intelligence`
initiative. One section per delivered phase. (Authoritative docs live here
because the repo's `docs/` tree is gitignored.)

---

## FLK-P1 — Intermittency + error-signature analysis  *(delivered 2026-06-13)*

**Goal.** Make a flaky verdict a *discriminating* signal: separate a
high-volatility flake (flips pass↔fail, many distinct errors, in-run framework
retries) from a low-volatility regression (fails persistently with one repeated
error — a real bug that must not be quarantined on the flake track).

**New scorer — `backend/app/services/flaky_signals.py`** (pure, no-DB,
never-raise; safe under `AI_OFFLINE_MODE` by construction).
`compute_intermittency_signals(records) -> IntermittencySignals` over a per-run
window yields:

| signal | definition |
|--------|------------|
| `status_volatility` | `flips / (runs - 1)` — adjacent pass↔fail changes |
| `error_signature_diversity` | unique denoised error-prefix signatures / fail_count |
| `stack_trace_diversity` | unique stack-trace fingerprints / fail_count (many ⇒ environmental/race; one ⇒ deterministic bug) |
| `in_run_retry_rate` | fraction of runs with a framework in-run retry / `is_flaky_run` flag (granular PR #169 columns) |
| `intermittency_label` | `intermittent_flaky` · `environmental_flaky` · `low_volatility_flaky` · `persistent_regression` · `insufficient_data` |

Error/stack signatures are denoised (hex addresses, long ids, timestamps, bare
numbers → `#`) so run-specific values don't inflate diversity. Every input is
coerced defensively — `None`, non-`Mapping` items, enum-repr statuses,
non-numeric `retry_count`, and un-stringable error objects all degrade to a
neutral contribution rather than raising.

**Wiring — `test_health_coach_service.py`.**
- `refresh_flaky_coach` joins the granular `TestCase` meta (`error_message`,
  `stack_trace`, `retry_count`, `is_flaky_run`, keyed by
  `TestCaseHistory.test_case_id`) into its existing windowed query (no new
  round-trip) and scores intermittency per fingerprint. A `persistent_regression`
  that would otherwise be `QUARANTINE` is downgraded to `INVESTIGATE`, and
  label-aware stabilization lines are appended. **The quarantine state machine is
  untouched** — only the advisory recommendation string changes.
- `get_flaky_coach` scores intermittency at read time for the bounded
  leaderboard set (`_load_intermittency_signals`, one extra batched windowed
  query) and surfaces the numeric signals on new optional `FlakyCoachEntry`
  fields, so `/flaky-coach` shows the new signal.

**Guarantees / non-goals.** No migration (read-time compute + verdict
refinement; the numeric signals are derived, not persisted — FLK-P2 owns the
confidence-interval columns). No new service commit. No outbound calls. `/failures`
surfacing is FLK-P4. Manual-triage leaderboard rows have no window and carry
`None` signals.

**Tests.** `backend/tests/test_flaky_signals.py` (16) — discrimination
(environmental vs persistent-regression vs in-run-retry), stack/error diversity,
noise normalisation, never-raise on malformed input, the refresh downgrade, and
the read-time surfacing. Existing `test_flaky_coach_batched_queries.py` round-trip
pins still hold (refresh stays at 4 queries).

**Rollout.** Pure code + tests. Rollback = revert the commit; nothing to unwind.

---

## AIQ-P5 — Report-quality eval harness for agent outputs  *(delivered 2026-06-12)*

### What it does
A **pure-local, offline-safe, never-raise** scorer for **recorded** agent
outputs, plus a golden-set **CI gate** that fails the build if an agent change
regresses report quality. It answers *"are our agents' reports still coherent,
complete, actionable, accurate, and well-calibrated?"* — measured, not asserted.

The harness scores 5 report-quality metrics over a list of `AgentEvalSample`
(recorded verdict + confidence + evidence + suggested action + ground truth),
returns an `AgentEvalReport` with a per-metric pass map and an overall verdict,
and a CI test runs it over a checked-in golden set so a calibration/evidence/
action/accuracy regression turns the build red.

### How it works
- `backend/app/services/agent_eval_harness.py` — `evaluate_agent_outputs(samples)
  -> AgentEvalReport`. Pure in-memory scoring over **already-recorded** agent
  outputs: **no LLM, no network, no DB**, no outbound/LLM imports (offline by
  construction). Any malformed input degrades to a fail-closed result — it
  **never raises**.
- `backend/app/services/golden_agent_outputs.py` — a small golden set of recorded
  agent outputs + ground truth (passes all thresholds, accuracy `0.889`), plus
  negative fixtures and an always-wrong under-confident fixture.
- `backend/app/services/ai_eval_service.py` — `compute_agent_report_quality(
  samples) -> dict` (additive helper).
- `backend/app/services/eval_gate_service.py` — `evaluate_agent_report_quality_rules(
  report) -> list` of gate rule dicts (6 metric rules + an overall
  `agent_report_quality` rule).

### The 5 metrics

| Metric | Definition | Pass when |
|--------|-----------|-----------|
| **coherence** | per-sample internal invariants hold | `>= 0.90` |
| **completeness** | evidence is present whenever `confidence >= 60` | `>= 0.90` |
| **actionability** | a fix / action is present whenever the verdict is non-flaky | `>= 0.85` |
| **accuracy** | `verdict == ground truth` | `>= 0.70` |
| **calibration — Brier** | `mean((conf/100 - outcome)^2)` | `<= 0.20` |
| **calibration — ECE** | `sum over 10 bins of (|S_b|/N) * |acc_b - conf_b|` | `<= 0.15` |

Where `outcome ∈ {0,1}` is whether the verdict matched ground truth, and the ECE
bins are the 10 equal-width confidence bins; `acc_b` / `conf_b` are the
accuracy / mean-confidence within bin `b`, `|S_b|` the bin population, `N` the
sample count.

### Thresholds + gate
`PASS_THRESHOLDS`:

```
coherence     >= 0.90
completeness  >= 0.90
actionability >= 0.85
accuracy      >= 0.70
brier         <= 0.20
ece           <= 0.15
```

`MIN_SAMPLES = 5` — fewer than 5 samples **fails** (insufficient data is not a
pass). `evaluate_agent_outputs` returns an `AgentEvalReport` with `per_metric_pass`
and an overall `passed` that is the AND of every metric.

### Why accuracy is in the gate (design note)
An **adversarial review** found that a **calibration-only** gate let an
**under-confident, always-wrong** agent pass: by reporting low confidence on
every (wrong) verdict, an agent can keep Brier/ECE inside threshold while being
useless. The **accuracy** metric (`>= 0.70`) was added specifically to close that
bypass — an honestly-uncertain-but-always-wrong agent now fails the gate.

### How it plugs into CI
- `backend/tests/test_architectural_agent_eval_harness.py` is the **CI GATE** —
  it runs inside the **existing backend-test pytest job** (no new CI job), scores
  the golden set, and fails CI if an agent change regresses calibration /
  evidence / actions / accuracy.
- `backend/tests/services/test_agent_eval_harness.py` is the unit suite (metric
  math, threshold edges, never-raise, the under-confident-always-wrong fixture).

### Run it locally
```
cd backend
pytest tests/test_architectural_agent_eval_harness.py     # the CI gate
pytest tests/services/test_agent_eval_harness.py          # unit suite
```

### Guarantees / non-goals
- **Offline by construction**: no LLM, no network, no DB; no outbound/LLM imports
  in the harness. `AI_OFFLINE_MODE=True` (default) semantics untouched.
- **Never-raise**: malformed input degrades to a fail-closed result.
- Scores **recorded** outputs only — it does not invoke agents, so it adds no
  runtime cost to the analytic pipeline.
- **No migration, no outbound calls.**

### Rollout
Pure code + tests. No infra, no env, no migration. The gate ships inside the
existing pytest job; rollback = revert the commit, with no data to unwind.

---

## AIQ-P4 — Gap-detection + report-refinement agents  *(delivered 2026-06-12)*

### What it does
Two new **pure-local, offline-safe, never-raise** agents close out the analytic
deep workflow by auditing and reconciling its output:

- **GapDetectionAgent** answers *"what did we NOT analyze?"* — for every failed
  test it classifies whether the test was analyzed, skipped, or errored, and
  enforces a referential-integrity invariant so coverage can be trusted.
- **ReportRefinementAgent** answers *"do our parallel signals agree?"* — it
  dedups any test analyzed by more than one route and resolves contradictions
  across the anomaly / analysis / cluster signals.

Both are wired **OPTIONALLY** into the DEEP workflow behind a default-off flag,
so there is **zero runtime impact until enabled**.

### How it works
- `backend/app/agents/gap_detection_agent.py` — `GapDetectionAgent`. Audits
  every failed test and emits a validated `GapDetectionAgentOutput`.
- `backend/app/agents/report_refinement_agent.py` — `ReportRefinementAgent`.
  Reconciles parallel signals and emits a validated
  `ReportRefinementAgentOutput`.
- Both are deterministic in-memory reconciliation over already-computed state:
  **no LLM, no network, no DB**. Any malformed / non-dict input degrades to a
  fallback contract — **they never raise**.
- Contracts live in `backend/app/models/agent_contracts.py` (Pydantic v2,
  `extra="ignore"`, all coercion in `field_validator(mode="before")`, nested
  reports self-recompute their invariants in a non-raising `@model_validator`).

### Contracts

**GapDetectionAgentOutput.gap_report** (`GapReport`):

| Field | Meaning |
|-------|---------|
| `failed_count` | number of failed tests audited |
| `analyzed_count` / `skipped_count` / `errored_count` | per-bucket disposition counts |
| `coverage_ratio` | analyzed share, clamped `[0,1]`; forced to `1.0` when `failed_count == 0` |
| `integrity_ok` | re-derived: `analyzed + skipped + errored == failed_count` |
| `inconclusive_count` / `no_evidence_count` | quality-gap tallies |
| `gaps` | list of `GapItem` (one per gap) |

`GapItem`: `test_id`, `reason` ∈ {`unanalyzed`, `errored`, `inconclusive`,
`no_evidence`, `low_confidence`} (`GapReason`), `detail` (structural tokens
only, truncated), `bucket` ∈ {`analyzed`, `skipped`, `errored`}.

**ReportRefinementAgentOutput.refined_report** (`RefinedReport`):

| Field | Meaning |
|-------|---------|
| `dedup_count` | tests deduped because ≥2 routes analyzed them |
| `contradictions` | list of `Contradiction` |
| `contradictions_resolved` / `unresolved_count` | re-derived from the contradiction list (`flag_for_review` ⇒ unresolved) |
| `multi_route_test_ids` | ids analyzed by more than one route |
| `reconciled_tests` | per-test reconciled view after dedup/resolution |

`Contradiction`: `test_id`, `type` ∈ {`flaky_vs_regression`,
`category_disagreement`, `confidence_split`} (`ContradictionType`), `routes`
(subset of `analysis` / `anomaly` / `cluster`), `resolution` ∈
{`prefer_analysis`, `prefer_anomaly`, `merge`, `flag_for_review`}
(`ResolutionStrategy`), `detail`.

### Referential-integrity invariant
The gap report holds `analyzed_count + skipped_count + errored_count ==
failed_count`. The `GapReport.@model_validator` **recomputes** `integrity_ok`
from the counts (and forces `coverage_ratio = 1.0` when there are no failures),
so a caller cannot stamp `integrity_ok=True` over inconsistent counts.

### Contradiction taxonomy + resolution
- **Dedup** — a test analyzed by ≥2 routes is collapsed using a **deterministic
  precedence: `analysis > anomaly > cluster`**.
- **Detection** — a `flaky_vs_regression` contradiction is raised when analysis
  says `is_flaky` but the test is a fresh regression; `category_disagreement`
  and `confidence_split` cover the other cross-route conflicts.
- **Resolution** — each contradiction is resolved as `prefer_analysis`,
  `prefer_anomaly`, `merge`, or `flag_for_review`; `RefinedReport`'s
  `@model_validator` derives `contradictions_resolved` / `unresolved_count` from
  the list (anything left at `flag_for_review` counts as unresolved).

### Where they sit in the deep workflow
Wired into the DEEP graph (`backend/app/agents/workflow.py`) behind
**`AIQ_GAP_REFINEMENT_ENABLED`** (default `False`, in
`backend/app/core/config.py`). The **graph topology is identical whether the
flag is on or off** — when off the nodes early-return a skip delta
(`skipped_stages`). When on, the chain is:

```
summary → (triage) → gap_detection → report_refinement → flaky_sentinel → test_health → release_risk → END
```

Both stages are added to `DEEP_OPTIONAL_STAGES` (`workflow.py`) and to the
agent_planner's `_DEEP_STAGES` (`backend/app/services/agent_planner.py`),
flag-gated.

### Guarantees / non-goals
- **Pure-local, offline-safe**: no LLM, no network, no DB; `AI_OFFLINE_MODE=True`
  (default) semantics untouched.
- **Never-raise**: any malformed / non-dict input degrades to a fallback
  contract.
- **No behavior change** with the flag off (topology identical, skip delta only).
- **No migration** — pure in-memory, no new DB columns. No outbound calls, no new
  service commits. `detail` fields carry structural tokens only (no PII).

### Tests / ratchets
- `backend/tests/test_gap_detection_agent.py` — integrity invariant, bucket
  classification, gap-reason mapping, coverage ratio, never-raise.
- `backend/tests/test_report_refinement_agent.py` — dedup precedence,
  contradiction taxonomy + resolution, resolved/unresolved derivation,
  never-raise.
- `backend/tests/test_aiq_p4_workflow_wiring.py` — flag-on vs flag-off topology
  identity and the skip-delta wiring.
- Contracted-output-model ratchet floor raised `12 → 14` in
  `backend/tests/test_architectural_agent_contracts.py`.

### Rollout / deploy
**Default-off ⇒ zero runtime impact until enabled.** Enable per-environment by
setting `AIQ_GAP_REFINEMENT_ENABLED=True`. **No migration required** (pure
in-memory, no new DB columns); rollback = unset the flag (or revert the commit),
with no data to unwind.

---

## AIQ-P2 — Self-critique / verification pass  *(delivered 2026-06-12)*

### What it does
Three analytic agents now **critique their own output** for internal
contradictions before returning it. The check is additive: results ride along in
the existing `agent_contracts` metadata (the `decision_reason` gains a
`; consistency_ok` or `; consistency_check_failed:<names>` suffix and one extra
`consistency_report` evidence ref), and every failed check emits a structlog
`consistency_check_failed` event. The underlying analysis is **never dropped or
mutated** — this is a read-only verification layer.

### How it works
- `backend/app/agents/consistency.py` defines `ConsistencyCheck`
  (`name`, `passed`, `severity` ∈ {warning,error} via a Pydantic v2
  `field_validator`, `details`, `offending_refs`), `ConsistencyReport`
  (`decision_suffix()` / `evidence_ref()` / `all_passed` / `failed`),
  `log_consistency_failures(report, *, pipeline_run_id=None)`, and the three
  checkers.
- Each checker is a **pure function** of already-computed data: no LLM, no DB,
  no outbound call, no migration. **Hard invariant: a checker never raises on
  any input shape** — all list-typed fields are coerced via an `isinstance`
  guard (`_as_list`), non-dicts become `{}`, and "no data to evaluate" is
  treated as `passed=True`.
- Wiring: in `summary_agent.py`, `release_risk_agent.py`, and
  `analysis_agent.py`, the checker runs on the success path immediately before
  `validate_agent_contract(...)`; its suffix is appended to `decision_reason`
  and its evidence ref to `evidence_refs`. No agent output shape changes.

### Checks per agent
- **SummaryAgent** (`check_summary_consistency`): referential integrity of
  cited test ids — every id in `flaky_test_ids` / `citations` must exist in
  `failed_test_ids ∪ analyses.keys()`, **compared as strings** so int/UUID keys
  never false-positive; an **empty analyzed universe with cited ids** is itself
  flagged. Plus cross-layer coherence: release signal vs failed-count/pass-rate,
  criticality vs release-impact, evidence vs action-plan.
- **ReleaseRiskAgent** (`check_release_consistency`): the prose must not
  contradict the deterministic score band (`<20 GO`, `20–55 CONDITIONAL_GO`,
  `≥55 NO_GO`, matching `score_to_recommendation`). Phrase matching is
  **negation-aware** (a correct "not low risk" NO_GO is not flagged). A
  recommendation that is *more conservative* than its score (e.g. a
  pass-rate-floored NO_GO at a low score) or any `policy_id` override is
  downgraded from error to warning (`conservative_override` /
  `policy_override_present`).
- **AnalysisAgent**: expanded `_validate_confidence` rules
  (`category_unknown_high_confidence`, `error_present_zero_confidence_floor`,
  `evidence_count_vs_confidence` keyed off the **raw** pre-cap confidence,
  `flaky_contradicts_history`) that only ever lower/correct values and record an
  auditable adjustment; plus a stage-level `check_analysis_consistency`
  (coverage completeness, flaky-with-zero-confidence floor).

### Guarantees / non-goals
- **No behavior change** to analytic payloads; outputs remain supersets.
- **No migration, no outbound calls, no new service commits.** Checkers never
  raise. No PII in reports (only ids / check-names / numeric scores).
- `AI_OFFLINE_MODE=True` (default) semantics untouched.

### Tests / ratchets
- `backend/tests/test_agent_consistency.py` — DB-free behavioral coverage of all
  three checkers incl. the never-raise (non-list inputs), int-key, negation, and
  conservative-override edge cases, and the structlog event emission.
- `backend/tests/test_architectural_agent_contracts.py` —
  `test_target_agents_run_consistency_checks` asserts each of the three agents
  calls its checker and that the literal `consistency_check_failed` appears in
  `consistency.py` only (single source of the event name).

### Rollout
Pure code + tests. No infra, no env, no migration. Ships dark (additive metadata
only). Rollback = revert the commit; no data to unwind.

---

## AIQ-P1 — Structured agent contracts  *(delivered 2026-06-12)*

### What it does
Every analytic agent now returns a **validated, audit-friendly contract**. The
agent's own output shape is unchanged; a metadata block is added under the
output dict's `agent_contracts[<agent_name>]` key carrying:

| Field | Meaning |
|-------|---------|
| `confidence_score` | 0-100, clamped (Pydantic v2 `field_validator`) |
| `evidence_count` | number of evidence refs supporting the verdict |
| `decision_reason` | short machine/human-readable reason string |
| `evidence_refs` | list of `{type, id, ...}` source references |
| `fallback_used` | whether a deterministic fallback path produced the output |
| `agent_name` / `agent_version` / `schema_version` / `generated_at` | provenance |

### How it works
- `backend/app/models/agent_contracts.py` defines `AgentContractMetadata`,
  `ContractedAgentOutput` (+ one `*Output` subclass per agent), and
  `validate_agent_contract(schema, payload, *, agent_name, confidence,
  evidence_refs, decision_reason, ...)`.
- `validate_agent_contract` validates `{**payload, "contract": metadata}`
  against the schema, then returns the model dump **minus** the nested
  `contract` key, with the metadata re-attached under `agent_contracts`. On
  validation failure it logs a structlog warning and returns the **original**
  payload stamped with `contract_validation_error`. **It never raises** — the
  deterministic fallback is guaranteed.
- `confidence_score` and `evidence_count` are derived internally from the
  existing `confidence`/`evidence_refs` kwargs, so the 9 agents already calling
  the helper required **zero** changes.

### Agents brought under contract in this phase
- **RunCompareAgent** — both `generate()` returns (parsed-success and
  exception-fallback). `RunCompareAgentOutput` sets `extra="allow"` so any extra
  LLM-produced keys pass through unchanged (no behavior change for the
  `run_compare_ai_service` consumer, which reads `fallback_used` and stores the
  whole dict).
- **LogIntelligenceAgent** — `investigate()` return; tiered confidence
  (80 both sources ok / 40 one ok / 0 none) and per-source evidence refs.
- **RegressionWatchman** — both `run()` returns; averages per-cluster
  confidence on success, 0 on the error path. The standalone `_classify` /
  `run_regression_watchman` path is intentionally left raw (it feeds a
  different consumer).

### Guarantees / non-goals
- **No behavior change**: outputs are supersets of their prior shape.
- **No migration, no outbound calls, no new service commits.**
- `AI_OFFLINE_MODE=True` (default) semantics untouched.

### Tests / ratchets
- `backend/tests/test_architectural_agent_contracts.py` — AST scan: every
  non-infra agent module calls `validate_agent_contract`; required metadata
  fields present; contracted-output-model count held at a `>= 12` floor
  (downward-ratchet guard). Infra allowlist: `__init__`, `base`, `state`,
  `workflow`, `conversation`, `contract_agent`, `defect_commander`,
  `agent_planner`.
- `backend/tests/test_agent_contract_outputs.py` — runtime: LogIntelligence
  success/fallback wrapping and RegressionWatchman contracted output shape.

### Rollout
Pure code + tests. No infra, no env, no migration. Ships dark (additive
metadata only); downstream consumers can begin reading `agent_contracts` at
will. Rollback = revert the commit; no data migration to unwind.
