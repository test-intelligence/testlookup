# Agentic Architecture — Implementation Handover (Claude Code Opus 5 → GPT‑5.6‑Sol)

**Date:** 2026-09-13 · **Repository:** `test-intelligence/testlookup` · **Base:** `main` @ `0d7c80c1` (PR #81 merged; last programme PR #82 is `19b9bdef`)
**Design source of truth:** [`architecture/AGENTIC_OPENAPI_ARCHITECTURE.md`](AGENTIC_OPENAPI_ARCHITECTURE.md) (rev 3 + per-story "shipped" notes in §12). This handover does not replace it; it tells you what is true in the code today, why, and what to do next.
**Visual status report (same facts, for humans):** https://claude.ai/code/artifact/0dd966a6-d71a-47a4-9304-354c292eba7b (private to the owner's claude.ai account; not needed to resume)

> **How to read this file.** Every claim below was verified against the code on `main` @ `19b9bdef` during this session. PR #81, which merged afterwards, is summarised from its merged diff and PR description. Where a statement is a recommendation or an unverified assumption, it is labelled **[Recommendation]** or **[Unverified]**. File paths are repo-relative. Line numbers drift; search by symbol.

---

## Contents

> **Continuation note, 2026-09-16:** this document retains historical inventory
> and commands alongside later shipped updates. The original 42 guards / 373
> mypy baseline and “config_overrides not yet accepted” statements are
> historical: consult the current guard registry, per-file mypy baseline and
> invoke implementation. D1 and D3 shipped; D2 is still a user-configured release
> date, not an instruction to enable enforcement now. The new
> [exploratory execution package](testing/EXPLORATORY_EXECUTION_PACKAGE.md)
> supersedes the per-story PR cadence for testing work with one branch/PR/merge.

1. [Executive Summary](#1-executive-summary)
2. [Epic‑by‑Epic Handover Package](#2-epicbyepic-handover-package)
3. [Architecture Documentation](#3-architecture-documentation)
4. [Code‑Level Handover](#4-codelevel-handover)
5. [Reasoning & Rationale](#5-reasoning--rationale)
6. [Pending Work & Next Steps](#6-pending-work--next-steps)
7. [Risks & Mitigations](#7-risks--mitigations)
8. [GPT‑5.6‑Sol Continuation Plan](#8-gpt56sol-continuation-plan)
9. [Final Handover Checklist](#9-final-handover-checklist)
- [Appendix A — Working conventions that CI and reviewers enforce](#appendix-a--working-conventions-that-ci-and-reviewers-enforce)
- [Appendix B — Command reference](#appendix-b--command-reference)
- [Appendix C — Resume prompt for GPT‑5.6‑Sol](#appendix-c--resume-prompt-for-gpt56sol)

---

## 1. Executive Summary

### 1.1 Programme

TestLookup (local-first test-failure intelligence) is being extended from "pipelines of agents triggered internally" into an **agentic platform**: every agent invokable over OpenAPI, runs with a provable state machine, human review enforced for AI reports, per-agent configuration, model tiering (SLM/LLM), a generic reviewer agent, user-defined workflows, evals as the control loop, and hardened observability. The backlog is **9 epics / 50 stories / ~165 estimated dev-days** (sizes: S ≤ 2 d, M ≤ 5 d, L ≤ 10 d).

### 1.2 Status at handover

| Epic | Requirement(s) | Stories | State | PRs |
|---|---|---|---|---|
| **E7** Run state machine, leases, fencing, retry | 8, 9 | 6/6 | **Complete** | #52 #53 #54 #56 #57 #58 |
| **E8** Human review gate | 10 | 6/6 | **Complete** (enforcement flag **off**) | #59 #60 #62 #63 #64 #65 #66 #68 #69 |
| **E1** OpenAPI agent exposure | 1 | 6/6 | **Complete** | #70 #71 #72 #73 #74 #75 #76 #77 |
| **E4** Per-agent configuration | 4 | 4/4 | **Complete** — E4.4 migration shipped in T5 | #78 #79 #80 #82 T5 |
| **E5** Model tiering, SLM summarization | 5, 6 | 5/5 | **Complete** | #87 #91 #92 #93 #94 |
| **E6** Generic reviewer | 7 | 4/4 | **Complete** | #96 #97 |
| **E3** User-customizable workflows | 3 | 5/5 | **Complete** | #99, T17/E3.5 |
| **E9** Evals as the control loop | 13 | 10/10 | **Complete** | #88 #89 #90 #98 #100 #101 #102 #103 #104 #105 |
| **E2** Hardening, observability | 2, 11, 12 | 4/4 | **Complete** | #85 #106 #107 #108 |

**Totals at the original handover:** 21 stories shipped + 1 partial (E4.4). The continuation completed E4.4, E5, E6, E9, E2, and E3: all 50/50 stories are shipped after T17/E3.5.

### 1.3 The three owner decisions that gate work

| # | Decision | Blocks | Options |
|---|---|---|---|
| D1 | Where `investigator` and `fixer` policies live (E4.4) | Requirement 4 completion; shrinking `agents.agent-mode-single-writer` baseline | **Answered:** migrate both to `agent_configs` while preserving pinned APIs and runtime budgets |
| D2 | When to set `REVIEW_GATE_ENFORCED=true` (E8.4) | Requirement 10 enforcement | **Answered:** on a release date configured by the user |
| D3 | Investigator narrative excerpts in notifications (E8.4) | Enforcement completeness once D2 is on | **Answered and shipped 2026-09-16:** exact Investigator pipeline review subject |

**T1 completed on 2026-09-13.** The decisions above unblock their stories; D2 is a future scheduling requirement and does not enable the flag immediately.

### 1.4 Repository state at handover

- `main` @ `0d7c80c1`. PR #81 (one-click Jira exactly-once, from an owner session) merged at 2026-09-13 19:20 UTC, after #82, and added no migration. Alembic head **`0179_agent_configs`**; quality gate **42 guards, 18 ratchets**; mypy ratchet baseline **373**.
- The empty local branch `feat/e5-1-model-router` (created seconds before the handover request, never committed to) was deleted. E5.1 starts fresh from `origin/main`.
- This handover is committed on branch `docs/agentic-handover-2026-09-13` (merged to `main` through its PR).
- Owner-started sessions: *"Stop one-click Jira filing duplicate issues on retry"* **landed as PR #81** (see §4.2). *"Emit the advertised release.decided webhook"* has **no open PR** at handover (PR #67 had already fixed emission). Run `gh pr list` before branching.

---

## 2. Epic‑by‑Epic Handover Package

Format per epic: **Title · Description · Business value · Technical scope · Dependencies · Acceptance criteria · Status (implemented / partial / not implemented / abandoned / re-evaluate)**.

### 2.1 E7 — Run state machine, leases, fencing, unified retry  ✅ Complete

- **Description.** Replace the ad-hoc `pending/running/completed/failed/partial` vocabulary and 30-minute stale heuristic with an explicit state machine, lease-based liveness, fencing tokens, a unified retry policy, manual retry/cancel, a four-value public status, and at-most-once tool calls.
- **Business value.** No run is ever stuck or mislabelled; failures retry automatically with backoff; users can retry or cancel; external side effects (Jira tickets) are never duplicated by retries.
- **Technical scope.** `agent_pipeline_runs` status CHECK + lease/attempt columns (migration 0173), `rerun_of` (0174); `workflow_run_state.py`, `retry_policy.py`, `pipeline_lease.py`, `pipeline_cancellation.py`, `pipeline_retry_config.py`, `tool_call_idempotency.py`; reaper rewritten to lease expiry; frontend chips.
- **Dependencies.** None upstream. Downstream: E1.2 (invocations run as pipeline runs), E8 (passed/failed transitions), E9.7 (manifest in `execution_metadata`).
- **Acceptance criteria (from §12).** Delays within jitter bounds for attempts 1..5; attempt 6 ⇒ `failed` + DLQ; kill worker mid-stage ⇒ `retry_wait` then `completed` attempt 2; paused worker's late writes affect zero rows; cancel racing a retryable failure always lands `failed`; crash after ticket creation ⇒ retry creates no second ticket.
- **Implemented.** All six stories (see §4.2 inventory).
- **Partial / gaps.**
  - E7.4: retry endpoints now refuse review-rejected pipeline and invocation runs → **K1 fixed by T2**.
  - E7.5: every pipeline type runs `summary`, so no pipeline auto-passes yet (rule exists for invocations). The e2e spec `frontend/tests/e2e/agents-public-status.spec.ts` needs a live stack and is **not in CI**.
  - E7.6: agent paths still do not reach the idempotent tool calls (Jira ticket creation always requires approval; approved-action execution is deferred). The one live filing path, one-click `POST /api/v1/projects/{project_id}/defects/jira`, now uses `run_once` (PR #81):
    - `pg_advisory_xact_lock` on (project, signature) before the dedup read;
    - a claim keyed by (project, signature, generation);
    - an ambiguous POST raises `OutcomeUnknown`, which returns 409 `jira_outcome_unknown` with the claim left `executing`;
    - reconciliation by the `testlookup-sig-<hash>` label;
    - `confirm_not_filed=true` clears a stuck claim (audited).
- **Abandoned / changed.** E7.4 "retry re-resolves config on the same row" — **rejected** (see §5.2 R3). Replaced by fingerprint-compare: resume if unchanged, else new run with `rerun_of`.
- **Re-evaluate.** Live verification of the kill-worker / paused-worker acceptance criteria has **not** been done on a running stack.

### 2.2 E8 — Human review gate  ✅ Complete (not enforced)

- **Description.** Every AI report becomes a proposal until a human accepts it; review state is visible on every response and gates every distribution channel.
- **Business value.** Compliance and trust: no unreviewed AI output is exported, notified, commented on a PR, or read as a release `GO` once enforcement is on.
- **Technical scope.** `review_requests` (0175), `review_request_service.py`, `routers/reviews.py`, `review_envelope.py` (headers + `review` block), `report_distribution_policy.py` (PDF, notifications, digests, PR/MR comments, webhooks, release verdict), MCP/CLI review state, `/reviews` page + banners, two quality-gate guards, mutating-call invariant in `agent_action_ledger_service`.
- **Dependencies.** E7 (`completed → passed/failed` transitions).
- **Acceptance criteria.** Pending report ⇒ 409 on export (when enforced); setting on ⇒ watermark + audit row; accept flips run to `passed` with reviewer in audit log and absent from API payloads; e2e accept moves chip to `passed`.
- **Implemented.** All six stories. `REVIEW_GATE_ENFORCED=False` (shadow): decisions are recorded as `not_enforced_would_refuse`, AI content is marked unreviewed/watermarked, nothing is withheld.
- **Partial / gaps.**
  - E8.2: **K2 fixed by T3.** Authenticated triggers are durable and act-mode self-review is refused. The per-project read-only SoD toggle remains.
  - E8.3: `/runs/compare` AI comparison has no review subject.
  - E8.5: `passed · reviewed <time>` is now rendered from the identity-free pipeline review summary → **K3 fixed by T20**.
  - E8.6: pipeline-originated mutations now require the proposing agent's current mode to be `act` → **K4 fixed by T4**.
- **Re-evaluate.** Implement the user-configured release-date rollout (D2). The Investigator excerpt review subject (D3) shipped on 2026-09-16.

### 2.3 E1 — OpenAPI agent exposure  ✅ Complete

- **Description.** Discover and invoke any single agent over the public API, with sync/async modes, live progress, retry/cancel, idempotency, generated Postman/curl docs, MCP tools, and a stricter authorization ratchet.
- **Business value.** Agents usable from curl, Postman, CI, MCP clients and scripts without triggering whole pipelines.
- **Technical scope.** `agent_catalog.py`; `agent_invocations` (0176/0177/0178); `routers/agent_invoke.py`; `invocation_stream.py`; `invocation_idempotency.py`; `agent_planner.invocation_*`; `tasks.run_agent_invocation`; `agent_api_docs.py` + `architecture/api/*`; `mcp/tools/agents.py`; authorization ratchet extension.
- **Dependencies.** E7 (runs, leases, retry), E8 (review state on outputs).
- **Acceptance criteria.** `GET /openapi.json` lists every capability under `/api/v1/agents/{agent_id}/invoke` with per-agent input schema; Postman run returns `202` then `passed`/`completed` on the seed project **[not yet run live]**.
- **Implemented.** All six stories.
- **Partial / gaps.**
  - E1.1: **K5 fixed by T21.** All capability inputs now resolve to closed Pydantic models and the `agents.catalog-schema-complete` baseline is empty. Thirteen output schemas remain unresolved. The invoke route still accepts only `SubjectRef` (a stored test run); direct structured payload execution is an E1.2 follow-up. The workflow currently consumes its durable shared-state projections rather than accepting these catalog models directly.
  - E1.2: arbitrary payloads other than a stored subject are a follow-up.
  - E1.3: deviation — DB idempotency key never expires; only the 24 h Redis lock does.
  - E1.6: 34 unclassified path ids remain in a shrink-only backlog; `require_workflow_access` arrives with E3.

### 2.4 E4 — Per-agent configuration  🟢 4/4

- **Description.** Per-project, per-agent configuration (mode, tier, model endpoints, thresholds, retry, timeout, tools, budget, shadow sampling, review, override policy) resolved through four tighten-only layers.
- **Business value.** Teams tune cost, autonomy and reliability per agent without code changes; unsafe loosening is impossible by construction.
- **Technical scope.** `agent_configs` (0179), `agent_config_service.py`, `agent_config_resolver.py`, `routers/agent_configs.py`, invoke-route enforcement, Settings panel (`AgentConfigPanel.tsx`), mode single-writer guard.
- **Dependencies.** E1 (invoke route), E8 (review policy), E7 (`execution_metadata`).
- **Acceptance criteria.** Monotonicity table enforced as validators; composition `max_attempts × timeout_seconds ≤ AI_PIPELINE_DEADLINE_SECONDS` with arithmetic in the error; `config_version` frozen into runs; offline clamp at resolve time; unit test per precedence pair; guard that only `agent_config_service` writes `mode`.
- **Implemented.** E4.1–E4.4. Migration 0187 moves both legacy rows into strict AgentConfig extensions, preserves runtime/pinned contracts, and removes the legacy table/model. Deprecated GET aliases remain for one release; old PUTs return 405.
- **Implemented follow-up.** T11 added tighten-only invoke `config_overrides`, froze the credential-free resolved snapshot, and wired Summary/Root Cause runtime consumers.

### 2.5 E5 — Model tiering and SLM summarization  ✅ Complete

- **Description.** A `ModelRouter` that picks deterministic/SLM/LLM per capability, escalates SLM→LLM on validation failure or low confidence with a budget re-check, SLM summaries with repair/escalation/fallback, root-cause split, per-provider circuit breaker, dev tag pairs.
- **Business value.** Lower cost/latency for summaries and classification; LLMs reserved for reasoning; graceful degradation.
- **Dependencies.** E4.1/E4.2 (resolved config — done). **Promotion of any tier default is forbidden before E9.3** (tier comparison), which needs E9.2 golden sets and E9.1.
- **Acceptance criteria.** SLM summary non-inferior to LLM on the summary golden set by the G2 rule (E9); provenance `tier_requested/tier_used/escalations/fallback_used`; escalation never exceeds budget.
- **Implemented.** E5.1–E5.5 shipped in T6–T10; T11 added invocation overrides and frozen runtime config.

### 2.6 E6 — Generic reviewer  ✅ Complete

- **Description.** `ReviewerAgent` (`agent.reviewer.v1`) with check families 1–5, `ReviewVerdictV1`, self-consistency (SLM), heterogeneous second-model agreement, supervisor handling of `retry/reject/pass_with_flags`, shared `step_llm_budget`.
- **Business value.** Any workflow (including user-defined ones) can attach an independent verifier; reduces single-model blind spots.
- **Dependencies.** E5.1 (tiers), E4 (`review.second_model_check`). E9.4 depends on E6.1.
- **Implemented.** E6.1–E6.4 shipped in T15–T16. Workflow attachment remains an E3 runtime concern.

### 2.7 E3 — User-customizable workflows  ✅ Complete

- **Description.** Versioned `workflow_definitions` with publish immutability; `WorkflowCompiler` (registry validation, dependency closure, cycle rules, capability permission ≤ mode, step tools ⊆ config allowlist, typed JSON condition AST); runs record `workflow_id@version`; editor UI; guard that built-ins match compiled graphs.
- **Business value.** Teams can govern, measure, publish, and execute project-specific workflows without changing application code.
- **Dependencies.** E4 (permissions, allowlists), E1.6 (`require_workflow_access`). E9.5 depends on E3.1.
- **Implemented.** E3.1 stores and governs versioned definitions. E3.2 semantically validates and emits LangGraph graphs with built-in topology parity. E3.3 freezes published workflow and plan authority on each run, executes the emitted graph, and refuses cross-version replay. E3.4 adds the project-scoped list/fork/edit/preview/validate/evaluate/publish UI at `/agents/workflows`, linked from the active agent pipeline page. E3.5 makes the three-way compiler/live topology regression an absolute quality-gate contract and documents the complete workflow lifecycle.

### 2.8 E9 — Evals as the control loop  ✅ Complete

- **Description.** One `EvalVerdict`; recordings keyed by `(prompt hash, provider/model@tier)`; golden sets (every capability n ≥ 20, AnalysisAgent n ≥ 100); G2 tier comparison + shadow sampling; G3 reviewer quality; G4 workflow evaluation; G5 drift; manifest provenance; eval-on-eval; release incident marks; label-leakage rules.
- **Business value.** Every prompt/model/tier/routing/reviewer/workflow change is measured before and after shipping.
- **Dependencies.** E9.3 needs E4.1 (config hook — done) and E5.1 (`default_tier`); E9.4 needs E6.1; E9.5 needs E3.1; E9.6 needs E8 + E9.9; E9.7 needs E7.1 (done).
- **Implemented.** E9.1–E9.10 shipped in T12–T14, T18, and the subsequent E9 continuation PRs; detailed deviations and gaps are on each architecture §12 story line.

### 2.9 E2 — Hardening and observability  ✅ Complete

- **Description.** OTel spans with `gen_ai.*` attributes; §10 counters with emission tests; DLQ list/replay; alerts (in-progress age beyond deadline, DLQ depth, pending-review age) each with a positive test; keep the architecture doc current.
- **Dependencies.** E7, E8 (signals exist).
- **Implemented.** E2.1–E2.3 are shipped. E2.3 retained the existing `/api/v1/admin/maintenance/dlq` reader as a compatibility route and added the canonical list/replay API; the architecture premise that no DLQ router existed was stale. Replay covers allowlisted Celery STREAM entries. The ingestion LIST and live-event entries remain inspection-only because their recovery protocols differ.

---

## 3. Architecture Documentation

### 3.1 System architecture (as built)

```
                      ┌───────────────────────────────────────────────────────────────────────────┐
 curl / Postman ─────►│ FastAPI backend (app/bootstrap.py)                                         │
 React SPA ──────────►│                                                                            │
 CLI (cli/) ─────────►│  routers/agents.py          /api/v1/agents/catalog[/{agent_id}]  (E1.1)    │
 MCP server (mcp/) ──►│                             /api/v1/agents/pipelines/{id}/retry|cancel (E7.4)│
                      │  routers/agent_invoke.py    /api/v1/agents/{agent_id}/invoke     (E1.2-1.3)│
                      │                             /api/v1/agents/invocations/{id}[/events|retry|cancel]
                      │  routers/reviews.py         /api/v1/reviews/{id}/accept|reject   (E8.2)    │
                      │  routers/agent_configs.py   /api/v1/projects/{pid}/agent-configs[/{agent_id}] (E4)
                      │                                                                            │
                      │  services: workflow_run_state · pipeline_lease · retry_policy ·            │
                      │            pipeline_cancellation · tool_call_idempotency ·                 │
                      │            review_request_service · review_envelope ·                      │
                      │            report_distribution_policy · agent_catalog ·                    │
                      │            invocation_idempotency · invocation_stream ·                    │
                      │            agent_config_service · agent_config_resolver                    │
                      └──────────────┬──────────────────────────────┬─────────────────────────────┘
                                     │ Celery (queues: critical > ingestion > ai_analysis > default)
                                     ▼                              │
                      ┌──────────────────────────────┐              │
                      │ worker/tasks.py              │              │
                      │  run_agent_invocation (E1.2) │              │
                      │  pipeline tasks + retry_wait │              │
                      │  reaper (lease expiry, E7.3) │              │
                      └──────────────┬───────────────┘              │
                                     ▼                              ▼
                      ┌──────────────────────────────┐   ┌──────────────────────────────────────┐
                      │ agents/workflow.py (LangGraph)│   │ PostgreSQL: agent_pipeline_runs,      │
                      │  _create_pipeline_run        │──►│  agent_stage_results, review_requests,│
                      │  checkpointed nodes, fencing │   │  agent_invocations, agent_configs,    │
                      │  BaseAgent hooks, budgets    │   │  agent_configs extensions, activity   │
                      └──────────────┬───────────────┘   │ Redis: idempotency locks, SSE         │
                                     ▼                   │  tickets, cost reservations, model    │
                      ┌──────────────────────────────┐   │  registry active models, dedup locks  │
                      │ llm_factory.get_llm →        │   │                                       │
                      │  BudgetedLLM (cost reserve)  │   │ MongoDB: pipeline event log           │
                      │  enforce_provider_policy     │   └──────────────────────────────────────┘
                      └──────────────────────────────┘
```

Note: leases and fencing tokens are **Postgres columns** on `agent_pipeline_runs` (`lease_owner`, `lease_expires_at`, `fencing_token`, `heartbeat_at`), not Redis.

### 3.2 Component interaction — single-agent invocation (E1.2–E1.3, E4.2)

```
Client            agent_invoke router                 DB / Redis                     Worker
  │ POST /agents/{agent_id}/invoke (Idempotency-Key?)     │                               │
  │──────────────►│ _validate_invocation (catalog wrapper) │                               │
  │               │ load TestRun; resolve_project_scope    │                               │
  │               │ body.project_id == run.project_id ?400 │                               │
  │               │ resolve_for_project(db,pid,agent) ────►│ agent_configs row + ai_config │
  │               │   AgentConfigInvalid → 409             │                               │
  │               │   invocation_refusal (disabled) → 403  │                               │
  │               │ idempotency: replay→200 / claim ──────►│ Redis lock (24h) + unique idx │
  │               │   conflict 422 / in-flight 409         │                               │
  │               │ existing in-progress invocation → 200  │                               │
  │               │ sync? SYNC_SLOTS.try_acquire else 503  │                               │
  │               │ insert AgentInvocation (pipeline_run_id minted) ─► commit               │
  │               │ record_activity(agent.invoked)         │                               │
  │               │ apply_async(run_agent_invocation) ───────────────────────────────────►│
  │◄──202 (async) │ or wait ≤ AGENT_INVOKE_SYNC_WAIT_SECONDS → 200/202                     │
  │ POST /invocations/{id}/events/ticket → GET /events?ticket=  (SSE, single-use ticket)    │
  │                                                         │  _create_pipeline_run(invocation_stage)
  │                                                         │  plan = agent + dependency closure
```

### 3.3 Data flow — pipeline run to reviewed report (E7 + E8)

```
trigger/invoke ─► _create_pipeline_run
                   ├─ execution_metadata: initial_workflow_plan, run_budget, budget_spend,
                   │   *_settings flags, agent_config_versions (E4.1)
                   └─ agent_stage_results rows (planned/skipped)
       ─► stages run (BaseAgent: lease heartbeat, fencing, budget reservation if expected_cost_usd > 0)
       ─► finalize
           ├─ report-producing stage completed? (REPORT_OUTPUT_SCHEMAS)
           │    yes → create_run_review_request (state pending_review; supersede older pending)
           │    no  → completed → passed (review_policy = not_applicable)
           └─ distribution (PDF, notifications, digests, PR/MR comments, webhooks, release verdict)
                 └─ report_distribution_policy.decide_run_distribution
                        REVIEWED | NOT_AI_GENERATED | PROJECT_ALLOWS_DRAFTS | INCLUDE_UNREVIEWED
                        | REFUSED (enforced) | WOULD_REFUSE (not enforced) → audit row
reviewer ─► POST /reviews/{id}/accept → guarded_transition completed→passed
        ─► POST /reviews/{id}/reject (reason_code) → completed→failed "review_rejected: <code>"
every AI response ─► review_envelope: X-TestLookup-AI-Generated, X-TestLookup-Review-State, body.review
```

### 3.4 Agentic workflow (LangGraph, unchanged topology)

```
ingestion ─┬─► anomaly_detection
           ├─► failure_clustering ─► cluster_investigation_dispatch ─► cluster_investigation* ─► cluster_investigation_join
           ├─► root_cause_analysis ─┬─► triage (propose_action)
           │                        ├─► contract_validation / log_intelligence
           │                        └─► defect_commander (mutating; flag default off)
           ├─► summary ─► gap_detection ─► report_refinement
           ├─► flaky_sentinel ─┐
           ├─► test_health ────┴─► release_risk ─► decision_report ─► decision_report_critic
           ├─► regression_watchman
           └─► change_ownership
Investigator (separate workflow): investigator_plan ─► hypothesis_{infra,commit,environment,known_flaky,regression} ─► investigator_synthesis
(* child_spawned)   Stage order per workflow type: services/agent_planner.py (_PIPELINE_STAGES, _DEEP_PIPELINE_STAGES, _LIVE_PIPELINE_STAGES)
```

### 3.5 Tool-usage mapping

`agent_config_service.AGENT_TOOL_PERMISSIONS` — all 19 LangChain `@tool` functions under `backend/app/tools/` are `read_only` (a test holds the map equal to the `@tool` set):

| Tool | Module | Used by (import sites) |
|---|---|---|
| `validate_api_contract` | `tools/validate_api_contract.py` | `agents/contract_agent.py` |
| `detect_log_rate_anomaly` | `tools/detect_log_anomaly.py` | `agents/log_intelligence_agent.py` |
| `reconstruct_distributed_trace` | `tools/reconstruct_trace.py` | `agents/log_intelligence_agent.py` |
| `fetch_build_changes` | `tools/fetch_build_changes.py` | `agents/flaky_sentinel_agent.py` |
| `embed_and_cluster` | `tools/embed_and_cluster.py` | `agents/cluster_agent.py` |
| `list_recent_runs`, `list_run_failures`, `get_failure_clusters`, `check_quarantine_status`, `get_release_gate_verdict`, `recall_failure_history`, `count_failure_kinds` | `tools/chat_read_tools.py` | `agents/conversation.py` |
| `fetch_allure_stacktrace`, `fetch_rest_api_payload`, `query_splunk_logs`, `check_test_flakiness`, `analyze_openshift_pod_events`, `recall_similar_failures`, `fetch_app_metrics` | `tools/*.py` | ReAct triage / evidence (`_AUTHORIZED_TOOLS` in `evidence_artifact_service.py` lists six of these) |

Mode permits tools by permission: `shadow ⇒ read_only`, `suggest ⇒ + propose_action`, `act ⇒ + mutating`.

### 3.6 API contract summary (programme routes)

| Method & path | Auth | Success | Errors | Story |
|---|---|---|---|---|
| `GET /api/v1/agents/catalog` | authenticated | 200 `AgentCatalogEntry[]` | — | E1.1 |
| `GET /api/v1/agents/catalog/{agent_id}` | authenticated | 200 `AgentCatalogDetail` (input wrapper + JSON Schemas) | 404, 422 bad id | E1.1 |
| `POST /api/v1/agents/{agent_id}/invoke` | ≥ QA_ENGINEER; project scope | 202 async / 200 sync done or replay | 400 project mismatch · 403 disabled agent · 404 run/agent · 409 invalid stored config or key in flight · 422 body/key reuse · 503 sync slots full | E1.2, E1.3, E4.2 |
| `GET /api/v1/agents/invocations/{id}` | `require_invocation_access` (404 non-member) | 200 | 404 | E1.2 |
| `POST /api/v1/agents/invocations/{id}/events/ticket` → `GET …/events?ticket=` | access guard; single-use 60 s ticket | SSE stream | 401 bad ticket · 503 tickets unavailable | E1.2 |
| `POST /api/v1/agents/invocations/{id}/retry` · `/cancel` | access guard | 202 | 409 (+`links.rerun` when config changed) | E1.2 |
| `POST /api/v1/agents/pipelines/{pipeline_id}/retry` | project access | 202 | 409 at max attempts (+`links.rerun`) | E7.4 |
| `POST /api/v1/agents/pipelines/{pipeline_id}/cancel` | project access | 200 | 409 terminal | E7.4 |
| `GET /api/v1/projects/{project_id}/reviews` | project access | 200 | — | E8.2 |
| `GET /api/v1/reviews/{review_id}` | `require_review_access` | 200 | 404 | E8.2 |
| `POST /api/v1/reviews/{review_id}/accept` · `/reject` | QA_LEAD (global role) + review access; JWT only; synthetic users refused | 200 | 409 run moved on · 422 reject without `reason_code` | E8.2 |
| `GET /api/v1/projects/{project_id}/agent-configs` | project access | 200 `{configs[], tools{}}` | — | E4.1, E4.3 |
| `GET /api/v1/projects/{project_id}/agent-configs/{agent_id}` | project access | 200 | 404 unknown agent | E4.1 |
| `PUT /api/v1/projects/{project_id}/agent-configs/{agent_id}` | project access + project role ≥ QA_LEAD | 200 (version bumped) | 404 · 422 schema / mismatch / provider policy | E4.1 |

Public pipeline/invocation status: `in_progress | completed | failed | passed`. Review states: `pending_review | accepted | rejected | superseded`. Reject reason codes: `wrong_category, unsupported_claim, missing_evidence, contradiction, stale_data, other`. Review kinds: `report, eval_drift`. Subject types: `pipeline_run, invocation, decision_report, summary, capability`.

Generated references (CI-checked): `architecture/api/agents.postman_collection.json`, `architecture/api/AGENT_API_CURL.md` (regenerate with `python -m app.services.agent_api_docs`; CI runs `--check`).

### 3.7 Configuration model (E4)

Precedence — each layer may only **tighten** the one above:

```
env ceiling       AI_OFFLINE_MODE, AI_LLM_PROVIDER_ALLOWLIST, AI_LLM_ALLOWED_BASE_URLS,
                  AGENT_MAX_ATTEMPTS_CEILING=10, AGENT_MAX_TIMEOUT_CEILING=600, AI_PIPELINE_DEADLINE_SECONDS=1500
   ▲
global ai_config  app_settings key "ai_config": provider, model, temperature, max_tokens, base_url,
                  offline override (can only turn egress off), confidence_threshold
   ▲
project           agent_configs row (mode + enabled columns; config JSONB), or default_config(agent_id)
   ▲
request           AgentConfigPatch → apply_patch   (implemented; NOT yet accepted by any route)
```

`AgentConfigV1` (all sub-objects `extra="forbid"`; `backend/app/services/agent_config_service.py`):

```text
agent_id (registered, non-runtime capability id) · enabled · mode ∈ {shadow, suggest, act}
model { tier ∈ {auto, deterministic, slm, llm}; slm/llm {provider, model, temperature, max_tokens} (no base_url/api_key);
        escalation {on_validation_failure, on_confidence_below 0-100, max_escalations 0-3} }
thresholds { confidence_min, max_failures_analyzed, degraded_ratio }
retry { max_attempts ≥1 ≤ ceiling (default AGENT_PIPELINE_MAX_ATTEMPTS=5), base_seconds, cap_seconds ≥ base, jitter [0,1), retry_on ⊆ DEFAULT_RETRYABLE }
timeout_seconds ≤ AGENT_MAX_TIMEOUT_CEILING      composition: max_attempts × timeout_seconds ≤ AI_PIPELINE_DEADLINE_SECONDS
tools { allowlist ⊆ AGENT_TOOL_PERMISSIONS permitted by mode }
budget { max_llm_calls_per_run, max_tokens_per_run, max_cost_usd_per_run, max_runs_per_day }   (DEFAULT_BUDGETS names)
shadow { sample_rate, daily_token_budget }
review { policy ∈ {human_required, human_required_plus_auto_reviewer} (plus ⇒ auto_reviewer), auto_reviewer, second_model_check }
override_policy { allow_tier_downgrade, allow_retry_decrease, allow_tool_narrowing }
Validator: enabled ⇒ mode permits the capability's own permission.
```

Defaults (`default_config`): lowest mode that runs the capability; **mutating capability ⇒ `shadow` + disabled**; attempts/timeout clamped to ceilings and deadline; tools = all permitted by mode; `review.auto_reviewer=false`.

Tier tightness (for patches): `deterministic < slm < llm < auto` (`auto` may escalate to `llm`, so it is loosest).

Resolver (`agent_config_resolver.resolve` pure; `resolve_for_project` async): re-applies `apply_offline_ceiling`; clamps stored rows to lowered ceilings; raises `AgentConfigInvalid` for unrepairable rows; per tier endpoint: project block or inherited global model; provider refused by policy ⇒ fall back to permitted global model else `None`; every change recorded in `clamps[]` with layer `env|ai_config`; async residency check via `enforce_provider_policy_async` for endpoints with a `base_url`. Resolved object never carries API keys.

Settings added by the programme (`backend/app/core/config.py`): `AGENT_PIPELINE_MAX_ATTEMPTS=5`, `AGENT_MAX_ATTEMPTS_CEILING=10`, `AGENT_MAX_TIMEOUT_CEILING=600`, `AGENT_RETRY_BASE_SECONDS=30`, `AGENT_RETRY_CAP_SECONDS=600`, `AGENT_INVOKE_SYNC_CONCURRENCY=4`, `AGENT_INVOKE_SYNC_WAIT_SECONDS=25`, `AGENT_INVOKE_STREAM_TICKET_SECONDS=60`, `REVIEW_GATE_ENFORCED=False`. Guard `backend.settings-are-consumed` fails any setting nobody reads.

### 3.8 Error-handling strategy

| Layer | Rule | Where |
|---|---|---|
| Router | Owns `db.commit()` for the unit of work; services only `add/flush`; never `rollback()` an injected session | `tests/test_architectural_transaction_boundaries.py` (allowlist cap **88**) |
| State writes | Only `apply_transition` / `guarded_transition`; illegal edge ⇒ `IllegalTransition`; lost race ⇒ `TransitionLost` (409 at routers) | `services/workflow_run_state.py`; guard `agents.pipeline-status-writes-via-state-machine` |
| Stale writers | Fencing token on every stage/ledger write; mismatch ⇒ refuse | `services/pipeline_lease.py` (`fence_or_raise`) |
| Retryable vs not | `DEFAULT_RETRYABLE={model_unavailable, timeout, tool_error, lease_expired, lease_lost, unknown}`; `NON_RETRYABLE={validation_failed, policy_denied, budget_exceeded, review_rejected, cancelled}` | `services/retry_policy.py` |
| Cancellation | Sticky `cancel_requested` + `FOR UPDATE`; both race orderings end `failed` with error prefix `cancelled: ` | `services/pipeline_cancellation.py` |
| External side effects | `run_once(tool, scope_id=test_run, subject_id)`; an `executing` row found later ⇒ `outcome_unknown`, not retried; an ambiguous external result raises `OutcomeUnknown` and leaves the claim `executing` (PR #81) | `services/tool_call_idempotency.py` |
| Degraded output | Run `completed` + `execution_metadata.stage_quality='degraded'` (no `partial`) | `mark_degraded`, guard `agents.no-partial-pipeline-status` |
| AI content w/o review | Fail closed: `pending_review` envelope, watermark, audit decision | `review_envelope.py`, `report_distribution_policy.py` |
| LLM egress | `enforce_provider_policy(_async)` before client construction; `BudgetedLLM` refuses when pipeline budget context is `blocked`; cost reservation fails closed when store unavailable | `llm_policy_service.py`, `llm_factory.py`, `llm_cost_reservation.py` |
| Logging | structlog kwargs only; no `print`; PII redaction | guards `backend.structlog-positional-args`, `backend.no-print`, `backend.pii-log-redaction` |

### 3.9 Retry / backoff logic

```python
# services/retry_policy.py — RetryPolicy(max_attempts=5, base_seconds=30, cap_seconds=600, jitter=0.2)
raw_delay(n)  = min(base * 2**(n-1), cap)                 # n = 1-based failed attempt
delay(n)      = raw_delay(n) * (1 - jitter + 2*jitter*r)  # r ∈ [0,1): symmetric ±20 %
```

- Pipeline task no longer uses Celery `max_retries`; on a retryable failure the row goes `running|failed → retry_wait`, `next_retry_at` set, and `resume_agent_pipeline` resumes the **same id** carrying `(run_id, expected_attempt)`.
- Attempt beyond `max_attempts` ⇒ `failed` + DLQ.
- Manual retry (`POST …/retry`): `decide_retry_mode` compares `config_fingerprint` over `("resolved","provider","model","offline")`: unchanged ⇒ resume same row; changed ⇒ new run with `rerun_of` (409 + `links.rerun` at max attempts).
- Leases: `HEARTBEAT_SECONDS=30`, `LEASE_SECONDS=60`, `RENEW_MARGIN_SECONDS=5`; reaper acts on lease expiry.

### 3.10 State-machine definitions

```
                 ┌──────────┐
                 │ pending  │───────────────┐
                 └────┬─────┘               │
                      ▼                     ▼
 ┌──────────────► ┌──────────┐  ───────► ┌──────────┐
 │  (resume)      │ running  │           │  failed  │◄───────────┐
 │                └┬──┬───┬─┘  ◄─────── └┬───┬─────┘            │
 │                 │  │   │    (resume)  │   │ retry_wait        │
 │                 │  │   └──► ┌────────────┐◄┘                    │
 │                 │  │        │ retry_wait │──► running / failed │
 │                 │  │        └────────────┘                     │
 │                 │  ▼                                           │
 │           ┌───────────┐  review accepted / no report  ┌────────┐
 └───────────│ completed │──────────────────────────────►│ passed │ (terminal)
             └───────────┘  review rejected ─────────────┴────────┘──► failed
```

Authoritative table (`workflow_run_state.TRANSITIONS`):

| From | Allowed to |
|---|---|
| `pending` | `running`, `failed` |
| `running` | `completed`, `failed`, `retry_wait`, `pending` |
| `retry_wait` | `running`, `failed`, `pending` |
| `completed` | `passed`, `failed`, `running` |
| `failed` | `running`, `pending`, `retry_wait` |
| `passed` | — (terminal) |

`PUBLIC_STATUS`: `pending/running/retry_wait → in_progress`, `completed → completed`, `passed → passed`, `failed → failed`. Legacy reads: `partial → completed`, `cancelled/canceled → failed`.

Review request lifecycle: `pending_review → accepted | rejected | superseded` (one live request per subject, partial unique index; newer run supersedes only *pending*; accepted never overwritten; evidence-hash change after settlement supersedes).

---

## 4. Code‑Level Handover

### 4.1 How to obtain exact diffs

Diffs are not pasted here (tens of thousands of lines). Regenerate them precisely:

```bash
git log --first-parent --format='%h %s' 89650f79..19b9bdef          # programme merges
git show -m --first-parent --stat <merge-sha>                      # files of one PR
git diff 89650f79 19b9bdef -- backend/app/services/agent_config_resolver.py
gh pr view 79 --json title,body,files                              # PR body = design notes + test plan
```

`89650f79` is the merge of PR #51 (architecture rev 3), the last commit before E7.1.

### 4.2 File-by-file inventory (merge SHA → files; CHANGELOG/architecture doc omitted)

| PR · merge | Story | Production files | Tests |
|---|---|---|---|
| #52 `c6491c31` | E7.1 | `models/enums.py` (`PipelineRunStatus`), `services/workflow_run_state.py`, `migrations/0173_pipeline_run_state_machine.py`, `agents/workflow.py`, `agents/investigator/workflow.py`, `routers/agents.py`, `services/run_downstream_outbox.py`, `worker/tasks.py`, `models/postgres.py`, `models/schemas.py`; frontend `types/agent.ts`, `AgentStatusPage.tsx`, `AgentWorkflowPage.tsx`, `RunIntelligencePage.tsx`, `DecisionTrailDrawer.tsx`, `deepInvestigationService.ts`; `scripts/quality_gate.py` | `tests/services/test_workflow_run_state.py`, `tests/integration/test_pipeline_state_machine_postgres.py`, gate self-tests |
| #53 `0083a5c1` | E7.2 | `services/retry_policy.py`, `core/config.py`, `worker/tasks.py`, `agents/workflow.py`, `routers/agents.py`, `services/workflow_run_state.py` | `test_retry_policy.py`, `test_pipeline_retry_scheduling.py`, `test_decision_evidence_checkpoint_resume.py` |
| #54 `3dc644fb` | E7.3 | `services/pipeline_lease.py`, `agents/state.py`, `agents/workflow.py`, `routers/agents.py`, `worker/tasks.py` | `test_pipeline_lease.py`, `test_pipeline_reaper_lease.py`, `regression/test_executed_stage_records_its_row.py` |
| #55 `19c13ef8` | (activity) | `routers/agents.py`, `routers/release_attribution_rules.py`, `services/activity/events.py`; `backend.activity-coverage` baseline | `regression/test_agents_activity_events.py` |
| #56 `349f78fb` | E7.4 | `services/pipeline_cancellation.py`, `services/pipeline_retry_config.py`, `migrations/0174_pipeline_rerun_of.py`, `routers/agents.py` (retry/cancel), `worker/tasks.py`, `agents/workflow.py` | `test_pipeline_cancellation.py`, `test_pipeline_retry_config.py`, `test_agents_retry_cancel_endpoints.py`, `test_pipeline_cancel_retry_race.py`, integration postgres test |
| #57 `5fea6f8d` | E7.5 | `services/workflow_run_state.py` (`PUBLIC_STATUS`, `passes_without_review`), `services/agent_capability_registry.py` (`REPORT_OUTPUT_SCHEMAS`), `routers/agents.py`, `routers/runs.py`, `services/agentic_runtime_service.py`, `services/pipeline_replay_service.py`; frontend status chips | `test_pipeline_public_status.py`, `AgentStatusPage.publicStatus.test.tsx`, e2e `agents-public-status.spec.ts` (not in CI) |
| #58 `148fd1ce` | E7.6 | `services/tool_call_idempotency.py`, `agents/triage_agent.py`, `agents/defect_commander.py` | `test_tool_call_idempotency.py`, `test_triage_tool_call_idempotency.py`, integration postgres test |
| #59 `a0394cc4` | E8.1 | `migrations/0175_review_requests.py`, `models/postgres.py` (`ReviewRequest`, vocabularies), `services/review_request_service.py`, `services/default_qa_lead_service.py`, `agents/workflow.py` (finalize) | `test_finalize_creates_review_request.py`, `test_review_request_service.py`, migration + integration tests |
| #60 `8fce1524` | E8.2 | `routers/reviews.py`, `services/review_request_service.py` (`settle_review`), `services/activity/events.py` | `test_reviews_api.py`, authorization ratchet |
| #62 `9f201834` | E8.3 | `services/review_envelope.py`, `routers/agents.py`, `routers/run_intelligence.py`, `models/schemas.py`, `bootstrap.py` (CORS expose) | `test_review_envelope.py` |
| #63 `ad2a7901` | E8.4 s1 | `services/report_distribution_policy.py`, `routers/reports.py`, `routers/shared_reports.py`, `routers/release_readiness.py`, `services/report_*_renderer.py`, `services/report_composition_service.py`, `core/config.py` (`REVIEW_GATE_ENFORCED`) | `test_distribution_gates.py` |
| #64 `0a0cee0e` | E8.4 s2 | `report_distribution_policy.py` (`gate_ai_summary_text`), `analysis_report_service.py`, `worker/tasks.py` | `test_notification_distribution_gates.py` |
| #65 `f67e415d` | E8.4 s3 | `report_distribution_policy.py` (`gate_kind_labels`, `record_distribution_detached`), `github_pr_comment_service.py`, `gitlab_integration_service.py` | `test_comment_distribution_gates.py` |
| #66 `4e68d25b` | E8.4 s4 | `mcp/review_notice.py`, `mcp/tools/reviews.py` (`list_pending_reviews`), `mcp/tools/intelligence.py`, `mcp/tools/reports.py`, `mcp/server.py`; `cli/testlookup_cli/commands/reviews.py`, `commands/intelligence.py`, `output.py` | `mcp/tests/test_mcp_reviews.py`, `cli/tests/test_reviews_commands.py` |
| #68 `2e2ddbf8` | E8.5 | `pages/ReviewsPage.tsx`, `components/reviews/ReviewBanner.tsx`, `hooks/useReviews.ts`, `services/reviewService.ts`, `types/review.ts`, `config/routeScope.ts`, `App.tsx`, `Sidebar.tsx`, `AgentStatusPage.tsx`, `RunIntelligencePage.tsx` | `ReviewsPage.test.tsx`, `ReviewBanner.test.tsx`, e2e `reviews-accept.spec.ts` |
| #69 `d7db36fe` | E8.6 | `scripts/quality_gate.py` (2 review guards), `services/agent_action_ledger_service.py` (`_proposing_run_review_accepted`), `routers/run_intelligence.py` | gate self-tests, `test_agent_action_ledger_service.py`, `test_intelligence_export_review_envelope.py` |
| #70 `2de6e492` | E1.1 | `services/agent_catalog.py`, `services/agent_capability_registry.py` (`SYNC_ELIGIBLE`), `routers/agents.py`; guard `agents.catalog-schema-complete` + baseline | `test_agent_catalog.py`, `test_architectural_route_shadowing.py` |
| #71 `c20933fd` | E1.2 s1 | `migrations/0176_agent_invocations.py`, `models/postgres.py` (`AgentInvocation`), `routers/agent_invoke.py`, `services/agent_planner.py` (`invocation_workflow_type`, `invocation_stage_closure`), `agents/workflow.py` (`invocation_stage`), `worker/tasks.py` (`run_agent_invocation`), `bootstrap.py` | `test_agent_invocations.py` |
| #72 `0fa391fc` | E1.2 s2 | `migrations/0177_agent_invocation_dispatched_at.py`, `routers/agent_invoke.py` (retry/cancel/output) | `test_agent_invocation_retry_cancel.py` |
| #73 `5d9b1b4a` | E1.2 s3 | `services/invocation_stream.py`, `routers/agent_invoke.py` (sync, SSE), `core/config.py` | `test_agent_invocation_sync_sse.py` |
| #74 `87946f96` | E1.3 | `services/invocation_idempotency.py`, `migrations/0178_agent_invocation_idempotency.py`, `routers/agent_invoke.py` | `test_agent_invocation_idempotency.py` |
| #75 `56710a40` | E1.4 | `services/agent_api_docs.py`, `architecture/api/*`, `.github/workflows/ci.yml` | `test_agent_api_docs.py` |
| #76 `99ca764e` | E1.5 | `mcp/tools/agents.py`, `mcp/client.py` (`extra_headers`), `mcp/server.py`, `mcp/README.md` | `mcp/tests/test_mcp_agent_catalog.py`, `backend/tests/test_mcp_agent_catalog_parity.py` |
| #77 `069b39f6` | E1.6 | — | `tests/test_architectural_authorization.py` (`GLOBAL_PATH_PARAMS`, `UNCLASSIFIED_PATH_PARAMS_BACKLOG`=34, `STRICT_ROUTER_MODULES`) |
| #78 `078c5709` | E4.1 | `migrations/0179_agent_configs.py`, `models/postgres.py` (`AgentConfig`, `AGENT_CONFIG_MODES`), `services/agent_config_service.py`, `routers/agent_configs.py`, `bootstrap.py`, `services/activity/events.py` (`agent_config.updated`), `core/config.py` (`AGENT_MAX_TIMEOUT_CEILING`), `agents/workflow.py` (`agent_config_versions`) | `test_agent_configs.py` |
| #79 `ff4b99fd` | E4.2 | `services/agent_config_resolver.py`, `routers/agent_invoke.py` (403/409) | `test_agent_config_resolver.py`; invoke harnesses stub `resolve_for_project` |
| #80 `64ab6aca` | E4.3 | `routers/agent_configs.py` (`tools` in list); frontend `components/agents/AgentConfigPanel.tsx`, `utils/agentConfig.ts`, `types/agentConfig.ts`, `services/agentGovernanceService.ts`, `hooks/useAgentGovernance.ts`, `pages/settings/AIAgentsPage.tsx` | `AgentConfigPanel.test.tsx`, `agentGovernanceService.test.ts`, `AIAgentsPage.test.tsx` |
| #82 `19b9bdef` | E4.4 p1 | `scripts/quality_gate.py` (`agents.agent-mode-single-writer`), baseline file, `architecture/DEVELOPER_GUIDE.md` | `scripts/test_quality_gate.py` (4 self-tests) |
| #81 `0d7c80c1` | E7.6 follow-up (owner session) | `services/defect_jira_service.py` (advisory lock, claim, label reconciliation, `confirm_not_filed`), `services/tool_call_idempotency.py` (`OutcomeUnknown`, `record_outcome`), `routers/defect_jira.py`, `models/schemas.py`; frontend `components/defects/CreateJiraIssueModal.tsx`, `services/defectJiraService.ts`; `.github/workflows/ci.yml` (Postgres suite) | `test_defect_jira_endpoint.py`, `services/test_tool_call_idempotency.py`, `integration/test_defect_jira_exactly_once_postgres.py`, `CreateJiraIssueModal.test.tsx` |

Non-programme merges in the same window: #61 (test fixtures stop stubbing app modules), #67 (release.decided webhook emission). #81 is listed above because it extends E7.6's `tool_call_idempotency`.

### 4.3 Key functions and classes

| Symbol | File | Contract |
|---|---|---|
| `apply_transition(run, to, *, error=None, degraded=False)` | `services/workflow_run_state.py` | In-memory legal edge + timestamps; raises `IllegalTransition` |
| `guarded_transition(db, run_id, *, expected, to, error=None, extra=None, fencing_token=None)` | same | Single conditional UPDATE; raises `TransitionLost` on 0 rows |
| `passes_without_review(...)`, `is_resumable(status, metadata)`, `public_status(value)` | same | E7.5 rules |
| `acquire_lease_fields`, `renew_lease`, `verify_lease`, `fence_or_raise`, `held_lease` | `services/pipeline_lease.py` | Lease + fencing |
| `RetryPolicy`, `pipeline_retry_policy()` | `services/retry_policy.py` | Backoff |
| `request_cancel`, `raise_if_cancelled`, `terminalize_cancelled` | `services/pipeline_cancellation.py` | Cancel |
| `config_fingerprint`, `decide_retry_mode` → `RetryPlan` | `services/pipeline_retry_config.py` | Resume vs rerun |
| `run_once(...)`, `prior_tool_calls(...)`, `tool_call_key(...)`, `OutcomeUnknown`, `record_outcome(...)` (PR #81) | `services/tool_call_idempotency.py` | At-most-once |
| `create_run_review_request`, `stage_run_review_request`, `settle_review` | `services/review_request_service.py` | Review lifecycle |
| `review_envelope_for_run`, `envelope_from_review`, `ReviewEnvelope.apply_headers` | `services/review_envelope.py` | Review state on responses |
| `decide_run_distribution`, `record_distribution`, `gate_ai_summary_text`, `gate_kind_labels`, `gate_release_verdict`, `apply_release_review_gate` | `services/report_distribution_policy.py` | Distribution gates |
| `list_catalog`, `get_catalog_detail`, `input_wrapper`, `resolve_schema_model` | `services/agent_catalog.py` | Catalog |
| `invoke_agent`, `require_invocation_access`, `_wait_for_terminal`, `SYNC_SLOTS` | `routers/agent_invoke.py` | Invocation |
| `claim/complete/release`, `request_fingerprint` | `services/invocation_idempotency.py` | Idempotency |
| `issue_stream_ticket`, `redeem_stream_ticket` | `services/invocation_stream.py` | SSE tickets |
| `AgentConfigV1`, `AgentConfigPatch`, `apply_patch`, `default_config`, `put_config` (ON CONFLICT upsert), `config_versions`, `serialize`, `provider_environment_errors`, `mode_permits` | `services/agent_config_service.py` | Config |
| `resolve`, `resolve_for_project`, `invocation_refusal`, `AgentConfigInvalid`, `Clamp`, `ResolvedAgentConfig` | `services/agent_config_resolver.py` | Resolution |
| `_create_pipeline_run(pipeline_run_id, test_run_id, project_id, workflow_type, *, rerun_of=None, invocation_stage=None)` | `agents/workflow.py` | Plan + metadata freeze |
| `BaseAgent` stage hooks (budget reservation when `expected_cost_usd > 0`) | `agents/base.py` | Budget per stage |

### 4.4 TODO markers

`git grep -n "TODO\|FIXME"` over the programme modules (`agent_config_service.py`, `agent_config_resolver.py`, `agent_invoke.py`, `reviews.py`, `workflow_run_state.py`, `pipeline_lease.py`, `report_distribution_policy.py`, `agent_catalog.py`, `invocation_idempotency.py`, `tool_call_idempotency.py`) returns **nothing**. Follow-ups are recorded as "Gap"/"Deviation" notes in §12 of the architecture doc and in §4.5 / §6 here — treat those as the TODO list.

### 4.5 Known defects and gaps (with reproduction)

| ID | Severity | Defect | Reproduction | Fix sketch |
|---|---|---|---|---|
| K1 | P1 | Manual retry accepted a review-rejected run (§7.2 says terminal) | **Fixed by T2:** pipeline and invocation retry endpoints return 409 with `reason=review_rejected` before config comparison, attempt-ceiling handling, or dispatch | Regression coverage in `test_agents_retry_cancel_endpoints.py` and `test_agent_invocation_retry_cancel.py` |
| K2 | P1 | Separation of duties inert | Trigger a pipeline as user A, accept its review as user A → accepted (SoD should refuse for `mode=act`) because `review_requests.requested_by` is NULL | Record triggering user on `agent_pipeline_runs` (new nullable column + migration CONCURRENTLY rules) and pass it to `create_run_review_request`; then enforce SoD for `mode=act` per §8 |
| K3 | P2 | Status chip lacked "passed · reviewed <time>" | **Fixed by T20:** pipeline responses carry the live report review state and settlement time; accepted PASSED cards render the time beside the four-value chip | Regression and mutation coverage in `test_agents_pipeline_review_summary.py` and `AgentStatusPage.publicStatus.test.tsx` |
| K4 | P1 | `mode=act` half of mutating-call invariant not wired | **Fixed by T4:** pipeline proposals carry a hashed `proposing_agent_id`; `execute_agent_action` resolves it and fails `policy_denied` unless `mode == "act"` | Regression and mutation coverage in `tests/services/test_agent_action_ledger_service.py` |
| K5 | P2 | 13 capability inputs were labels (SubjectRef only) | **Fixed by T21:** all 30 capabilities report `input_schema_resolved=true`; the completeness baseline is empty | Nine closed input contracts cover the 13 entries; direct structured invoke payloads remain refused by E1.2 |
| K6 | P3 | Idempotency key never expires in DB | Invoke with key K; after 24 h, same key with different body → 422 (lock expired, row remains) | Documented deviation; decide with owner whether keys should expire (partial index on `created_at` or cleanup job) |
| K7 | P3 | Codacy push workflow failed in 0 s on every branch (pre-existing) | GitHub rejected a step `if` that referenced `secrets.CODACY_PROJECT_TOKEN` directly | **Fixed by T23:** compute token availability in job-level `env`, then check that value in the step condition; regression test pins the workflow shape |
| K8 | P2 | E7.5 e2e (`frontend/tests/e2e/agents-public-status.spec.ts`) not in CI | CI job list | Run against a CI stack or document live-probe procedure |
| K9 | P1 (live) | Programme DoD never verified on a running stack | — | See §8.5 |

### 4.6 Missing tests

- Live/integration: kill worker mid-stage ⇒ `retry_wait → running → completed (attempt=2)`; paused worker late writes affect 0 rows; Postman collection run on seed project (DoD bullets 1–3).
- `tests/integration/` Postgres test for `agent_configs` upsert concurrency (two concurrent PUTs get distinct versions) — only SQL shape is unit-tested.
- Resolver residency with a real DNS resolver (unit test monkeypatches `enforce_provider_policy_async`).
- Frontend e2e for the agent config panel (hermetic vitest only).
- Invoke route with `config_overrides` (does not exist yet; add with E5).

### 4.7 Areas needing refactoring

- `agents/workflow.py` (`_create_pipeline_run` ≈ 130 lines of flag resolution) — extract a `PipelineSetup` builder before E3's compiler replaces stage lists.
- Investigator and Fixer compatibility fields now use separate strict extensions in `agent_configs`; remove their deprecated read-only aliases after one release.
- `routers/agent_invoke.py` (≈ 850 lines) — split validation, idempotency flow and SSE into services when adding `config_overrides`.
- `llm_factory.get_llm(provider, model, temperature, track)` has no per-call `max_tokens`/`base_url` parameters; E5 needs an endpoint-aware entry point (see §6.3).

### 4.8 Suggested patterns to apply

- **Module-level registry maps instead of new `CapabilitySpecV1` fields** (as `SYNC_ELIGIBLE`, `REPORT_OUTPUT_SCHEMAS`) — keeps `capability_registry_snapshot()` and frozen plans stable.
- **Pure core + async shell**: `resolve()` pure, `resolve_for_project()` I/O — mirror for `ModelRouter`.
- **Ratchet guards** for every "only X may do Y" rule, with fixture self-tests and a real-repo test pinning the baseline.
- **Mutation-test new tests** (scripted string mutations toward the wrong fix; assert each mutation applied).
- **Single upsert statement** for versioned rows (`INSERT … ON CONFLICT DO UPDATE … RETURNING`).

---

## 5. Reasoning & Rationale

### 5.1 Architectural choices made

| Choice | Why | Tradeoff accepted |
|---|---|---|
| Orchestration (LangGraph graph is the single authority) not choreography | Requirement 9 is only provable if one controller writes terminal status | Less decoupling between agents |
| Six internal states, four public | Internal needs `pending/retry_wait`; users need a stable small vocabulary | Projection layer to maintain (`PUBLIC_STATUS`) |
| Leases + fencing tokens in Postgres columns | Same transaction as status writes; no Redis dependency for correctness | Heartbeat writes to the row every 30 s |
| Single-agent invocation = pipeline run restricted to agent + dependency closure | Leases, fencing, retry, review apply identically; no second runtime | An invocation of a late stage runs its dependencies too |
| Review request per run subject with supersede | One live decision per report lineage; history kept | Extra table + partial unique index |
| Review gate behind `REVIEW_GATE_ENFORCED` (default off) | Ship gating code in shadow, observe `would_refuse` audit rows before withholding content | Requirement 10 not enforced until owner flips it |
| `mode`/`enabled` as columns, config JSONB for the rest | `mode` has exactly one writable home; queries on mode stay cheap | Two storage shapes to serialize |
| Tighten-only four-layer config | Makes loosening structurally impossible; offline ceiling cannot be bypassed from DB or request | Some fields (thresholds, temperature) are project-only |
| Resolve-time offline clamp (not only PUT-time) | Env can flip after a row was written | Resolver must run on every use |
| Defaults: mutating capability disabled in shadow | Never grant `act` implicitly | `defect_commander` invoke returns 403 until enabled |
| Idempotency: Redis lock + unique partial index | Redis for in-flight window; DB index is authority when Redis is down | Key never expires in DB (K6) |

### 5.2 Approaches rejected

| ID | Rejected approach | Reason |
|---|---|---|
| R1 | Keep `partial` status | Neither success nor failure; unprovable terminal set |
| R2 | Celery `max_retries` for pipelines | Retry count not configurable per run; state invisible to API |
| R3 | E7.4 "retry re-resolves config on the same row" | `_claim_pipeline_resume` replays checkpoints authorised under the frozen plan; re-resolving underneath can re-enter a tool the new allowlist forbids. Replaced by fingerprint compare → resume or `rerun_of` |
| R4 | Tool-call idempotency scoped to pipeline run | Manual retry under changed config starts a new pipeline, and offline + deep both triage the same test run → duplicate tickets. Scoped to test run instead |
| R5 | Retrying an `executing` tool-call row | The earlier attempt may have completed the external call; marked `outcome_unknown` |
| R6 | One discriminated union for all invoke inputs | Input contracts are shared by several capabilities, so the discriminator is not injective; one wrapper per capability chosen by path `agent_id` |
| R7 | `default_tier`/`sync_eligible` as `CapabilitySpecV1` fields | Changes `capability_registry_snapshot()` and frozen plan snapshots |
| R8 | Accept `config_overrides` on invoke in E4.2 | Nothing at run time applies a resolved config before E5; accepting a no-op override would read as applied |
| R9 | Migrate `agent_policies` → `agent_configs` as written (E4.4) | Rows are `investigator` and `fixer` (not registry agents); Investigator budgets (incl. `max_seconds_per_run`, cluster keys) drive every deep pipeline's run budget (`_create_pipeline_run`) and `cluster_investigation_orchestrator`; Fixer stores `runner/test_globs/schedule` in `budgets` behind pinned FixerConfig API. Changes pinned APIs and runtime budgets ⇒ owner decision D1 |
| R10 | Global `ai_config.confidence_threshold` flooring `thresholds.confidence_min` | §4.1 table makes thresholds project-only |
| R11 | Separate settings page for agent configs (E4.3 "SettingsPage tab") | Existing QA_LEAD+ route `/settings/ai-agents` already hosts agent governance; a panel there avoids a second place to manage agents |

### 5.3 Model / tool selection reasoning

- Local-first: default provider `ollama`, `LLM_MODEL=qwen2.5:7b`; `AI_OFFLINE_MODE=True` by default; `LOCAL_PROVIDERS={ollama, lmstudio, localai, vllm}`; remote providers refused while offline, including a "local" provider whose `base_url` resolves off-box.
- Tiers (§5.1): deterministic (rules/ML/templates) → SLM ≤ ~8B (summaries, classification, extraction) → LLM ≥ ~14B or cloud (multi-hop reasoning, hypotheses, synthesis, second opinion).
- All agent tools are read-only today; mode gates future propose/mutating tools.

### 5.4 Performance considerations

- Sync invocations only wait on the worker, bounded by `AGENT_INVOKE_SYNC_CONCURRENCY=4` and 25 s; no request-thread execution.
- SSE ends at terminal status / disconnect / 30 min; one event per change.
- Idempotency and config resolution add one indexed lookup each to invoke.
- Lease heartbeat every 30 s per running stage.
- `agent_configs` upsert is one statement; list route is one query.
- Indexes on existing tables must be built `CONCURRENTLY` inside `op.get_context().autocommit_block()` with `if_not_exists`.

### 5.5 Reliability considerations

- Fail closed where correctness matters (unknown stage ⇒ requires review; AI content without request ⇒ `pending_review`; unrepairable config ⇒ 409; cost reservation store down ⇒ refuse call).
- Fail open only where availability beats strictness and a second control exists (fixer dispatch lock falls back to DB gate).
- Guarded transitions everywhere a race is possible; cancel wins retry.

### 5.6 Safety considerations

- Offline ceiling is one-way (`env OR override`), re-applied at every resolution and on cache hits.
- Config documents cannot carry `base_url`/`api_key`; resolved config never contains keys.
- MCP has no review accept/reject tool (parity test); CLI refuses accept/reject on API-key profiles; accept/reject JWT-only, synthetic users refused.
- Mutating actions require an accepted review of the proposing run (`policy_denied`).
- Authorization ratchet: every path id classified; strict routers (`agent_invoke`, `reviews`) take no exemptions.

---

## 6. Pending Work & Next Steps

### 6.1 Task list (priority, complexity, files, tests, docs)

Priority: **P0** = blocks correctness/safety or other epics · **P1** = needed for a requirement · **P2** = quality/completeness · **P3** = nice to have. Complexity S/M/L as in the doc.

| ID | P | Size | Task | Files to modify | New modules | Tests | Docs |
|---|---|---|---|---|---|---|---|
| T0 | P0 | S | Environment check: run gate, ruff, mypy ratchet, key suites on `main` (Appendix B) | — | — | — | — |
| T1 | P0 | — | **Shipped:** owner answered D1, D2, D3 | — | — | — | Architecture §12 owner-decision note |
| T2 | P1 | S | **Shipped:** refuse retry of review-rejected runs (pipeline + invocation retry) | `routers/agents.py` (retry), `routers/agent_invoke.py` (invocation retry) | — | `test_agents_retry_cancel_endpoints.py`, `test_agent_invocation_retry_cancel.py`; mutation harness | CHANGELOG; §12 E7.4/E8.2 notes |
| T3 | P1 | M | **Shipped:** fix K2; record trigger user on pipeline runs, wire review `requested_by`, enforce SoD for act-mode proposals | `models/postgres.py`, migration 0188, pipeline dispatch/workflow, review service | migration 0188 | trigger propagation, review SoD, migration and mutation tests | DATABASE_SCHEMA.md; §12 |
| T4 | P1 | S | **Shipped:** fix K4, the `mode=act` half of the mutating-call invariant | `services/agent_action_ledger_service.py` | — | `tests/services/test_agent_action_ledger_service.py` | §12 E8.6 note |
| T5 | P1 | S–M | **Shipped:** E4.4 remainder per D1; both policy rows migrated, pinned GET aliases retained read-only, old PUTs return 405, mode baseline zero | `agent_investigation_service.py`, `fixer_service.py`, Investigator workflow, Fixer scheduler, routers/config service, frontend governance services | migration 0187 | alias, migration, projection, guard and mutation tests | §12 E4.4; CHANGELOG |
| T6 | P1 | S | **Shipped: E5.1** ModelRouter + registry `default_tier`/escalation maps (design in §6.3) | `agent_capability_registry.py`, `agent_catalog.py` | `services/model_router.py` | `test_model_router.py`, registry invariants, catalog test; `agent_api_docs --check` | §12 E5.1 note; E1.1 note |
| T7 | P1 | M | **Shipped: E5.2** Summary on SLM with repair, LLM escalation, deterministic fallback, and provenance | `agents/summary_agent.py`, `llm_factory.py` | — | summary escalation tests; mutation | §12 |
| T8 | P1 | S | **Shipped: E5.3** Root-cause split | `agents/analysis_agent.py`, `services/analysis_router.py` | — | tests | §12 |
| T9 | P1 | S | **Shipped: E5.4** Circuit breaker per provider+base_url | `services/llm_factory.py` | `services/llm_circuit_breaker.py` | breaker tests | OBSERVABILITY.md |
| T10 | P2 | S | **Shipped: E5.5** `make dev-llm` SLM+LLM tag pair | `Makefile`, compose | — | — | docs |
| T11 | P1 | M | **Shipped:** accept `config_overrides` on invoke and freeze resolved config into the invocation run | `routers/agent_invoke.py`, `agents/workflow.py` | migration 0181 | invoke tests; E1.4 docs | §12 E4.2 |
| T12 | P0 for E5 promotion | M | **Shipped: E9.1** EvalVerdict + recordings keyed by model@tier | `agent_eval_harness.py`, `eval_gate_service.py`, `prompt_registry.py`, `prompt_eval_recordings.py` | `eval_verdict.py` | harness, recording, schedule and guard tests | AI_EVALUATION.md |
| T13 | P0 for E5 promotion | L | **Shipped: E9.2** golden sets (n ≥ 20 all, n ≥ 100 AnalysisAgent), sample schemas, `eval_coverage_by_capability()`, `eval_exempt` | `golden_agent_outputs.py`, `tests/evals/*` | `agent_eval_samples.py` | coverage tests | AI_EVALUATION.md |
| T14 | P1 | M | **Shipped: E9.3** G2 tier comparison + shadow sampling + `PUT agent-configs` hook | `routers/ai_evaluation.py`, config router | migration 0180 | tests | §12 |
| T15 | P1 | M | **Shipped: E6.1** ReviewerAgent + `ReviewVerdictV1`, `agent.reviewer.v1` | registry, catalog modules | `agents/reviewer_agent.py`, contract model | tests + mutation | §12 |
| T16 | P1 | S/M/S | **Shipped: E6.2–E6.4** reviewer model checks, supervisor, shared budget | reviewer, supervisor, budgets | — | tests + mutation | §12 |
| T17 | P1 | M/L/S/M/S | **Shipped:** E3.1–E3.5 | `agents/workflow.py`, `agent_planner.py`, workflows router, `/agents/workflows` UI | `agents/workflow_compiler.py`; migration 0183 | compiler fuzz, topology/runtime replay, UI regression, guard self-tests, mutation | §4.4, §12; `architecture/WORKFLOWS.md` |
| T18 | P1 | M×3, S×5 | **Shipped: E9.4–E9.10** reviewer quality, workflow eval, drift, provenance, eval mutations, release outcomes, label leakage | eval services, releases UI | migrations 0182, 0184–0186 | tests + mutations | AI_EVALUATION.md |
| T19 | P2 | S×3 | **Shipped: E2.1–E2.3** OTel, counters, DLQ + alerts with positive tests | `core/tracing.py`, `core/metrics.py`, `worker/tasks.py`, `infra/` | — | emission + alert tests | OBSERVABILITY.md |
| T20 | P2 | S | **Shipped:** K3 chip review time | `routers/agents.py` list response, `AgentStatusPage.tsx` | — | backend/frontend regression tests; six-mutation harness | CHANGELOG; §12 E8.5 |
| T21 | P2 | M | **Shipped:** K5 label-only catalog inputs → nine concrete models | `models/agent_input_contracts.py`, catalog module list | — | catalog + contract tests; empty baseline; nine-mutation harness | CHANGELOG; §12 E1.1 |
| T22 | P2 | S | **Shipped:** Live DoD verification (§8.5); fixed stale-worker finalization fence and pending intelligence export | catalog projection, workflow finalization, intelligence export, repeatable probe | — | live probes + seven-mutation harness | §12 + `architecture/verification/AGENTIC_LIVE_DOD_2026-09-15.md` |
| T23 | P3 | S | K7 Codacy workflow **(shipped early as a prerequisite)** | `.github/workflows/codacy.yml` | — | workflow syntax regression test | CHANGELOG; §12 E2.4 |

### 6.2 Recommended order

```
T0 → T1 (ask owner) ─┬─► T2, T4 (small correctness fixes, independent)
                     ├─► T6 E5.1 ─► T7 E5.2 ─► T8 E5.3 ─► T9 E5.4 ─► T10 E5.5 ─► T11 overrides
                     ├─► T12 E9.1 ─► T13 E9.2 ─► T14 E9.3 (unlocks tier-default promotion)
                     ├─► T3 (SoD, needs a migration)
                     └─► T5 when D1 answered
then E6 (T15–T16) ─► E9.4 · E3 (T17) ─► E9.5 · E9.6/9.7/9.9/9.10 · E2 (T19) · T20–T23
```

### 6.3 E5.1 — investigation results (implemented by T6)

Facts verified before stopping:

1. **Where to put `default_tier` and escalation rules.** `capability_registry_snapshot()` dumps every `CapabilitySpecV1`; adding fields changes snapshots. Use module-level maps next to `SYNC_ELIGIBLE` (e.g. `DEFAULT_TIERS: dict[str, str]`, `ESCALATION_TRIGGERS: dict[str, frozenset[str]]`, `CLASSIFY_CAPABILITIES`).
2. **Budget coupling (critical).** `agents/base.py` reserves pipeline budget for a stage only when `get_capability(stage).expected_cost_usd > 0`; `services/agent_planner.py` allocates graph budget only to stages with `expected_cost_usd > 0`. A stage with cost 0 that calls a model makes an **unbudgeted** call at the pipeline level (the monthly cost reservation in `BudgetedLLM` still applies).
3. **`release_risk` calls an LLM.** `agents/release_risk_agent.py` `_get_llm_reasoning` calls `get_llm(temperature=0.0)` for an optional narrative (skipped for extreme scores). §5.2 says set `release_risk` to `cost_usd=0, default_tier=deterministic`; doing so would remove its budget reservation. **[Recommendation]** set `cost_usd=0` only for `flaky_sentinel` and `test_health` (no model calls found), keep `release_risk` cost > 0 with `default_tier=deterministic` (score) and record the deviation.
4. **Which agent modules call models today** (grep for `get_llm`/`BudgetedLLM`/`.ainvoke(`): `anomaly_agent`, `release_risk_agent`, `regression_watchman`, `summary_agent`, `run_compare_agent`, `conversation`, `cluster_agent` (1 hit — verify), `contract_agent` (1 hit — verify), `flaky_sentinel_agent` (1 hit — verify, likely a comment/import), `log_intelligence_agent` (2 hits — verify), `investigator/hypotheses`, `investigator/synthesis`; `analysis_agent` via `analysis_router`; triage via `services/agent.py` (`track="reasoning"`). **Verify each hit before assigning a tier.**
5. **Invariant to add:** `default_tier != "deterministic" ⇒ expected_cost_usd > 0` (test over the whole registry), and router refuses a non-deterministic tier for a capability with `expected_cost_usd == 0` (reason `capability_unbudgeted`).
6. **Remaining budget:** `pipeline_budget_service._ledger(metadata)` returns `(budget, spend)`; remaining USD = `budget["max_cost_usd"] - spend["cost_usd"] - spend["reserved_cost_usd"]`. `_ledger` is private — add a public helper.
7. **Model call API:** `llm_factory.get_llm(provider=None, model=None, temperature=None, track=None)`; `max_tokens` and `base_url` come from the global config, not per call. E5 needs an endpoint-aware variant that accepts a `ResolvedEndpoint`.
8. **Promoted classifier:** `ModelRegistry.get_active_model("classifier")` (async, Redis); tracks `classifier, reasoning, embedding`.
9. **Escalation cost estimate** `cost_estimate(cap, "llm")` has no source in the registry. **[Recommendation]** a consumed setting (e.g. an LLM/SLM cost factor) rather than a hardcoded constant; confirm with owner.
10. **Promotion rule:** no tier default may be promoted before E9.3.

Suggested `ModelRouter` shape (pure core, mirrors the resolver):

```python
@dataclass(frozen=True)
class ModelChoice:
    tier: Literal["deterministic", "slm", "llm"]
    endpoint: ResolvedEndpoint | None
    reason: str                      # tier | budget | llm_unavailable | capability_unbudgeted | classifier
    fallback: str | None = None      # capability.fallback when degraded

def choose_model(stage: str, resolved: ResolvedAgentConfig, *, budget_remaining_usd: float | None,
                 promoted_classifier: str | None = None) -> ModelChoice: ...

def decide_escalation(stage: str, choice: ModelChoice, resolved: ResolvedAgentConfig, *,
                      trigger: Literal["validation_failure", "low_confidence", "not_enough_evidence", "contradictions"] | None,
                      confidence: int | None, escalations: int, step_llm_calls_remaining: int,
                      budget_remaining_usd: float | None) -> EscalationDecision: ...   # accept | escalate | fallback

def provenance(requested: str, final: ModelChoice, escalations: int, fallback_used: bool) -> dict[str, Any]: ...
```

Escalation allowed only when: capability rule allows the trigger; project `escalation` toggles allow it; current tier is `slm`; `resolved.config.model.tier == "auto"` (an explicit `slm` pin must not be loosened); `escalations < max_escalations`; `endpoints["llm"]` is not `None`; step LLM budget ≥ 1; budget remaining ≥ LLM cost estimate. Otherwise fallback to `capability.fallback` and mark `stage_quality=degraded`.

### 6.4 Documentation updates required with every story

- `CHANGELOG.md` entry (binary-safe prepend under `# Changelog`).
- Architecture doc §12: append **(shipped)** note with deviations/gaps on the story line.
- `architecture/DEVELOPER_GUIDE.md` when adding/removing a guard (row + counts "N guards, M ratchets").
- `architecture/DATABASE_SCHEMA.md` for new tables.
- Regenerate `architecture/api/*` when agent/review routes change (`python -m app.services.agent_api_docs`).
- `mcp/README.md` when MCP tools change; `INVOKABLE_AGENT_IDS` literal in `mcp/tools/agents.py` must match the backend parity test.

---

## 7. Risks & Mitigations

| Category | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| Architectural | E5 tier routing bypasses pipeline budget for cost-0 capabilities | Medium / High | Registry invariant test (§6.3.5); router refuses non-deterministic tier on cost-0 capability |
| Architectural | Adding fields to `CapabilitySpecV1` silently changes frozen plan snapshots and resume authority | Medium / High | Module-level maps; snapshot tests |
| Architectural | E3 compiler diverges from today's hand-built graphs | High / High | E3.2 diff-tests built-ins against compiled graphs; guard `workflows.builtins-match-compiled` |
| Implementation | Service writes `mode` or `status` outside the single writer | Low / High | Guards `agents.agent-mode-single-writer`, `agents.pipeline-status-writes-via-state-machine` |
| Implementation | Multiple Alembic heads from parallel sessions | Medium / Medium | Re-check head before migration; gate `database.single-alembic-head` |
| Implementation | Tests pass only with mocks (constraints, races) | Medium / Medium | Postgres integration tests under `tests/integration/`; mutation checks |
| Integration | Concurrent sessions touching `CHANGELOG.md` / `main` (owner sessions land PRs, e.g. #81) | High / Low | Merge base in (never rebase/force-push), resolve, re-verify |
| Integration | Enabling `REVIEW_GATE_ENFORCED` withholds content users rely on | Medium / High | Review `not_enforced_would_refuse` audit rows first; D2 |
| Integration | E4.4 migration changes Investigator/Fixer budgets and pinned APIs | Medium / High | D1; keep FixerConfig and AgentPolicy wire shapes; read-only alias for one release |
| Missing dependency | E5 promotion needs E9.2/E9.3 data that does not exist (golden store: 9 usable AnalysisAgent entries) | High / High | Start E9.1/E9.2 early |
| Missing dependency | No Ollama in CI for recordings | High / Medium | Treat unrecordable prompts as `insufficient_samples`, never `pass` (E9.1) |
| Ambiguous requirement | Escalation cost estimate; SLM/LLM default tags; auto-reviewer default | Medium / Medium | Ask owner; record decisions in §12 |
| Ambiguous requirement | E4.4 premise (§5.2 R9) | Certain / Medium | D1 |
| Failure mode | Lease heartbeat stops under event-loop stall ⇒ reaper retries a live stage | Low / Medium | Fencing token rejects the stale writer; verify live (T22) |
| Failure mode | Redis unavailable ⇒ idempotency lock/SSE tickets/cost reservation | Medium / Medium | DB unique index authority; SSE 503 with poll link; cost reservation fails closed |
| Failure mode | Stored config invalid after env ceiling lowered | Medium / Low | Clamped at resolve; unrepairable ⇒ 409 + UI `valid:false` banner |

---

## 8. GPT‑5.6‑Sol Continuation Plan

### 8.1 Do first (in order)

1. `git fetch origin && git checkout main && git pull`. Confirm `git log -1` is at or after `0d7c80c1`, and run `gh pr list` for anything merged or opened since. If a local `feat/e5-1-model-router` branch exists in your checkout, delete it: it never had commits.
2. Read, in order: this file §1–§3; architecture doc §0 (requirements), §4 (config), §5 (tiers), §7 (state machine), §8 (review), §12 (backlog with shipped notes); `backend/CLAUDE.md`-equivalent conventions in Appendix A.
3. Run the baseline verification in Appendix B. All must be green before changing code.
4. Ask the owner D1, D2, D3 (§1.3). Do not implement their subjects until answered.
5. Start with T2 and T4 (small correctness fixes), then T6 (E5.1) using §6.3, and in parallel T12/T13 (E9.1/E9.2) if capacity allows.

### 8.2 Do NOT change

- `workflow_run_state.TRANSITIONS`, `PUBLIC_STATUS`, or write `AgentPipelineRun.status` outside `workflow_run_state.py`.
- The four public statuses or review states/reason codes (pinned API + DB CHECK constraints).
- Fencing/lease semantics or the reaper predicate without live verification.
- The offline ceiling direction (env OR override), `apply_offline_ceiling` on cache hits, or provider policy checks before client construction.
- `AgentConfigV1` `extra="forbid"`, the rule that config documents never contain `base_url`/`api_key`, or the tighten-only precedence.
- Write `agent_configs.mode` outside `agent_config_service.py`.
- Add review accept/reject to MCP or allow it for API-key/synthetic principals.
- `CapabilitySpecV1` fields (use module-level maps).
- Pinned wire shapes: `AgentPolicy` (`/agent-policies`), `FixerConfig`, invocation response, catalog entry (additive fields only).
- Existing migrations (add new revisions only).
- Baselines by regenerating all of them (`--update-baseline` without `--only`).

### 8.3 Must validate (per story)

- Branch from `origin/main`; regression test; CHANGELOG entry; §12 note.
- `ruff check app/ tests/` (backend scope matches CI), mypy ratchet not above baseline, quality gate green, relevant pytest suites (Appendix B), frontend `npx tsc --noEmit`, `npx eslint <files>`, `npx vitest run <files>`.
- Mutation check of new tests (each mutation toward the wrong fix must fail a test; assert each mutation applied).
- If agent/review routes change: `python -m app.services.agent_api_docs --check` and authorization ratchet.
- If a setting is added: it must be read somewhere (`backend.settings-are-consumed`).
- If a project-scoped mutation route is added: activity event recorded (`backend.activity-coverage`).
- If a migration is added: single head, real `downgrade()`, `CONCURRENTLY` for indexes on existing tables.
- PR body via `--body-file`; merge only with CI green; merge base in on conflicts (never rebase/force-push).

### 8.4 How to extend the architecture

- New capability: `_capability(...)` in `agent_capability_registry.py` + planner stage list or explicit `execution=`; input/output Pydantic models in `CATALOG_SCHEMA_MODULES`; add to tier/escalation maps; `REPORT_OUTPUT_SCHEMAS` if it produces a report; MCP `INVOKABLE_AGENT_IDS` if invocable; regenerate API docs.
- New tool: `@tool` in `app/tools/`; add to `AGENT_TOOL_PERMISSIONS` with its real permission (test fails otherwise).
- New AI route: apply `review_envelope` (guard `reviews.report-consumers-carry-review-block`).
- New distribution channel: go through `report_distribution_policy` and record the decision.
- New "only X may do Y" rule: ratchet guard + fixture self-tests + real-repo test + DEVELOPER_GUIDE row.

### 8.5 Complete and test the final implementation

- Finish E5 (T6–T11) behind today's default tiers; promote defaults only after E9.3 passes non-inferiority.
- E6 then E9.4; E3 then E9.5; E9.6/9.7/9.9/9.10; E2.1–E2.3.
- Live definition-of-done on a running stack (`make dev-llm`, `make seed-data`):
  1. Postman run of `architecture/api/agents.postman_collection.json`: `202` then `passed` for independently invokable sync-eligible agents, `202` then `completed` for independently invokable report-producing agents. Catalog entries that require surrounding workflow state expose `invokable=false`.
  2. `SELECT DISTINCT status FROM agent_pipeline_runs` ⊆ six internal states; API returns only the four public values.
  3. Kill a worker mid-run ⇒ `retry_wait → running → completed` with `attempt=2`; paused worker late writes affect zero rows.
  4. Pending-review decision report cannot be exported/notified/read as `GO` with enforcement on; accept flips to `passed`, reviewer in audit log only.
  5. Stuck-beyond-deadline alert fires while an active run is older than deadline plus grace, and clears after reaping and successful recovery terminalize it. Lease expiry triggers the reaper; the intermediate `retry_wait` state remains active by design.
  6. Eval DoD bullets from §12 (attestation blocks prompt/model/tier changes without fresh inference-backed attestation; coverage thresholds; SLM summary non-inferiority; reviewer recall per class).

T22 ran on 2026-09-15 against a seeded Podman lite stack with review enforcement enabled. All eight independently invokable sync/report capabilities passed their API terminal contract. The pause/reap probe found `_mark_pipeline_done` was the only unfenced pipeline write; it is now fenced and locked, and the repeated attempt completed at attempt 2 while a stale token affected zero rows. The review probe found `/runs/{id}/export` bypassed the distribution policy; it now refuses and audits pending AI exports under enforcement. Notification withholding, `PENDING_REVIEW`, human acceptance, identity-only-in-audit, public/internal status projection, and the positive overdue alert were verified. The original alert premise was wrong because its metric is deadline age rather than lease expiry and `retry_wait` is active. Eval guard/coverage/G2/reviewer/provenance/workflow-refusal checks passed statically; the bundled attestation is still source-review evidence and the 2 GiB local VM could not run the SLM+LLM pair, so fresh inference-backed release evidence remains open. See `architecture/verification/AGENTIC_LIVE_DOD_2026-09-15.md`.

### 8.6 Release readiness

- Owner sign-off on D2 with review-queue SLO and a rollback plan (flag off).
- Alerts from E2.3 live with positive tests; dashboards for `gen_ai.*` spans.
- Migrations 0173–0179 (+ new) applied on staging with `make migrate`; downgrade tested.
- Air-gapped deployment check (`k8s/overlays/openshift-artifactory/`) with `AI_OFFLINE_MODE=true`: resolver clamps any stored cloud provider; invoke works with local models.
- Architecture doc §0 traceability table updated to final statuses; CHANGELOG release section.

---

## 9. Final Handover Checklist

| # | Item | State |
|---|---|---|
| 1 | All programme PRs merged, CI green (#52–#82) | ✅ |
| 2 | No uncommitted production code in working tree | ✅ (handover committed on `docs/agentic-handover-2026-09-13`) |
| 3 | Empty local branch `feat/e5-1-model-router` | ✅ deleted |
| 4 | Alembic head `0179`; single head | ✅ |
| 5 | Quality gate 42 guards / 18 ratchets green; mypy baseline 373 | ✅ at `19b9bdef` |
| 6 | Architecture doc §12 has shipped notes for E7, E8, E1, E4 | ✅ |
| 7 | Owner decisions D1–D3 | ✅ recorded and answered in §1.3 and architecture §12 |
| 8 | Known defects K1–K9 listed with reproduction | ✅ §4.5 |
| 9 | E5.1 investigation captured | ✅ §6.3 |
| 10 | Live DoD verification | ✅ T22 completed on the seeded Podman lite stack; inference-backed SLM/LLM release evidence remains open (§8.5) |
| 11 | Owner-started sessions | ✅ Jira duplicate fix merged as #81 · ⚠️ release.decided session has no open PR; run `gh pr list` before branching |
| 12 | Visual status report | ✅ https://claude.ai/code/artifact/0dd966a6-d71a-47a4-9304-354c292eba7b |

---

## Appendix A — Working conventions that CI and reviewers enforce

- **Traceability:** every change = branch + regression test + tracked `CHANGELOG.md` entry. `docs/` is gitignored (local notes only).
- **Transactions:** services `add/flush` and return; routers own `commit()`; services never `rollback()` an injected session. New service commits need an allowlist entry and a cap bump in `tests/test_architectural_transaction_boundaries.py` (cap 88).
- **Authorization:** path params must be classified; project-scoped routes use `require_project_access()` / role guards; subject routes use `require_<subject>_access()` returning 404 to non-members; verify the *provided* id.
- **Pydantic v2 only**; async everywhere; structlog kwargs only; no `print`.
- **Alembic:** single head; implement `downgrade()`; indexes on existing tables `CONCURRENTLY` in `autocommit_block()` with `if_not_exists`; migrations never import app code (freeze vocabularies; hold them in step with a test).
- **Lint scope:** `ruff check app/ tests/` from `backend/` (CI does not lint `scripts/`, but do not add new findings there).
- **Frontend:** SWR hooks for reads; single axios instance; helpers outside component files (fast refresh); `strict` TypeScript.
- **Windows specifics:** use `.venv311` (Python 3.11, CI-faithful); pytest needs `-p no:testlookup`; write files as bytes/LF (Path.write_text is CRLF on Windows); avoid backslash escapes in generated code via shell heredocs.
- **Guards:** a new guard needs fixture self-tests in `scripts/test_quality_gate.py` and a DEVELOPER_GUIDE row; update a single baseline with `--only <guard> --update-baseline`.
- **Mutation-test new tests**; a mutation harness must assert each mutation applied.
- **Git:** check `git branch --show-current` before commit/push (concurrent sessions); on conflicts merge base in, never rebase/force-push; PR body via `--body-file`; commits end with the agent attribution line the owner requires.

## Appendix B — Command reference

```bash
# backend (from backend/)
../.venv311/Scripts/python -m ruff check app/ tests/
../.venv311/Scripts/python -X utf8 ../scripts/mypy_ratchet.py --check-stale
../.venv311/Scripts/python -X utf8 -m pytest tests/test_agent_configs.py tests/test_agent_config_resolver.py \
  tests/test_agent_invocations.py tests/test_agent_invocation_idempotency.py tests/test_agent_invocation_sync_sse.py \
  tests/test_agent_invocation_retry_cancel.py tests/test_reviews_api.py tests/test_agent_catalog.py \
  tests/test_agent_api_docs.py tests/test_architectural_authorization.py tests/test_architectural_transaction_boundaries.py \
  tests/services/test_workflow_run_state.py tests/services/test_retry_policy.py tests/services/test_pipeline_lease.py \
  -p no:randomly -p no:testlookup --basetemp=.pytest_tmp -q
../.venv311/Scripts/python -m app.services.agent_api_docs --check

# repo root
.venv311/Scripts/python -X utf8 scripts/quality_gate.py
.venv311/Scripts/python -X utf8 -m pytest scripts/test_quality_gate.py -q -p no:randomly -p no:testlookup
.venv311/Scripts/python -X utf8 scripts/quality_gate.py --only <guard> --update-baseline

# frontend (from frontend/)
npx tsc --noEmit
npx eslint <changed files>
npx vitest run <test files>

# MCP / CLI
cd mcp && ../.venv311/Scripts/python -m pytest tests -q
cd cli && ../.venv311/Scripts/python -m pytest tests -q

# GitHub
gh pr create --base main --head <branch> --title "<title>" --body-file <file>
gh pr checks <n> --watch
gh pr merge <n> --merge
```


## Appendix C — Resume prompt for GPT‑5.6‑Sol

Paste the block below as the first message to GPT‑5.6‑Sol in a checkout of `test-intelligence/testlookup`.

```text
You are the lead implementer resuming the TestLookup agentic architecture programme in the
repository test-intelligence/testlookup (this checkout). The previous implementer handed over on
2026-09-13. Work autonomously within the rules below, one backlog story at a time.

1. READ BEFORE CHANGING ANYTHING
   - architecture/AGENTIC_HANDOVER_2026-09-13.md (entire file): status, architecture, code
     inventory, known defects K1-K9, task list T0-T23, what not to change, continuation plan.
   - architecture/AGENTIC_OPENAPI_ARCHITECTURE.md: section 0 (requirements), 4 (agent config),
     5 (model tiers), 7 (state machine), 8 (human review), 12 (backlog, including the
     "(shipped)" notes on each finished story).
   - architecture/DEVELOPER_GUIDE.md section 1 (quality-gate guards).
   Treat the code on origin/main as the source of truth. If the code and a document disagree,
   trust the code, say so, and update the document in the same PR.

2. VERIFY THE BASELINE (task T0)
   - git fetch origin; git checkout main; git pull. Expect main at or after 0d7c80c1; run
     gh pr list and note anything newer.
   - Run everything in the handover's Appendix B: backend ruff (app/ tests/), the mypy ratchet
     (baseline 373), the key pytest suites with -p no:testlookup, the quality gate (42 guards),
     scripts/test_quality_gate.py, the agent API docs --check, frontend tsc/eslint/vitest, and the
     MCP and CLI tests.
   - If anything is red on main, stop and report it before starting new work.

3. OWNER DECISIONS: ASK, DO NOT DECIDE (task T1)
   D1 Where the investigator/fixer agent_policies rows live (E4.4): migrate both, migrate
      the Investigator only, or defer.
   D2 When to set REVIEW_GATE_ENFORCED=true.
   D3 How to handle Investigator narrative excerpts in notifications once enforcement is on.
   Ask the owner these three questions first. Do not implement their subjects until the owner
   answers. Everything else is unblocked.

4. WORK ORDER
   - T2 (K1: refuse retry of review-rejected runs) and T4 (K4: mode=act half of the
     mutating-call invariant).
   - T6 E5.1 ModelRouter, using the handover's section 6.3 findings. In particular:
     release_risk calls an LLM, so keep its expected_cost_usd > 0; add the invariant that a
     default_tier other than deterministic requires expected_cost_usd > 0; put tier and escalation
     maps at module level next to SYNC_ELIGIBLE, not as CapabilitySpecV1 fields.
   - Then E5.2 to E5.5 and T11 (config_overrides on invoke).
   - In parallel, or right after E5.1: E9.1 and E9.2. No tier default may be promoted before E9.3.
   - Then E6, E9.4, E3, E9.5, the rest of E9, E2, and the P2/P3 items.
   - T3 (record the trigger user, separation of duties) and T5 (E4.4 per D1) when ready or answered.

5. PER-STORY DEFINITION OF DONE
   - Branch from origin/main.
   - A regression test that fails without the change.
   - A CHANGELOG.md entry.
   - A "(shipped)" note with deviations and gaps on the story's line in section 12 of the
     architecture doc.
   - ruff (app/ tests/) clean; mypy ratchet not above baseline; quality gate green; relevant pytest,
     vitest, tsc and eslint clean.
   - A mutation check: mutate the fix toward the wrong behaviour; every mutation must fail a test,
     and the harness must assert each mutation actually applied.
   - If routes changed: regenerate architecture/api/* and pass the authorization ratchet.
   - If a guard was added: fixture self-tests, a DEVELOPER_GUIDE row, updated guard counts.
   - If a migration was added: a single Alembic head, a real downgrade(), and indexes on existing
     tables built CONCURRENTLY inside autocommit_block() with if_not_exists.
   - Open a PR with gh pr create --body-file. Merge only when CI is green. On conflicts, merge main
     into the branch; never rebase or force-push.

6. DO NOT CHANGE (see handover section 8.2)
   - The state-machine TRANSITIONS or the public statuses.
   - Writes to status or mode outside their single writers (workflow_run_state.py,
     agent_config_service.py).
   - The one-way offline ceiling, or provider policy checks.
   - AgentConfigV1 extra="forbid" and tighten-only precedence.
   - Any base_url or api_key in stored configs.
   - The review accept/reject restrictions (JWT only, no MCP tool).
   - CapabilitySpecV1 fields.
   - Pinned wire shapes (AgentPolicy, FixerConfig, invocation response, catalog entry; additive
     changes only).
   - Existing migrations.
   - All baselines at once (update one guard's baseline with --only).

7. REPORTING
   After each merged story, post a short status:
   - story id and PR number;
   - what shipped;
   - deviations;
   - remaining gaps;
   - the next story.
   Flag immediately:
   - any new owner decision;
   - any red CI you cannot fix;
   - any requirement whose written premise does not match the code (as happened with E4.4).
```
