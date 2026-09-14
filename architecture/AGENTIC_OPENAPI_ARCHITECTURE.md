# TestLookup — Agentic Architecture & Requirements Analysis (2026-09-10)

**Scope.** Analysis of the 12 agentic requirements (plus requirement 13, evals) against the codebase as it exists on `main` @ `2dc27a87` (re-scanned 2026-09-11; revisions 1 and 2 were against `bb4d848b`), a target architecture that closes every gap, and an epic/story backlog to build it. Everything marked **EXISTS** was verified by reading the code; file paths are given so a reviewer can check. Everything marked **NEW** is a design proposal.

**Review status.** Revision 3 (2026-09-11) re-scans against the re-audit merges (#24, #25, #26, #50) that landed after the handover: migrations 0166-0172 are now taken, the prompt-eval recordings module runs gated prompts through the model (closing part of the section 11 gap), a cluster-wide LLM slot lease and an atomic cost reservation now exist at the invocation boundary, and API-key scopes are enforced. Changes are marked **(rev 3)** inline. Revision 2 (2026-09-10) Revision 1 was cross-reviewed by four independent reviewers (fact-check against code: 86 claims, 4 wrong, 10 imprecise; reliability; API/security; evaluation methodology and roadmap). All HIGH and MED findings are resolved in this revision; the material changes are: fencing tokens and intra-stage heartbeats (§7.3), dedup-lock TTL extension instead of release-and-reacquire (§7.4), `passed` defined for no-review workflows (§7.1), tool-call idempotency keys (§7.5), body-scoped authorization and idempotency semantics (§3), a single owner for `mode` and a monotonicity table for overrides (§4), review enforcement keyed on credential kind and a real synthetic-account column (§8), and a rewritten evaluation section that starts from the CI attestation gate that already exists and states what the current metrics do and do not measure (§11). Sizing now states the team assumption (§12).

**One-line verdict.** TestLookup already has ~70% of an orchestrated, verified, budgeted agent runtime (LangGraph graphs, 20+ stage agents, a capability registry, a planner/verifier pair, a terminal critic, Celery retry with jittered backoff, a stale-pipeline reaper, per-project agent policies). What is missing is the *product surface* around it: agents are not individually callable, workflows are not user-editable, model selection has no SLM/LLM tier, the status vocabulary has an escape hatch (`partial`, and read-time-only stale derivation), human-review is a flag on some payloads rather than an enforced gate, and retries are fixed per task rather than user-configurable.

---

## 0. Requirements traceability (read this first)

| # | Requirement | Status today | Evidence | Gap closed by |
|---|---|---|---|---|
| 1 | Agents callable via OpenAPI / curl / Postman | **PARTIAL** — whole pipelines are triggerable (`POST /api/v1/agents/pipelines/trigger`), two agents have direct endpoints (`/defect-command`, `/regression-watch`), investigations are startable. No per-agent invoke surface, no shared request/response envelope. | `backend/app/routers/agents.py:281,917,946`, `routers/agent_investigations.py:157` | Epic E1 |
| 2 | Framework with orchestrator + tools separation | **EXISTS** — LangGraph `StateGraph` per workflow type; `BaseAgent` (stage lifecycle, cost, provenance); `app/tools/` for LangChain tools; capability registry declares inputs/outputs/permission per stage. | `agents/workflow.py:746-964`, `agents/base.py`, `services/agent_capability_registry.py` | Hardening only (E2) |
| 3 | User-customizable workflows (steps, branching, tool usage) | **MISSING** — graphs are compiled from Python; the only user levers are per-project feature flags that freeze a stage on/off (`contract_agent_enabled`, etc.). | `agents/state.py` (the `*_enabled` fields), `workflow.py:812-946` | Epic E3 |
| 4 | End-user-configurable agents (params, model, thresholds, retries) | **PARTIAL** — global AI config (provider/model/temperature/confidence threshold) is DB-overridable via `/settings/ai`; per-project `AgentPolicy` has `enabled/mode/budgets`. No per-agent model, no retry limits, no per-agent thresholds. | `services/ai_config_resolver.py:154-224`, `models/postgres.py:1856`, `routers/agent_investigations.py:294-308` | Epic E4 |
| 5 | Summarization on SLMs | **PARTIAL** — default model is already small (`qwen2.5:7b` via Ollama) and summary falls back deterministically; but there is one model for everything, no "tier" concept. `ModelRegistry` has `classifier/reasoning/embedding` tracks and `get_llm` does serve a promoted model at inference time (`llm_factory.py:213`), so the registry is a per-track model switch, not a tier router: nothing chooses a track per capability or escalates between tracks. | `core/config.py:260`, `agents/summary_agent.py:205-305`, `services/model_registry.py`, `services/llm_factory.py:213` | Epic E5 |
| 6 | LLMs for reasoning-heavy tasks | **PARTIAL** — root-cause, deep investigation, decision report use the (single) configured model; `ANALYSIS_MODE auto` picks rules/ml/llm. No escalation rule from SLM to LLM. | `core/config.py:512-516`, `agents/investigator/` | Epic E5 |
| 7 | Agents review multi-agent decisions | **EXISTS (strong)** — `decision_report_critic` is an independent terminal verifier with 12+ checks and bounded single-repair; `consistency.py` cross-checks summary vs analysis vs release decision; planner/verifier compares final state to the hashed plan; `report_refinement` reconciles contradictions. Missing: a *generic* reviewer that any user-defined workflow can attach, and a second-model (heterogeneous) check. | `agents/decision_report_critic_agent.py:352-460`, `agents/consistency.py`, `services/agent_planner.py:923` | Epic E6 |
| 8 | Re-runnable + retriable, backoff, configurable limit (default 5), manual retry | **PARTIAL** — `_exponential_backoff` (30s·2^n, cap 600s, one-sided +0..20% jitter; the docstring says ±20% but the code only adds), Celery `max_retries=2` on the pipeline task, DLQ on exhaustion, `resume_agent_pipeline` with checkpoint restore. Retry count is hard-coded per task, not configurable; no user-facing "Retry" for a failed pipeline other than re-trigger (which creates a new run). **(rev 3)** `AI_MAX_RETRIES` now governs step-level retries for every LLM provider (re-audit L2), and a failure between creating the pipeline record and starting the graph now marks the record failed and is resumable under the same id (2026-09-10 fix), which removes one class of stuck `running` rows; the 30-minute heuristic remains. No cancel exists for pipelines (`cancel_requested` is a column on `AgentInvestigation` only, `postgres.py:1781`). | `worker/tasks.py:228-232,1886-2056`, `agents/workflow.py` (`_make_checkpointed_node`) | Epic E7 |
| 9 | No invalid/stale state; terminal set = in-progress, completed, failed, passed | **PARTIAL** — DB status vocabulary is `pending/running/completed/failed/partial`. A read-time derivation flips a `running` row to failed when any stage row is failed or when it has run past a fixed 30 minutes; the reaper beat (every 10 min) persists the same rule. Three problems: (a) `partial` is neither success nor failure; (b) the 30-minute heuristic misclassifies legitimately long deep runs and, between the 30-minute mark and the next beat, DB and UI disagree; (c) no `passed` concept distinct from `completed`. | `models/postgres.py:2011`, `routers/agents.py:61-130`, `worker/tasks.py:4184`, `worker/celery_app.py:245` | Epic E7 |
| 10 | Human review required for all AI reports, clearly communicated | **PARTIAL** — `requires_human_review` is a DB column on AI analyses (`postgres.py:1317`) and a field inside the decision report's `quality_review` JSON (no column); the critic forces it conservative, and `AgentInvestigation.mode` / `AgentPolicy.mode` gate `shadow/suggest/act`. But it is a *field*, not an *enforced state*: nothing prevents a report from being consumed/exported unreviewed, and there is no review record. | `agents/decision_report_critic_agent.py:302-448`, `agents/decision_report_agent.py:427`, `models/postgres.py:1317,1764,1877` | Epic E8 |
| 11 | Real-world patterns and standards | §9 (document-only; no code change) | | E2.4 (tracked doc) |
| 12 | Agentic best practices | §10 (document-only; each practice that is enforceable is a guard or invariant in E2, E7, E8) | | E2.4 |
| 13 | **Evals drive the system (added 2026-09-10)** — every prompt, model, tier, routing, reviewer or workflow change is measured before and after it ships | **PARTIAL** — more exists than revision 1 said. A scoring harness (accuracy, Brier, ECE against ground truth; coherence/completeness/actionability as deterministic contract checks), task-type metrics, feedback-to-dataset, a 7-day-vs-7-day drift comparison, baselines, and a release-gate service with a manifest checksum exist. **CI already blocks a prompt change that lacks a fresh eval-gate attestation** (`scripts/quality_gate.py:3020-3210`, attestation via `python -m app.services.prompt_registry --attest`, which calls the gate service), and a daily beat runs a scheduled agent eval. **(rev 3)** `prompt_eval_recordings.py` (re-audit M16) now runs each *gated* prompt through `llm_factory.get_llm` on its golden cases, records the raw outputs with the prompt `content_hash` and a provenance digest, scores them against `min_score`, and fails the gate when the current hash differs from the recorded one. So candidate inference exists for prompts. Still missing: recordings cover only prompts whose output is a scorable decision (not the summary or decision-report prose), nothing re-records when the *model*, *tier* or *routing* changes under an unchanged prompt, and the attestation guard watched set is prompts only; the golden store has 9 usable entries for one agent (AnalysisAgent) plus 4 negative and 8 wrong-agent fixtures; the 4 pilot files in `tests/evals/` hold 0 to 2 harness-shaped samples; nothing evaluates tiers, the reviewer, routing decisions, or workflow definitions; and nothing produces `AIEvalRun` rows on a schedule for drift to compare. | `services/agent_eval_harness.py:25-46,168-230,343`, `services/ai_eval_service.py:185,520`, `services/golden_agent_outputs.py:78-197,227-324`, `services/eval_gate_service.py`, `services/prompt_registry.py:1186-1195`, `scripts/quality_gate.py:3020-3210`, `worker/celery_app.py:120-124`, `routers/ai_evaluation.py:32,419-467` | §11, Epic E9 |

---

## 1. Architecture overview

### 1.1 Target system (ASCII)

```
                         ┌──────────────────────────────────────────────────────────┐
  curl / Postman / UI    │  FastAPI  (OpenAPI 3.1 at /docs, /openapi.json)          │
  CLI / MCP server ─────►│                                                          │
                         │  /api/v1/agents/catalog          (discover agents)       │
                         │  /api/v1/agents/{agent_id}/invoke (sync ≤30s | async)    │
                         │  /api/v1/agents/invocations/{id} (poll / SSE)            │
                         │  /api/v1/workflows                (CRUD definitions)     │
                         │  /api/v1/workflows/{id}/runs      (start / retry / cancel│
                         │  /api/v1/agent-configs            (per-project config)   │
                         │  /api/v1/reviews                  (human review gate)    │
                         └───────────────┬──────────────────────────────────────────┘
                                         │ enqueue (Celery, queue=ai_analysis)
                                         ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATION LAYER                                                              │
 │                                                                                  │
 │  WorkflowDefinition (JSON, versioned, per project) ──► WorkflowCompiler          │
 │        │  steps[], edges[], conditions[], tool_allowlist, retry_policy           │
 │        ▼                                                                         │
 │  LangGraph StateGraph (compiled at run start, plan hashed = today's planner)     │
 │        │                                                                         │
 │        ├── Supervisor node  ── decides next step from state (deterministic       │
 │        │                       first, LLM only for open branches)                │
 │        ├── Worker nodes     ── one per AgentCapability (existing 20+ agents)     │
 │        ├── Reviewer node    ── generic critic: schema, evidence, consistency,    │
 │        │                       cross-model agreement (existing critic = special) │
 │        └── Finalize node    ── settles status, writes ReviewRequest, emits event │
 │                                                                                  │
 │  Per-node middleware (existing _make_checkpointed_node + BaseAgent):             │
 │    checkpoint ▸ budget check ▸ model-tier select ▸ timeout ▸ retry ▸ provenance  │
 └────────────┬──────────────────────────────┬───────────────────────┬──────────────┘
              │                              │                       │
              ▼                              ▼                       ▼
 ┌──────────────────────┐   ┌────────────────────────────┐   ┌─────────────────────┐
 │ MODEL ROUTER         │   │ TOOL LAYER (app/tools/)    │   │ STATE & LEDGERS     │
 │ tier: deterministic  │   │ read_only | propose_action │   │ Postgres: runs,     │
 │       slm            │   │ | mutating (policy-gated)  │   │  stages, configs,   │
 │       llm            │   │ every call → tools_used[]  │   │  reviews, workflows │
 │ llm_factory +        │   │ + audit ledger             │   │ Mongo: event log    │
 │ llm_policy (egress,  │   │ Circuit breaker per tool   │   │ Redis: checkpoints, │
 │ sanitize, allowlist) │   │ (Jira, GitHub, Ollama…)    │   │  budgets, locks     │
 │ Ollama SLM ─ Ollama  │   └────────────────────────────┘   │ MinIO: reports      │
 │ LLM ─ cloud (if not  │                                    └─────────────────────┘
 │ AI_OFFLINE_MODE)     │
 └──────────────────────┘
```

### 1.2 Design stance

1. **Orchestration, not choreography.** The graph is the single authority on order, branching and termination. Agents never enqueue other agents directly (the `cluster_investigation_dispatch/join` pair is the sanctioned exception and it is still a graph node). This is what makes requirement 9 (no stale states) provable: only the Finalize node writes a terminal status.
2. **Deterministic outer loop, probabilistic inner calls.** Routing decisions are computed from state fields (pass rate, failure count, confidence, budget) and recorded in `_workflow_route_decisions`. An LLM is consulted only *inside* a worker, never to decide the next node, unless the workflow author opts a branch into `condition.kind = "llm_judgment"` (and then the judgment is itself reviewed).
3. **Every AI output is a proposal until a human accepts it.** Introduce a `ReviewRequest` row per AI-generated report with `state ∈ {pending_review, accepted, rejected, superseded}`; the report's public representation carries `review.state` and export/notify paths refuse `pending_review` unless the project has opted into draft distribution (an audited per-project setting, §8.2), in which case the payload is watermarked.
4. **Cheapest model that passes the check.** Tiering is per *capability*, with escalation on failed structured-output validation or low confidence, and the tier actually used is provenance.

---

## 2. Detailed component design

### 2.1 Component map

| Component | Layer | Exists? | Location (existing / proposed) | Responsibility |
|---|---|---|---|---|
| `AgentCapability` registry | orchestration | EXISTS | `services/agent_capability_registry.py` | Declares id, inputs/outputs contract, dependencies, latency/cost/timeout, fallback, permission class. Becomes the **agent catalog** served by OpenAPI. |
| `BaseAgent` | agent | EXISTS | `agents/base.py` | Stage lifecycle (`mark_stage_running/done`), cost estimation, decision logging, progress broadcast. Gains `model_tier` + `retry_policy` hooks. |
| `WorkflowDefinition` | config | NEW | `models/postgres.py` + `services/workflow_definition_service.py` | Versioned JSON graph spec per project (steps, edges, conditions, tool allowlist, retry policy, review policy). |
| `WorkflowCompiler` | orchestration | NEW | `agents/workflow_compiler.py` | Validates a definition against the registry (dependencies satisfied, contracts type-align, no cycles except explicit loops with max_iterations) and emits a `StateGraph`. Existing `_build_offline/deep/live_graph` become three *built-in* definitions. |
| `Supervisor` node | orchestration | NEW (thin) | `agents/supervisor.py` | Evaluates `conditions[]` on state; records route decisions. Reuses today's conditional-edge lambdas. |
| `ReviewerAgent` | agent | NEW (generalizes existing) | `agents/reviewer_agent.py` | Generic post-step critic. Wraps `consistency.py` checks + schema validation + optional second-model agreement. `decision_report_critic` stays as the specialized terminal instance. |
| `ModelRouter` | model | NEW | `services/model_router.py` | Resolves `(capability, project config, state signals) → (tier, provider, model)`; wraps `llm_factory.get_llm`. |
| `RetryPolicy` | reliability | NEW | `services/retry_policy.py` | Single implementation of exponential backoff used by both Celery task retries and in-node LLM/tool retries. Replaces the ad-hoc `_exponential_backoff` callers. |
| `WorkflowRunStateMachine` | reliability | NEW | `services/workflow_run_state.py` | The *only* code allowed to write `agent_pipeline_runs.status`. Enforces the transition table in §7. |
| `ReviewRequest` + `/reviews` | HITL | NEW | `models/postgres.py`, `routers/reviews.py` | One row per AI report; accept/reject with reviewer identity; export/notify gates. |
| `AgentInvocation` | API | NEW | `models/postgres.py`, `routers/agent_invoke.py` | Record of a single-agent call made through the public API (sync or async), with idempotency key. |
| `agent_eval_harness` + `ai_evaluation` router | evaluation | EXISTS | `services/agent_eval_harness.py`, `routers/ai_evaluation.py` | Golden datasets, drift, release gate for the agent stack. Extended with per-tier eval. |
| Cost budget | governance | EXISTS | `services/llm_cost_budget.py`, `BudgetedLLM` in `llm_factory.py` | Pre-invocation budget check; project quotas; used as an SLM/LLM escalation input. |
| Event log / replay | observability | EXISTS | Mongo pipeline event log, `GET /pipelines/{id}/replay`, `/agentic-runtime` projection | Extended with invocation + review events. |

### 2.2 Interfaces (Python protocols)

```python
class AgentCapability(Protocol):
    capability_id: str            # "agent.summary.v1"
    input_schema: type[BaseModel] # e.g. RunEvidenceBundleV1
    output_schema: type[BaseModel]
    permission: Literal["read_only", "propose_action", "mutating"]
    default_tier: Literal["deterministic", "slm", "llm"]
    escalation: Literal["none", "on_validation_failure", "on_low_confidence", "always_review"]
    fallback: str                 # named deterministic fallback
    async def run(self, state: WorkflowState, ctx: StepContext) -> dict: ...

class StepContext(BaseModel):
    run_id: UUID
    step_id: str
    attempt: int                  # 1-based
    retry_policy: RetryPolicy
    model_tier: str               # resolved by ModelRouter
    tool_allowlist: list[str]
    deadline_ts: float
    review_policy: Literal["human_required", "human_required_plus_auto_reviewer"]  # no "none": every report-producing step is reviewed; non-report steps carry review.state=not_applicable
    fencing_token: str            # lease id; every stage write carries it in its WHERE clause (§7.3)
    step_llm_budget: int          # shared ceiling for loop iterations + model escalations for this step (§6.2)
```

---

## 3. OpenAPI exposure strategy

### 3.1 Principles

- **Catalog first.** Clients discover agents; they never hard-code stage names. The catalog is a projection of the capability registry, so adding an agent to the registry publishes it.
- **One envelope.** Every agent call, sync or async, uses the same request/response shape; agent-specific payloads live under `input`/`output` and are described by discriminated `oneOf` schemas in the generated OpenAPI so Postman can render per-agent forms.
- **Async by default, sync for cheap agents.** Sync eligibility is an explicit registry flag `sync_eligible=True`, set only on capabilities whose `default_tier == "deterministic"` and `latency_ms ≤ 5_000` (`ingestion`, `flaky_sentinel`, `test_health`, `release_risk`, the orchestration nodes). It is not derived from `cost_usd`: only `ingestion`, the orchestration nodes and the critic carry `cost_usd=0` today, the others inherit the registry default of 0.01 (`agent_capability_registry.py:32`). Everything else returns `202` with a poll URL and an SSE stream. Sync calls run under a process-wide semaphore (default 4) and return `503` with `Retry-After` when it is full.
- **Idempotency.** `Idempotency-Key` header, implemented as a router dependency after authentication (not ASGI middleware, which would hash the body before it knows the user). Key is stored scoped to `(user_id, project_id, method+route)` with a SHA-256 of the canonical body, 24 h TTL in Redis plus a unique index on `agent_invocations(user_id, idempotency_key)`. Same key + same body ⇒ same invocation (200 with the stored row). Same key + different body ⇒ `422`. Same key while the first request is in flight ⇒ `409`. A key never resolves across users or projects, so a key leaked in a Postman collection cannot read another tenant's invocation. Failures are cached too: a `failed` invocation is returned, not re-executed, until the caller uses the explicit retry route.
- **Authorization is body-aware and subject-derived.** The project is derived from the *subject* (`test_run_id`, `pipeline_id`, `workflow_id`) via the existing `_authorize_run_and_project` pattern (`routers/agents.py:873`); the body `project_id` is an assertion that must equal the derived project, otherwise `400`. Every route with a UUID-only path (`/invocations/{id}`, `/reviews/{id}`, `/workflows/{id}`) uses a `require_<subject>_access()` dependency modelled on `require_investigation_access()` (`routers/agent_investigations.py:69-110`): look up the owning project, check membership or ADMIN, return 404 not 403. Project-bound API keys are checked against the derived project via `_enforce_api_key_project_binding`. The authz ratchet in `scripts/quality_gate.py` keys on path `{project_id}` and would auto-pass all of these routes (memory: a guard blind to query params passed 9 IDORs); story E1.6 extends it to inspect Pydantic body models and to require an access dependency on every UUID-only route in the new routers.
- **Roles.** Invoke, retry, cancel: ≥ `QA_ENGINEER` (matches the existing trigger, `routers/agents.py:286`). Workflow publish/delete, agent-config PUT (which sets `mode`, `tools`, `override_policy`): ≥ `QA_LEAD`. Review accept/reject: ≥ `QA_LEAD`, project member, JWT credential only (§8.3). Catalog and reads: any project member. **(rev 3)** API keys are additionally gated by their enforced scopes (`core/deps.py:177`): invoke/retry/cancel need `project:write`; agent-config PUT and workflow publish need `project:admin`; a key with an empty scope list is read-only for non-GET methods (re-audit N31/N32). Review accept/reject remain JWT-only regardless of scope.

### 3.2 Endpoints

| Method + path | Purpose | Sync/async |
|---|---|---|
| `GET  /api/v1/agents/catalog` | List capabilities: id, version, description, permission, default tier, input/output schema refs, sync-eligible flag | sync |
| `GET  /api/v1/agents/catalog/{agent_id}` | One capability incl. JSON Schema for input/output | sync |
| `POST /api/v1/agents/{agent_id}/invoke` | Invoke one agent. `mode: "sync"` allowed only if sync-eligible; else 202 | both |
| `GET  /api/v1/agents/invocations/{invocation_id}` | Status + output + provenance + review state | sync |
| `GET  /api/v1/agents/invocations/{invocation_id}/events` | SSE progress. `EventSource` cannot send `Authorization`, so the client first `POST`s `…/events/ticket` (authenticated) and receives a 60-second single-use stream ticket passed as a query parameter; this is the same mechanism the live event stream should use, and the ticket is bound to the invocation id | stream |
| `POST /api/v1/agents/invocations/{invocation_id}/retry` | Manual retry (new attempt, same invocation); `409` at max attempts with `links.rerun` | 202 |
| `POST /api/v1/agents/invocations/{invocation_id}/cancel` | Cooperative cancel. **Net-new for pipelines**: today `cancel_requested` exists only on `AgentInvestigation`; E7.4 adds it to `agent_pipeline_runs` and checks it atomically in the state machine (§7.2) | 202 |
| `GET/POST /api/v1/projects/{project_id}/workflows` · `GET/PUT/DELETE …/workflows/{id}` · `POST …/{id}/validate` · `POST …/{id}/evaluate` · `POST …/{id}/publish` | Workflow definitions (§4). Built-ins (`offline`, `deep`, `live`) are global, read-only templates: `PUT`/`DELETE` on them returns `405`; `POST …/{built_in}/fork` creates a project copy | sync |
| `POST …/workflows/{id}/runs` · `GET …/runs/{pipeline_run_id}` · `POST …/runs/{pipeline_run_id}/retry` · `POST …/runs/{pipeline_run_id}/cancel` | Workflow execution; `runs/{pipeline_run_id}` is an alias over today's `agents/pipelines/{pipeline_id}`. Named `pipeline_run_id` because `run_id` already means a TestRun UUID on `/agents/runs/...` and a slug on live sessions | 202 / sync |
| `GET/PUT /api/v1/projects/{project_id}/agent-configs/{agent_id}` | Per-agent config (§4). **Supersedes** `…/agent-policies/{agent_id}`: `AgentPolicy` rows migrate into `agent_configs` (E4.4) and the old route becomes a read-only alias for one release, then is removed | sync |
| `GET /api/v1/projects/{project_id}/reviews?state=pending_review` · `GET /api/v1/reviews/{id}` · `POST /api/v1/reviews/{id}/accept` · `…/reject` | Human review gate (§8). Not exposed through the MCP server (§8.3) | sync |
| `GET /api/v1/admin/dlq` · `POST /api/v1/admin/dlq/{id}/replay` | Dead-letter inspection and replay for runs that exhausted retries (ADMIN) | sync / 202 |
| `POST /api/v1/ai-eval/agent-stack-release-gate` (exists) · `POST /api/v1/ai-eval/tier-comparison` · `POST /api/v1/ai-eval/reviewer-quality` · `GET /api/v1/ai-eval/gates/{checksum}` | Evaluation gates (§11). The router prefix is `/api/v1/ai-eval` (`routers/ai_evaluation.py:32`) | sync / 202 |

All paths in this document are written in their full `/api/v1/...` form; where a section abbreviates, the table above is authoritative. Existing routes are kept and documented as "pipeline-level" aliases: `POST /api/v1/agents/pipelines/trigger` becomes sugar for `POST …/workflows/{built_in}/runs`.

### 3.3 Schemas

```yaml
AgentInvokeRequest:
  type: object
  required: [project_id, input]
  additionalProperties: false
  properties:
    project_id:        {type: string, format: uuid, description: "Assertion; must equal the project derived from the subject inside input, else 400"}
    input:             {$ref: '#/components/schemas/<AgentId>InvokeInput'}  # per-agent wrapper model, see note below
    mode:              {type: string, enum: [sync, async], default: async}
    config_overrides:  {$ref: '#/components/schemas/AgentConfigPatch'}  # tighten-only, see §4.3
    add_auto_reviewer: {type: boolean, default: false, description: "May ADD the auto-reviewer on top of the project's review policy; can never remove human review"}
    correlation_id:    {type: string, maxLength: 128, pattern: '^[A-Za-z0-9._:-]+$'}

# Per-agent input wrappers. The registry's input contracts (RunEvidenceBundleV1 etc.) are shared
# by several capabilities, so a oneOf discriminated on a field inside them is not injective and
# Pydantic would reject the union at import. Instead the catalog generates one wrapper per capability:
#   class SummaryInvokeInput(BaseModel):
#       agent_id: Literal["agent.summary.v1"]
#       payload: RunEvidenceBundleV1 | SubjectRef      # SubjectRef = {test_run_id} resolved server-side
#       model_config = ConfigDict(extra="forbid")
# and the handler validates `input` against the wrapper named by the path's agent_id.

AgentConfigPatch:              # the ONLY fields a request may override; everything else is 422
  type: object
  additionalProperties: false
  properties:
    model_tier:        {type: string, enum: [deterministic, slm]}   # downgrade only; llm never requestable here
    max_attempts:      {type: integer, minimum: 1}                  # must be <= project value
    timeout_seconds:   {type: integer, minimum: 10}                 # must be <= project value
    tools_allowlist:   {type: array, items: {type: string}}         # must be a subset of project allowlist
    max_cost_usd:      {type: number, minimum: 0}                   # must be <= project budget

AgentInvocation:
  type: object
  required: [id, agent_id, project_id, status, attempt, max_attempts, requires_human_review, review]
  properties:
    id:              {type: string, format: uuid}
    project_id:      {type: string, format: uuid}
    agent_id:        {type: string, example: agent.summary.v1}
    status:          {type: string, enum: [in_progress, completed, failed, passed]}   # §7 vocabulary
    attempt:         {type: integer, minimum: 1}
    max_attempts:    {type: integer, example: 5}
    next_retry_at:   {type: string, format: date-time, nullable: true}
    output:          {$ref: '#/components/schemas/AgentOutputUnion', nullable: true}
    error:           {$ref: '#/components/schemas/AgentError', nullable: true}
    provenance:
      type: object
      properties:
        model_tier:   {type: string, enum: [deterministic, slm, llm]}
        provider:     {type: string}
        model:        {type: string}
        prompt_versions: {type: object, additionalProperties: {type: string}}
        tools_used:   {type: array, items: {type: string}}
        fallback_used: {type: boolean}
        tokens_in:    {type: integer}
        tokens_out:   {type: integer}
        cost_usd:     {type: number}
        latency_ms:   {type: integer}
    requires_human_review: {type: boolean, description: "ALWAYS true for report-producing agents. Output is a draft until review.state == accepted."}
    review:
      type: object
      required: [state, message]
      properties:
        state:   {type: string, enum: [pending_review, accepted, rejected, superseded, not_applicable]}
        message: {type: string, example: "AI-generated. Human review required before use. Accept at /api/v1/reviews/{id}."}
        review_id: {type: string, format: uuid, nullable: true}
        reviewed_at: {type: string, format: date-time, nullable: true}   # identity of the reviewer is NOT in API/export payloads (§8.2)
    links:
      type: object
      properties:
        self:   {type: string}
        events: {type: string}
        retry:  {type: string}
        review: {type: string}

AgentError:
  type: object
  properties:
    code:      {type: string, enum: [validation_failed, model_unavailable, budget_exceeded, timeout, tool_error, policy_denied, max_retries_exceeded, cancelled]}
    message:   {type: string}
    retryable: {type: boolean}
    attempts:  {type: array, items: {type: object, properties: {attempt: {type: integer}, code: {type: string}, at: {type: string, format: date-time}}}}
```

### 3.4 curl examples

```bash
# Discover
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agents/catalog | jq '.[].agent_id'

# Invoke the summary agent asynchronously on a run
curl -s -X POST http://localhost:8000/api/v1/agents/agent.summary.v1/invoke \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 018f3c2e-7b1a-7c1d-9e4f-2a6b8c0d1e2f" \   # client-generated UUIDv4/ULID; scoped per user+project, not a secret
  -d '{"project_id":"<uuid>","input":{"agent_id":"agent.summary.v1","test_run_id":"<uuid>"},"config_overrides":{"model_tier":"slm"}}'
# → 202 {"id":"...","status":"in_progress","links":{"self":"/api/v1/agents/invocations/..."}}

# Poll
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agents/invocations/<id>

# Manual retry after a failure
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agents/invocations/<id>/retry
```

### 3.5 Implementation notes specific to this repo

- **Route ordering, not a regex, prevents collisions.** Starlette matches `{agent_id}` against any single path segment before Pydantic validation runs, so a pattern on `agent_id` only turns a collision into a confusing 422. The literal routes (`/agents/pipelines/...`, `/agents/catalog`, `/agents/invocations/...`, `/agents/active-runs`, `/agents/runs/...`) must be registered **before** `/agents/{agent_id}/invoke`, and E1.1 adds a test that enumerates the router and asserts that order (memory: router prefix conflict with UUID path param). The pattern `^agent\.[a-z_]+\.v\d+$` is still validated for clear errors; the runtime pseudo-capability is named `agent.finalize.v1` so it matches too.
- Use `Query(None)` defaults only on routes with no direct test callers, or tests receive the `Query` object (memory: bit five times).
- The per-agent input wrappers are generated from the capability registry; a quality-gate guard (`agents.catalog-schema-complete`) fails if any registered capability lacks a Pydantic input model or a wrapper.
- `correlation_id` is validated by pattern and never interpolated into log format strings (structlog key-value only), to prevent log injection.
- `X-AI-Generated` and `X-Human-Review` follow the repo's header convention as `X-TestLookup-AI-Generated` and `X-TestLookup-Review-State`, and are added to CORS `expose_headers`.

---

## 4. Agent configuration model

### 4.1 Three layers, strict precedence

```
 env (ceiling: AI_OFFLINE_MODE, provider allowlist, hard caps)
   ▲ can only tighten
 global ai_config (DB, /settings/ai)        ← EXISTS: provider, model, temperature, confidence_threshold …
   ▲
 project AgentConfig[agent_id] (DB)         ← NEW: per-agent tier/model/thresholds/retry/tools/review
   ▲
 invocation config_overrides (request body)  ← NEW: bounded by AgentConfig.override_policy
```

The existing rule "offline mode is a one-way ratchet" (`ai_config_resolver.py:14-30`) generalizes: **every layer may only make the system safer or cheaper than the layer above**, never the reverse. "Tighter" is not self-evident for every field (a lower `max_tokens` causes more validation failures and therefore more LLM escalations), so the direction is fixed per field in the table below. Anything not in the table is **not overridable** from a request and not settable below the project layer.

| Field | Tighter means | Overridable per request |
|---|---|---|
| `model.tier` | `llm` → `slm` → `deterministic` | yes (downgrade only) |
| `retry.max_attempts`, `timeout_seconds`, `budget.*`, `max_failures_analyzed` | lower | yes (lower only) |
| `tools.allowlist` | subset | yes (narrow only) |
| `mode` (`shadow` < `suggest` < `act`) | lower | no (project layer only, ≥ QA_LEAD) |
| `review.policy` | `human_required_plus_auto_reviewer` over `human_required` | add-only (`add_auto_reviewer`) |
| `review.second_model_check` | on | no |
| `temperature`, `max_tokens`, `escalation.*`, `thresholds.*`, `degraded_ratio` | ambiguous | **no**; project layer only, and validated for composition (§4.3) |
| `provider`, `base_url`, `api_key`, any endpoint | n/a | **never present** at project or request layer (§4.3) |

Ownership rule: `mode` has exactly one writable home, `agent_configs.mode`. The existing `AgentPolicy.mode` (`postgres.py:1877`) is migrated into it (E4.4) and `tools.mutating_allowed` from revision 1 is removed because it is derivable from `mode` (memory: two modules, one rule). A guard asserts only `agent_config_service` writes `mode`. **(rev 3)** `agent_runs.mode` (`postgres.py:1909`) is a third column with the same name, but it is a per-run ledger *record* of the mode the run executed under, not a policy; the guard treats it as read-only provenance.

### 4.2 `AgentConfig` schema (per project, per agent)

```json
{
  "agent_id": "agent.root_cause_analysis.v1",
  "enabled": true,
  "mode": "suggest",
  "model": {
    "tier": "auto",
    "slm": {"provider": "ollama", "model": "qwen2.5:3b", "temperature": 0.0, "max_tokens": 1024},
    "llm": {"provider": "ollama", "model": "qwen2.5:14b", "temperature": 0.1, "max_tokens": 4096},
    "escalation": {"on_validation_failure": true, "on_confidence_below": 70, "max_escalations": 1}
  },
  "thresholds": {"confidence_min": 80, "max_failures_analyzed": 50, "degraded_ratio": 0.3},
  "retry": {"max_attempts": 5, "base_seconds": 30, "cap_seconds": 600, "jitter": 0.2, "retry_on": ["model_unavailable", "timeout", "tool_error"]},
  "timeout_seconds": 120,
  "tools": {"allowlist": ["get_test_history", "get_similar_failures", "search_logs"]},
  "budget": {"max_llm_calls": 30, "max_tokens": 60000, "max_cost_usd_per_run": 5.0, "max_runs_per_day": 10},
  "shadow": {"sample_rate": 0.1, "daily_token_budget": 200000},
  "review": {"policy": "human_required", "auto_reviewer": true, "second_model_check": false},
  "override_policy": {"allow_tier_downgrade": true, "allow_retry_decrease": true, "allow_tool_narrowing": true}
}
```

The `model.slm` / `model.llm` sub-objects are `extra="forbid"` and contain only `provider`, `model`, `temperature`, `max_tokens`. **They may not carry `base_url`, `api_key`, or any endpoint.** Provider endpoints come only from the environment; otherwise a `provider: ollama` entry (residency `local`, so it passes `enforce_provider_policy` under offline mode) could point `base_url` at an external host and egress data while the offline ceiling reports itself intact.

**Defaults** come from the capability registry (`default_tier`, `timeout_seconds`, `cost_usd`) merged with the existing `DEFAULT_BUDGETS` (`agent_investigation_service.py:57-63`: 10 runs/day, 30 LLM calls/run, 60k tokens, 300 s, `max_cost_usd_per_run=5.0`, `max_cluster_children_per_run=1`). The budget field names above are the existing ones; revision 1's `max_cost_usd: 0.50` silently diverged from the 5.0 default and is withdrawn. Missing row ⇒ defaults, exactly as `AgentPolicy` does today (`models/postgres.py:1859`).

### 4.3 Validation rules (server-side, Pydantic)

- `retry.max_attempts ∈ [1, AGENT_MAX_ATTEMPTS_CEILING]` (env ceiling, default 10), default **5** (requirement 8). The pipeline task no longer uses Celery's `max_retries`; the state machine schedules retries itself (§7.4) and reads the same `RetryPolicy` object as the in-node LLM retry loop.
- Composition: `max_attempts × timeout_seconds ≤ deadline_seconds` (default deadline `AI_PIPELINE_DEADLINE_SECONDS=1500`), and `timeout_seconds ≤ AGENT_MAX_TIMEOUT_CEILING` (env, default 600). Ten attempts at a 300 s timeout do not fit a 1500 s deadline and are rejected with the arithmetic in the error.
- `model.tier ∈ {deterministic, slm, llm, auto}`; `auto` means "start at capability default, escalate per rules".
- **Offline ceiling is enforced at resolve time, not only at PUT time.** Every invocation passes the resolved per-agent config through `apply_offline_ceiling` + `enforce_provider_policy` + `configured_provider_allowlist()`; a stored cloud provider that predates an env flip to offline is clamped to the local default and stamped `offline_mode_env_pinned`. PUT-time 422 (provider not in `LOCAL_PROVIDERS`, `llm_policy_service.py:20`) is a courtesy, not the control.
- `tools.allowlist ⊆ registry tools with permission ≤ config.mode` (`shadow` ⇒ read_only only; `suggest` ⇒ + propose_action; `act` ⇒ + mutating). The **capability's own** `permission` is checked against `mode` too: a `mutating` capability (`defect_commander`) cannot be invoked or placed in a workflow for a `shadow` project. Workflow step-level `tools` must be a subset of the referenced config's allowlist. All three checks run at publish time and again at invoke time against the frozen config version.
- Every PUT bumps `config_version`; runs freeze `config_version` into `execution_metadata` so a later config change never alters how a past run is interpreted (mirrors today's frozen `*_enabled` snapshots in state). Manual retry re-resolves against the *current* config (§7.4).

### 4.4 `WorkflowDefinition` schema (requirement 3)

```json
{
  "workflow_id": "wf.custom.fast_triage",
  "version": 3,
  "project_id": "<uuid>",
  "base": "offline",
  "steps": [
    {"id": "ingest",   "agent_id": "agent.ingestion.v1"},
    {"id": "anomaly",  "agent_id": "agent.anomaly_detection.v1"},
    {"id": "rca",      "agent_id": "agent.root_cause_analysis.v1", "config_ref": "agent.root_cause_analysis.v1", "tools": ["get_test_history"]},
    {"id": "review",   "agent_id": "agent.reviewer.v1", "reviews": ["rca", "anomaly"]},
    {"id": "summary",  "agent_id": "agent.summary.v1", "model": {"tier": "slm"}},
    {"id": "triage",   "agent_id": "agent.triage.v1"},
    {"id": "finalize", "agent_id": "agent.finalize.v1"}
  ],
  "edges": [
    {"from": "ingest", "to": "anomaly"},
    {"from": "ingest", "to": "rca", "when": {"kind": "state", "expr": "len(failed_test_ids) > 0"}},
    {"from": "ingest", "to": "summary", "when": {"kind": "state", "expr": "len(failed_test_ids) == 0"}},
    {"from": ["anomaly", "rca"], "to": "review", "join": "all"},
    {"from": "review", "to": "summary"},
    {"from": "summary", "to": "triage", "when": {"kind": "state", "expr": "review.verdict == 'pass' and max_confidence >= thresholds.confidence_min"}},
    {"from": "summary", "to": "finalize", "when": {"kind": "else"}},
    {"from": "triage", "to": "finalize"}
  ],
  "loops": [{"from": "review", "to": "rca", "when": {"kind": "state", "expr": "review.verdict == 'retry'"}, "max_iterations": 2}],
  "retry_policy": {"max_attempts": 5, "base_seconds": 30, "cap_seconds": 600},
  "review_policy": "human_required",
  "deadline_seconds": 1500
}
```

Compiler rules: every `agent_id` must exist in the registry; every registry `dependencies` of a step must appear upstream on all paths; loops require `max_iterations`; exactly one `finalize` (`agent.finalize.v1`); a definition is immutable once `published` (edits create a new version); `config_ref` resolves only within the workflow's own project. Runs record `workflow_id@version` and the plan hash, reusing `compute_workflow_plan_hash`.

**Condition language.** The `when.expr` strings in the example above are display sugar; the stored and evaluated form is a typed JSON condition AST, not a parsed Python subset (a parsed subset with attribute access reaches `__class__`/`__globals__`, and the enum-vocabulary bug class in memory #735 returns the moment a string literal is compared with a status). Grammar:

```json
{"all": [
  {"field": "failed_test_ids", "op": "count_gt", "value": 0},
  {"field": "review.verdict",  "op": "eq",       "value": "pass"},
  {"field": "max_confidence",  "op": "gte",      "ref": "thresholds.confidence_min"}
]}
```

- Node kinds: `all`, `any`, `not`, leaf. Leaf ops: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `count_eq/gt/gte/lt/lte`, `is_null`, `is_true`, `else`.
- `field` must name a key in the **published state schema** (a Pydantic projection of `WorkflowState`, typed); `ref` must name a key in the frozen config snapshot. Unknown names fail at publish, not at run time.
- Enum-typed fields accept only members of their declared enum; a literal outside the vocabulary is a publish error.
- Limits: depth ≤ 6, ≤ 32 leaves, `in` lists ≤ 50, no string operators beyond `eq/ne/in`, no arithmetic. Evaluation is O(leaves) and cannot loop or allocate.
- Edges from a node are evaluated in declaration order; the first true condition wins; `else` fires only when no sibling matched. Exactly one `else` edge per branching node; the compiler rejects branch sets that are not exhaustive.

---

## 5. SLM vs LLM decision matrix

### 5.1 Tiers

| Tier | Typical models (local-first) | Latency / cost | Use |
|---|---|---|---|
| **deterministic** | rules, scikit-learn (`ANALYSIS_MODE=rules|ml`), templates (`_build_fallback_structured_report`) | ms / $0 | Anything with a closed vocabulary or arithmetic; every tier's fallback |
| **slm** (≤ ~8B) | `qwen2.5:3b`, `qwen2.5:7b`, `phi-4-mini`, `gemma-3-4b`, fine-tuned `classifier` track | 1–5 s / ~$0 local | Summaries, classification, extraction, rewriting, JSON shaping |
| **llm** (≥ ~14B or cloud) | `qwen2.5:14b/32b`, `llama-3.3-70b`, `deepseek-r1`, cloud models only when `AI_OFFLINE_MODE=false` | 10–60 s / $ | Multi-hop causal reasoning, hypothesis generation, cross-artifact synthesis, second-opinion review |

### 5.2 Matrix by capability

| Capability | Default tier | Escalate to LLM when | Never escalate because |
|---|---|---|---|
| `ingestion`, `flaky_sentinel`, `test_health`, `release_risk` | deterministic | — | closed-form primary results. E5.1 sets `flaky_sentinel` and `test_health` to `cost_usd=0`; `release_risk` stays positive because its optional narrative calls an LLM |
| `anomaly_detection` | deterministic → slm (label text) | — | statistical; the SLM only phrases `anomaly_summary` |
| `summary` (exec summary, 4 layers) | **slm** | JSON validation fails twice, or `consistency.check_summary_consistency` fails after one SLM repair | — |
| `failure_clustering` | deterministic (embeddings + TF-IDF) → slm (cluster label) | — | — |
| `root_cause_analysis` (per-test) | slm for `category` + `confidence`; **llm** for `explanation` when `confidence < confidence_min` or evidence spans >1 artifact type | as stated | — |
| `cluster_investigation`, `investigator/*` hypotheses | **llm** | — (already LLM) | multi-step tool use; SLMs mis-call tools |
| `log_intelligence`, `contract_validation`, `change_ownership` | slm extraction → llm synthesis only if `not_enough_evidence` after slm | | |
| `regression_watchman`, `gap_detection` | deterministic → slm | | |
| `report_refinement` | slm | contradictions found > 0 | |
| `decision_report` | **llm** (bounded prose over a signed snapshot) | — | release decisions are reasoning-heavy and human-visible |
| `decision_report_critic`, `reviewer` | deterministic checks; **llm second-model** check optional (`review.second_model_check`) | | must not share the generating model's blind spots |
| `triage`, `defect_commander` (mutating) | slm drafts ticket text | — | mutation is policy-gated, not model-gated |

### 5.3 Escalation algorithm (ModelRouter)

```python
async def resolve(cap, cfg, state) -> ModelChoice:
    tier = cfg.model.tier if cfg.model.tier != "auto" else cap.default_tier
    if budget.remaining(state) < cap.cost_usd:          # existing llm_cost_budget precheck
        return ModelChoice("deterministic", fallback=cap.fallback, reason="budget")
    if tier == "deterministic":
        return ModelChoice("deterministic")
    if ModelRegistry.get_active_model("classifier") and cap.kind == "classify":
        return ModelChoice("slm", model=<promoted fine-tune>)
    return ModelChoice(tier, **cfg.model[tier])

# inside the worker, after a call:
if not output_schema.validate(result) or result.confidence < cfg.model.escalation.on_confidence_below:
    if (escalations < cfg.model.escalation.max_escalations
            and tier == "slm" and cfg allows llm
            and ctx.step_llm_budget_remaining >= 1                      # shared with reviewer loops, §6.2
            and budget.remaining(state) >= cost_estimate(cap, "llm")):  # re-check budget BEFORE upgrading
        tier = "llm"; escalations += 1; ctx.step_llm_budget_remaining -= 1
        retry the call (counts as a *model escalation*, not a run attempt)
    else:
        use cap.fallback; provenance.fallback_used = True; stage_quality = "degraded"
```

The pre-call budget check alone is not enough: an escalation triggered by low confidence would otherwise upgrade to a call the budget never approved. Budget is a routing input on both the first call and the escalation. **(rev 3)** The hard cap itself is now enforced atomically at the invocation boundary by `llm_cost_reservation.py` (re-audit M13: a Redis Lua reserve-then-settle inside `BudgetedLLM`), so the router makes the *soft* decision (choose a cheaper tier while budget remains) and relies on the reservation to refuse rather than re-implementing the wall.

Provenance records `tier_requested`, `tier_used`, `escalations`, `fallback_used`. The eval harness gets a per-tier axis so a cheaper default can be proven against the golden set before promotion.

---

## 6. Multi-agent review mechanism

### 6.1 What exists and should be kept as-is

- **Planner → Verifier.** `build_workflow_plan` freezes the expected stage path and hashes it; `verify_workflow_execution` compares the final state to the plan (evidence support, provenance, mutating-action policy alignment, release-policy trace, terminal verification). This is a *process* review.
- **Consistency checks.** `check_summary_consistency`, `check_release_consistency`, `check_analysis_consistency` catch contradictions between agents (e.g. prose says "no regressions" while `is_regression=True`). This is a *content* review.
- **Terminal critic.** `decision_report_critic` reruns 12+ checks over the signed evidence snapshot, performs **at most one** deterministic repair, rechecks, and forces `requires_human_review=True` whenever it repaired or failed. Fallback is `reject_publication`. This is the Reviewer-Validator pattern done correctly: bounded, deterministic, fail-closed.
- **Contract disagreements** are first-class (`FLAKY_VS_REGRESSION`, `CATEGORY_DISAGREEMENT`, `CONFIDENCE_SPLIT` → `PREFER_*`, `MERGE`, `FLAG_FOR_REVIEW`).

### 6.2 What to add: a generic `ReviewerAgent`

Attachable at any point in a user workflow (`{"agent_id":"agent.reviewer.v1","reviews":[...]}`), producing `ReviewVerdictV1`:

```python
class ReviewVerdictV1(BaseModel):
    reviewed_steps: list[str]
    checks: list[Check]                       # name, passed, severity, detail
    verdict: Literal["pass", "pass_with_flags", "retry", "reject"]
    disagreements: list[Disagreement]         # kind, agents, resolution
    hallucination_risk: Literal["low", "medium", "high"]
    requires_human_review: bool               # always True if verdict != pass
    second_model: Optional[SecondModelCheck]  # provider/model/agreement_score
```

Check families, in execution order (cheap → expensive):

1. **Schema & grounding (deterministic).** Output validates against the step's contract; every cited `test_case_id`, `cluster_id`, artifact id exists in state (extends `_repair_references`); numbers quoted in prose match state fields within tolerance.
2. **Cross-agent consistency (deterministic).** Run the existing `consistency.py` families over the reviewed steps; disagreements map to `Disagreement`.
3. **Self-consistency (slm, optional).** Re-ask the *same* tier model to extract claims from its own prose, then diff claims vs state. Cheap and catches most confabulated specifics.
4. **Second-model agreement (llm, optional, `review.second_model_check`).** A *different* model (different family if available, e.g. `qwen` generates → `llama` reviews) answers "does this conclusion follow from this evidence? list unsupported claims". Agreement < 0.7 ⇒ `pass_with_flags`; unsupported claim on a blocking issue ⇒ `retry` (once) then `reject`.
5. **Policy (deterministic).** Mutating proposals present while `mode != act` ⇒ downgrade to proposals; any `mutating` tool in `tools_used` without policy ⇒ `reject` and open an incident event.

**The reviewer's own verdict is checked before anyone acts on it.** Families 3 and 4 are LLM calls whose output drives a state transition, so the Supervisor applies deterministic invariants to `ReviewVerdictV1` first and treats any violation as `reject` (fail-closed, the same standard the terminal critic meets today):

- `verdict == "pass"` requires zero `disagreements` with blocking severity and zero failed checks in families 1, 2, 5.
- `hallucination_risk == "high"` is incompatible with `pass`.
- `requires_human_review` must be `True` unless `verdict == "pass"`.
- `second_model.agreement_score` must be in `[0, 1]` and, when `< 0.7`, `verdict` must not be `pass`.

Verdict handling by the Supervisor: `retry` re-runs the reviewed steps with `tier=llm`; `reject` routes to `finalize` with status `failed` and error `validation_failed`; `pass_with_flags` continues and pins `requires_human_review=True`.

**One budget for loops and escalations.** A workflow loop (`loops[].max_iterations`) and a model escalation (`escalation.max_escalations`) fire on the same trigger for the same step and would otherwise each spend LLM calls the other believes are used up. Both decrement a single `step_llm_budget` on the `StepContext` (default `max_escalations + max_iterations`, capped by the run's `budget.max_llm_calls`). When it reaches zero the step takes its deterministic fallback and the reviewer verdict for it is at best `pass_with_flags`.

### 6.3 Anti-collusion rules

- Reviewer never sees the generator's chain-of-thought, only output + evidence snapshot (same as the critic today).
- Reviewer prompts are versioned separately from generator prompts (`prompt_versions` already recorded).
- Reviewer's own output is deterministic-checked (schema, reference grounding, and the verdict invariants above) but never LLM-reviewed — one level of review, not a tower.

---

## 7. Workflow reliability, retry logic and state machine

### 7.1 Status vocabulary (requirement 9)

Public statuses are exactly **`in_progress | completed | failed | passed`**. Mapping to internal states:

| Internal (DB) | Public | Meaning |
|---|---|---|
| `pending` (queued, not started) | `in_progress` | Enqueued; `queued_at` set; a lease will be taken on start |
| `running` (lease held) | `in_progress` | Worker holds a lease with heartbeat |
| `retry_wait` (NEW) | `in_progress` | Attempt failed with a retryable error; `next_retry_at` set |
| `completed` | `completed` | All steps finished; outputs exist; review pending |
| `passed` (NEW) | `passed` | Review is settled positively. See the definition below |
| `failed` | `failed` | Non-retryable error, retries exhausted, reviewer `reject`, human reject, cancelled, or lease expired with no attempts left |

**Definition of `passed`, for every workflow.** Revision 1 left `passed` unreachable for workflows without review. Fixed:

- Report-producing runs (`review.policy = human_required*`, the default and the only policy for anything that emits a summary, analysis, decision report or refined report): `passed` ⇔ `completed` ∧ auto-reviewer verdict ∈ {`pass`, `pass_with_flags`} (if enabled) ∧ a human `accepted` the `ReviewRequest`.
- Non-report runs (single-agent invocations of deterministic, sync-eligible capabilities such as `flaky_sentinel`; workflows whose every step has `output` outside the report contracts): Finalize moves them **directly** `completed → passed` in the same transaction, with `review.state = not_applicable`. No `ReviewRequest` is created. Clients may therefore always wait for `passed | failed` as the terminal pair.
- A run can rest at `completed` indefinitely (awaiting a human). That is a legitimate, visible state, not a stale one: the `pending_review` age alert (§10) watches it.

`partial` is **removed** from the public API: a run that finished with degraded/skipped stages is `completed` with `stage_quality="degraded"` and `skipped_stages[]` populated. Cancellation is `failed` with `error.code="cancelled"`. The backfill `partial → completed` changes the meaning of any existing `status == "partial"` comparison outside the DB layer, so E7.1 includes a repo-wide sweep (backend routers/services, frontend, release-gate rules, Grafana queries) and a quality-gate guard that fails on the literal `"partial"` used as a pipeline-status comparison after migration 0173.

### 7.2 Transition table (enforced by `WorkflowRunStateMachine`, the only writer)

```
pending ───start(lease acquired)────────► running
running ──all steps ok, report-producing─► completed
running ──all steps ok, non-report───────► passed        (via completed, same txn)
running ──retryable error────────────────► retry_wait    (attempt < max_attempts)
running ──non-retryable error────────────► failed
running ──retryable, attempt==max────────► failed        (error.code = max_retries_exceeded, → DLQ)
running ──cancel_requested───────────────► failed        (error.code = cancelled)
running ──lease expired (reaper)─────────► retry_wait | failed   (counts as an attempt)
retry_wait ─timer / manual retry─────────► running       (attempt += 1, new lease)
retry_wait ─cancel───────────────────────► failed        (scheduled task finds the row not in retry_wait and exits)
completed ─review pass + human accept────► passed
completed ─auto-reviewer reject / human reject ► failed  (error.code = review_rejected)  [terminal]
failed ────manual retry──────────────────► running       (attempt += 1, only if attempt < max_attempts after re-resolving config; else 409 + links.rerun)
passed ────(none; terminal)
```

Every transition is one `UPDATE … WHERE id = :id AND status = :expected AND (fencing_token = :token OR :token IS NULL) RETURNING status`. Zero rows ⇒ the caller lost a race and must re-read; it never writes anyway. **Cancel is resolved in the same statement** as the retry decision: the error handler runs `UPDATE … SET status = CASE WHEN cancel_requested THEN 'failed' ELSE 'retry_wait' END … WHERE status='running' AND fencing_token=:token`, so a cancel that lands in the same tick as a retryable failure wins deterministically and a scheduled retry can never resurrect a cancelled run.

Invariants (each becomes a quality-gate guard and a DB `CHECK`):
- `status IN ('pending','running','retry_wait','completed','passed','failed')`.
- `status='running' ⇒ lease_owner IS NOT NULL AND fencing_token IS NOT NULL AND lease_expires_at > now() - grace`.
- `status IN ('completed','passed','failed') ⇒ completed_at IS NOT NULL`.
- `status='failed' ⇒ error IS NOT NULL`.
- `status='passed' AND review_policy LIKE 'human_required%' ⇒ EXISTS review_requests(subject_id=id, state='accepted')`.
- A row that has emitted a `mutating` tool call has `mode='act'` and, at the time of the call, an `accepted` review for the run that proposed it (§7.5).

### 7.3 Lease, heartbeat and fencing replace the 30-minute guess

Today: a `running` row is displayed as failed once it is older than a fixed 30 minutes or has any failed stage row; the reaper beat persists the same rule every 10 minutes. A legitimately long deep run is misreported, and between the 30-minute mark and the next beat the DB and UI disagree.

Proposed:

- **Lease.** On start the worker writes `lease_owner`, a fresh `fencing_token` (UUID), and `lease_expires_at = now + 2·heartbeat_interval` (interval 30 s). **(rev 3)** The repo now has a worked precedent for this pattern: `llm_cluster_semaphore.py` (re-audit M12) holds LLM slots as Redis sorted-set members scored by lease expiry on the *server* clock, renews from inside the call, bounds each renew by the time left, and stops a live holder that cannot keep its lease (`LLMSlotLost`). E7.3 reuses its clock-skew and renew-bounding rules; the run lease lives in Postgres (it must be transactional with status) but the timing discipline is the same.
- **Heartbeat inside stages, not only between them.** Revision 1 hooked heartbeats to `mark_stage_running/done`, which run at stage boundaries; a single 3-minute LLM call would look dead. The heartbeat is instead an `asyncio` task started by `_make_checkpointed_node` for the duration of the node and cancelled on exit, independent of whether the agent calls the `BaseAgent` hooks (the code comment at `workflow.py:1621-1630` notes that three specialists and the cluster-investigation node functions never call them; E7.3 also audits that gap because under leases it would become a wrongful reap, not a cosmetic status).
- **Fencing on every write.** All stage writes (`_checkpoint_stage`, `_mark_stage_executed/_failed`, stage rows, ledger rows, state-machine transitions) carry `WHERE fencing_token = :token`. If the reaper has reassigned the run, the old attempt's writes affect zero rows and the node raises `LeaseLost`, which is swallowed at the task boundary (no retry, no status write). This is what actually prevents double execution; the lease alone only prevents double *dispatch*.
- **Reaper.** `reap_stuck_agent_pipelines` keeps its name and schedule; its predicate becomes `status='running' AND lease_expires_at < now()`. It rotates the fencing token, then transitions to `retry_wait` or `failed` by attempts. `RUNNING_STALE_THRESHOLD` and `_apply_effective_status` are deleted. The failed-stage flip (`routers/agents.py:112-129`) is replaced by the state machine: a failed stage raises inside the node and the error handler decides `retry_wait`/`failed` immediately.

### 7.4 Retry policy (requirement 8)

One implementation, two consumers:

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5          # requirement default; env ceiling AGENT_MAX_ATTEMPTS_CEILING
    base_seconds: float = 30
    cap_seconds: float = 600
    jitter: float = 0.2            # symmetric ±20%; NOTE today's _exponential_backoff is one-sided +0..20% (tasks.py:231), so this is a deliberate change
    retry_on: frozenset[str] = frozenset({"model_unavailable", "timeout", "tool_error", "lease_expired"})

    def delay(self, attempt: int) -> float:              # attempt is 1-based
        raw = min(self.base_seconds * 2 ** (attempt - 1), self.cap_seconds)
        return raw * (1 - self.jitter + 2 * self.jitter * random.random())
```

- **Run-level.** The pipeline task is enqueued with Celery retries disabled. On a retryable failure the state machine writes `retry_wait` + `next_retry_at` and enqueues the same task with `countdown=delay`, carrying `(run_id, expected_attempt)`. The scheduled task re-enters through the state machine (`retry_wait → running` guarded on `attempt = expected_attempt`); if the row is no longer in `retry_wait` (cancelled, manually retried earlier) it exits without side effects.
- **Dedup lock is extended, not released.** Revision 1 proposed releasing the dedup lock (`testlookup:dedup:pipeline:{test_run_id}:{workflow_type}`, TTL 7200 s, `tasks.py:1911`) before scheduling and re-acquiring on fire. That reopens the mirror image of the bug in memory ("a dedup lock can silence its own retry"): a manual retry or webhook redelivery in the gap would start a second concurrent run. Instead the lock's TTL is extended to `next_retry_at + grace` on entering `retry_wait`, and **the state machine, not the lock, decides whether a new trigger is accepted**: a trigger for a subject whose latest run is `in_progress` returns the existing run (200) rather than creating one. The lock remains a belt for the case where the DB check races.
- **Step-level.** LLM/tool calls retry with the same policy but `cap_seconds` bounded by the step timeout; step retries do not consume run attempts. Model escalation (§5.3) and reviewer loops share the `step_llm_budget` (§6.2), separate from both.
- **Manual retry (shipped E7.4; T2).** In-progress runs (including `retry_wait`) refuse with 409. Failed or degraded-completed runs may resume; review-rejected runs are terminal and refuse with 409 plus `links.rerun`. The current analysis-mode snapshot is compared with the frozen fingerprint: unchanged resumes the same run, changed starts a new pipeline with `rerun_of`; invocation retry instead returns 409 plus `links.rerun`. The stored attempt ceiling refuses retry with 409. Request-level retry idempotency and full per-agent config comparison remain gaps; this does not re-resolve configuration underneath frozen checkpoints.
- **Re-run.** `POST …/runs` with `rerun_of=<run_id>` copies inputs, starts at attempt 1, links lineage; checkpoint restore (`_checkpoint_stages`) is offered via `resume_from_checkpoint=true` and is *disabled* when the workflow definition version or any referenced agent config version changed.
- **Non-retryable by construction**: `validation_failed` after escalation, `policy_denied`, `budget_exceeded`, `review_rejected`, `cancelled`, `lease_lost`.

### 7.5 Saga semantics for mutating steps

`triage` and `defect_commander` can create or update Jira/GitHub issues. The existing `AgentActionLedger` (`agent_action_ledger_service.py`) has the vocabulary `proposed → pending_review → approved → executing → executed | failed | rejected | rolled_back`; revision 1's `intended/applied` were new names and are withdrawn in favour of the existing ones.

- **Idempotency key for a tool call is `(run_id, tool, subject_id)`**, never including `attempt`. `subject_id` is the stable thing being acted on (the cluster id or canonical test id), so a re-run of the stage after a mid-stage crash computes the same key.
- **Ledger lookup is the first statement of the stage node**, before any planning logic, not only inside the tool wrapper. Checkpoints are written only at the end of a successful stage (`workflow.py:1637-1643`), so a stage that created a ticket and then failed will re-run from scratch; the stage entry loads `executed` rows for this run and removes those subjects from its work list before deciding what to create.
- The wrapper still writes `executing` before the external call and `executed`/`failed` after, both fenced by the lease token.
- Mutating calls are permitted only when `mode='act'` **and** the proposing run has an `accepted` `ReviewRequest` (checked in the wrapper; violation raises `policy_denied` and the run fails). This is the §10 rule promoted to an invariant with a test in E8.
- On run failure after an `executed` action, Finalize emits a compensation *proposal* (never auto-reverts external tickets) and marks the run `failed` with `error.detail.uncompensated_actions[]`.

---

## 8. Human-review enforcement (requirement 10)

### 8.1 Model

`ReviewRequest(id, project_id, kind ∈ {report, eval_drift}, subject_type ∈ {pipeline_run, invocation, decision_report, summary, capability}, subject_id, capability_id NULL, state ∈ {pending_review, accepted, rejected, superseded}, requested_by, created_by='system', reviewed_by, reviewed_at, reason_code NULL, notes NULL, evidence_bundle_sha256, ai_disclaimer_version)`.

Created by Finalize for every run/invocation that produced a report (the report contracts: `DecisionReportV1`, `PreliminarySummary`, `RefinedReport`, `AnalysisAgentOutput`), and by the drift gate (§11) with `kind = eval_drift`. There is **one** `ReviewRequest` per run or invocation; the reports it produced inherit that review state (a decision report cannot be accepted separately from its run). A new run over the same subject marks older requests `superseded`. `requested_by` is the user who triggered the run; `evidence_bundle_sha256` is the existing decision-report payload field (compared by `compute_decision_evidence_hash`; revision 1 called it `evidence_fingerprint`, which is only the critic's check name).

Reject requires a `reason_code` from a closed enum (`wrong_category`, `unsupported_claim`, `missing_evidence`, `contradiction`, `stale_data`, `other`). `notes` is free text and is **never** exported to eval datasets, compliance packs, or webhooks; it is redacted with the same `sanitize_for_llm` path used for prompt egress before persistence. Only `reason_code` becomes an eval label (§11.3).

### 8.2 Where the requirement is communicated

| Surface | Mechanism |
|---|---|
| **API** | Every report-bearing response carries `requires_human_review: true` and `review.{state,message,review_id,reviewed_at}`; `X-TestLookup-AI-Generated: true` and `X-TestLookup-Review-State: <state>` response headers; OpenAPI descriptions state it on every schema. Reviewer identity is shown in-app only; API, export and webhook payloads carry `reviewed: true` and a timestamp, not a name, unless the project opts in. |
| **Export / PDF / notifications** | `summary_report_pdf`, digests, PR comments and webhooks refuse `pending_review` subjects. Inclusion of drafts is a **per-project setting** (`allow_unreviewed_distribution`, default off, settable by ADMIN/QA_LEAD, audit-logged), not a request flag: automated channels have no human caller to pass a flag. When on, the artifact is watermarked "DRAFT — AI-generated, not human-reviewed", the notification body carries the same sentence, and every inclusion writes an `access_audit_logs` row. Interactive export may additionally pass `include_unreviewed=true` only with ≥ QA_LEAD. |
| **Pre-existing report routes** | Routes that already return these contracts (run summary, decision report GET, MCP `get_run_summary`, notification templates) predate the envelope. A consumer-side guard (`reviews.report-consumers-carry-review-block`) requires every `response_model` containing a report contract to include the `review` block and every MCP tool returning one to include `review_state`. |
| **UI** | Banner on run detail, decision report, summary; "Review queue" page (`/reviews`) with accept/reject; status chip shows `completed · awaiting review` vs `passed · reviewed <time>` (name on hover, in-app). |
| **MCP server** | Tool results include `review_state` and the disclaimer string; a `list_pending_reviews` tool exists. **Accept and reject are not exposed as MCP tools**, and a parity test asserts no MCP tool reaches `/reviews/*/accept|reject`: the MCP server forwards the caller's bearer, so an agent acting for a QA lead could otherwise approve its own output. |
| **CLI** | `testlookup reviews list/accept/reject`; accept/reject work only on a JWT profile (the CLI supports both JWT and `X-API-Key`); report commands print the disclaimer to stderr. |
| **Release gate** | GO/CONDITIONAL_GO/NO_GO from an unreviewed decision report changes the **value clients already read**: `decision` becomes `PENDING_REVIEW` (not `GO` plus an additive `advisory` flag, which every existing CI consumer would ignore and fail open). Consumers that want the draft value pass `allow_advisory=true` and receive `decision: "ADVISORY_GO"` etc. Blocking CI integrations require `passed`. Coordinate with the pending pass-rate-bands wiring of `/release-gate`. |

### 8.3 Enforcement, not decoration

- Accept/reject require `credential_kind == "jwt"` (the API-key path is bound as `CREDENTIAL_KIND_API_KEY` in `deps.py`; there is no service role in `UserRole`, so revision 1's "role=service" had nothing to key on). API keys are refused outright.
- Synthetic accounts are refused on a real column: E8.1 adds `users.is_synthetic` (backfilled from the `qa-lead.testlookup.local` domain used by `default_qa_lead_service.py`), and `require_role` refuses promotions for synthetic accounts generally. Domain-string matching alone is spoofable by any admin who creates a user.
- Separation of duties: `reviewed_by != requested_by` is enforced when the run proposes mutating actions (`mode=act`); configurable per project for read-only reports, default on.
- `passed` is unreachable without a `ReviewRequest.accepted` row for report-producing runs (state-machine invariant, §7.2).
- Acceptance is recorded in the access-audit log and the pipeline event log; the accepted payload's `evidence_bundle_sha256` is stored on the review so a later change to the payload invalidates it (`superseded`).
- Quality-gate guards: `reviews.report-producers-create-review-request` (every capability whose `output` is a report contract is on a path that reaches Finalize with review enabled) and the consumer-side guard above.

---

## 9. Real-world patterns and standards

| Pattern / standard | Where it applies here | Notes |
|---|---|---|
| **Orchestrator (not choreography)** | LangGraph graph is the single controller | Choreography (agents publishing events that trigger other agents) would make requirement 9 unprovable. |
| **Supervisor-Worker** | Supervisor node + capability workers | Keep the supervisor deterministic; LLM-in-the-loop routing only for explicitly authored `llm_judgment` branches. |
| **Reviewer-Validator (Critic)** | `decision_report_critic`, new `ReviewerAgent` | Bounded repair (max 1), fail-closed, heterogeneous second model. |
| **Planner-Executor-Verifier** | `agent_planner.build_workflow_plan` / graph / `verify_workflow_execution` | Plan hash = tamper evidence. |
| **Toolformer / function-calling with allowlists** | `app/tools/`, capability `permission`, config `tools.allowlist` | Tool permission classes read_only < propose_action < mutating, gated by `mode`. |
| **Saga with compensation proposals** | mutating steps via `AgentActionLedger` | Never auto-compensate external systems; propose. |
| **Outbox / idempotent consumers** | `Idempotency-Key`, ledger-before-call, dedup lock | Existing dedup lock pattern; document the release-before-reschedule rule. |
| **Lease + heartbeat (fencing)** | run lease in §7.3 | Replaces time-based staleness. |
| **Exponential backoff with full jitter** | `RetryPolicy` | AWS Architecture Blog "Exponential Backoff and Jitter"; cap at 600 s already matches. |
| **Dead-letter queue** | existing `_send_to_dlq` (no DLQ router exists today) | Add `GET /api/v1/admin/dlq` and `POST /api/v1/admin/dlq/{id}/replay` to satisfy manual retry for exhausted runs. |
| **Circuit breaker** | per provider (Ollama, cloud) and per external tool (Jira, GitHub) | Open after N consecutive `model_unavailable`; while open, ModelRouter returns `deterministic` immediately (no 300 s timeouts stacking). Memory: a shared cache spreads one outage everywhere — scope breakers per provider+base_url, not globally. |
| **Bulkhead** | Celery queues `critical > ingestion > ai_analysis > default`, `LLM_MAX_CONCURRENT_ANALYSES` | Agent invocations go to `ai_analysis`; sync invocations bounded by a semaphore. |
| **Budget / rate governance** | `llm_cost_budget`, `BudgetedLLM` | Budget is a routing input, not just a kill switch. |
| **OpenTelemetry semantic conventions for GenAI** (`gen_ai.*` attributes) | spans per step with `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, plus `testlookup.agent_id`, `tier`, `attempt` | Existing Prometheus `llm_requests_total`/`llm_request_duration_seconds` stay; add traces. |
| **OpenAPI 3.1 + JSON Schema 2020-12** | catalog and envelopes | Discriminated unions per agent; `Idempotency-Key` per IETF draft-ietf-httpapi-idempotency-key-header. |
| **NIST AI RMF / ISO/IEC 42001** | human oversight (MANAGE), provenance (MAP/MEASURE) | `ReviewRequest`, provenance block, eval gate map onto these; useful for the compliance packs already produced. |
| **EU AI Act transparency (Art. 50)** | AI-generated content disclosure | The disclaimer + `X-TestLookup-AI-Generated` header. |
| **Model Context Protocol** | `mcp/server.py` | Expose the same catalog as MCP tools; one registry, two transports. |

---

## 10. Best practices for agentic AI (actionable for this repo)

**Safety**
- Keep `AI_OFFLINE_MODE` as an env ceiling; new per-agent configs must pass through `enforce_provider_policy` and `sanitize_invocation` (already the only construction boundary for chat models).
- Mutating tools require `mode=act` **and** a reviewer `pass` **and** an accepted human review for the run that proposed them; shadow-mode promotion counter (`shadow_runs_completed`) stays the evidence for promotion.
- Restricted expression language for workflow conditions; never `eval`. Fuzz it in tests.

**Reliability**
- One `RetryPolicy`, one state-machine writer, one reaper predicate (lease). Add a quality-gate guard that greps for direct writes to `AgentPipelineRun.status` outside the state machine (memory: guard the class, not the module).
- Deterministic fallback for *every* capability (`fallback` field is already mandatory in the registry); the fallback is exercised in tests by faking `model_unavailable`.
- Checkpoint restore disabled across config/definition version changes.

**Evaluation**
- Per-tier golden runs in `agent_eval_harness`: promote an SLM default only when its score is within the tolerance band of the LLM on the same dataset; record in `ai_eval_gate_runs`.
- Mutation-test new reviewer checks (memory: a fixture on a misread scale passed regardless of the rule it named).
- Track the reviewer's false-omission rate: human `reject` after reviewer `pass`; feed into the G5 drift gate (`/api/v1/ai-eval`, §11.2).

**Monitoring**
- Emit *and* assert emission (memory: declaration is not emission): `agent_invocations_total{agent,tier,status}`, `agent_run_transitions_total{from,to}`, `agent_retry_attempts_total{agent,reason}`, `review_requests_total{state}`, `model_escalations_total{agent,from,to}`, `circuit_breaker_state{provider}`.
- Alert on `in_progress` age > deadline + grace (should be impossible with leases; the alert proves the invariant), on DLQ depth, and on `pending_review` age.
- Report `measured:false` rather than 0% when no reviews exist (memory: absence is not health).

**Human-in-the-loop**
- Default `human_required`; auto-reviewer is *in addition*, never instead.
- Review UI shows the evidence snapshot and the reviewer's checks side by side, not just prose.
- Rejections require a reason; reasons become eval labels (closes AI-F1 "label integrity" from the July plan).

**Model selection**
- Tier per capability, escalation bounded to one step, both recorded in provenance.
- Prefer heterogeneous families for generator vs reviewer.
- Pin exact model tags in config (`qwen2.5:7b-instruct-q5_K_M`, not `latest`).

**Workflow design**
- Deterministic routing, bounded loops, a single finalize.
- Steps declare contracts; the compiler type-checks edges.
- Keep the three built-in workflows as read-only templates; custom workflows fork them.

---

## 11. Evaluation architecture — evals as the control loop

### 11.1 Can evals be used here? Yes, and the loop is half-built

Every proposal in §3–§8 introduces a choice that is currently a human guess: which tier a capability defaults to, what the escalation confidence threshold is, whether a reviewer check is worth its cost, whether a user's custom workflow is better than the built-in. Each is an **A/B question over a fixed dataset**. The repo already has more of the answer than revision 1 credited:

| Exists | Where | What it does today |
|---|---|---|
| CI blocks unattested prompt changes | `scripts/quality_gate.py:3020-3210`, job `quality-gate` (`ci.yml:136-148`) | Fails when a registered prompt's content changes and `prompt_manifest_eval.json` does not carry a fresh attestation for it |
| Attestation calls the gate | `python -m app.services.prompt_registry --attest <change-id>` → `evaluate_agent_stack_release_gate` (`prompt_registry.py:1186-1195`) | Offline (golden) and online modes; writes the attestation digest |
| Gate service | `eval_gate_service.py` | `GateStatus ∈ {PASS, WARN, FAIL, NO_BASELINE}`; manifest checksum; `ai_eval_gate_runs` rows |
| Harness | `agent_eval_harness.py` | `accuracy` (vs ground truth), `brier`, `ece`; plus `coherence`, `completeness`, `actionability`, which are **deterministic contract checks** (confidence in range, reason non-empty, evidence ≥ 1 at confidence ≥ 60, an action present for non-flaky verdicts). They are 1.0 on the golden set by construction and detect malformed output, not wrong output |
| Task-type metrics | `ai_eval_service.py:179-450` | classification F1, kind classification, root-cause match, duplicate detection, release-decision agreement |
| Feedback → dataset | `build_dataset_from_feedback` | `expected_output.correct` derived from `AIFeedback.rating` |
| Drift | `detect_quality_drift` | 7 days vs the *preceding* 7 days, fixed 0.02 absolute delta, unweighted mean of `AIEvalRun` rows, no variance |
| Scheduled eval | beat `daily-agent-eval` 04:00 → `run_scheduled_agent_eval` | exists; what it writes is checked in E9.1 |
| Golden store | `golden_agent_outputs.py` | 9 usable entries, all AnalysisAgent; 4 negative, 8 wrong-agent fixtures |
| Scorer tests | `tests/services/test_agent_eval_harness.py` (22 tests) | wrong-agent corpus must fail accuracy; `_failed_report` never-raises path |
| **(rev 3)** Prompt-eval recordings | `services/prompt_eval_recordings.py` + `prompt_eval_recordings.json` (re-audit M16) | `--record <prompt_id>` runs the CURRENT prompt through `get_llm` on golden cases, stores raw outputs + prompt `content_hash` + provenance digest; `check_recordings` fails when the hash moved without re-recording, when a measured entry has no outputs, or when outputs score below `min_score` |

**The two structural gaps** the rest of this section closes:

1. **The attestation gate scores recorded outputs; the recordings module runs the model, but only for gated prompts.** `evaluate_pre_release_gate → compute_metrics_for_task_type(items)` reads `expected_output.correct` stored *inside* the dataset item (`ai_eval_service.py:185`), so on its own a regressing prompt attests identically to a good one. **(rev 3)** M16 recordings fix this for prompts whose output is a scorable decision: an edit must be re-recorded through the live model or the gate fails on the hash mismatch. Still unmeasured: (a) prose-producing prompts (summary, decision report, refinement) have no recordings; (b) a *model*, *tier* or *routing* change under an unchanged prompt leaves every recording valid, because the hash is over the prompt text; (c) the attestation guard watched set is prompts only. E9.1 therefore extends recordings, not the gate: key recordings by `(prompt content_hash, provider/model@tier)` and require re-recording when any component of that key changes.
2. **One agent has data; one signal is measured twice.** Coverage is 1 of ~29 capabilities. Feedback-derived datasets and the drift agreement rate both read `AIFeedback.rating`, so "accuracy" after a change reproduces the accept rate by construction.

```
            offline (pre-merge, pre-publish)                       online (production)
   ┌──────────────────────────────────────────┐        ┌───────────────────────────────────────┐
   │ golden INPUTS per capability + labels    │        │ review accept / reject(reason_code)   │
   │ pilot corpora → per-contract samples     │        │ AIFeedback (rating / corrected_*)     │
   │ mutation generator per output contract   │        │ reviewer verdict vs human outcome     │
   │ CANDIDATE INFERENCE (Ollama / recorded   │        │ release outcome (manual incident mark)│
   │   fixture provider) → outputs → scorers  │        └──────────────┬────────────────────────┘
   └──────────────┬───────────────────────────┘                       │ time-split, holdout (§11.3)
                  │ AgentEvalReport (+ CI of each metric)              ▼
   ┌──────────────────────────────────────────────────────────────────────────────────────────┐
   │ EVAL GATE  eval_gate_service (EXISTS) + EvalVerdict enum (ONE vocabulary, §11.5)         │
   │  manifest = {prompt_versions, model_versions, routing_versions, tier_defaults,           │
   │              reviewer_checks, workflow_ref}                                              │
   │  verdict = pass | fail | insufficient_samples  (WARN/NO_BASELINE map onto these)         │
   └───────┬──────────────────┬──────────────────┬──────────────────┬───────────────────────────┘
           │                  │                  │                  │
   CI attestation guard   config PUT (§4)   ModelRegistry       WorkflowDefinition publish
   (EXISTS; watched set   refuses regressing promote/retire     refuses regression unless
    extended to models/   tier/threshold                        accept_regression + reason
    tiers/routing)
```

### 11.2 Five gates: trigger, dataset, metrics, what is blocked, and what `insufficient_samples` does

| Gate | Trigger | Dataset | Metrics | On `pass` / `fail` / `insufficient_samples` |
|---|---|---|---|---|
| **G1 Prompt/model/routing change** (EXISTS for prompts; extended) | The attestation guard's watched set grows from registered prompts to `llm_factory.py`, `model_router.py`, capability `default_tier`/`escalation`, and reviewer check lists. Attestation **must include candidate inference** (§11.4; **(rev 3)** exists as `prompt_eval_recordings --record` for decision prompts, to be generalised) | golden inputs + labels per capability, negative + wrong-agent fixtures | Ground-truth metrics only decide: `accuracy` (with Wilson 95% CI), `brier`, `ece`; task-type metrics where defined. Contract checks (`coherence/completeness/actionability`) are reported as **validity** and must be 1.0 (any drop is a schema regression, a hard fail) | pass ⇒ attestation written, merge allowed. fail ⇒ merge blocked. insufficient ⇒ merge blocked **for that capability's prompts only**, with the missing count in the CI annotation; a capability may be marked `eval_exempt` in the registry with a reason, which the dashboard shows |
| **G2 Tier promotion** (NEW, feeds §5) | `PUT agent-configs` that changes `model.tier` / `escalation`; nightly | same golden inputs run at both tiers, **paired** | paired difference per ground-truth metric with a 95% CI; non-inferiority if the CI's lower bound ≥ −δ (δ default 0.05); cost and latency deltas reported | pass ⇒ write accepted. fail ⇒ 422 with the report. insufficient ⇒ 422 for a *downgrade*, allowed for an *upgrade* (upgrading is safe by the tighten rule) |
| **G3 Reviewer quality** (NEW, feeds §6) | nightly; reviewer prompt/check change | (a) mutated goldens **per mutation class** including semantic classes (unsupported causal claim, plausible prose with wrong category, correct numbers wrong conclusion) that only families 3–4 can catch; (b) clean goldens for false-flag rate; (c) human outcomes for reviewer-passed reports | recall per (mutation class × check family); false-flag rate on clean corpus; **false-omission rate** = P(human reject \| reviewer pass) (revision 1 called this precision) | pass/fail gates reviewer prompt changes. `second_model_check` may be auto-disabled only if its recall on the *semantic* classes is not above the deterministic families' by ≥ 0.10 over 30 days **and** each class has ≥ 30 samples; otherwise no change |
| **G4 Workflow definition** (NEW, feeds §4.4) | `POST …/workflows/{id}/evaluate` and `publish` | replay corpus: the project's last N runs with outputs **cached by `(agent_id, prompt_version, input_hash)`**; a cache hit replays, a miss either calls the local model (if `allow_live_inference`) or marks the step `unmeasured` | plan-verification pass rate, degraded rate, reviewer reject rate, cost/latency, **coverage** = fraction of steps measured | pass ⇒ publish. fail ⇒ refuse unless `accept_regression=true` + reason (recorded). insufficient (coverage < 0.8 or N < 20) ⇒ publish allowed but the definition carries `eval_coverage` and the UI shows it; a low-coverage workflow cannot be set as the project default |
| **G5 Online drift** (EXISTS; sources and producer fixed) | weekly beat | last 7 days vs preceding 7 (as the code does; revision 1's "7 vs 30" was wrong), over `AIEvalRun` rows produced by a **new nightly per-task-type producer** (today only the admin router writes them, so a beat would compare two empty windows), plus review reason_code rates and manual incident marks on releases | accept rate, reason_code mix, incident-after-GO rate, each with a CI; drift = non-overlapping CIs, not a fixed 0.02 | drift ⇒ a `ReviewRequest(kind=eval_drift, subject_type=capability)` for a human and, for that capability, tier downgrades are blocked and auto-reviewer-only acceptance is disabled until closed (this is what "pin" means; `human_required` is already the default so pinning it would be a no-op). insufficient ⇒ `measured: false` on the dashboard, no action |

Statistical floor, stated once: a proportion on n = 20 moves in steps of 0.05, so a 0.05 band is one sample and a genuinely equal SLM fails a per-metric check roughly a third of the time. Detecting a 0.05 drop at 80% power with paired samples needs about 250 to 400 items per capability. Until a capability reaches n ≥ 100, G1/G2 run as **smoke gates**: validity must be 1.0 and accuracy's CI must overlap the baseline's; a hard threshold is applied only at n ≥ 100. Every gate result records n and the CI, never a bare rate. Brier and ECE are reported but do not decide below n = 100 (ECE with 10 bins on 20 samples has ~2 items per bin).

### 11.3 Labels: sources, leakage rules

| Source | Exists? | Becomes | Leakage rule |
|---|---|---|---|
| `AIFeedback.rating / corrected_category / corrected_root_cause` | EXISTS | classification + root-cause datasets | Feedback given to outputs generated under configuration X may **not** gate configuration X. Datasets are stamped with the `eval_manifest_checksum` of the run that produced the output; a gate for manifest M excludes rows whose stamp is M. Time-split: labels from the last 14 days are held out from the gate and used only for G5 |
| Human review `accepted` / `rejected(reason_code)` (§8) | NEW | report-quality labels (`reason_code` maps to a failed metric family) | same stamp rule; `notes` never enters a dataset |
| Reviewer verdict vs human outcome | NEW | false-omission labels (G3) | G3 reads only human outcomes, never the reviewer's own confidence |
| Release outcome | NEW: E9.9 adds a manual "mark incident/rollback" action on a release (no incident source exists today; the activity ledger records lifecycle, not incidents) | release-decision calibration (Brier on GO/NO_GO) | — |
| Mutated golden outputs | EXISTS for critic (`get_negative_fixtures`, `get_wrong_agent_fixture`) | generalized mutation generator per output contract, with semantic classes | synthetic; never mixed into accuracy |
| Pilot corpora `tests/evals/` | EXISTS as tests, **not** as samples (0 to 2 harness-shaped rows each) | per-contract sample schemas (E9.2), then data | — |

Coverage: a new `eval_coverage_by_capability()` (the existing `get_label_health` is ML training-pool label health, a different thing) drives the dashboard; below floor ⇒ `measured: false` and G2 refuses downgrades.

### 11.4 Eval as a runtime component

- **Candidate inference in the gate.** Attestation runs each golden input through the candidate prompt/model on the local Ollama (or a recorded-fixture provider in CI when Ollama is absent, which then yields `insufficient_samples`, not `pass`). Outputs are scored by the harness. This is the single change that turns the existing attestation into a measurement.
- **Shadow eval, bounded.** With `mode=shadow`, the ModelRouter runs the candidate tier alongside the incumbent on a **sampled** fraction of live runs (`shadow.sample_rate`, default 0.1) under a daily token budget (`shadow.daily_token_budget`), on the `default` queue behind `critical/ingestion/ai_analysis` so a serialised local Ollama is not starved. Pairs are scored with the deterministic checks and stored as **candidates for labelling**; they are G2 evidence only after a human labels them or they match an existing golden input. Revision 1 overstated this as "evidence without human effort".
- **Eval results in provenance.** Each run's `execution_metadata` gets `eval_manifest_checksum` of the gate that admitted its prompt/model/tier set; `GET /api/v1/ai-eval/gates/{checksum}` resolves it; a run whose checksum does not resolve is flagged on the dashboard.
- **Eval on the eval.** Existing harness tests already cover the wrong-agent corpus and the never-raises path; E9.8 adds per-rule mutation of each scorer in both `agent_eval_harness` and `ai_eval_service` (a scorer that passes a deliberately broken sample is a dead scorer).

### 11.5 One vocabulary, interfaces

Today there are four: harness `passed=False` + `detail.insufficient_data`; gate service `PASS/WARN/FAIL/NO_BASELINE` (an empty dataset returns FAIL "No evaluation dataset found", which its own docstring admits reads as "regressed"); `prompt_registry._offline_gate_results` collapsing to PASS/FAIL + `informational`; revision 1's `measured`. E9.1 introduces one enum imported by all of them:

```python
class EvalVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_SAMPLES = "insufficient_samples"   # replaces NO_BASELINE, insufficient_data, informational, measured:false

class EvalChangeManifest(BaseModel):
    change_id: str
    prompt_versions: dict[str, str]
    model_versions: dict[str, str]           # capability -> "provider/model@tier"
    routing_versions: dict[str, str]
    tier_defaults: dict[str, str]            # NEW  capability -> tier
    reviewer_checks: list[str]               # NEW
    workflow_ref: str | None                 # NEW  "wf.custom.x@3"

class MetricResult(BaseModel):
    name: str; value: float | None; n: int; ci_low: float | None; ci_high: float | None
    kind: Literal["ground_truth", "validity", "calibration", "cost"]

class EvalGateResult(BaseModel):
    verdict: EvalVerdict
    manifest_checksum: str
    per_gate: dict[str, list[MetricResult]]  # G1..G5 keyed
    regressions: list[MetricDelta]           # metric, baseline, candidate, ci, tolerance
    smoke_mode: bool                         # True when n < 100 for the deciding metric
```

`WARN` is retired: a warning that blocks nothing is `pass` with `regressions` listed. Endpoints: existing `POST /api/v1/ai-eval/agent-stack-release-gate` gains the three new manifest fields; NEW `POST /api/v1/ai-eval/tier-comparison`, `POST /api/v1/ai-eval/reviewer-quality`, `POST …/workflows/{id}/evaluate` (G4 dry-run), `GET /api/v1/ai-eval/gates/{checksum}`.

---

## 12. Implementation roadmap and backlog (epics → stories)

**Sizing and team.** S ≤ 2 days, M ≤ 1 week, L ≤ 2 weeks. Epic labels are the *sum of their stories*, not a separate estimate. Story sum is about 165 dev-days; the sequencing chart assumes **four engineers in parallel for ten weeks**, or one engineer for about eight months. Nine epics, 50 stories. Each story ships as: branch + regression test + tracked `CHANGELOG.md` entry (repo convention). Order is dependency order; E7 and E8 are the correctness-critical ones and are sequenced early.

**Migrations.** **(rev 3)** Head is now `0172_suite_seq_index_bounded.py`; the re-audit merges consumed 0166-0172, which revision 2 had reserved. Reservations below are renumbered to 0173+ and remain *planning labels only*: the engineer who merges re-picks a fresh `down_revision` at merge time (memory: pre-assigning numbers invites a two-head conflict).

**Owner decisions (2026-09-13, T1).** D1: migrate both Investigator and Fixer policies to `agent_configs`, preserving the pinned APIs and runtime budgets. D2: enforcement starts on a release date configured by the user; this is a future implementation requirement, not permission to enable the flag immediately. D3: give Investigator narrative excerpts a review subject. These decisions unblock their stories; no policy migration, rollout scheduling, or excerpt review-subject implementation ships in T2.

### E7 — Run state machine, leases, fencing, unified retry (requirements 8, 9) — sum ≈ 22 days
- **E7.1 (M)** `WorkflowRunStateMachine` with the §7.2 table; migration 0173: `status` CHECK, columns `attempt`, `max_attempts`, `next_retry_at`, `lease_owner`, `lease_expires_at`, `fencing_token`, `heartbeat_at`, `cancel_requested`, `review_policy`; backfill `partial → completed` with `stage_quality='degraded'`. **Includes the repo-wide sweep of `"partial"` status comparisons** (backend, frontend, release-gate rules, Grafana) and a guard that fails on the literal afterwards. Guard: no direct status writes outside the state machine.
- **E7.2 (S)** `RetryPolicy` module; replace `_exponential_backoff` callers in `worker/tasks.py`; pipeline task stops using `self.retry`, schedules its own `retry_wait` carrying `(run_id, expected_attempt)`; dedup-lock TTL extension on `retry_wait`. Tests: delays within jitter bounds for attempts 1..5; attempt 6 ⇒ `failed` + DLQ row; a second trigger while `retry_wait` returns the existing run.
- **E7.3 (M)** Lease, intra-stage heartbeat task in `_make_checkpointed_node`, fencing token on every stage/ledger write; audit the stages that skip the `BaseAgent` hooks; rewrite `reap_stuck_agent_pipelines` predicate to lease expiry; delete `RUNNING_STALE_THRESHOLD`, `_apply_effective_status`, and the failed-stage flip. Tests: kill a worker mid-stage ⇒ `retry_wait` within one beat, then `completed` on attempt 2; a paused (not dead) worker resuming after reap has all its writes rejected by the fence and raises `LeaseLost`.
- **E7.4 (M)** `POST …/retry` (409 at max, with `links.rerun`), `POST …/cancel`, `rerun_of` on trigger. Tests: cancel racing a retryable failure always lands `failed`; retry under a narrowed allowlist runs narrowed. **(shipped, deviation)** Revision 3 said retry "re-resolves config". It cannot do that on the same row: `_claim_pipeline_resume` replays checkpoints authorised under the *frozen* plan, so re-resolving underneath them can re-enter a tool the new allowlist forbids. What shipped instead compares a fingerprint of the decision-relevant config (`resolved`, `provider`, `model`, `offline`) and **resumes when it is unchanged, reruns as a new run with `rerun_of` set when it changed** — which is what "runs narrowed" actually requires. The atomic cancel-vs-retry resolution is a shared `FOR UPDATE` on the row plus a sticky `cancel_requested`, not a SQL `CASE`; both orderings end `failed`. **(T2 shipped)** Both pipeline and invocation retry endpoints refuse review-rejected runs with 409 and `reason=review_rejected`, before config comparison or dispatch, including changed-config and exhausted-attempt cases. `links.rerun` offers an explicit fresh run; rejection never automatically starts one. No state-machine or public-status changes. Scope: manual retry endpoints; live verification remains T22.
- **E7.5 (S)** Public status projection `in_progress|completed|failed|passed`, `passed` auto-transition for non-report runs; frontend chips; e2e assertion that no list row ever shows another value. **(shipped)** "Non-report" is decided by capability output contract (`agent_capability_registry.REPORT_OUTPUT_SCHEMAS`), counting only stages that reached `completed`; an unregistered stage requires review. Auto-passed runs set `review_policy = not_applicable` so the section 7.2 invariant stays satisfiable once E8 enforces it. Every current pipeline type runs `summary`, so no pipeline auto-passes yet: the rule is in place for E1.2 invocations. Also fixed: the agentic-runtime projection reported `retry_wait`/`passed` as `failed`, and `/runs?only_pending` matched only `running`. The e2e spec lives in `frontend/tests/e2e`, which runs against a live stack and is not in CI.
- **E7.6 (S)** Tool-call idempotency `(run_id, tool, subject_id)` + ledger lookup at stage entry for `triage` and `defect_commander`; test: crash after ticket creation, retry creates no second ticket. **(shipped, two deviations)** (1) The key's scope is the **test run**, not the pipeline run: a manual retry under changed config starts a new pipeline (E7.4) and offline + deep both triage the same run, so a pipeline-scoped key would allow a second ticket for the same failure. (2) An `executing` row found by a later attempt is `outcome_unknown` and is **not** retried, because the earlier attempt may have completed the call; only `failed` is retried. Rows use `agent_action_ledger` with a `toolcall:` key prefix (`app/services/tool_call_idempotency.py`). **No live path reaches these calls yet:** `requires_approval` is always true for Jira ticket creation and approved-action execution is deferred (E8). The only path that files tickets today, one-click `POST /projects/{id}/defects/jira`, now uses the same guard (2026-09-13). Its key is (project, signature, generation). A per-signature advisory lock serializes double-submits. An `executing` claim is reconciled by a `testlookup-sig-*` label search, then returns 409 until the user confirms with `confirm_not_filed`. An ambiguous POST raises `OutcomeUnknown`, which leaves the claim `executing`. Also fixed: triage passed an unsupported `labels=` to `create_jira_issue`, so its call raised `TypeError` every time.

### E8 — Human review gate (requirement 10) — sum ≈ 16 days
- **E8.1 (M)** Migration 0174: `review_requests` (with `kind`, `capability_id`, `requested_by`, `reason_code`, `notes`, `evidence_bundle_sha256`), `users.is_synthetic` backfilled from the QA-lead domain, project setting `allow_unreviewed_distribution`. Finalize creates requests for report-producing runs; supersede logic. **(shipped as migration 0175)** 0174 was taken by E7.4's `rerun_of`. Added beyond the listed columns: `pipeline_run_id`, `test_run_id` and `workflow_type` (the supersede scope), `superseded_by` (so history links to its replacement), `created_by`. One live request per subject via a partial unique index; superseded rows are kept. A newer run supersedes only *pending* requests for the same test run and workflow type; an accepted review is never overwritten. A report whose evidence hash changes after its review settled gets its review superseded (section 8.3). `requested_by` is left null for now: pipeline runs do not record who triggered them, and separation of duties must not enforce against a guessed requester.
- **E8.2 (S)** `routers/reviews.py`: list (project-scoped), get/accept/reject with `require_review_access`; JWT-only; refuse synthetic; separation of duties for `mode=act`; audit-log; `passed` transition wired; `notes` redaction. **(shipped)** Reject moves the run `completed -> failed` with `review_rejected: <reason_code>`; accept moves it to `passed`; both via `guarded_transition`, so a run that moved on refuses the decision (409). Accept/reject also require QA_LEAD (checked on the user's global role, not the project role). `require_review_access` is router-local and returns 404 to non-members; the authorization ratchet now scans `{review_id}`. **Gaps:** separation of duties is enforced whenever `requested_by` is set, for every report rather than only `mode=act`, but `requested_by` is still null because pipeline runs do not record their trigger user, so it has no effect yet. No per-project toggle for read-only separation of duties yet. **(T2 shipped)** The E7.4 and invocation retry routes now enforce the terminal review rejection; see E7.4.
- **E8.3 (S)** Response headers (`X-TestLookup-*`, CORS expose) + `review` block on all report responses; disclaimer string versioned; reviewer identity kept out of API/export payloads. **(shipped)** Envelope on the agents run summary, `/runs/{run_id}/summary`, `/runs/{run_id}/intelligence` and `/runs/{run_id}/decision-reports` (per version, since that response is a bare list). AI content with no review request is `pending_review` with no `review_id` (fail closed); a deterministic fallback is `not_applicable` with `X-TestLookup-AI-Generated: false`. The envelope is applied per response and never written into the intelligence snapshot cache. **Not yet covered:** the AI comparison report on `/runs/compare` (no review subject exists for it); MCP tool results (`review_state` parity, E8.6); export and PDF paths (E8.4).
- **E8.4 (M)** Distribution gates: PDF, digests, PR comment, webhooks, MCP results, CLI read the project setting; watermark; audit row per inclusion; release gate returns `PENDING_REVIEW` / `ADVISORY_*` values. Test: pending report ⇒ 409 on export; setting on ⇒ watermark text in PDF and audit row. **(slice 1 shipped)** Policy in `report_distribution_policy.py`; gated so far: report PDF export (`include_unreviewed` for QA_LEAD+), public share links, and the release-readiness value (`PENDING_REVIEW` / `ADVISORY_*`, model value kept in `draft_recommendation`; synthesized quick-looks, human overrides and accepted reviews unchanged). Rejected and superseded reports are never distributed as drafts. **Rollout:** enforcement sits behind `REVIEW_GATE_ENFORCED` (default off), because runs finished before E8.1 have no accepted reviews and enforcing on merge would stop files and flip CI gates for every project at once; while off, would-be refusals are audited as `ai_report.distribution_would_refuse`. **Remaining slices:** notifications/digests, GitHub/GitLab comments + webhooks, MCP + CLI. **(slice 2 shipped)** AI summary notifications and event-driven digests are gated once before both fan-outs (withheld text replaced by an awaiting-review notice and the executive panel dropped when refused; DRAFT line under project opt-in; deterministic fallbacks never gated). The window analysis report's quoted release verdict gets the release-readiness projection. **Gap (D3 answered 2026-09-13):** Investigator narrative excerpts are not gated because investigations have no review subject. The owner chose to give excerpts a review subject; implementation remains pending. **(slice 3 shipped)** GitHub PR comment and GitLab MR note: AI failure-kind labels are gated (stripped when refused, a DRAFT note under project opt-in); the comment always posts, and the deterministic attribution summary is not gated. Audit rows from these read-only worker paths commit via `record_distribution_detached`. **Gap:** these comments post before the AI pipeline and are not re-posted on review acceptance. **Correction against main (PR #67):** `release_decision_webhook.py` now emits `release.decided` and applies the release review gate; the earlier no-emission finding is resolved. **(slice 4 shipped)** MCP: report tools end with `review_state` + disclaimer (`unknown` when absent); read-only `list_pending_reviews`; no accept/reject tool, with a parity test (pulled forward from E8.6). CLI: `reviews list/accept/reject` (accept/reject refused up front on API-key profiles); `intelligence show` prints review state + disclaimer to stderr. `reports pdf` unchanged (PDF carries the watermark; its route sends no review headers).
- **E8.5 (S)** `/reviews` page + banners (use `add-page` skill). e2e: accept moves chip to `passed`. **(shipped)** `/reviews` Review Queue (single-project; QA-lead accept/reject with required reason code; read-only otherwise); `ReviewBanner` on the run intelligence page and the `/agents` summary panel (no banner for `not_applicable` or a missing block); `/agents` COMPLETED cards carry an `awaiting review` tag. **Gap:** `passed · reviewed <time>` on the chip needs review data on the pipeline list response, which it lacks; the time shows on the banner and the queue.
- **E8.6 (S)** Guards: producer-side `reviews.report-producers-create-review-request`, consumer-side `reviews.report-consumers-carry-review-block`, MCP parity test that no tool reaches accept/reject; mutating-call invariant test (`policy_denied` without an accepted review). **(shipped)** Both guards are absolute rules (no baseline). The consumer guard found and fixed two routes without the envelope: `GET /runs/{id}/export` and `POST /runs/{id}/intelligence/refresh`. The invariant lives in `execute_agent_action`: an action whose proposing pipeline run has no `accepted` review fails `policy_denied` before any executor runs. **(T4 shipped)** Pipeline-originated actions also persist the proposing agent id in the request payload and resolve that agent's current project config immediately before execution; any mode other than `act`, or a legacy pipeline proposal with no proposer identity, fails `policy_denied`. Direct human actions with no proposing pipeline retain their approval path. Deviation: the proposer identity is an additive field in the existing hashed payload rather than a new ledger column. Gap: the generic external executor remains deliberately unregistered. MCP parity shipped in E8.4 slice 4.

### E1 — OpenAPI agent exposure (requirement 1) — sum ≈ 15 days
- **E1.1 (S)** `GET /api/v1/agents/catalog[/{agent_id}]` from the registry; `sync_eligible` flag; per-agent input wrapper generation; guard `agents.catalog-schema-complete`; router-order test. **(shipped)** `services/agent_catalog.py` plus the two routes (authenticated; declared first in the agents router). `SYNC_ELIGIBLE` is a module-level registry set, not a `CapabilitySpecV1` field, so frozen plan snapshots are unchanged. Per-agent `<StageName>InvokeInput` wrappers use `payload: <model> | SubjectRef`. **Found:** 20 of 31 registry schema names have no model; those entries report `*_schema_resolved: false`. The guard is a ratchet baselining the 13 label-only inputs. **(T6 shipped)** The catalog now publishes additive `default_tier` and sorted `escalation` trigger fields from module-level maps; `description` remains unpublished.
- **E1.2 (M)** `AgentInvocation` model with `project_id` (migration 0175), `POST /agents/{agent_id}/invoke` sync/async with semaphore, `GET /invocations/{id}`, SSE with stream ticket, retry/cancel; runs through a single-step compiled graph so leases/fencing/retry/review apply identically. **(slice 1 shipped)** Migration **0176** (0175 was taken by review_requests), `agent_invocations` with no status column. An invocation is a pipeline run with a pre-assigned id whose frozen plan keeps only the agent plus its declared dependencies (`build_workflow_plan(invocation_stage=...)`), so leases, retries, cancel and Finalize/review apply unchanged; status is read from the run. `POST /agents/{agent_id}/invoke` (async, stored subject only) and `GET /agents/invocations/{id}` (`require_invocation_access`, 404 for non-members). **Remaining:** `mode=sync` plus semaphore, model payloads, SSE with stream ticket, invocation retry/cancel routes, output projection. **(slice 2 shipped)** Invocation retry (resume same run; 409 in progress / clean finish / ceiling / config changed, last three with links.rerun; lost dispatch re-sent) and cancel (request_cancel; 409 before the run exists or after it finished). The poll view carries the invoked stage output once completed. Migration 0177 adds agent_invocations.dispatched_at. **Deviation:** a config-changed retry answers 409 + links.rerun instead of starting a rerun, because an invocation owns exactly one run. **Remaining:** mode=sync + semaphore, SSE with stream ticket. **(slice 3 shipped, E1.2 complete)** mode=sync only waits (the worker still runs it) on sync-eligible agents: 200 when finished within AGENT_INVOKE_SYNC_WAIT_SECONDS, 202 otherwise, 503 + Retry-After when AGENT_INVOKE_SYNC_CONCURRENCY waiters are in flight; sync on a non-eligible agent runs async. SSE: POST events/ticket (60 s, single-use GETDEL, bound to the invocation, stored as SHA-256) then GET events?ticket= (401 otherwise; fails closed; one event per change; ends at a terminal status, on disconnect, or after 30 min). **Follow-up:** payloads other than a stored subject.
- **E1.3 (S)** Idempotency dependency (scoped key, body hash, 422/409 semantics, 24 h TTL, unique index). **(shipped)** Idempotency-Key on POST /agents/{agent_id}/invoke: the same key and request returns the stored invocation with 200 (failed ones too); a different request is 422; in flight is 409 + Retry-After. Scoped by user, project and route; the fingerprint covers the whole body. Redis lock for the in-flight window; a unique partial index (requested_by, idempotency_key) built CONCURRENTLY in migration 0178 is the authority when Redis is down. **Deviation:** the lock has the 24 h TTL, but the stored row keeps answering for the key afterwards, because the index does not expire.
- **E1.4 (S)** Postman collection + curl docs generated from `/openapi.json` in CI; tracked under `architecture/` (verify with `git ls-files`). **(shipped)** architecture/api/agents.postman_collection.json (v2.1) and architecture/api/AGENT_API_CURL.md, generated by python -m app.services.agent_api_docs from app.openapi(). The CI step (backend job) runs it with --check; a body route without an EXAMPLE_BODIES entry fails generation. Section 3.4 curl examples predate the catalog wrapper; the generated reference is authoritative.
- **E1.5 (S)** MCP server exposes the catalog as tools; parity test that every catalog id has an MCP tool and no review-mutation tool exists. **(shipped)** mcp/tools/agents.py: list_agents, get_agent, invoke_agent (always sends an Idempotency-Key; side effect, confirm with the user) and get_agent_invocation (ends with review_state). Parity is kept by a literal INVOKABLE_AGENT_IDS checked by backend/tests/test_mcp_agent_catalog_parity.py against the invocable registry set, plus the agent-id pattern and no review-mutation tool. Catalog agents that cannot be invoked on their own are reachable read-only through list_agents/get_agent rather than one MCP tool each.
- **E1.6 (S)** Authorization: `require_invocation_access` / `require_review_access` / `require_workflow_access` dependencies; subject-derived project with body assertion; API-key project binding; **ratchet extension** to inspect Pydantic body models and require an access dependency on UUID-only routes in the new routers, with self-test. **(shipped)** Ratchet extension: every {*_id} path param must be classified (guarded, or GLOBAL_PATH_PARAMS with a reason; agent_id is global); the 34 unclassified ids in use form a shrink-only backlog capped at 34. STRICT_ROUTER_MODULES (agent_invoke, reviews) take no backlog and no exemption. Self-tests on fixture routes. Already in place: require_invocation_access, require_review_access, subject-derived project with body assertion, and API-key binding via resolve_project_scope; body models were already scanned. require_workflow_access waits for E4.

### E4 — Per-agent configuration (requirement 4) — sum ≈ 13 days
- **E4.1 (M)** `agent_configs` table (migration 0176) + Pydantic schema from §4.2 (`extra="forbid"`, no endpoints) with the monotonicity table as validators and composition checks; `GET/PUT …/agent-configs/{agent_id}` (≥ QA_LEAD); `config_version` frozen into `execution_metadata`. **(shipped)** Migration 0179 (0176 was taken by agent_invocations). Budget fields use the existing DEFAULT_BUDGETS names (max_llm_calls_per_run, max_tokens_per_run, max_cost_usd_per_run, max_runs_per_day), not the section 4.2 example names. `mode` and `enabled` are columns; `config` holds the rest. New ceiling AGENT_MAX_TIMEOUT_CEILING=600. Tool permissions live in AGENT_TOOL_PERMISSIONS (all 19 @tool functions are read_only; a test holds the map equal to app/tools). Tier tightness: deterministic < slm < llm < auto (auto may escalate to llm, so it is loosest). Defaults: the lowest mode that runs the capability, but a mutating capability starts disabled in shadow; defaults are clamped to the env ceilings; review.auto_reviewer defaults to false. A stored row that stops validating is returned as stored with valid=false. Added a list route GET .../agent-configs. AgentConfigPatch + apply_patch implement the monotonicity table but no request accepts overrides until E4.2. Runs freeze `agent_config_versions` ({agent_id: version}, configured agents only).
- **E4.2 (S)** Resolver merges env → ai_config → AgentConfig → `AgentConfigPatch`; **resolve-time** offline clamp via `apply_offline_ceiling` + `enforce_provider_policy`; unit tests for every precedence pair and for a stored cloud provider after an env flip. **(shipped)** `agent_config_resolver.resolve` (pure) and `resolve_for_project` (row + live ai_config + async residency check for endpoints with a base_url). A refused tier falls back to the global ai_config model when that is permitted, else it has no endpoint; every change is recorded in `clamps` with its layer. Stored rows are clamped to lowered attempt/timeout ceilings and the deadline before validation; a row no clamp can repair raises AgentConfigInvalid. A project block inherits the global base_url only for the same provider. Global ai_config does not floor thresholds (the section 4.1 table makes thresholds project-only). The invoke route refuses a disabled agent with 403 and an invalid stored config with 409; defect_commander is therefore not invocable until a project enables it in mode act. Deviation: the invoke body does not accept config_overrides yet, because nothing at run time consumes a resolved config before E5 tier routing.
- **E4.3 (S)** Settings UI tab per agent on `SettingsPage`. **(shipped)** Built as an Agent configuration panel on Settings → AI Agents (`/settings/ai-agents`, already a QA_LEAD+ management route) rather than a new SettingsPage tab: one tab per configurable agent. Edits mode, tier, attempts/timeout (worst case shown live), budget, tool allowlist, review and override policy; saves the whole document with PUT; server refusals (422 Pydantic errors or provider courtesy strings) are listed inline; a stored row with valid=false shows its errors. The list route now also returns `tools` ({tool: permission}). Model endpoint blocks (slm/llm provider+model), escalation, thresholds and shadow sampling are not editable in the UI yet; they keep their stored or default values and remain settable through the API. The agent-policy cards stay until E4.4.
- **E4.4 (S)** Migrate `AgentPolicy` rows into `agent_configs` (keep `shadow_runs_completed`); `agent-policies` becomes a read-only alias for one release; guard that only `agent_config_service` writes `mode`. **(part 1 shipped; migration pending, D1 answered 2026-09-13)** The guard shipped as the `agents.agent-mode-single-writer` ratchet, with the two agent_policies writers (agent_investigation_service.upsert_policy, fixer_service.upsert_fixer_config) baselined. The row migration was not done because this item's premise does not hold: agent_policies holds `investigator` and `fixer`, neither of which is a capability-registry agent that AgentConfigV1 accepts. The Investigator policy's budgets (including max_seconds_per_run and the cluster child keys, absent from AgentConfigV1.budget) are also read as every deep pipeline's run budget (workflow._create_pipeline_run) and by cluster_investigation_orchestrator. The fixer row stores runner/test_globs/schedule in its budgets JSONB behind the pinned FixerConfig API. The options (migrate both with an extensions document and configurable non-registry ids; migrate the investigator only; or defer) change pinned APIs and runtime budgets, so they required an owner decision. D1 now selects migrating both while preserving those contracts; implementation remains T5.

### E5 — Model tiering and SLM summarization (requirements 5, 6) — sum ≈ 12 days; **no tier default is promoted before E9.3**
- **E5.1 (S)** `ModelRouter` with §5.3 including the budget re-check on escalation; `default_tier`, `escalation`, `sync_eligible` added to the registry; `flaky_sentinel/test_health/release_risk` set to `cost_usd=0, default_tier=deterministic`. **(T6 shipped, with corrections)** `model_router.py` is a pure selector returning the resolved endpoint; it checks the declared capability cost before both initial selection and SLM-to-LLM escalation, refuses a non-deterministic choice for a zero-cost capability as `capability_unbudgeted`, honors explicit SLM pins, shares the step-call ceiling, and emits the pinned provenance fields. `pipeline_budget_service.remaining_cost_usd` includes spent plus reserved cost; the atomic `BudgetedLLM` reservation remains the hard authority. `DEFAULT_TIERS`, `ESCALATION_TRIGGERS`, and `CLASSIFY_CAPABILITIES` are module-level next to `SYNC_ELIGIBLE`, preserving `CapabilitySpecV1` and frozen plan snapshots. Only `flaky_sentinel` and `test_health` moved to zero cost: `release_risk` stays positive because `_get_llm_reasoning` calls an LLM. Defaults reflect code on main: deterministic-only agents remain deterministic and current LLM agents remain LLM, so the planned summary/root-cause/triage SLM downgrades are gaps pending E9.3 non-inferiority evidence. The LLM escalation soft estimate uses `expected_cost_usd`; provider pricing and the invocation reservation prevent overspend, while a distinct owner-configurable LLM/SLM factor remains an open design choice rather than a new setting in this story.
- **E5.2 (M)** Summary agent on `slm` tier: JSON-validate → one SLM repair → LLM escalation → deterministic fallback; provenance `tier_requested/tier_used/escalations`. **(T7 shipped)** `SummaryAgent` resolves the project's `agent.summary.v1` config and uses `ModelRouter`; the registered `auto` default is now `slm` after E9.3 shipped. Schema and cross-layer consistency failures trigger one complete SLM repair pass. A second failure escalates only when the project's automatic escalation policy, endpoint availability, step-call ceiling, and remaining cost allow it; failed/refused escalation uses the existing deterministic four-layer report. `get_llm(endpoint=...)` consumes the resolved provider, model, temperature, token cap, and base URL together while credentials remain global. Provenance records `tier_requested`, `tier_used`, `escalations`, and `fallback_used`; paired SLM/LLM outputs call E9.3's stable, token-bounded shadow hook. **Deviation:** repair regenerates the complete four-layer report so consistency failures spanning layers are corrected together rather than repairing isolated JSON fragments. **Gap:** live pairing occurs only on runs that actually escalate and pass the configured shadow sample; a non-escalated second-model shadow call is intentionally not introduced because it would add inference outside this story's model-call budget.
- **E5.3 (S)** Root-cause split: SLM for category/confidence, LLM for explanation under the §5.2 rule.
- **E5.4 (S)** Circuit breaker per provider+base_url in `llm_factory`; open breaker ⇒ immediate deterministic path.
- **E5.5 (S)** `make dev-llm` pulls one SLM and one LLM tag; docs list tested tag pairs.

### E6 — Generic reviewer (requirement 7) — sum ≈ 14 days
- **E6.1 (M)** `ReviewerAgent` with check families 1, 2, 5, the verdict invariants, and `ReviewVerdictV1`; registered as `agent.reviewer.v1`.
- **E6.2 (S)** Self-consistency claim extraction (family 3) on SLM.
- **E6.3 (M)** Second-model agreement (family 4) with heterogeneous model preference; config `review.second_model_check`.
- **E6.4 (S)** Supervisor handling of `retry/reject/pass_with_flags`; shared `step_llm_budget`; mutation-tested checks (orphan ids, contradicted numbers, unsupported claims).

### E3 — User-customizable workflows (requirement 3) — sum ≈ 26 days
- **E3.1 (M)** `workflow_definitions` table (migration 0177) with versioning + publish immutability; CRUD, validate, fork, publish; built-ins read-only (405).
- **E3.2 (L)** `WorkflowCompiler`: registry validation, dependency closure, cycle rules, capability-permission ≤ mode, step tools ⊆ config allowlist, typed JSON condition AST evaluator with limits and enum checks (fuzz tests), emits `StateGraph`; three built-ins expressed as definitions and diff-tested against today's compiled graphs.
- **E3.3 (S)** Runs record `workflow_id@version` + plan hash; checkpoint restore refuses across versions.
- **E3.4 (M)** Workflow editor UI (list/fork/edit with schema validation/preview graph/publish, shows `eval_coverage`) on `AgentWorkflowPage`.
- **E3.5 (S)** Guard `workflows.builtins-match-compiled` + docs.

### E9 — Evals as the control loop (requirement 13) — sum ≈ 34 days
- **E9.1 (M)** One `EvalVerdict` enum adopted by harness, gate service, `prompt_registry` and `prompt_eval_recordings`; **(rev 3)** generalise the existing recordings: key each recording by `(prompt content_hash, provider/model@tier)`, add entries for prose prompts scored by the harness contract checks plus a rubric, and treat an unrecordable prompt (no Ollama in CI) as `insufficient_samples`, never `pass`; extend the existing attestation guard's watched set to `llm_factory.py`, `model_router.py`, registry `default_tier`/`escalation`, reviewer check lists; verify what `run_scheduled_agent_eval` writes and make it the nightly `AIEvalRun` producer per task type. Guard self-test pins the watched paths. **(T12 shipped)** The shared enum supplies the three lowercase values across the harness, release gate, prompt attestation and recording gate; `WARN` and `NO_BASELINE` are retired. Recordings persist under content hash then `provider/model@tier`, and anomaly plus Investigator narratives add grounding/length/action rubrics. The attestation hashes `llm_factory.py`, `model_router.py`, the capability registry, and the future reviewer path; its self-test pins that set. The nightly job writes one existing `AIEvalRun` per evaluated task type alongside its aggregate gate run, so no migration was needed. Gap: CI has no Ollama and the five current prompt cases remain grandfathered `insufficient_samples`; `--check` permits only those unchanged hashes so ordinary branches remain green, while offline re-attestation and any changed unrecorded prompt fail closed. Corpus floors and tier comparison remain E9.2/E9.3.
- **E9.2 (L)** Per-output-contract sample schemas (the harness's `AgentEvalSample` fits only triage-shaped agents); convert the four pilot corpora; golden **inputs + labels** for every registered capability to n ≥ 20 (smoke floor), AnalysisAgent to n ≥ 100; mutation generator with semantic classes; `eval_coverage_by_capability()`; `eval_exempt` registry flag with reason. **Recurring**: re-run for each new capability (reviewer in E6, tiers in E5, workflows in E3). **(T13 shipped)** A frozen capability-sample contract pins the declared input/output schema and chooses a classification, structured, or narrative label schema per output contract. All 28 executable capabilities have at least 20 unique inputs and labels, `root_cause_analysis` has 100, and the four pilot suites now consume the registered corpora. The only exemption is the `workflow` runtime bookkeeping pseudo-capability, with its reason held in the evaluation registry's module-level `EVAL_EXEMPTIONS` map so `CapabilitySpecV1` and frozen plans remain unchanged. `eval_coverage_by_capability()` reports every registry entry, its count/floor, and the exemption. Semantic mutations are class-labelled and kept out of accuracy samples. Deviation: eval-only metadata lives in `agent_eval_samples.py`; putting it in the routing registry would trigger a misleading model-routing attestation despite no route change. Gap: these version-controlled smoke labels establish contract coverage; inference-backed tier non-inferiority and shadow pairs remain E9.3.
- **E9.3 (M)** G2 tier comparison: paired harness run, CI-based non-inferiority, `POST /api/v1/ai-eval/tier-comparison`, `PUT agent-configs` hook (refuse regressing downgrade; allow upgrade on insufficient); shadow sampling with `shadow.sample_rate` and token budget on the `default` queue; shadow pairs stored as labelling candidates. Migration 0178: `ai_eval_shadow_pairs`, `ai_eval_gate_runs` manifest columns. **(T14 shipped)** Paired incumbent/candidate outputs are scored against the same frozen capability labels. The response records Wilson accuracy intervals, a paired 95% difference interval, perfect-validity smoke criteria below n=100, hard non-inferiority at n≥100, and cost/latency deltas. The project-scoped POST persists a typed G2 gate row; agent-config PUT refuses a failed comparison and refuses an insufficient downgrade while permitting an insufficient upgrade. Stable shadow sampling enqueues already-produced pairs on `default`; a transaction advisory lock makes the per-project/agent/day token cap hard, and stored pairs remain `pending` labelling candidates. **Deviation:** migration 0180 was used because origin/main already assigns 0178 to invocation idempotency and 0179 to agent configs; subsequent planned migrations move to 0181+. **Gap:** the ModelRouter still has no runtime callers on main, so E5.2/E5.3 must run both outputs and call the shipped enqueue hook before live shadow pairs appear; no tier default was promoted.
- **E9.4 (M)** G3 reviewer quality (after E6.1 merges, not parallel): per-class × per-family recall, clean-corpus false-flag rate, false-omission rate from human outcomes; auto-disable rule with the n ≥ 30 and Δrecall ≥ 0.10 guards. Migration 0181+: `ai_eval_reviewer_quality`.
- **E9.5 (M)** G4 workflow evaluation: replay cache keyed by `(agent_id, prompt_version, input_hash)`, `unmeasured` on miss, coverage metric, `POST …/workflows/{id}/evaluate`, publish refusal unless `accept_regression` + reason. Migration 0182+: `workflow_replay_corpus`.
- **E9.6 (S)** G5: CI-based drift over the nightly `AIEvalRun` rows + review reason_code rates + incident marks; weekly beat; drift ⇒ `ReviewRequest(kind=eval_drift)` and the capability-level pin (block downgrade, disable auto-only acceptance).
- **E9.7 (S)** Provenance: `eval_manifest_checksum` frozen into `execution_metadata` (after E7.1); `GET /api/v1/ai-eval/gates/{checksum}`; dashboard flag for unresolvable checksums.
- **E9.8 (S)** Eval on the eval, scoped to the delta over the 22 existing harness tests: per-rule mutation of each scorer in `agent_eval_harness` and `ai_eval_service`.
- **E9.9 (S)** Release outcome source: manual "mark incident / rollback" action on a release (API + UI), recorded in the activity ledger, joined by G5.
- **E9.10 (S)** Label leakage rules: manifest stamp on feedback/review-derived rows, gate exclusion of same-manifest rows, 14-day holdout; test that a dataset built from feedback on manifest M cannot gate M.

### E2 — Hardening and observability — sum ≈ 8 days
- **E2.1 (S)** OTel spans per step with `gen_ai.*` attributes; Grafana panels.
- **E2.2 (S)** New counters from §10 with emission tests.
- **E2.3 (S)** DLQ list/replay endpoints; alert rules (`in_progress` age beyond deadline + grace, DLQ depth, `pending_review` age); each alert has a **positive** test that injects the condition and asserts it fires and clears.
- **E2.4 (S)** Keep this document (`architecture/AGENTIC_OPENAPI_ARCHITECTURE.md`, tracked) current as epics land: mark each EXISTS/NEW row as it flips, and add CHANGELOG entries. Covers requirements 11 and 12, which are document-only. **(T23 shipped early)** The pre-existing Codacy workflow failed validation before creating a job because a step `if` read `secrets.CODACY_PROJECT_TOKEN` directly. Token availability now flows through job-level `env`; a regression test pins the valid shape. This operational prerequisite was brought forward because the programme requires every story PR to merge only with green CI. No agent runtime behavior changed.

### Sequencing (four engineers)

```
Wk 1      E9.1 (verdict enum, candidate inference, watched set)  ‖ E7.1-E7.2 ‖ E9.8
Wk 2      E9.2 part 1 (sample schemas, AnalysisAgent n≥100)      ‖ E7.3-E7.6 ‖ E8.1
Wk 3      E8.2-E8.6                                              ‖ E1.1-E1.3 ‖ E4.1-E4.2
Wk 4      E1.4-E1.6                                              ‖ E4.3-E4.4 ‖ E9.2 part 2 (all capabilities n≥20)
Wk 5      E9.3 (tier comparison)  → then E5.1-E5.3               ‖ E9.7, E9.9, E9.10
Wk 6      E5.4-E5.5 ‖ E6.1-E6.2                                  ‖ E2.1-E2.3
Wk 7      E6.3-E6.4  → then E9.4 (needs E6.1 merged)             ‖ E3.1
Wk 8-9    E3.2-E3.3                                              ‖ E9.5 (needs E3.1)
Wk 10     E3.4-E3.5 ‖ E9.6 ‖ E2.4 ‖ E9.2 part 3 (reviewer, workflows)
```

Dependencies called out: E1.2 needs E7 (leases); E9.3 needs E4.1 (config hook) and E5.1 (`default_tier` exists); E9.4 needs E6.1; E9.5 needs E3.1; E9.6 needs E8 (reason codes) and E9.9 (incident marks); E9.7 needs E7.1 (`execution_metadata` columns).

### Definition of done for the programme (each bullet is a test or a query)

- `GET /openapi.json` lists every registry capability under `/api/v1/agents/{agent_id}/invoke` with a per-agent input schema; a Postman run of the generated collection returns `202` then `passed` for every sync-eligible agent and `202` then `completed` for every report-producing agent on the seed project.
- `SELECT DISTINCT status FROM agent_pipeline_runs` returns a subset of the six internal states; the public API never returns anything but the four; the "stuck beyond deadline" alert fires when a lease expiry is injected and clears when the reaper acts (positive test, not "zero alerts on seed traffic").
- Killing a worker mid-run yields `retry_wait → running → completed` with `attempt=2`, and a paused worker's late writes affect zero rows, both verified live (memory: verify in the running app).
- A `pending_review` decision report cannot be exported, notified, or read as `GO`; accepting it flips the run to `passed` with the reviewer recorded in the audit log and absent from the API payload.
- **Evals:** the attestation guard blocks a PR that edits a prompt, `llm_factory.py`, or a `default_tier` without a fresh inference-backed attestation; AnalysisAgent has n ≥ 100 and every other capability n ≥ 20 or an `eval_exempt` reason; the SLM summary is non-inferior to the LLM on the summary golden set (built in E9.2) by the G2 rule; reviewer recall per semantic mutation class is reported with n; every run in the last week resolves its `eval_manifest_checksum`; publishing a workflow that regresses the replay corpus is refused without a recorded reason.
