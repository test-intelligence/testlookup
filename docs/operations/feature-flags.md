# Feature flags

[Documentation home](../README.md) · [Configuration](configuration.md) · [Integrations](integrations.md)

Several TestLookup features ship behind feature flags, and some of them are **off on a fresh install**. This page lists every flag key the code reads, its default, what it gates, and how a flag is evaluated. If a feature described in the user guide is missing from your instance, check this page first.

## How a flag is evaluated

Flags are rows in the PostgreSQL `feature_flags` table (model `FeatureFlag`). Alembic migrations seed most rows; the rest are read by the code but never seeded, so they stay off until an administrator creates them. Every server-side check goes through `is_enabled()` in `backend/app/services/feature_flags.py`:

1. **Cache.** The row is cached in Valkey/Redis for 30 seconds (`feature_flag:<key>`). Saving a flag through the API or UI drops the cache entry after the change commits, so changes normally apply at once; at most they take 30 seconds. Agent pipeline runs read their stage flags with the cache bypassed.
2. **Row.** If a row exists it is evaluated:
   - `enabled_global` false: **off**, whatever else is set.
   - `enabled_projects` non-empty: off unless the request's project is in the list.
   - `enabled_roles` non-empty: off unless the caller's role (`VIEWER`, `TESTER`, `QA_ENGINEER`, `QA_LEAD`, `ADMIN`) is in the list.
   - `rollout_percent` below 100: on for a stable subset of callers. The bucket is `sha1("<key>:<user id>")` (or the project id when there is no user, or the literal `global` when there is neither) modulo 100, compared with the percentage. `0` is off.
3. **No row.** The flag is **off**, except for the four keys with an [environment fallback](#environment-fallback).

Every evaluation increments the Prometheus counter `feature_flag_evaluations_total{flag_key, result}`; `result="default"` means no row existed and the answer came from the fallback or the built-in "off".

### Project and role allow-lists only work where the caller passes them

A check can only honour `enabled_projects` and `enabled_roles` if the code that asks supplies a project and a user. The UI asks through `GET /api/v1/feature-flags/{key}/status?project_id=...` with the active project and the signed-in user, so UI gates honour both (the "All Projects" view asks without a project). Many server-side checks pass neither. For those flags, a non-empty project or role list turns the flag **off everywhere**, and a rollout percentage selects all-or-nothing through the fixed `global` bucket. The [catalogue](#flag-catalogue) marks each flag's scope:

- **project**: the server passes the project (and sometimes the user); allow-lists work.
- **global**: the server passes neither; use `enabled_global` only.
- **UI**: read only by the frontend; allow-lists work.

### Environment fallback

For four keys, a missing row falls back to an environment variable, read straight from the process environment (`os.getenv`; only `1`, `true`, `yes`, `on` mean on). Once a row exists, the row wins and the variable is ignored.

| Flag | Fallback variable |
|---|---|
| `knowledge_rag` | `KNOWLEDGE_RAG_ENABLED` |
| `contract_validation` | `AIQ_CONTRACT_VALIDATION_ENABLED` |
| `change_ownership` | `AIQ_CHANGE_OWNERSHIP_ENABLED` |
| `async_decision_report_supersession` | `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` |

`knowledge_rag` always has a row (migration 0062 seeds it from the value of `KNOWLEDGE_RAG_ENABLED` at migration time), so in practice its fallback no longer applies.

### Managing flags

- **UI:** Settings, Feature flags (`/settings/feature-flags`), ADMIN only. `knowledge_rag` also has a switch at `/settings/ai`.
- **API:** `/api/v1/feature-flags` (ADMIN): `GET` lists, `POST` creates (`key` must match `^[a-z][a-z0-9_]*$`, 2-80 characters), `GET`/`PATCH`/`DELETE /{key}` read, update and delete. `GET /{key}/status` (any signed-in user) returns `{key, enabled}` for the caller. Reference: [feature-flags API](../reference/api/feature-flags.md).
- **Legacy API:** `GET /api/v1/settings/flags` (QA_LEAD), `PUT` and `DELETE /api/v1/settings/flags/{key}` (ADMIN) go through the older facade `backend/app/services/feature_flag_service.py`, which only reads and writes `enabled_global` (a `PUT` creates the row if missing). Its `is_enabled` is fail-open (an unknown key counts as on) and its seven "well-known" key constants (`secure_settings`, `onboarding_wizard`, `demo_mode`, `evidence_provenance`, `snapshot_caching`, `cluster_ranking`, `access_audit`) are not read by any code; nothing calls that `is_enabled`. Use `/api/v1/feature-flags`.
- Every create, update and delete through either API writes a settings audit entry.
- To turn on a flag that has no row (for example `defect_commander`), create it: `POST /api/v1/feature-flags` with `{"key": "defect_commander", "enabled_global": true}`, or "Add flag" in the UI.

```bash
# Turn on outbound webhooks for the whole instance (ADMIN JWT).
curl -X PATCH "$TL_URL/api/v1/feature-flags/outbound_webhooks" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled_global": true}'
```

## Flag catalogue

Default = the value on a fresh install after `alembic upgrade head`. "Not seeded" flags have no row and are off unless noted.

### User-visible features and integrations

| Flag | Seeded by | Default | Scope | What it gates (reader) |
|---|---|---|---|---|
| `manual_upload` | 0092 (off); 0201 turns it on for new installs | **on** for a new install; an upgraded install keeps its stored value (off unless an admin changed it) | UI | The "Upload report" button on Runs and the `/runs?upload=1` deep link (`frontend/src/pages/RunsPage.tsx`; the user also needs QA_ENGINEER or higher). The API (`POST /api/v1/ingest/file`) and the CLI are never gated by it. See [below](#manual_upload-on-new-installs-only). |
| `ask_ai_chat` | 0100 | on | UI | The "Ask AI" sidebar entry and "Ask AI about this run" on run detail (`Sidebar.tsx`, `RunDetailPage.tsx`). The entry is also hidden while the analysis mode is `rules`. The `/chat` route itself stays reachable. |
| `cypress_ingest` | 0063 (off), 0099 (on) | on | project | Parsing Cypress / Mochawesome JSON reports. Off: such uploads answer 503 (`backend/app/routers/ingest.py`). |
| `playwright_ingest` | 0063 (off), 0099 (on) | on | project | Parsing Playwright JSON reports; same behaviour. |
| `flaky_auto_quarantine` | 0065 | **off** | global (the flaky page reads it per project) | The whole quarantine workflow: proposals, approvals, rechecks and the CI quarantine manifest (`GET /api/v1/projects/{project_id}/quarantine/manifest`, empty while off) that `testlookup ci-verdict` reads, so with the flag off every failure counts as real (`backend/app/services/flaky_quarantine_service.py`). While off, the Flaky tests page says quarantine is off for the project. |
| `outbound_webhooks` | 0068 | **off** | global | Outbound webhook deliveries, test sends and replays (`backend/app/services/webhook_service.py`). Also requires `AI_OFFLINE_MODE=false`. See [Outbound webhooks](integrations.md#outbound-webhooks). |
| `github_checks` | 0067 | **off** | global | GitHub check runs and the sticky PR comment (`backend/app/services/github_checks_service.py`). Also requires `AI_OFFLINE_MODE=false`. Setup: the GitHub section of [user-guide/administration.md](../../user-guide/administration.md). |
| `gitlab` | 0111 | **off** | global | GitLab merge-request notes and commit statuses (`backend/app/services/gitlab_integration_service.py`). Also requires `AI_OFFLINE_MODE=false`. Setup: the GitLab section of [user-guide/administration.md](../../user-guide/administration.md). |
| `release_compliance_pack` | 0066 | **off** | global | Release compliance-pack generation; 503 while off (`backend/app/services/compliance_pack_service.py`). See [user-guide/compliance.md](../../user-guide/compliance.md). |
| `release_phase_gate_enforcement` | 0158 | **off** | global | Completing a release phase consults the phase gate: anything but GO is refused with 409 and the gate's blocking reasons, unless the caller supplies an override reason (the override is audited) (`backend/app/services/release_service.py`). Off: phases complete without the check. Reading the flag fails to off. |
| `weekly_retro_digest` | 0069 | **off** | global (the digests page reads it per project) | The "weekly retro" digest type (`backend/app/services/retro_digest_service.py`, `frontend/src/pages/settings/DigestsPage.tsx`). |
| `llm_cost_budget` | 0064 | **off** | global | LLM spend metering and per-project caps. See [LLM cost budget](#llm-cost-budget). |
| `perf_regression_detection` | 0069 | **off** | global | Per-test duration regression detection with rolling statistics (`backend/app/services/perf_regression_service.py`). |
| `knowledge_rag` | 0062 | value of `KNOWLEDGE_RAG_ENABLED` when the migration ran (`false` by default) | global | Knowledge sources and RAG-grounded test generation: the knowledge-source APIs answer 503 while off, and retrieval returns nothing (`knowledge_source_service.py`, `rag_retrieval_service.py`, `rag_eval_service.py`). Toggle at `/settings/ai`. See [user-guide/knowledge-base.md](../../user-guide/knowledge-base.md). |
| `rag_faithfulness_gate` | 0069 | **off** | global | Faithfulness scoring of RAG-generated test cases and the auto-accept threshold (`backend/app/services/rag_faithfulness_service.py`). Only meaningful with `knowledge_rag` on. |
| `test_case_rich_detail` | 0142 | on | project | Versioned test-case detail metadata (definition, provenance, steps, attachments, links) on test-case views (`backend/app/services/runs_service.py`). |
| `test_case_lifecycle_v2` | 0144 | on | project + user | The governed lifecycle for authored test cases (review and approval states, the Approvals inbox) in the API (`test_case_lifecycle_service.py`, 404 while off) and the UI (`TestManagementPage.tsx`, `inbox/CaseApprovals.tsx`). |
| `retention_activation_nudge` | 0145 | on | UI | Kill switch for the first-run prompt on Settings → Retention that invites an operator to enable data retention (`frontend/src/components/retention/RetentionActivationNudge.tsx`, read with `useFeatureFlagStatus` for the active project). Off: the prompt never shows. On: it shows until retention is enabled or the user dismisses it. While the flag is loading, or if reading it fails, the prompt stays hidden. It never enables or disables retention itself. |

### Agent pipeline stages and background analysis

These are evaluated per project (and, for pipeline stages, once per pipeline run, with the cache bypassed; the value is frozen for that run). None is seeded.

| Flag | Default | Fallback variable | What it gates (reader) |
|---|---|---|---|
| `defect_commander` | off | none | The mutating pipeline stage that promotes high-confidence failure clusters to defects and files Jira issues (`backend/app/agents/defect_commander.py`; wired in `backend/app/agents/workflow.py`). Jira filing also needs Jira configured and `AI_OFFLINE_MODE=false`. The on-demand `POST /api/v1/agents/defect-command` is not gated by this flag. |
| `contract_validation` | off | `AIQ_CONTRACT_VALIDATION_ENABLED` | The API-contract specialist stage. |
| `log_intelligence` | off | none | The log and trace evidence stage. |
| `regression_watchman` | off | none | The stage that classifies failures as new regression, known-flaky recurrence or environmental anomaly. |
| `change_ownership` | off | `AIQ_CHANGE_OWNERSHIP_ENABLED` | The baseline-diff and ownership stage. |
| `async_decision_report_supersession` | off | `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | Asynchronous supersession of decision reports in deep pipelines (`workflow.py`, `workflow_evaluation_service.py`). |
| `cluster_investigator_children` | off | none | Child investigations per failure cluster, in shadow mode, also subject to the investigator agent policy (`backend/app/services/cluster_investigation_orchestrator.py`). |
| `flaky_detection_timing` | off | none | The beat tasks that screen new and modified tests and sweep flaky-detection timing per project (`backend/app/worker/tasks.py`, `screen_new_test_fingerprints` and the timing sweep). |

The stage table in the in-app guide (`/docs/ai-agents`) describes what each stage contributes to a report.

### Retired flags

Migration 0192 seeded six visualization rollout flags (`viz_report_context`, `viz_multi_filters`, `viz_chart_data_api`, `viz_advanced_charts`, `viz_customize`, `viz_three_d`); migrations 0196 and 0197 deleted the rows once the features shipped for everyone. Nothing reads them; `backend/app/core/viz_flags.py` and `contracts/viz/flags.json` keep the keys only as a retirement record.

## `manual_upload` on new installs only

Migration 0092 seeded `manual_upload` off, which hid the Upload button that the getting-started guides lead with. Migration 0201 turns it on **only for a new install**: it flips the row to on when the database has no users and no projects yet (the state `alembic upgrade head` runs in before the first admin exists) and the row still holds 0092's value. An upgraded install keeps whatever it has. To turn it on for an existing install:

```bash
curl -X PATCH "$TL_URL/api/v1/feature-flags/manual_upload" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled_global": true}'
```

## LLM cost budget

`llm_cost_budget` (off by default) switches on metering and per-project spend caps for LLM calls (`backend/app/services/llm_cost_budget.py`, `llm_cost_reservation.py`). While it is off nothing is metered against a cap and no analysis is downgraded, even if a project has a quota configured; the user guide's description of budgets downgrading analysis applies only with the flag on.

With the flag on:

- Usage is metered per project per **calendar month (UTC)** from the cost of each agent stage. Cost comes from the price table in `backend/app/services/llm_pricing.py`, overridable with `LLM_PRICE_OVERRIDES` ([configuration](configuration.md#ai-and-llm-providers)). Local providers (Ollama, vLLM and the other local ones) are metered as self-hosted (zero cost).
- A project's quota (`GET`/`PUT /api/v1/projects/{project_id}/llm-quota`) has `hard_cap_usd`, `soft_warn_threshold_pct` (default 100) and `at_cap_action`: `SOFT_WARN` (log and metric only), `AUTO_DOWNGRADE_TO_ML` (default), `AUTO_DOWNGRADE_TO_RULES`, or `HARD_BLOCK` (no LLM work). The action fires once spend crosses `soft_warn_threshold_pct` of the hard cap.
- Reaching the hard cap emits the `quota.exceeded` [outbound webhook](integrations.md#outbound-webhooks) event (when webhooks are on).
- Usage: `GET /api/v1/projects/{project_id}/llm-usage` and `/llm-usage/history`; instance overview: `GET /api/v1/billing/overview`; UI: `/settings/billing`.
- With a cap configured, the cap check fails closed: if the meter cannot be read, LLM work is refused rather than run unmetered.

This flag is separate from the agent policy and budgets configured at `/settings/ai-agents` (see the administration guide).

## Environment switches that behave like flags

These are environment variables, not rows; change them in the deployment and restart. Details in [Configuration](configuration.md).

| Variable | Default | Effect |
|---|---|---|
| `AI_OFFLINE_MODE` | `true` | Blocks remote LLM providers and most outbound integrations, regardless of flags (`outbound_webhooks`, `github_checks`, `gitlab` and `defect_commander`'s Jira filing all stay inert while it is on). `/settings/ai` can tighten it, never loosen it. |
| `REVIEW_GATE_ENFORCED` | `false` | Human review gate for distributing AI reports (PDF export, share links, release-readiness value, `release.decided` webhooks). Off: would-be refusals are only audited. |
| `AIQ_GAP_REFINEMENT_ENABLED` | `false` | Runs the `gap_detection` and `report_refinement` deep-pipeline stages. |
| `AIQ_CONTRACT_VALIDATION_ENABLED`, `AIQ_CHANGE_OWNERSHIP_ENABLED`, `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | `false` | [Environment fallbacks](#environment-fallback) for the matching flags while no row exists. |
| `KNOWLEDGE_RAG_ENABLED` | `false` | Seeds `knowledge_rag` at migration time. |
| `DEEP_INVESTIGATION_ENABLED` | `true` | Allows deep investigations of a run (422 when false). |
| `FINETUNE_ENABLED` | `false` | Fine-tuning export and trigger tasks. |
| `JIRA_ENABLED`, `SLACK_ENABLED`, `TEAMS_ENABLED`, `SMTP_ENABLED`, `SPLUNK_ENABLED`, `OCP_ENABLED` | `false` | Integration master switches (most can also be set in the UI; see [Integrations](integrations.md)). |
| `SSO_ENABLED`, `SCIM_ENABLED` | `false` | SAML SSO and SCIM provisioning. |
| `LIVE_EVENTS_REQUIRE_PROJECT_KEY` | `true` | Live events accept only project-scoped API keys. |
| `AI_REPORT_EVAL_ALLOW_CALLER_CORPUS` | `false` | Test-only: lets report evaluation take a caller-supplied corpus. |
| `HIGH_VOLUME_AUTO_DETECT_ENABLED`, `LIVE_SESSION_DRAIN_ENABLED`, `INGESTION_DLQ_ENABLED` | `true` | Ingestion safety mechanisms; leave on. |
| `ML_MODEL_SYNC_ENABLED` | `false` (`true` in the Kubernetes base) | Share retrained ML models across pods through object storage. |
