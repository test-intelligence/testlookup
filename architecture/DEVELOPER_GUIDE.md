# TestLookup — Developer Guide

> How to change this codebase without tripping a ratchet or re-introducing a
> known bug. Pair this with [README.md](./README.md) (runtime architecture) and
> [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md) (data model). Generated 2026-06-25;
> all gate ids, guard names, and test files referenced here were verified
> against the tree at that date.

## 0. Orientation

| You want to… | Look in |
|---|---|
| Add/change an API endpoint | `backend/app/routers/` + `backend/app/services/` + `models/schemas.py` |
| Add a DB table/column | `backend/app/models/postgres.py` + `backend/migrations/versions/` |
| Change AI behaviour | `backend/app/agents/` (pipeline) + `backend/app/services/analysis_router.py` (routing) |
| Add a UI page | `frontend/src/pages/` + `frontend/src/hooks/` + `frontend/src/services/` + `App.tsx` |
| Understand a cross-cutting rule | `scripts/quality_gate.py` + `backend/tests/test_architectural_*.py` |

**Commands** (Makefile is the supported entry point):

```bash
make dev            # core stack (no LLM)        make dev-llm    # + Ollama + ChromaDB
make migrate        # apply Alembic migrations    make seed-data  # demo data
make lint           # ruff + eslint               make format     # ruff + prettier (run before commit)
make type-check     # mypy + tsc                  make quality-gate  # cross-cutting invariant guards
make test-backend / test-frontend / test-e2e
```

There are **scaffolding skills** that encode the exact file patterns — prefer
them over hand-rolling: `add-endpoint`, `add-agent`, `add-page`, `add-migration`.

---

## 1. Quality gates — the invariant ratchets

`make quality-gate` runs `scripts/quality_gate.py`, which enforces **17 guards**.
Most are *ratchets*: pre-existing violations are baselined in
`scripts/quality-gate-baselines/` and the count can only shrink. New violations
fail CI. A few ship at zero with **no baseline file at all** — those are
absolute rules, not ratchets. Know these before you write code.

### Backend

| Gate id | Forbids / requires | How to satisfy |
|---|---|---|
| `backend.no-print` | `print()` in `backend/app/` | Use the structlog `logger.*` |
| `backend.analysis-router` | direct `RulesEngine`/`MLClassifier`/`run_triage_agent` calls outside the router | Route classification through `services/analysis_router.classify_test()` |
| `backend.finalize-run` | committing `test_case` rows without `finalize_run()` | Always `await ingestion_pipeline.finalize_run(...)` after the commit |
| `backend.pii-log-redaction` | logging `email`/`password`/`api_key`/`raw_key` verbatim | Sanitize PII before logging |
| `backend.structlog-positional-args` | `logger.warning("x: %s", e)` (stdlib style) | `logger.warning("event_name", error=str(e))` — positional args raise `TypeError` mid-request |
| `backend.audit-write-discipline` | any UPDATE of an audit table, and any DELETE outside the retention purge | Append a **new** audit row instead of mutating one; if a purge is needed, extend `services/retention_service.py` |

**`backend.audit-write-discipline` — why it exists.** `settings_audit_log`,
`access_audit_logs`, `test_case_audit_logs` and `identity_events` are
append-only **by convention only**: the entire migration set contains exactly
one trigger (the search-vector trigger in `0001`), no grants are restricted,
and no store is WORM. This guard *is* the enforcement, so the model docstrings
describe something real. It ships at zero — no baseline — and catches Core
`update()`/`delete()` constructs, `db.query(...).update(...)`, attribute
assignment / `setattr` / `session.delete` on a **fetched** audit row, and raw
SQL `UPDATE` / `DELETE FROM` / `TRUNCATE` of those tables. Building a row and
setting its fields before `flush` is an INSERT and stays legal.

`services/retention_service.py` is the single allowlisted deleter: US-11.4
purges `access_audit_logs` + `test_case_audit_logs` past the per-project
**audit clock** (`audit_days`, floor 365 d, default ≈7 y, validated
`>= runs_days`), opt-in and self-audited. Pinned by
`backend/tests/test_architectural_audit_write_discipline.py` — which also
fails if a *second* deleter appears.

### Frontend

| Gate id | Forbids / requires | How to satisfy |
|---|---|---|
| `frontend.swr-only-fetching` | raw `fetch()`/`axios()` in `pages/*.tsx` | Move the fetch into a `hooks/` SWR hook |
| `frontend.single-axios` | a second `axios.create()` | Import the shared base from `services/api.ts` (it owns the 401-refresh queue) |
| `frontend.all-projects-literal` | inlining the `'all'` project sentinel | Use the `ALL_PROJECTS_ID` constant; convert to `null` before API calls |
| `frontend.clipboard-util` | raw `navigator.clipboard` | Use `copyTextToClipboard` from `@/utils/clipboard` (HTTP homelabs lack the secure-context API) |

### Database

| Gate id | Requires | How to satisfy |
|---|---|---|
| `database.single-alembic-head` | a linear migration chain (one head) | Set your `down_revision` to the current head; rebase on conflict |
| `database.downgrade-implemented` | a real `downgrade()` (no empty stubs) | Implement the inverse, or a documented no-op with a reason |

### Agents

| Gate id | Requires | How to satisfy |
|---|---|---|
| `agents.base-agent-subclass` | every agent subclasses `BaseAgent` | Subclass it (support modules like `state.py`/`workflow.py` are exempt) |
| `agents.log-decision-present` | every agent calls `self.log_decision(...)` | Log every non-trivial route/fallback/skip |
| `agents.routing-metadata` | `classify_test()` populates `_routing` | Set `result['_routing'] = {...}` before returning |

### AI

| Gate id | Requires | How to satisfy |
|---|---|---|
| `ai.prompt-manifest-sync` | every LLM prompt matches its pinned hash in `prompt_manifest.json`, and that manifest digest carries a green eval-gate attestation | Bump the prompt version, re-run the eval gate, re-attest (see `architecture/AI_EVALUATION.md`) |

### Homelab

| Gate id | Requires |
|---|---|
| `homelab.build-tag-placeholder` | `k8s/overlays/homelab/kustomization.yaml` keeps `newTag: BUILD_TAG_PLACEHOLDER` at rest |

Beyond the gate script, **architectural tests** ratchet structure:
`test_architectural_transaction_boundaries.py`,
`test_architectural_authorization.py`, `test_architectural_agent_contracts.py`
(+ `..._agent_eval_harness.py`), and
`test_architectural_audit_write_discipline.py`.

---

## 2. Backend conventions

### Layering & transactions

**The router owns the transaction.** Services *stage* changes (`db.add()`,
mutate, `await db.flush()`) and return; the router handler calls
`await db.commit()` so one commit covers the whole unit of work (business
mutation + audit row + counter). This keeps atomic units explicit at the request
edge.

- **Never call `await db.rollback()` on an injected request session** inside a service — it aborts the caller's transaction and produces a downstream 500 with no traceback.
- A new service that commits on an injected session fails `test_architectural_transaction_boundaries.py`. Legitimate exceptions are **allowlisted with a reason** in that test (Celery-task-owned sessions, orchestration layers like `ingestion_pipeline`, and dedicated write-session services). The sum of allowlist caps is itself capped — raising it needs sign-off.

### Authorization & the IDOR rule

Every route with a scoped path param (`{project_id}`, `{run_id}`, `{session_id}`,
…) must depend on a guard from `backend/app/core/deps.py`, or be on the small,
shrinking `KNOWN_EXEMPT` list in `test_architectural_authorization.py`.

Available guards: `require_project_access`, `require_run_access`,
`require_release_access`, `require_knowledge_source_access`,
`require_generation_batch_access`, `require_live_session_access`,
`require_session_access`, `require_link_access`, `require_api_key_owner`, plus
the role gates `require_role` / `require_project_role`. New resource guards are
built from the `_make_project_scoped_guard()` factory.

> **IDOR rule:** verify the **provided** id, not just the `None` path. A route
> that accepts `{run_id}` must *load the run, read its `project_id`, and check
> membership against that project* — not merely 403 when `run_id` is missing.
> Past incidents hit metrics/value-metrics/test-management routes that gated only
> the `None` case.

### Query scoping (tenant isolation, defence-in-depth)

Tenant filtering happens at three layers (router guard → service scope → query
predicate). In the query layer:

- **Project scope.** Build `allowed_project_ids` via `get_accessible_project_ids(db, user)` (`None` ⇒ admin/unrestricted, empty set ⇒ no access). Analytics helpers (`analytics_service._tenant_filter()`) turn that into `project_id = :pid` / `IN (:ids)` / `AND FALSE` / no-filter. A `None` project must *never* silently drop the tenant filter.
- **Effective suite.** Use `_effective_suite_sql()` (`analytics_service.py`) for any suite filter/group: live-stream tests bucket by `tr.primary_suite_name` (the session label), file-upload tests by `tc.suite_name` (per-event). An OR over both over-returns and leaks tests across suites.
- **Fingerprints are per-project.** Any `test_fingerprint` query must be scoped by `project_id`; a cross-project fingerprint match is spurious.
- **ChromaDB collections are per-project** — never share a collection across tenants.

### Async-session gotchas

- `app/db/postgres.py` builds the engine at **import time** — `DATABASE_URL` must be set in any process that imports it (tests included).
- After flushing a row with a `func.now()` / `onupdate` server-side default, `await db.refresh(row)` before a Pydantic serializer reads it, or you get `MissingGreenlet`.
- `IN :ids` needs `bindparam("ids", expanding=True)` under asyncpg, or it binds the tuple as one scalar and 500s.

---

## 3. How to add code (the skill patterns)

### Add an API endpoint (`add-endpoint`)

1. Pydantic request/response schemas in `models/schemas.py` (`ConfigDict(from_attributes=True)`).
2. ORM model in `models/postgres.py` if a new table is needed (UUID pk, tz timestamps).
3. Service in `services/` — async, structlog logger, **stages** writes (no commit unless allowlisted).
4. Router in `routers/` — thin; `Depends(get_db)` + the right `require_*_access`/`require_role` guard; owns the commit.
5. Register the router in `backend/app/bootstrap.py` (`PROTECTED_ROUTERS`, unless genuinely public).
6. Alembic migration if you added a table (§4).
7. Pytest with mocked DB (`conftest.py` `FakeExecuteResult` / `FakeRedis`).
8. `ruff check` + `ruff format` the changed files.

### Add an agent (`add-agent`)

1. New class in `agents/` subclassing `BaseAgent`; set `stage_name`; implement `async def run(self, state)`; `mark_stage_running()` → work → `mark_stage_done(result_data=… | error=…)`. Call `self.log_decision(...)` for every routing/fallback/skip.
2. Add the stage to the `WorkflowState` TypedDict in `agents/state.py`.
3. Wire it into `agents/workflow.py` (singleton + node wrapper + graph edges; add a fast-path skip if it should be bypassed when there are no failures).
4. **Pure helpers go in `services/`, not `agents/`** — a ratchet keeps agents to orchestration only.
5. Tests mocking the LLM + DB; `ruff` the changed files.

### Add a UI page (`add-page`)

1. Types in `frontend/src/types/`.
2. Service in `frontend/src/services/` using `getData`/`postData` from `services/http.ts`.
3. **SWR hook** in `frontend/src/hooks/` (`useSWR` / project-scoped variant). Pages must not fetch directly.
4. Page in `frontend/src/pages/` using `PageHeader`/`LoadingSpinner`/`EmptyState`/`Pagination`.
5. Lazy route in `App.tsx` (**verify the route mapping** — pages get renamed) + sidebar entry.
6. `npm run lint` + `npm run type-check`.

### Add a migration (`add-migration`) — §4

---

## 4. Migrations

Alembic root is `backend/migrations/`; versions in `backend/migrations/versions/`,
named `NNNN_description.py`.

```bash
make migrate-create MSG="add_x"   # generate    make migrate         # apply
make migrate-down                 # rollback 1   make migrate-status  # head + pending
```

Rules:

- **Pick a fresh `down_revision` = current head.** Multiple heads block
  `alembic upgrade head` and container startup (`database.single-alembic-head`).
  Find the head: `ls backend/migrations/versions/*.py | sort | tail -1`. On a
  conflict (someone landed a migration in parallel), re-base: set your
  `down_revision` to the *new* head.
- **Implement `downgrade()`** for real, or document a no-op with a reason
  (`database.downgrade-implemented`).
- **Enums are `String(N)`**, not native PG enums — widen the string in a
  migration if a new value won't fit.
- Migrations run automatically at backend container startup.

---

## 5. Frontend conventions

- **SWR for all data fetching.** Pages read through hooks; hooks wrap `useSWR`. This gives refetch-on-focus, dedup, and refetch-on-project-switch. Mutations patch the cache via `mutate` (often `{ revalidate: false }` for optimistic local edits).
- **One Axios base** (`services/api.ts`) owns the JWT attach + the 401-refresh queue. A second instance silently bypasses refresh.
- **Zustand is client state only** — `authStore`, `projectStore` (active project), `themeStore`, `timeWindowStore`. **Server data goes through SWR**, never Zustand.
- **`react-hooks/set-state-in-effect` is now `error`.** Don't reset/derive state in an effect. Reset on a dependency change via the *adjust-state-during-render* previous-value pattern; load data via SWR. (Two justified `eslint-disable`s remain for genuine network/DOM effects.)

### Silent-failure gotchas (check in this order on an "empty page, no toast")

1. **`activeProjectId` lock / `'all'` sentinel** — nothing renders until a project resolves; stale localStorage can wedge it.
2. **Time-window store** collapsed to an empty range.
3. **Backend 422 from a strict Pydantic enum over a `String(N)` column** — Axios *suppresses 422 toasts*, so a drifted enum value fails silently. This is the #1 silent-failure cause; run a live probe (`frontend/probe-live.config.ts`) before re-editing.

Also: when a change seems "missing" in the running app, inspect the built bundle
for a marker string before blaming deploy.

---

## 6. Testing

### Backend

Use the **CI-faithful Python 3.11 venv** (`.venv311` at repo root). The default
`.venv` (3.14) hard-crashes some tests. Required env (values needn't point at
live services — most unit tests stub the DB, but the engine builds at import):

```bash
DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test \
MONGODB_URL=mongodb://localhost:27017 REDIS_URL=redis://localhost:6379/0 \
JWT_SECRET_KEY=testkey STORAGE_BACKEND=local \
.venv311/Scripts/python -m pytest backend/tests --ignore=tests/integration -q
```

- **Run the whole suite to reproduce ordering bugs** — a failure that passes in isolation is usually test-ordering pollution (`importlib.reload` / `cache_clear` / `monkeypatch.setattr` on PEP-562-served names like `engine`, `AsyncSessionLocal`).
- `tests/integration/` needs real asyncpg + Postgres; skipping locally is expected.
- Routers that do `from app.core.deps import X` at module load need a per-file autouse fixture to patch — a conftest monkeypatch on `app.core.deps.X` won't reach them.

### Frontend

```bash
npm run test                          # vitest (unit + component)
npx vitest run src/hooks/useFoo.test.ts   # single file
npm run test:e2e                      # Playwright (needs the full stack)
```

### Docs — Mermaid diagrams

Every `` ```mermaid `` block in tracked markdown is validated in CI (the **Docs —
Mermaid diagrams** job) by parsing it with the Mermaid grammar — the same parse
GitHub runs before rendering. A diagram that doesn't parse renders as "Unable to
render rich display" on GitHub, so the check fails the build instead. Run it
locally before pushing diagram changes:

```bash
cd scripts/mermaid-check && npm ci && node validate.mjs
```

### The traceability convention

Every code change ships with: **a branch + a regression test + a `CHANGELOG.md`
entry** (the tracked `CHANGELOG.md` is the git-visible record; `docs/` is
gitignored). Add the regression test at the layer the bug lived at.

---

## 7. Recurring bug classes

A field guide — most production incidents here are one of these:

1. **Missing `finalize_run()`** → `/suites` empty while `/runs` populated. Always finalize after committing cases. (`backend.finalize-run`)
2. **Suite-count / effective-suite** → use `_effective_suite_sql()`; never OR `tc.suite_name` with `tr.primary_suite_name` (over-returns, leaks across suites).
3. **Fingerprint scoping** → `test_fingerprint` queries must include `project_id`.
4. **`AI_OFFLINE_MODE` not checked** → new outbound integration fires in offline/air-gapped mode. Check the flag (default `True`) in every outbound path.
5. **IDOR** → verify the provided id's ownership, not just the `None` path.
6. **Idempotency** → per-`(entity, run)` writes must be `INSERT … ON CONFLICT DO UPDATE`, or re-ingest duplicates/orphans rows.
7. **Enum vocab mismatch (producer ↔ consumer)** → a new `String(N)` enum value the consumer doesn't know 422s silently (Axios eats 422). Keep vocab in sync across backend/SDK/frontend.
8. **Transaction-boundary leakage** → a service committing an injected session; stage instead, or get allowlisted with a reason.
9. **Analysis-router bypass** → calling rules/ML/LLM engines directly skips mode-selection + the `_routing` trail. Go through `classify_test()`.
10. **structlog positional args** → `logger.warning("x: %s", e)` raises `TypeError` mid-request (often in an `except` on a request path → 500). Use kwargs.
11. **`MissingGreenlet` after flush** → `await db.refresh(row)` before serializing server-defaulted columns.
12. **`IN` binding under asyncpg** → `bindparam("ids", expanding=True)`.
13. **LiveSession.run_id is a slug, not a UUID** → convert via `canonical_test_run_uuid()` before queuing Celery tasks with UUID FKs, or the insert silently fails.

---

## 8. Debugging workflow

1. **Reproduce against the running app**, not just unit tests (`frontend/probe-live.config.ts`, the `verify`/`run` skills). Many bugs are env/scope/offline-gate issues invisible to unit tests.
2. **Empty UI?** Walk the silent-failure checklist (§5). Check the network tab for a suppressed 422.
3. **500 on a request?** Look for structlog positional args, a service `rollback()` on the request session, a missing `expanding=True`, or `MissingGreenlet`.
4. **Data present in one view, missing in another?** Suspect effective-suite or fingerprint/project scoping (§2).
5. **Live session shows N in the hero but 1 in the table (or vice-versa)?** Dedup by `run_id`, not `build_number`; check the suite-name NULL fallbacks.
6. **AI features inert?** Check `AI_OFFLINE_MODE` and whether the Ollama model is installed (the router falls back to ML→rules).
7. **Write the regression test first** at the layer the bug lived, then fix, then run the **whole** backend suite + `make quality-gate` before pushing.
