# HANDOVER — Activity tab (epic ACT)

Implementation handover for an autonomous coding agent (Codex). Written 2026-09-05 against
`main` at `df246f7f`. This file is **tracked** on purpose: `docs/` and `CLAUDE.md` are
gitignored, so a clone can only see what is in here. Treat this document as the spec of
record. Where it disagrees with the local-only `docs/EPIC_ACTIVITY_TAB_2026_09.md`, this
file wins.

---

## 0. The prompt to start with

Paste this into the agent as the opening message. Everything it needs is in this file.

> You are implementing epic ACT (the "Activity" tab) in the TestLookup repository at
> `C:\Users\anand\Downloads\Projects\testlookup_new`. Read `HANDOVER_ACTIVITY.md` at the
> repo root in full before writing any code. It contains the spec, the PR plan, the
> conventions this repo enforces in CI, and a list of traps that have already cost this
> project real bugs.
>
> Work the PR plan in section 7 in order, one PR at a time. For each PR: create a branch,
> implement, write the tests named in that PR's acceptance criteria, run the full local
> verification in section 5, add a `CHANGELOG.md` entry, open the PR, and wait for CI to
> go green before starting the next one. Do not batch PRs. Do not skip the verification
> steps. If a step fails, fix the cause rather than narrowing the check.
>
> Before every PR, re-read section 6 (traps) and confirm which of them your change can hit.
> Say which ones you checked in the PR description.
>
> Stop and report if: a migration head conflict cannot be resolved by rebasing, CI fails
> for a reason outside your change, `git push` fails with a TLS error (the user pushes
> manually in that case), or an acceptance criterion turns out to be unsatisfiable as
> written. Otherwise keep going without asking.
>
> Start with PR 1.

---

## 1. What is being built, in one paragraph

A project-scoped **Activity** ledger. One append-only Postgres table records every
meaningful thing that happens inside a project: test runs received and completed, AI
analysis outcomes, release decisions and phase changes, quarantine and defect transitions,
and every configuration, integration and membership change. One write helper is the only
path into it. One cursor-paginated read API, guarded by the existing project-access
dependency, serves a feed page at `/activity` that **any project member** can read, plus a
"Recent activity" panel on `/overview`, a detail drawer, CSV/NDJSON export, and read-only
CLI and MCP tools.

---

## 2. Why — the gaps this closes

Verified against the code at `df246f7f` and the running deployment at
`http://testlookup.local` on 2026-09-05. Do not re-derive these; they are the premise.

- **21 of the 26 surveyed routers that have mutations carry no audit call in the router
  file:** `projects`, `runs`, `ingest`, `flaky_quarantine`, `ownership`,
  `release_gate_policies`, `release_attribution_rules`, `retention`, `api_keys`,
  `saved_views`, `integrations`, `webhooks_outbound`, `notifications`, `github_integration`,
  `gitlab_integration`, `knowledge_sources`, `compliance_packs`, `defect_jira`,
  `agent_actions`, `test_management_cases`, `admin_maintenance`.
  **Read that number carefully:** it is a router-level grep, and three of those routers are
  in fact audited in their service layer — `flaky_quarantine` (via
  `flaky_quarantine_service._audit`, into the wrong table, see below),
  `test_management_cases` (via `test_management_service`) and `gitlab_integration` (via
  `gitlab_integration_service`, the single heaviest audit writer in the codebase). So 21 is
  an upper bound on the gap, not the gap. Do not quote it as the baseline: PR 1's coverage
  guard measures the real one by walking routers *and* the services they call.
- **Test runs emit no lifecycle event of any kind.** The most frequent event in the system
  has no record beyond its row in `test_runs`.
- **The existing Audit Dashboard cannot serve this need.** `/settings/audit` is
  `require_role(QA_LEAD)` on every endpoint; `query_unified_audit` takes `page_size` rows
  *per source* and merges them, so `page` is accepted and ignored and page 2 does not
  exist; and the live first page is dominated by `ws_connect`/`ws_disconnect` pairs and
  duplicated `retention-scheduler purge` rows.
- **Quarantine actions are audited into the wrong table.**
  `flaky_quarantine_service._audit` writes `settings_audit_log`, which has **no project
  column**, so `query_unified_audit` omits it entirely for any non-ADMIN caller.

## 2a. What is deliberately NOT being built

- No migration or replacement of `settings_audit_log`, `access_audit_logs`,
  `test_case_audit_logs`, `identity_events`. **They remain the compliance record.** The new
  table is a derived product feed that links back to them.
- No instance-wide events (SSO, SCIM, global settings, user creation). They have no project
  and stay in the Audit Dashboard.
- No SSE / live push. SWR polling first.
- No per-test-case result events. The ledger records the run, not each test.
- No backfill. The ledger starts at deploy; the UI states the ledger start date per project
  so an empty older range is not misread as "nothing happened".

---

## 3. Data model

### 3.1 Table `project_activity_events` — migration 0159

Current head is `0158_phase_gate_enforcement_flag`. **Re-check the head before writing the
migration** and pick a fresh `down_revision` (see trap T4).

```text
id                 UUID PK (default uuid4)
project_id         UUID NOT NULL  FK projects.id ON DELETE CASCADE
release_id         UUID NULL      FK releases.id ON DELETE SET NULL
occurred_at        TIMESTAMPTZ NOT NULL  server_default now(); producers may pass the true time
recorded_at        TIMESTAMPTZ NOT NULL  server_default now()
category           VARCHAR(20)  NOT NULL
event_type         VARCHAR(60)  NOT NULL
schema_version     SMALLINT     NOT NULL DEFAULT 1
actor_type         VARCHAR(20)  NOT NULL
actor_id           UUID NULL      FK users.id ON DELETE SET NULL
actor_name         VARCHAR(200) NULL   -- snapshot; survives user deletion
actor_ref          VARCHAR(120) NULL   -- api key prefix, agent name, celery task name
entity_type        VARCHAR(40)  NOT NULL
entity_id          VARCHAR(120) NOT NULL  -- TEXT, not UUID: live sessions use slugs
entity_label       VARCHAR(300) NULL   -- snapshot for rendering after deletion
target_type        VARCHAR(40)  NULL
target_id          VARCHAR(120) NULL
summary            VARCHAR(500) NOT NULL  -- human sentence, redacted, searchable
diff               JSONB NULL   -- {"before":…, "after":…, "changed_fields":[…]} redacted at write
context            JSONB NULL   -- small facts: counts, reason, PR number, ticket key
source_table       VARCHAR(40)  NULL   -- compliance row this mirrors
source_id          UUID NULL
request_id         VARCHAR(64)  NULL   -- correlates with structlog request id
group_key          VARCHAR(120) NULL   -- producer-set, for bulk bursts and dedup
```

Vocabularies (CHECK constraints on the first two, validated in Python for `event_type`):

- `category`: `runs | analysis | release | quality | configuration | membership |
  test_management | integration | agent | system`
- `actor_type`: `user | api_key | service_account | system | agent`
- `entity_type`: `run | test | suite | release | policy | attribution_rule | ownership_rule
  | quarantine | defect | project | member | integration | webhook | api_key |
  retention_policy | saved_view | knowledge_source | agent_action | export`

`event_type` is **not** a DB enum — it is validated against the Python registry so adding
an event needs no migration.

### 3.2 Indexes

All keyed for the feed's keyset order, `occurred_at DESC, id DESC`:

```text
ix_pae_project_time           (project_id, occurred_at DESC, id DESC)
ix_pae_project_category_time  (project_id, category, occurred_at DESC, id DESC)
ix_pae_project_entity         (project_id, entity_type, entity_id, occurred_at DESC)
ix_pae_project_actor          (project_id, actor_id, occurred_at DESC)
ix_pae_project_release        (project_id, release_id, occurred_at DESC) WHERE release_id IS NOT NULL
ix_pae_summary_trgm           GIN (summary gin_trgm_ops)     -- for ?q=
```

The migration creates `pg_trgm` idempotently (`CREATE EXTENSION IF NOT EXISTS pg_trgm`).
**Filters are ANDed columns on one table — never an OR across tables** (a 5-way OR over 3
tables previously forced a Seq Scan; see trap T9).

### 3.3 Event registry — `backend/app/services/activity/events.py`

One frozen mapping `event_type -> (category, entity_type, summary_template,
allowed_actor_types, write_mode)` where `write_mode` is `outcome` or `attempt`. Producers
cannot emit an unregistered name: raise `UnknownActivityEvent` under test, drop and count
in production. The registry is also the source for the frontend filter list, served by
`GET /api/v1/activity/event-types`.

Naming rule: `<object>.<past-tense verb>`, lowercase, objects singular, matching
`^[a-z_]+\.[a-z_]+$`, at most 60 characters.

| Category | Event types (v1) |
|---|---|
| runs | `run.received` `run.completed` `run.ingest_failed` `run.deleted` `run.release_attached` `run.live_started` `run.live_finalized` `run.live_recovered` |
| analysis | `analysis.completed` `analysis.failed` `analysis.report_superseded` `investigation.started` `investigation.completed` |
| release | `release.created` `release.activated` `release.closed` `release.decided` `release.decision_overridden` `release.phase_advanced` `release.phase_skipped` `release.phase_gate_overridden` `release.linked_run` `release.synced_external` |
| quality | `quarantine.requested` `quarantine.approved` `quarantine.rejected` `quarantine.released` `defect.promoted` `defect.create_requested` `defect.ticket_created` `flaky.detected` `test.newly_failing` `test.recovered` |
| configuration | `project.created` `project.updated` `project.reset` `policy.created` `policy.updated` `policy.deleted` `policy.activated` `attribution_rule.created` `attribution_rule.updated` `attribution_rule.deleted` `attribution_rule.enabled` `attribution_rule.disabled` `ownership_rule.created` `ownership_rule.updated` `ownership_rule.deleted` `ownership_rules.bulk_imported` `codeowners.imported` `team_channel.set` `team_channel.removed` `retention_policy.updated` `retention.purge_executed` `saved_view.created` `saved_view.updated` `saved_view.deleted` `saved_view.shared` `feature_flag.project_toggled` |
| membership | `member.added` `member.role_changed` `member.removed` `invitation.sent` |
| test_management | `test_case.created` `test_case.updated` `test_case.status_changed` `test_case.reviewed` `test_case.approved` `test_case.rejected` `test_case.deleted` `test_plan.created` `test_plan.updated` `test_plan.deleted` `suite.sync_deleted` |
| integration | `api_key.created` `api_key.revoked` `webhook.created` `webhook.updated` `webhook.deleted` `webhook.disabled` `integration.connected` `integration.disconnected` `integration.tested` `notification_pref.updated` `knowledge_source.added` `knowledge_source.synced` `knowledge_source.removed` |
| agent | `agent.investigation_run` `agent.fix_proposed` `agent.fix_rejected` `agent.action_approved` `agent.action_executed` `agent.action_denied` |
| system | `activity.exported` `compliance_pack.generated` `report.exported` `report.shared` `maintenance.executed` |

Two hard rules on the registry, both test-enforced:

1. The four names that already exist in the webhook catalog — the module-private
   `_SUPPORTED_EVENTS` dict in `backend/app/services/webhook_service.py`, currently
   `run.completed`, `defect.promoted`, `defect.create_requested`, `release.decided` — are
   reused **verbatim**, so one vocabulary serves webhooks, notifications and the ledger.
   The parity test will need that dict exported under a public name; rename it to
   `SUPPORTED_EVENTS` in PR 1 and update its call sites rather than importing a private.
2. **No `ws_*` and no `heartbeat*` names ever.** That noise is why the existing dashboard is
   unreadable. A registry test asserts their absence.

There is deliberately **no `project.deleted` ledger event**: the table cascades on project
delete, so the row would vanish with the thing it records. `project.deleted` is written to
`access_audit_logs` (the existing compliance table) instead. Say so in a code comment.

### 3.4 API

```text
GET /api/v1/projects/{project_id}/activity
    ?category=&event_type=&actor_id=&actor_type=&entity_type=&entity_id=
    &release_id=&since=&until=&q=&limit=50&cursor=
  -> { items: ActivityEvent[], next_cursor: str|null,
       ledger_started_at: iso|null, window: {since, until} }

GET /api/v1/projects/{project_id}/activity/{event_id}          -> ActivityEvent with diff
GET /api/v1/projects/{project_id}/activity/export?format=csv|ndjson&<same filters>
GET /api/v1/projects/{project_id}/activity/entity/{entity_type}/{entity_id}
GET /api/v1/activity/event-types                               -> registry, auth only
```

`ActivityEvent` (Pydantic v2, in `models/schemas.py`): id, project_id, release_id,
occurred_at, category, event_type, `actor {type, id, name, ref}`,
`entity {type, id, label, href}`, `target {…}|null`, summary, context, has_diff,
`source {table, id}|null`, group_key. `diff` is returned **only** by the single-event
endpoint. `href` is computed server-side from a small entity-type-to-route map so the CLI
and MCP get working links too.

Cursor is base64 of `"{occurred_at_iso}|{id}|{project_hash}"`. A cursor minted for one
project used on another returns 400.

`ledger_started_at` is `min(occurred_at)` for the project and **ignores every filter** (see
trap T3).

---

## 4. Architecture and where the code goes

```text
routers/services (mutations) ──┐
worker tasks (ingest, analysis)├─► activity_service.record(...) ─► project_activity_events
webhook_service.emit_event ────┘        │  registry check                    │
                                        │  redact_dict(diff, context)        │ keyset read
                                        │  summary template                  ▼
                                        │  record_outcome / record_attempt   GET /activity
                                        └─ Prometheus counters               │
                                                                             ▼
                              useActivityFeed (SWR) ─► /activity · Overview panel · drawer
                                                     · CLI `activity list` · MCP tool
```

New files:

| Path | Purpose |
|---|---|
| `backend/app/services/activity/__init__.py` | package |
| `backend/app/services/activity/events.py` | the registry (3.3) |
| `backend/app/services/activity/service.py` | `record(...)`, actor resolution, redaction |
| `backend/app/services/activity/query.py` | keyset read, filters, export serialisation |
| `backend/app/routers/activity.py` | the endpoints in 3.4 |
| `backend/migrations/versions/0159_*.py` | the table |
| `frontend/src/services/activityService.ts` | Axios calls on the shared base |
| `frontend/src/hooks/useActivityFeed.ts` | SWR / `useSWRInfinite` |
| `frontend/src/pages/ActivityPage.tsx` | the feed page |
| `frontend/src/components/activity/*.tsx` | `ActivityRow`, `ActivityFilters`, `ActivityDrawer`, `ActivityGroup` |

Files you will modify (non-exhaustive; the coverage guard in PR 1 will tell you the rest):
`backend/app/bootstrap.py` (register router), `models/postgres.py`, `models/schemas.py`,
`services/retention_service.py` (audit clock), `services/ingestion_pipeline.py`,
`worker/tasks.py`, `services/flaky_quarantine_service.py`, `services/release_service.py`,
`services/release_lifecycle_service.py`, `services/release_phase_gate_service.py`,
`scripts/quality_gate.py`, `scripts/test_quality_gate.py`, `frontend/src/App.tsx`,
`frontend/src/config/routeScope.ts`, `frontend/src/components/layout/Sidebar.tsx`,
`frontend/src/pages/OverviewPage.tsx`, `CHANGELOG.md`.

Five design decisions that are settled — do not re-litigate them:

1. **One writer.** `activity_service.record` is the only insert path. It wraps the existing
   `services/audit_log_service.record_outcome` (caller's session; row dies with a rollback)
   and `record_attempt` (fresh `AsyncSessionLocal`; survives a rollback). The registry picks
   which per event: `project.reset` is an attempt, `policy.updated` is an outcome.
2. **Dual-write, not migration.** Compliance writers keep writing their tables. Where a
   compliance row exists, the ledger row carries `source_table` / `source_id`.
3. **Run events come from the pipeline, not the router.** `run.received` from the ingest
   router (attempt); `run.completed` / `run.ingest_failed` from the worker after
   `finalize_run`, which is already the single guarded completion hook
   (`backend.finalize-run` gate). This keeps the extra INSERT off the ingest request path.
4. **Actor resolution** via a small `ActorRef` dataclass built from `current_user`
   (user / service_account), the API-key dependency (api_key + key prefix), Celery task
   context (system + task name), or the agent runtime (agent + agent name). All five paths
   get a test.
5. **Redaction at write time, never read time.** `redaction_service.redact_dict` on `diff`
   and `context`; summary templates never interpolate a secret-bearing field. Writers for
   API keys, webhooks, integrations and SMTP pass `changed_fields` only.

---

## 5. Local verification — run all of this before every PR

Nothing here needs Docker. **No environment variables are required for backend tests**
since #826, despite what an older `backend/CLAUDE.md` says.

```bash
# backend tests — CI-faithful Python 3.11 venv at repo root, NOT .venv (3.14)
cd backend && ../.venv311/Scripts/python -m pytest tests/ -q

# backend lint — CI's exact scope, both paths (see trap T7)
cd backend && ../.venv311/Scripts/python -m ruff check app/ tests/

# backend types
cd backend && ../.venv311/Scripts/python -m mypy app/ --ignore-missing-imports

# cross-cutting guards + their self-test (both run in CI job `quality-gate`)
python scripts/quality_gate.py
cd scripts && python -m pytest test_quality_gate.py -v

# frontend
cd frontend && npm run test && npm run type-check && npm run lint
```

A full-suite failure that passes in isolation is test-ordering pollution, not flakiness.
Reproduce with the whole suite and grep suspects for `importlib.reload`, `cache_clear`, and
`monkeypatch.setattr` on PEP-562 names such as `engine` and `AsyncSessionLocal`.

`tests/integration/` needs live Postgres and is expected to skip locally. The
`postgres-integration` CI job runs it.

---

## 6. Traps — read before every PR

Each of these has already caused a real defect in this repository. They are ordered by how
likely this epic is to hit them.

**T1 — A `Query(None)` default breaks direct handler calls.** This has bitten five times;
most recently, adding `release_id` to `/assigned-failures/count` broke 9 tests that call the
handler directly, because they receive the `Query` object rather than `None`. The activity
endpoint has **twelve** filter params. Put them in a Pydantic dependency object rather than
twelve `Query(...)` defaults, and grep for direct callers before adding any param.

**T2 — A guard blind to query params passes every such route.** The authorization ratchet
auto-passes a route with no scoped **path** param; nine IDORs hid there. `project_id` must
be a path param on every activity route, and the query must filter on the path value. Add a
contract test that a member of project A gets 403 for B *and* that a `?project_id=B` query
param on A's route is ignored.

**T3 — A global filter poisons an existence probe.** A release filter once made "has this
project EVER had a run?" answer no, so a populated project got the first-run wizard.
`ledger_started_at` must ignore every filter, and the Overview panel must not affect the
first-run wizard's own probe. Re-run the existing OverviewPage first-run test.

**T4 — Alembic head conflicts.** Parallel agent work forks the chain. Re-check the latest
revision immediately before writing the migration and pick a fresh `down_revision`.
`downgrade()` must be really implemented (`database.downgrade-implemented` gate).

**T5 — A new quality-gate guard needs its self-test too.** CI runs
`scripts/test_quality_gate.py`, which pins each guard and the counts. A new `Guard` without
a matching self-test fails CI. A green gate means "no NEW violations" — run the check
function directly and read its output, do not trust the exit code alone.

**T6 — Status filters must use the column's stored vocabulary.** `"quarantined"` versus
stored `QUARANTINED` matched nothing forever (#735). `category` and `actor_type` filters
must compare against exactly what the writer stores. There is a `backend.status-enum-vocab`
gate; make sure it sees these columns.

**T7 — Lint with CI's exact scope.** `ruff check app/ tests/`. A narrower path passes
locally and CI still fails; this cost a red cycle on #991 for one unused variable.

**T8 — Mutation-test your own new tests.** Two ACs depend on it: the retention boundary in
PR 1 and the redaction assertion in PR 2. Delete the behaviour, confirm the test goes red,
restore the *exact* prior code. A test that passes with the feature removed is not a test.

**T9 — An OR across tables defeats every index.** Keep all activity filters as ANDed columns
on the one table. Verify with `EXPLAIN ANALYZE` on seeded data, not by assumption.

**T10 — Invalidate caches after the commit.** The `ledger_started_at` Redis cache must be
invalidated after commit, never mid-transaction, or a reader repopulates it with pre-commit
state.

**T11 — structlog takes kwargs, not stdlib positional `%s`.** `logger.warning("x: %s", exc)`
raises mid-call inside an `except` block and 500s the endpoint. There is a gate, but its
regex is single-line and misses multi-line calls.

**T12 — An e2e skip must fail closed.** 18 tests once skipped silently when the control they
tested was missing. Also: Playwright's `request` fixture has no auth — the token is in
localStorage, not a cookie — so lookups 401, helpers return null, and tests skip while
reporting green. Use the real login helper.

**T13 — A live mutation control writes to the deployment.** Controlling an e2e refusal test
once left an enabled catch-all attribution rule on a real project. If you exercise anything
against `http://testlookup.local`, list and clean up what you created afterwards.

**T14 — Check the function is actually executed.** 102 tests once passed while the function
under test ran zero times. Grep `tests/` for the symbol; read coverage per function, not per
file (`hit<=2` means the body never ran).

**T15 — `git stash` is repo-wide, not per-worktree.** A no-op stash and pop once consumed
another worktree's stash. Prefer a branch commit.

**T16 — Do not use `sleep` poll loops** to wait for CI; foreground sleep is blocked and a
poll loop spins instantly, which once reported "settled" over a job that had never started.
Use `gh run watch`. After a force-push, re-check CI against the **new** head SHA.

---

## 7. PR plan

Fourteen PRs in three slices. **Slices A and B are the committed scope; C is stretch.** One
PR at a time, CI green before the next. Story IDs refer to
`docs/EPIC_ACTIVITY_TAB_2026_09.md` (local-only) and to section 3 above.

### Slice A — the ledger and its producers

**PR 1 — ACT-1 + ACT-2 + ACT-17 (foundation).** Registry, `ProjectActivityEvent` model,
migration 0159, `GET /activity/event-types`, plus the three new guards and their self-tests:
(a) `backend.activity-coverage` — every project-scoped `@router.post|put|patch|delete`
either calls `activity.record` (directly or through its service) or carries an
`# activity: none — <reason>` opt-out comment; (b) `project_activity_events` added to
`backend.audit-write-discipline`; (c) registry-to-frontend filter-list parity, modelled on
`frontend.ingest-formats-match-backend`. Also add the table to the retention audit clock in
`services/retention_service.py`.
*Verify:* `alembic upgrade head` and `downgrade -1` clean on empty and seeded DBs; `EXPLAIN`
shows the composite index in use at 1M seeded rows; the gate fails on an injected
`UPDATE project_activity_events`; retention purge respects `audit_days` floor 365 and the
`audit_days >= runs_days` validation. Mutation-test the retention boundary (T8).
*Note:* baseline files land in `scripts/quality-gate-baselines/` — the coverage guard will
have many existing violations on day one, which is the point of the ratchet.

**PR 2 — ACT-3 (write service).** `activity_service.record`, actor resolution for all five
actor types, redaction, summary rendering, outcome/attempt selection, Prometheus counters
`activity_events_written_total{category}` and `activity_events_dropped_total{reason}`.
*Verify:* an outcome event inside a rolled-back transaction leaves no row; an attempt event
survives the rollback; a payload with `token` / `secret` / `password` / `client_secret` /
`api_key` keys stores the redaction marker and never reaches `summary`; the counters are
actually incremented, satisfying `backend.metrics-are-emitted` (declaration is not
emission). Mutation-test the redaction (T8).

**PR 3 — ACT-4 (run lifecycle).** `run.received`, `run.completed`, `run.ingest_failed`,
`run.deleted`, `run.release_attached`, `run.live_*`, `analysis.completed`,
`analysis.failed`.
*Verify:* uploading a JUnit report produces exactly one `run.received` and one
`run.completed` with `context = {total, passed, failed, skipped, duration_ms,
source_format}` and `entity.href = /runs/{id}`; a failed ingest produces `run.ingest_failed`
with the error class, not a traceback; deletion keeps the event via `entity_label`; live
runs use `canonical_test_run_uuid()` for `entity_id`, not the slug; a retried `finalize_run`
is idempotent via `group_key` (a second identical event within 60s is dropped and counted);
ingestion throughput regression under 2% — and measure with SQL echo off, since
`echo=is_development` adds about 13% per request under `make dev`. Test through the real
pipeline; a mocked test cannot see the constraints.

**PR 4 — ACT-5 (configuration events).** Instrument all 19 routers from section 2.
*Verify:* each emits its registered event with `diff.changed_fields`; the coverage guard
reaches zero opt-outs in these routers; secret-bearing objects emit field names only; bulk
ownership import emits one `ownership_rules.bulk_imported` with `context.count`, not N
events; a PATCH with no effective change emits nothing.

**PR 5 — ACT-6 (mirroring).** Mirror `access_audit_logs` membership actions and
`test_case_audit_logs` actions at their existing write sites, carrying
`source_table`/`source_id`. Suite sync groups by `group_key = suite_sync:{run_id}`.
*Verify:* a negative test that `ws_connect`/`ws_disconnect` never appear in the ledger.

### Slice B — the product surface

**PR 6 — ACT-7 (read API).** The endpoints in 3.4 with `require_project_access()`, keyset
cursor, ANDed filters, trigram `q`, `ledger_started_at` with a 5-minute Redis cache.
*Verify:* T2's contract test; two consecutive pages never overlap or skip when rows are
inserted *between* the page fetches (insert in the test); a cross-project cursor returns
400; `limit` max 200, default 50; `since > until` returns 422; `q` under 3 chars is ignored
and documented; unknown `event_type` returns 422 listing valid names. Perf test belongs in
the nightly job, not the PR job.

**PR 7 — ACT-8 + ACT-16 (feed page).** `/activity` with filters bar, feed list, grouped
bursts, "N new events" banner, load-older, empty state, outage state, ledger-start notice.
Register `/activity` in `routeScope.ts` as `single-project` — **the ratchet test will fail
until you do**, and skipping it recreates exactly the dead-end PR #997 fixed. Add the
sidebar entry under Testing.
*Verify:* no direct service calls from components (`frontend.swr-only-fetching`); backend
5xx renders "Activity is unavailable", never "No activity yet" (reuse the
`outageRendersAsNoData` pattern — absence is not health); `role="feed"` with `aria-busy`; a
deleted entity shows its label with the link disabled. Playwright e2e for UC-1 using the
real login helper (T12).

**PR 8 — ACT-9 (detail drawer).** Opened from a row or `?event=<id>`. `role="dialog"` with
`aria-labelledby` pointing at the heading id — a static `aria-label` goes stale when the
title is dynamic, and the `frontend.modal-dialog-role` gate checks this. Esc closes, focus
returns to the row, changed keys render first, permalink copies via the clipboard util
(gate). Watch for portaling breaking `ref.contains` dismissal — that bit PR #942.

**PR 9 — ACT-10 (Overview panel).** Eight rows reusing `ActivityRow`, its own SWR key so it
never blocks the Overview render, hidden in All Projects mode with a one-line hint.
*Verify:* the existing first-run wizard test still passes (T3).

### Slice C — completeness

**PR 10 — ACT-11 (release, decision, phase events).** Extend `release-epic.spec.ts`.
Auto-created releases on project creation are attributed to `system`.

**PR 11 — ACT-12 (quarantine, defects, flaky).** Project-scoped quarantine events alongside
the existing `settings_audit_log` row (do not remove it). Transitions mirror only when the
evaluator fires, never per run — a broken deploy once produced 747 of 765 failures from one
incident, so rely on the existing debounce and group by `group_key = transition:{run_id}`.

**PR 12 — ACT-13 (agent events).** From the agentic runtime, `actor_type = agent`,
`actor_ref = agent name`, approving human as `target`. Link each Agent Activity page row to
its ledger event. `context.dry_run` renders as a chip.

**PR 13 — ACT-14 (export).** CSV and NDJSON, `StreamingResponse` (do not rebind the body —
there is a gate), QA_LEAD-on-project or ADMIN, capped at 100k rows with `X-Truncated: true`,
recorded as `activity.exported` with `context.row_count`. Secrets are already redacted at
write, so no second pass is needed — assert that rather than adding one.

**PR 14 — ACT-15 (CLI and MCP).** `testlookup activity list --project <slug> --since 7d
[--category] [--json]` and MCP tool `list_project_activity`. Read-only. Do not widen the
separately-tracked MCP SSE authentication exposure.

### Deferred — do not build

`ACT-D1` SSE live tail over a Redis stream. `ACT-D2` monthly partitioning past 50M rows.
`ACT-D3` generating ledger rows from compliance tables via a DB listener (needs an
actor-context table first). `ACT-D4` per-user "my activity" and mentions.

---

## 8. Repo conventions the PRs must satisfy

- **Traceability.** Every change is a branch, plus a regression test, plus an entry in the
  **tracked** `CHANGELOG.md`. `docs/` is gitignored, so a doc edit is not a record.
  CHANGELOG entries are headed `## YYYY-MM-DD — a sentence saying what changed`.
- **Transaction boundaries.** Services do not own transactions: they `db.add` / mutate /
  `db.flush()` and return; the **router handler owns `await db.commit()`**. A service must
  never call `db.rollback()` on an injected session. Adding a `db.commit()` to a service
  needs an allowlist entry and a cap bump in
  `tests/test_architectural_transaction_boundaries.py`. Note `record_attempt` is exempt by
  design: it opens its own session.
- **Pydantic v2 only** — no `@validator`, no `class Config`. **Async everywhere.**
- **No `print`** in app code; PII redacted at log boundaries.
- **`AI_OFFLINE_MODE` defaults to `True`** and every outbound integration checks it. This
  epic adds no outbound calls; do not add an offline gate it does not need.
- **Frontend:** SWR for all reads (pages import hooks, hooks wrap `useSWR`); the single
  Axios base in `services/api.ts`; the All-Projects sentinel is the imported constant, never
  an inline string; clipboard through the util; TypeScript strict.
- **Verify against the running app, not only unit tests.** `frontend/probe-live.config.ts`
  and the `verify` skill exist for this. Remember T13 about cleaning up after live writes.

## 9. PR and CI workflow

- Branch from `main`. Never commit to `main` directly.
- Commit message trailer: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- PR description ends with:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- Open the PR with `gh pr create`, then `gh run watch` — not a sleep loop (T16).
- Merge only when CI is green and the PR is `MERGEABLE`.
- CI jobs that matter here: `quality-gate` (guards plus their self-test), `backend-test`
  (pytest with coverage, `ruff check app/ tests/`, `mypy app/`), `postgres-integration`
  (real asyncpg, runs `alembic upgrade head`), `frontend-test`.
- **If `git push` fails with a TLS error, stop and tell the user.** Norton/NordVPN HTTPS
  interception breaks agent pushes; the user pushes manually when that happens.

## 10. Definition of done for the epic

| Metric | Baseline 2026-09-05 | Target |
|---|---|---|
| Project-scoped mutation endpoints emitting an event | see note | 100%, guard-enforced |
| Lifecycle events per ingested run | 0 | 2 |
| Feed readable by | QA_LEAD and above | every project member |
| p95 feed latency, 1M rows, filtered | n/a — no pagination exists | under 300ms |
| Noise share of the first page | ~60% observed live | 0% |
| Secret values in the ledger | n/a | 0, checked nightly |

**Note on the first row.** The exact denominator is not yet measured and must not be
guessed. Verified counts at `df246f7f`: 235 mutation endpoints across the ~72 routers, of
which 31 routers are project-scoped (they use `require_project_access` or carry a
`{project_id}` path param); in the 26 routers surveyed for section 2, only 7 modules
reference any audit write at all. **PR 1's `backend.activity-coverage` guard produces the
true denominator** — its first baseline file is the real starting number. Record that
number in the PR 1 description and use it as the baseline thereafter.
