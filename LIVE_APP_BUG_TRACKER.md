# Live Application Bug Tracker

> **Coding agents: read this before making code changes to the affected areas.**
> Bugs found by validating the live homelab deployment (`http://testlookup.local`)
> with the Java test client + application-log monitoring. Update the **Status**
> and link the fix branch when you act on an item. Add new findings at the top of
> the relevant section with the next ID.
>
> Validation harness: scheduled task `fad81f94` (every 6h) + manual runs of
> `testlookup-client\run-tests.ps1 -SkipInstall -Project allure -MvnArgs '-Dregression.totalCases=60','-Dregression.failCount=4','-Dregression.skipCount=8','-Dregression.targetMinutes=1','-Dregression.threads=4'`.
> Logs: `kubectl -n testlookup logs deploy/<name>` (needs Norton SSL scanning OFF).

---

## How to use this tracker (for agents)
1. Pick an OPEN bug. Reproduce it from the **Evidence** + **Repro** before editing.
2. Fix on a branch `auto/e2e-fix-YYYYMMDD-HHMM` (one branch per bug). Add a
   regression test that fails before / passes after. Update `CHANGELOG.md`.
3. Do NOT push to `main` / merge. Set **Status: FIX READY** + branch name here.
4. Re-run the validation harness; confirm the log error is gone.

Severity: **S1** breaks core flow · **S2** degraded/UX · **S3** noise/cosmetic.

---

## Functional / correctness

### BUG-004 — `/agents` shows no pipeline for completed runs  ·  S2  ·  FIX READY (`auto/e2e-fix-agents-bug004`, in `auto/live-fixes`)
- **Symptom:** `http://testlookup.local/agents` displays no AI pipeline for new
  runs even though the pipeline ran. (User-reported 2026-06-06.)
- **Evidence:** run `493d5c1f` produced pipeline `5c378cde`
  (`workflow_type=offline`, **status=partial**). DB confirms the
  `agent_pipeline_runs` row exists AND joins to `test_runs` (project
  `6783f331-9f51-4b0b-a27a-acc31e117b21`). The API (`GET /api/v1/agents/pipelines`)
  would return it.
- **Root cause (two candidates — confirm in the browser):**
  1. **Active-project scoping** — `frontend/src/hooks/useAgentRuns.ts::usePipelines`
     scopes to `useActiveProjectId()`. If the dashboard's active project ≠ the
     project the test client posts to (`6783f331`), the page is empty by design.
     The test client's project is fixed by its `testlookup.properties` / API key.
  2. **`partial` status not rendered** — `frontend/src/pages/AgentStatusPage.tsx`
     `StatusIcon` + `STATUS_COLOUR`/`STATUS_BG` maps have cases for
     running/completed/failed/skipped but **not `partial`**. A `partial` pipeline
     may render blank / be visually missing.
- **Fix direction:** (a) verify/auto-select the active project, or surface an
  "other projects have runs" hint; (b) add a `partial` case to the status
  icon/colour maps + treat `partial` as a visible (amber) state. Depends on
  BUG-003 (which is what makes the pipeline `partial`).
- **Note:** `AI_OFFLINE_MODE=true` (configmap) is a HARD GATE above the "LLM AI
  Agent" setting, so pipelines run **offline** (rules/ML) regardless of the LLM
  config, and `--skip-models` is NOT why pipelines "don't run" — they do run,
  just offline.

---

## Reliability / data integrity (backend workers)

### BUG-002 — asyncpg "another operation is in progress" in notification dispatch  ·  S2  ·  FIX READY (`auto/e2e-fix-agents-bug002`, in `auto/live-fixes`)
- **Symptom:** `Notification dispatch failed: (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError) ... cannot perform operation: another operation is in progress`
- **Evidence:** `testlookup-worker-default`, task `f8236970-97a0-4134-ad25-cd50a2af3021`, run `493d5c1f` (05:58:30Z).
- **Root cause:** a single asyncpg connection/`AsyncSession` is driven by
  concurrent coroutines (overlapping `await`s, or a second op started before the
  first finished) in the notification-dispatch path. Matches the repo's known
  "same session / second AsyncSessionLocal races under asyncpg" pitfall.
- **Fix direction:** ensure the dispatch path serializes DB ops on one session
  (no concurrent `await` on the same connection), or gives each concurrent unit
  its own `AsyncSessionLocal`. Add a regression test that drives concurrent
  dispatch and asserts no InterfaceError.

### BUG-003 — `RuntimeError: Event loop is closed` on asyncpg connection teardown  ·  S3→S2  ·  FIX READY (`auto/e2e-fix-agents-bug003`, in `auto/live-fixes`)
- **Symptom:** `RuntimeError: Event loop is closed` while terminating an asyncpg
  connection; the AI pipeline reports `errors=1` and ends **status=partial**
  (which is what hides it on `/agents` — see BUG-004).
- **Evidence:** `testlookup-worker-ai` (pipeline `5c378cde`) + `testlookup-worker-ingestion`, run `493d5c1f`.
- **Root cause:** connections are disposed/GC'd **after** the per-task asyncio
  event loop has closed (the engine isn't `await engine.dispose()`-d inside the
  loop before it ends; or a global engine is shared across short-lived loops).
- **Fix direction:** dispose the async engine/connections within the task's loop
  before it closes (or use a loop-scoped engine), so no connection is finalized
  on a dead loop. Promotes pipeline status from `partial` → `completed`.

---

## Noise / hygiene

### BUG-001 — ChromaDB anonymous telemetry floods AI-worker logs  ·  S3  ·  FIX READY (`auto/e2e-fix-20260606-0600`)
- **Symptom:** `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given` (≈5× per pipeline) in `testlookup-worker-ai`.
- **Root cause:** ChromaDB's bundled anonymous telemetry (posthog) is enabled by
  default and breaks against the installed posthog version. Multiple
  `chromadb.HttpClient(host, port)` call sites (agent_memory, knowledge_chunking,
  rag, defect_promotion, conversation) never disable it. Also: an outbound
  phone-home is wrong for this offline-first / privacy app.
- **Fix:** set `ANONYMIZED_TELEMETRY=False` process-wide before any chromadb
  import (`backend/app/core/config.py`) + in `k8s/base/configmap.yaml`.

---

## Open questions / not-yet-bugs
- Active-project UX: should the dashboard auto-select a project that has data, or
  warn when the active project is empty but others have runs? (Affects BUG-004.)
- Perf + security passes (load behaviour under concurrent runs; auth/tenant
  isolation; SSRF on connectors; PII in logs) — **not yet run**; queue as
  dedicated audits and log findings here.
