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

### BUG-004 — `/agents` shows no pipeline for completed runs  ·  S2  ·  DEPLOYED (cycle 1) — pipeline now renders; `partial`-state fix shipped as defense. Run `c1d41ac4`'s pipeline came back **completed** (not partial) once BUG-003 landed.
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

### BUG-002 — asyncpg "another operation is in progress" in notification dispatch  ·  S2  ·  DEPLOYED + VERIFIED (cycle 1) — 0 occurrences in worker-default after deploy+retest.
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

### BUG-003 — `RuntimeError: Event loop is closed` on asyncpg connection teardown  ·  S3→S2  ·  DEPLOYED + VERIFIED (cycle 1) — 0 occurrences in worker-ai/ingestion after deploy+retest; pipeline status went `partial` → `completed`.
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

### BUG-001 — ChromaDB anonymous telemetry floods AI-worker logs  ·  S3  ·  DEPLOYED + VERIFIED (cycle 3) — 0 occurrences after logger-silence deploy+retest ✓
- **Cycle-2 finding:** the explicit `Settings(anonymized_telemetry=False)` rework was deployed (confirmed `app/db/chroma.py` in image `build-20260606-072110`) and STILL fired 7×. So **chromadb 0.5.20 attempts the `ClientStartEvent` posthog capture regardless of `anonymized_telemetry`** (env *and* per-client Settings), failing against **posthog 7.18.0** — a version incompatibility, purely cosmetic (pipelines complete fine).
- **Definitive fix:** `backend/app/db/chroma.py` now `logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)` at import (before any client). Guaranteed to suppress the noise. Verify in cycle 3's log scan (target 0 occurrences).
- **Cycle-1 verification:** `ANONYMIZED_TELEMETRY=False` IS present in the worker pod env, yet the telemetry error STILL fires 7×. The installed **chromadb 0.5.20 does not honor that env var** for the HttpClient telemetry path.
- **Rework:** explicit `Settings(anonymized_telemetry=False)` via a shared `backend/app/db/chroma.py::get_chroma_client()` helper applied to all 8 `HttpClient` call sites (agent_memory, semantic_cache, semantic_search, knowledge_chunking, defect_promotion, conversation, embed_and_cluster). Env setdefault kept as belt-and-suspenders. Regression test 3/3.
- **Symptom:** `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given` (≈5× per pipeline) in `testlookup-worker-ai`.
- **Root cause:** ChromaDB's bundled anonymous telemetry (posthog) is enabled by
  default and breaks against the installed posthog version. Multiple
  `chromadb.HttpClient(host, port)` call sites (agent_memory, knowledge_chunking,
  rag, defect_promotion, conversation) never disable it. Also: an outbound
  phone-home is wrong for this offline-first / privacy app.
- **Fix:** set `ANONYMIZED_TELEMETRY=False` process-wide before any chromadb
  import (`backend/app/core/config.py`) + in `k8s/base/configmap.yaml`.

---

## UI / layout

### BUG-005 — `/intelligence` "Recent runs analyzed": Build eats the width, the other columns clip  ·  S2  ·  **FIXED 2026-09-18** (branch `qa/exploratory-e2e-2026-09-18`)
- **Measured, before → after** (computed geometry, two viewports):
  - 1345px: table 645 → 645, Build 207 → 169, Pass rate **86 → 120** (needs 120)
  - 1920px: table 946 → 860, Build **509 → 384** (53.8% → 44.7% of the row)
- **Fix:** Pass rate sized to its own content (56px bar + 8px gap + the
  percentage, inside 28px of padding = 120px), and the table capped at 860px so
  Build stops growing. Build keeps `max-w-0` — that is what makes it yield, and
  replacing it with a real max-width stopped it shrinking to fit and pushed the
  1345px table to 692px against a ~645px panel, i.e. a scrollbar where there
  had been none.
- **The first fix shipped a second gap, reported the same day.** Capping the
  table at 860px stopped Build growing but left the table short of its panel at
  1920px (860 of 946) — the empty band moved from the middle of the row to the
  right-hand edge. Nothing caught it because every assertion was about the
  COLUMNS and none about the table against the space it was given.
- **Final shape:** percentage widths with floors, plus `max-w-0` on the columns
  that must shrink past their content. Measured:

  | | table | container | Build | Suite | Status | Pass | Timing |
  |---|---|---|---|---|---|---|---|
  | 1345px | 645 | 645 | 169 | 110 | 88 | 120 | 158 |
  | 1920px | 946 | 946 | 378 | 142 | 104 | 142 | 180 |

  The table fills its panel exactly at both widths and the slack is **shared** —
  at 1920px every column grows instead of it all pooling in Build (40.0%).
- **Tests:** `frontend/tests/ci-e2e/intelligence-table-geometry.spec.ts` (runs
  in CI) measures computed geometry at both widths: the pass-rate cell is not
  clipped, Build stays under half the row, and the table neither overflows its
  panel (scrollbar) nor falls short of it (the band). Re-applying the 860px cap
  fails that last one with `fill at 1920 = 90.9%`.
- `IntelligenceHubPage.timing.test.tsx` was **reframed, not re-pinned.** It had
  hard-coded the pixel budget; those numbers have now moved twice. It asserts
  the invariants instead — the cell budget matches the header budget, every
  column has a floor, Build keeps `max-w-0` — and leaves the numbers to the
  geometry test.
- **Symptom (user-reported 2026-09-18, with screenshot, on a ~2000px display):**
  the Build column occupies a large, mostly empty band while Suite shows `—`,
  Pass rate is cut mid-glyph (`0'` and `1(` where `0%` and `100%` belong), and
  Timing is squeezed to the right edge.
- **Cause, stated in the code itself** (`frontend/src/pages/IntelligenceHubPage.tsx:718-742`):
  the column budget is *"measured live at a 1345px viewport"*. Suite 110 +
  Status 88 + Pass rate 86 + Timing 158 = **442px of hard `max-w` caps**, and
  Build alone is `w-full`. Every pixel past 1345px therefore goes to Build and
  none to the others. Separately, `max-w-[86px]` on Pass rate is narrower than
  its own content (bar + percentage), which is the clipping.
- **Why it shipped:** the same numbers fixed a real earlier collapse (Build at
  28px) by pinning four columns to one measurement. That trades one viewport's
  bug for every other viewport's.
- **Fix direction:** proportional / `minmax()` sizing so columns share slack
  instead of Build absorbing it, and a `min-width` on Pass rate that fits its
  content rather than a `max-width` that cuts it.
- **Tests required:** `textContent` cannot see this — every value is in the DOM
  and only its box is wrong. Assert **computed geometry** at two viewports (the
  single-measurement habit is what caused it), plus `scrollWidth <= clientWidth`
  on the Pass rate cell. Same approach as `TL-2026-08-29-01-001`.
- **Detail:** `qa/2026-09-18-01/defects/TL-2026-09-18-01-007.md`

### BUG-006 — four `/releases` controls only toast "coming in next iteration"  ·  S3  ·  **OPEN — backlog, needs a product decision**
- **Symptom:** on `/releases`, "Clone from previous release", "Generate from
  PRD", "Export schedule" and "Calendar view" are styled exactly like the
  working controls beside them and only raise a toast.
- **Sites:** `frontend/src/components/releases/ReleaseCard.tsx:99,106` and
  `frontend/src/pages/ReleasesPage.tsx:819,826`.
- **Count correction, twice over.** A 2026-09-05 design handoff scoped this as
  "two placeholder buttons on `ReleaseCard.tsx`". A grep for the exact string
  found **four**. Widening the grep to `coming in (the )?next iteration` finds
  **six** — `AgentStatusPage.tsx:987-988` say "coming in **the** next
  iteration" for the Audit and Compare workflow modes, and the narrower
  pattern missed them. The acceptance check below uses the wider pattern.
- **Decision needed:** implement the four actions, remove the controls until the
  actions exist, or keep them visibly disabled with a tooltip naming what is
  missing. These are three different products; a QA pass should not pick.
- **Acceptance check for whoever takes it:**
  `grep -rn "coming in next iteration" frontend/src --include=*.tsx --include=*.ts`
  returns nothing outside tests, and each control either acts or is disabled
  with a reason a user can read.
- **Detail:** `qa/2026-09-18-01/defects/TL-2026-09-18-01-004.md`

### BUG-007 — "0 flaky" on `/reports/summary` beside "1 quarantine" on `/flaky-coach`  ·  S3/S2  ·  **OPEN — investigate**
- **Reported:** user, 2026-09-18. Confirm whether the pair is accurate or a bug.
- **They are not the same measurement.** `/reports/summary` calls
  `metrics_service._count_flaky_tests` — a *behavioural* count requiring both a
  10-90% failure ratio **and** N pass<->fail transitions in run order.
  `/flaky-coach` counts *workflow rows*
  (`DETECTED -> PROPOSED -> APPROVED -> QUARANTINED -> RECHECK_SCHEDULED`).
- **And the states interact in exactly this direction.** `models/postgres.py:5738`
  says a QUARANTINED test is "excluded from release gate scoring" — a suppressed
  test stops producing flips, so it legitimately drops out of the behavioural
  count while its quarantine row stays live. **"0 flaky, 1 quarantined" is the
  expected steady state after a successful quarantine.**
- **Check the cheap thing first:** `/flaky-coach` is one of the nine
  single-project routes in `config/routeScope.ts`; `/reports/summary` is not. If
  the two pages were viewed under different project scopes the numbers describe
  different populations. Confirm the same project was pinned on both.
- **Also confirm which number was read:** `FlakyCoachPage.tsx:224-230` renders
  `flakyCount` AND `quarantine_candidates` in one subtitle. A *candidate* is not
  an active quarantine. If Flaky Coach's own `flakyCount` disagrees with the
  summary report's `flaky_test_count`, that IS a bug — one measurement, two
  answers.
- **If accurate, the fix is wording, not arithmetic:** a QA lead reading "0
  flaky" concludes there is no flakiness, while a quarantine exists precisely
  because there was. The summary should say what it excludes.
- **Detail + queries:** `qa/2026-09-18-01/defects/TL-2026-09-18-01-008.md`

### BUG-008 — ENHANCEMENT: `/agents` report above Agent Stages, and make Stages collapsible  ·  **DONE 2026-09-18** (branch `qa/exploratory-e2e-2026-09-18`)
- The AI report now leads the column under its own "AI Report" heading, and
  "Agent Stages" sits below it with a Show/Hide control (`aria-expanded`,
  expanded by default so the page is unchanged for anyone who ignores it).
- **Tests:** `frontend/src/pages/AgentStatusPage.layout.test.tsx` asserts order
  with `compareDocumentPosition` — for both the heading and the report BODY,
  since a heading-only move would satisfy a weaker check — and that collapsing
  the stages does not take the report with it. The expanded-by-default report
  assertion is re-pinned here as well as in `AgentStatusPage.test.tsx`.
- **Requested:** user, 2026-09-18. (1) The summary/report section belongs at the
  top of the page with "Agent Stages" below it. (2) Agent Stages needs an
  expand/collapse control.
- **The page's own code already argues for it:** the comment on `showSummary`
  calls the AI report "the headline output of the pipeline", which is why it
  defaults to expanded — yet it renders *below* the stage detail, so the reader
  scrolls past the mechanism to reach the conclusion.
- **Do not regress:** `AgentStatusPage.test.tsx` pins "shows the AI report by
  default (expanded) once a pipeline is selected".
- **Detail:** `qa/2026-09-18-01/defects/TL-2026-09-18-01-010.md`

---

## Open questions / not-yet-bugs
- Active-project UX: should the dashboard auto-select a project that has data, or
  warn when the active project is empty but others have runs? (Affects BUG-004.)
- Perf + security passes (load behaviour under concurrent runs; auth/tenant
  isolation; SSRF on connectors; PII in logs) — **not yet run**; queue as
  dedicated audits and log findings here.
