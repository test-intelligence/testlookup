# AI-Agent Quality (AIQ) & Flaky-Test Intelligence (FLK) — Feature Doc

Living feature documentation for the `feat/ai-agents-and-flaky-intelligence`
initiative. One section per delivered phase. (Authoritative docs live here
because the repo's `docs/` tree is gitignored.)

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
