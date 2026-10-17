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
3. **No row.** The flag is **off**, except for the five keys with an [environment fallback](#environment-fallback).

Every evaluation increments the Prometheus counter `feature_flag_evaluations_total{flag_key, result}`; `result="default"` means no row existed and the answer came from the fallback or the built-in "off".

### Project and role allow-lists only work where the caller passes them

A check can only honour `enabled_projects` and `enabled_roles` if the code that asks supplies a project and a user. The UI asks through `GET /api/v1/feature-flags/{key}/status?project_id=...` with the active project and the signed-in user, so UI gates honour both (the "All Projects" view asks without a project). Many server-side checks pass neither. For those flags, a non-empty project or role list turns the flag **off everywhere**, and a rollout percentage selects all-or-nothing through the fixed `global` bucket. The [catalogue](#flag-catalogue) marks each flag's scope:

- **project**: the server passes the project (and sometimes the user); allow-lists work.
- **global**: the server passes neither; use `enabled_global` only.
- **UI**: read only by the frontend; allow-lists work.

### Environment fallback

For five keys, a missing row falls back to an environment variable, read straight from the process environment (`os.getenv`; only `1`, `true`, `yes`, `on` mean on). Once a row exists, the row wins and the variable is ignored.

| Flag | Fallback variable |
|---|---|
| `knowledge_rag` | `KNOWLEDGE_RAG_ENABLED` |
| `contract_validation` | `AIQ_CONTRACT_VALIDATION_ENABLED` |
| `change_ownership` | `AIQ_CHANGE_OWNERSHIP_ENABLED` |
| `async_decision_report_supersession` | `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` |
| `aiq_gap_refinement` | `AIQ_GAP_REFINEMENT_ENABLED` |

All five now have a row: migration 0062 seeds `knowledge_rag` from the value of `KNOWLEDGE_RAG_ENABLED` at migration time, and migrations 0208 / 0209 seed the four pipeline keys, each taking its variable's value at migration time into account (see [Agent pipeline stages](#agent-pipeline-stages-and-background-analysis)). In practice the fallbacks apply only to a row an administrator deleted. After the migration, change these flags in Settings, Feature flags, not in the environment.

### Managing flags

- **UI:** Settings, Feature flags (`/settings/feature-flags`), ADMIN only. `knowledge_rag` also has a switch at `/settings/ai`.
- **API:** `/api/v1/feature-flags` (ADMIN): `GET` lists, `POST` creates (`key` must match `^[a-z][a-z0-9_]*$`, 2-80 characters), `GET`/`PATCH`/`DELETE /{key}` read, update and delete. `GET /{key}/status` (any signed-in user) returns `{key, enabled}` for the caller. Reference: [feature-flags API](../reference/api/feature-flags.md).
- **Legacy API:** `GET /api/v1/settings/flags` (QA_LEAD), `PUT` and `DELETE /api/v1/settings/flags/{key}` (ADMIN) go through the older facade `backend/app/services/feature_flag_service.py`, which only reads and writes `enabled_global` (a `PUT` creates the row if missing). Its `is_enabled` is fail-open (an unknown key counts as on) and its seven "well-known" key constants (`secure_settings`, `onboarding_wizard`, `demo_mode`, `evidence_provenance`, `snapshot_caching`, `cluster_ranking`, `access_audit`) are not read by any code; nothing calls that `is_enabled`. Use `/api/v1/feature-flags`.
- Every create, update and delete through either API writes a settings audit entry.
- To turn on a flag that has no row (for example `flaky_detection_timing`), create it: `POST /api/v1/feature-flags` with `{"key": "flaky_detection_timing", "enabled_global": true}`, or "Add flag" in the UI.
- To turn a flag on for one project only, switch it on globally (`enabled_global` is the kill switch) and list the project under Edit (`enabled_projects`); the allow-list only narrows a flag that is on.

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
| `flaky_auto_quarantine` | 0065 (off); 0219 turns it on where the row was never touched | **on** for a new install and for an upgraded install whose row is still 0065's untouched seed; a row an admin changed keeps its value | global (the flaky page reads it per project) | The whole quarantine workflow: proposals, approvals, rechecks and the CI quarantine manifest (`GET /api/v1/projects/{project_id}/quarantine/manifest`, empty while off) that `testlookup ci-verdict` reads, so with the flag off every failure counts as real (`backend/app/services/flaky_quarantine_service.py`). On, nothing is quarantined until a QA lead approves a proposal. While off (or limited to other projects), the Flaky tests page says quarantine is off for the project and Failures' Mute is disabled. See [below](#flaky_auto_quarantine-on-unless-an-admin-changed-it). |
| `outbound_webhooks` | 0068 | **off** | global | Outbound webhook deliveries, test sends and replays (`backend/app/services/webhook_service.py`). Also requires `AI_OFFLINE_MODE=false`. See [Outbound webhooks](integrations.md#outbound-webhooks). |
| `github_checks` | 0067 | **off** | global | GitHub check runs and the sticky PR comment (`backend/app/services/github_checks_service.py`). Also requires `AI_OFFLINE_MODE=false`. Setup: the GitHub section of [user-guide/administration.md](../../user-guide/administration.md). |
| `gitlab` | 0111 | **off** | global | GitLab merge-request notes and commit statuses (`backend/app/services/gitlab_integration_service.py`). Also requires `AI_OFFLINE_MODE=false`. Setup: the GitLab section of [user-guide/administration.md](../../user-guide/administration.md). |
| `release_compliance_pack` | 0066 (off); 0212 turns it on for new installs | **on** for a new install; an upgraded install keeps its stored value (off unless an admin changed it) | global (evaluated per project) | Release compliance-pack generation; 503 while off (`backend/app/services/compliance_pack_service.py`). Listing and downloading existing packs (including the releases page's side rail, `GET /api/v1/compliance-packs`) is never gated. See [below](#release-flags-on-new-installs-only) and [user-guide/compliance.md](../../user-guide/compliance.md). |
| `release_phase_gate_enforcement` | 0158 (off); 0212 turns it on for new installs | **on** for a new install; an upgraded install keeps its stored value (off unless an admin changed it) | global (evaluated per project) | Completing a release phase consults the phase gate: anything but GO is refused with 409 and the gate's blocking reasons unless the caller supplies `gate_override_reason`; skipping a phase needs `skip_reason`. Both are recorded in the access audit log and as `release.phase_gate_overridden` / `release.phase_skipped` activity with the reason; the release page asks for the reason in a dialog (`backend/app/services/release_service.py`). Off: phases complete without the check. Reading the flag fails to off. See [below](#release-flags-on-new-installs-only). |
| `weekly_retro_digest` | 0069 | **off** | global (the digests page reads it per project) | The "weekly retro" digest type (`backend/app/services/retro_digest_service.py`, `frontend/src/pages/settings/DigestsPage.tsx`). |
| `llm_cost_budget` | 0064 (off); 0207 turns it on for new installs | **on** for a new install; an upgraded install keeps its stored value (off unless an admin changed it) | global | LLM spend metering, per-project caps and the billing overage figures. Same fresh-install rule as [`manual_upload`](#manual_upload-on-new-installs-only). See [LLM cost budget](#llm-cost-budget). |
| `perf_regression_detection` | 0069 (off); 0204 turns it on for new installs | **on** for a new install; an upgraded install keeps its stored value (off unless an admin changed it) | project | Per-test duration regressions: each run's PASSED tests judged against their rolling baselines (a spike is 3σ above the mean over 10+ runs), the baselines fed once per run on ingest, and the nightly baseline refresh (`backend/app/services/perf_regression_service.py`, `duration_regression_service.py`). Off: the run page's "Did anything get slower?" says detection is off instead of listing tests, and only suite regressions (no flag) are checked. Same fresh-install rule as [`manual_upload`](#manual_upload-on-new-installs-only). |
| `knowledge_rag` | 0062 | value of `KNOWLEDGE_RAG_ENABLED` when the migration ran (`false` by default) | global | Knowledge sources and RAG-grounded test generation: the knowledge-source APIs answer 503 while off, and retrieval returns nothing (`knowledge_source_service.py`, `rag_retrieval_service.py`, `rag_eval_service.py`). Toggle at `/settings/ai`. See [user-guide/knowledge-base.md](../../user-guide/knowledge-base.md). |
| `rag_faithfulness_gate` | 0069 | **off** | global | Faithfulness scoring of RAG-generated test cases and the auto-accept threshold (`backend/app/services/rag_faithfulness_service.py`). Only meaningful with `knowledge_rag` on. |
| `test_case_rich_detail` | 0142 | on | project | Versioned test-case detail metadata (definition, provenance, steps, attachments, links) on test-case views (`backend/app/services/runs_service.py`). |
| `test_case_lifecycle_v2` | 0144 | on | project + user | The governed lifecycle for authored test cases (review and approval states, the Approvals inbox) in the API (`test_case_lifecycle_service.py`, 404 while off) and the UI (`TestManagementPage.tsx`, `inbox/CaseApprovals.tsx`). |
| `retention_activation_nudge` | 0145 | on | UI | Kill switch for the first-run prompt on Settings → Retention that invites an operator to enable data retention (`frontend/src/components/retention/RetentionActivationNudge.tsx`, read with `useFeatureFlagStatus` for the active project). Off: the prompt never shows. On: it shows until retention is enabled or the user dismisses it. While the flag is loading, or if reading it fails, the prompt stays hidden. It never enables or disables retention itself. |

### Agent pipeline stages and background analysis

These are evaluated per project (and, for pipeline stages, once per pipeline run, with the cache bypassed; the value is frozen for that run, and a resumed run keeps it). Settings, Feature flags lists the seeded stage flags together under "Agent stages (deep pipeline)". A stage that is off is skipped and the run says why ("... feature flag is off for this project").

#### Seeded by 0208 / 0209

"Legacy variable on" means the variable was `1`, `true`, `yes` or `on` in the environment of the process that ran `alembic upgrade` (the value it had been switching the stage with while no row existed).

| Flag | Seeded by | Default | Fallback variable | What it gates (reader) |
|---|---|---|---|---|
| `contract_validation` | 0208 | **on** for a new install; an upgraded install has it off unless its legacy variable was on | `AIQ_CONTRACT_VALIDATION_ENABLED` | The API-contract specialist stage (read-only). |
| `log_intelligence` | 0208 | **on** for a new install; off on an upgraded install | none | The log and trace evidence stage (read-only; needs Splunk, which stays off while `AI_OFFLINE_MODE` is on). |
| `regression_watchman` | 0208 | **on** for a new install; off on an upgraded install | none | The stage that classifies failures as new regression, known-flaky recurrence or environmental anomaly (read-only; may use the LLM). |
| `change_ownership` | 0208 | **on** for a new install; an upgraded install has it off unless its legacy variable was on | `AIQ_CHANGE_OWNERSHIP_ENABLED` | The baseline-diff and ownership stage (read-only). |
| `defect_commander` | 0208 | **off** on every install (owner decision D20) | none | The MUTATING pipeline stage that promotes high-confidence failure clusters to defects and files Jira issues (`backend/app/agents/defect_commander.py`; wired in `backend/app/agents/workflow.py`). Off until Act mode routes its effects through approvals; the stage also needs its agent enabled in Act mode under Settings › AI agents (it starts disabled, in Shadow mode), otherwise the plan says the agent is disabled. Jira filing also needs Jira configured and `AI_OFFLINE_MODE=false`. The on-demand `POST /api/v1/agents/defect-command` is not gated by this flag. |
| `async_decision_report_supersession` | 0208 | **off**, unless its legacy variable was on | `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED` | Asynchronous supersession of decision reports in deep pipelines (`workflow.py`, `workflow_evaluation_service.py`). |
| `aiq_gap_refinement` | 0209 | **on** for every install, new or upgraded, unless `AIQ_GAP_REFINEMENT_ENABLED` was set when the migration ran (then its value) | `AIQ_GAP_REFINEMENT_ENABLED` | The deterministic `gap_detection` and `report_refinement` deep-pipeline stages (no LLM, no egress); they add the "Analysis coverage" line to decision reports. |

The "new install" rule is the one of [`manual_upload`](#manual_upload-on-new-installs-only): the database has no user and no project when the migration runs. **An existing install** (for example one upgraded in place) therefore gets the four read-only stage rows OFF; switch them on after the upgrade:

```bash
for key in contract_validation log_intelligence regression_watchman change_ownership; do
  curl -X PATCH "$TL_URL/api/v1/feature-flags/$key" \
    -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
    -d '{"enabled_global": true}'
done
```

A row that existed before the migration (created by hand) is never changed by it. Downgrading 0208 / 0209 deletes a row only while it still equals its seed; a row an administrator edited (including a toggle) stays.

#### Not seeded

| Flag | Default | Fallback variable | What it gates (reader) |
|---|---|---|---|
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

## Release flags on new installs only

Migration 0066 seeded `release_compliance_pack` off and 0158 seeded `release_phase_gate_enforcement` off, so a new install could neither generate a compliance pack nor ask for a reason when a phase was completed over a failing gate. Migration 0212 turns both on **only for a new install**, by the rule of [`manual_upload`](#manual_upload-on-new-installs-only): no users and no projects yet, and each row still holds its seed's off. An upgraded install keeps whatever it has. To turn them on for an existing install (ADMIN token):

```bash
for key in release_phase_gate_enforcement release_compliance_pack; do
  curl -X PATCH "$TL_URL/api/v1/feature-flags/$key" \
    -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
    -d '{"enabled_global": true}'
done
```

`GET /api/v1/feature-flags/release_phase_gate_enforcement/status` then reports `enabled: true`. With enforcement on, a release manager moving a phase to Completed while its gate is not GO, or to Skipped, gets a dialog quoting the gate and asking for a reason; the reason is stored with the change.

## `flaky_auto_quarantine` on unless an admin changed it

Migration 0065 seeded `flaky_auto_quarantine` off, so quarantine, the Flaky tests page's main workflow, was unavailable until an administrator found the flag. Migration **0219** turns it on wherever the row is still exactly 0065's untouched seed: `enabled_global = false`, no `updated_by_user_id`, and `updated_at = created_at` (every save through the API or UI stamps the actor and moves `updated_at`). That covers a new install and an upgraded install nobody configured; a row an administrator changed (switched on or off, given a project list, toggled at any time) keeps its value. The flipped row's description gains "Default ON since migration 0219: a test is quarantined only after a QA lead approves it." The API sees the change within 30 seconds (the cache).

Turning it on quarantines nothing by itself: a QA lead must approve each proposal, and only then do the release gate and `testlookup ci-verdict` leave that test's failures out. To turn it off for the whole instance, or limit it to some projects (ADMIN token):

```bash
# Off everywhere.
curl -X PATCH "$TL_URL/api/v1/feature-flags/flaky_auto_quarantine" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled_global": false}'

# On only for the listed projects (the allow-list narrows a flag that is on).
curl -X PATCH "$TL_URL/api/v1/feature-flags/flaky_auto_quarantine" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled_global": true, "enabled_projects": ["<project-id>"]}'
```

Either is an admin change, so a later upgrade leaves it alone. Downgrading 0219 turns the flag back off only on the row 0219 flipped and nobody has edited since.

## LLM cost budget

`llm_cost_budget` switches on metering and per-project spend caps for LLM calls (`backend/app/services/llm_cost_budget.py`, `llm_cost_reservation.py`). It is **on for a new install** (migration 0207, the fresh-install rule of [`manual_upload`](#manual_upload-on-new-installs-only)); an upgraded install keeps its stored value, which is off unless an administrator changed it. While it is off nothing is metered against a cap and no analysis is downgraded, even if a project has a quota configured; the user guide's description of budgets downgrading analysis applies only with the flag on.

With the flag on:

- Usage is metered per project per **calendar month (UTC)** from the cost of each agent stage. Cost comes from the price table in `backend/app/services/llm_pricing.py`, overridable with `LLM_PRICE_OVERRIDES` ([configuration](configuration.md#ai-and-llm-providers)). Local providers (Ollama, vLLM and the other local ones) are metered as self-hosted (zero cost).
- A project's quota (`GET`/`PUT /api/v1/projects/{project_id}/llm-quota`) has `hard_cap_usd`, `soft_warn_threshold_pct` (default 100) and `at_cap_action`: `SOFT_WARN` (log and metric only), `AUTO_DOWNGRADE_TO_ML` (default), `AUTO_DOWNGRADE_TO_RULES`, or `HARD_BLOCK` (no LLM work). The action fires once spend crosses `soft_warn_threshold_pct` of the hard cap.
- Reaching the hard cap emits the `quota.exceeded` [outbound webhook](integrations.md#outbound-webhooks) event (when webhooks are on).
- `included_usd` ("Included per month") and `overage_rate_usd` ("Overage rate") are billing-dashboard figures and are never enforced: spend up to `included_usd` is covered, spend above it is overage, and overage x rate is the charge. `GET .../llm-usage` and `GET /api/v1/billing/overview` return `covered_usd`, `overage_usd`, `overage_charge_usd` and a linear month-end projection (`projected_period_cost_usd`, `projected_overage_usd`, `projected_overage_charge_usd`, `projection_basis`). With `included_usd` at 0 (the form default: no allowance configured) those figures are `null`, never a charge; a project without an enabled quota has all of them `null`.
- Usage: `GET /api/v1/projects/{project_id}/llm-usage` and `/llm-usage/history`; instance overview: `GET /api/v1/billing/overview`; UI: `/settings/billing`.
- With a cap configured, the cap check fails closed: if the meter cannot be read, LLM work is refused rather than run unmetered.

This flag is separate from the agent policy and budgets configured at `/settings/ai-agents` (see the administration guide).

## Environment switches that behave like flags

These are environment variables, not rows; change them in the deployment and restart. Details in [Configuration](configuration.md).

| Variable | Default | Effect |
|---|---|---|
| `AI_OFFLINE_MODE` | `true` | Blocks remote LLM providers and most outbound integrations, regardless of flags (`outbound_webhooks`, `github_checks`, `gitlab` and `defect_commander`'s Jira filing all stay inert while it is on). `/settings/ai` can tighten it, never loosen it. |
| `REVIEW_GATE_ENFORCED` | `false` | Human review gate for distributing AI reports (PDF export, share links, release-readiness value, `release.decided` webhooks). Off: would-be refusals are only audited. |
| `AIQ_CONTRACT_VALIDATION_ENABLED`, `AIQ_CHANGE_OWNERSHIP_ENABLED`, `AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED`, `AIQ_GAP_REFINEMENT_ENABLED` | `false` | Read once by migrations 0208 / 0209 for the seed value of the matching flag, then only as [environment fallbacks](#environment-fallback) while no row exists. The flags themselves are switched in Settings, Feature flags. |
| `KNOWLEDGE_RAG_ENABLED` | `false` | Seeds `knowledge_rag` at migration time. |
| `DEEP_INVESTIGATION_ENABLED` | `true` | Default for the Deep Investigation toggle in Settings, AI. A value saved there wins in both directions (applies within a minute). Off: every way of starting a deep investigation answers 422 `Deep investigation is disabled in Settings › AI.` |
| `FINETUNE_ENABLED` | `false` | Default for the Fine-Tuning Pipeline toggle in Settings, AI; a value saved there wins in both directions. Gates the export-triggered fine-tuning tasks only; the weekly training-data export is not gated. |
| `JIRA_ENABLED`, `SLACK_ENABLED`, `TEAMS_ENABLED`, `SMTP_ENABLED`, `SPLUNK_ENABLED`, `OCP_ENABLED` | `false` | Integration master switches (most can also be set in the UI; see [Integrations](integrations.md)). |
| `SSO_ENABLED`, `SCIM_ENABLED` | `false` | SAML SSO and SCIM provisioning. |
| `LIVE_EVENTS_REQUIRE_PROJECT_KEY` | `true` | Live events accept only project-scoped API keys. |
| `AI_REPORT_EVAL_ALLOW_CALLER_CORPUS` | `false` | Test-only: lets report evaluation take a caller-supplied corpus. |
| `HIGH_VOLUME_AUTO_DETECT_ENABLED`, `LIVE_SESSION_DRAIN_ENABLED`, `INGESTION_DLQ_ENABLED` | `true` | Ingestion safety mechanisms; leave on. |
| `ML_MODEL_SYNC_ENABLED` | `true` | Share retrained ML models across pods and containers through object storage, so a model trained by the worker (nightly or "Train now") reaches the API. Best-effort: with the store unreachable each attempt logs a warning and the process keeps its local model. |
