# AI-Agent Quality (AIQ) & Flaky-Test Intelligence (FLK) — Feature Doc

Living feature documentation for the `feat/ai-agents-and-flaky-intelligence`
initiative. One section per delivered phase. (Authoritative docs live here
because the repo's `docs/` tree is gitignored.)

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
