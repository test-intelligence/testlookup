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

### BUG-006 — four `/releases` controls only toast "coming in next iteration"  ·  S3  ·  **FIXED 2026-09-21**

**Fixed without needing the product decision.** The filed defect is *"a control
that promises an action it never performs"*, not *"these four features are
missing"* — so the fix is how they are advertised. All four are now `disabled`,
carry the reason in `title`, and say **(planned)** in the visible label. The
state is in the label rather than only the tooltip because a tooltip does not
exist on touch and `title` is not reliably announced by screen readers.

They were not deleted: `docs/BACKLOG.md` carries all four, and removing the
controls would drop the only signal that the capability is coming. Whether and
when to build them remains a roadmap question — it is just no longer answered
by a button that lies.

`ReleasesPage.plannedControls.test.tsx` guards the whole class rather than the
four known sites: any control answering a click with that toast fails it.
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

### BUG-007 — "0 flaky" on `/reports/summary` beside "1 quarantine" on `/flaky-coach`  ·  S3  ·  **FIXED 2026-09-21** on `fix/bug007-flaky-count-states-its-rule-2026-09-21`
- **Reported:** user, 2026-09-18. Confirm whether the pair is accurate or a bug.

#### Answer: both numbers were right, and the product never said so

Measured live against `build-20260921-001432`, all five projects:

| Project | Flaky Coach `total_flaky` | candidates | summary `flaky_test_count` |
|---|---|---|---|
| **ExploreQA 134934** | **1** | 1 | **0** |
| **Inventory Service** | **5** | 4 | **6** |
| Auth Service | 5 | 2 | 5 |
| Checkout Service | 2 | 0 | 2 |
| Payment Service | 9 | 4 | 9 |

Two of five disagree — so this is **not** the "0 flaky + 1 quarantined is the
expected steady state" case this entry first proposed, and not the
`quarantine_candidates` misreading either. Flaky Coach's own flaky count
disagrees with the summary's, which this entry already identified as the
condition that makes it a real defect.

**Root cause: two populations, same word.** Both surfaces apply the *same* flip
threshold (`MIN_FLIPS_FOR_INTERMITTENCY`, which is **2**), but to different data:

| | window | min runs | ratio band |
|---|---|---|---|
| Flaky Coach | last 30 days | 3 | none |
| summary report | each test's last 10 runs | **5** | 10-90% |

ExploreQA's one flaky test has exactly **3 runs** (`FAILED, PASSED, FAILED`) —
enough for Flaky Coach, below the summary's 5. Inventory diverges the other way:
`testReservationExpiry` shows 1 flip in the coach's 8-run window, so the coach
excludes it, while the summary's last-10-runs window sees enough flips.

#### The fix is disclosure, NOT alignment

`_count_flaky_tests` feeds `_evaluate_hard_caps` (`max_flaky_count`, which forces
NO_GO) and `_compute_readiness`. Lowering `min_runs` to 3 to match Flaky Coach —
the obvious "fix" — would have changed release verdicts on live projects because
a KPI tile was confusing. A display problem must not be fixed by moving a gate,
and `test_flaky_count_publishes_its_rule.py::TestTheGateDidNotMove` is what holds
that line.

So the count now publishes the rule it applied (`flaky_criteria` on the summary
response), the tile states it, and a zero reads *"none met the 5-run threshold"*
rather than `0% of total`. Flaky Coach's subtitle names its own window. The
ratio band moved out of the SQL into named constants so the published criteria
cannot drift from the query that enforces them.

- **Detail + queries:** `qa/2026-09-18-01/defects/TL-2026-09-18-01-008.md`
- **They are not the same measurement.** `/reports/summary` calls
  `metrics_service._count_flaky_tests` — a *behavioural* count requiring both a
  10-90% failure ratio **and** N pass<->fail transitions in run order.
  `/flaky-coach` counts *workflow rows*
  (`DETECTED -> PROPOSED -> APPROVED -> QUARANTINED -> RECHECK_SCHEDULED`).
*The triage notes below are kept for the reasoning. Two of them were **wrong**,
and are marked so rather than deleted — an unmarked disproven hypothesis reads
like a finding.*

- ~~**And the states interact in exactly this direction.**~~ **DISPROVEN.**
  `models/postgres.py:5738` says a QUARANTINED test is "excluded from release
  gate scoring", so the theory was that a suppressed test stops flipping and
  legitimately leaves the behavioural count. It does not explain the measured
  data: Flaky Coach's *own* `total_flaky` reads 1 where the summary reads 0, and
  Inventory diverges in the **opposite** direction (coach 5, summary 6), which a
  quarantine-suppression story cannot produce.
- ~~**Check the cheap thing first** (project scope)~~ — **RULED OUT.** All five
  projects were queried by explicit `project_id` against the same deployment in
  one pass, so no scope difference was possible.
- ~~**Also confirm which number was read**~~ — **RULED OUT**, and it was the
  right question. `quarantine_candidates` is indeed not an active quarantine,
  but the disagreement survives comparing `total_flaky` to `flaky_test_count`
  directly. This note's own test — "if Flaky Coach's `flakyCount` disagrees with
  the summary's `flaky_test_count`, that IS a bug" — is what the measurement met.
- **CONFIRMED:** the fix is wording, not arithmetic. A QA lead reading "0 flaky"
  concludes there is no flakiness. The summary now says what it excludes, and
  deliberately does **not** change what it counts, because that number gates
  releases.

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

---

### BUG-009 — `/settings` sub-pages have inconsistent back-navigation  ·  S3  ·  **FIXED 2026-09-19, VERIFIED LIVE 2026-09-21**

- Reported by: **user**, against `http://testlookup.local/settings`
- "The back button or navigation back to settings is not consistent across all
  the sub-pages in settings. There should be a consistent approach for all pages
  and standard options."

**This entry was stale, not open.** It was fixed the day it was reported, in
`d8ba9360` ("one consistent way back, rendered by the layout — BUG-009"), and
the status line here was never updated. Corrected 2026-09-20 after verifying
against the deployment rather than the commit message.

#### What was wrong — three behaviours across 23 sub-pages

| Behaviour | Count | Pages |
|---|---|---|
| `btn-secondary` "Back" in `PageHeader`'s `actions` slot | 5 | AIConfig, Storage, FeatureFlags, GitHub, Integrations |
| muted "Settings" breadcrumb above the header | 4 | Billing, GitLab, Retention, OutboundWebhooks |
| **nothing at all** | **14** | Profile, Notifications, SSO, Audit, API keys, Digests, MFA policy, Performance, Project data, Seed data, AI agents, AI eval, Agent activity, Integration health |

#### The fix

`components/layout/SettingsBackBar.tsx`, rendered by `AppLayout` and keyed off
the route, not by the pages. That placement is the substance of the fix: a
per-page control is a rule every future sub-page has to remember, and 14 of 23
already did not. A new `/settings/<thing>` route now gets the affordance without
its author doing anything, so the inconsistency cannot return one page at a time.

The breadcrumb form won over the button because `PageHeader`'s `actions` slot
holds real page actions (Save, Create, Rotate); a "Back" button there competes
with them and reads as one of them.

#### Verified live

`probe-jr-homelab-verify.spec.ts` walks **every** `/settings/*` route declared in
`App.tsx` — all 23, not a sample — and checks three separate failure modes:

```
LIVE_SETTINGS missing=[] duplicated=[] staleButton=[]
```

On `testlookup.local` at `build-20260921-001432`. Measured placement is
identical on every sub-page (x=264, y=80, height 24) and **absent on
`/settings` itself**, which is the destination rather than a sub-page.

### BUG-010 — the AI-pipeline debouncer is dead code, and config describes it as live  ·  S3  ·  **FIXED 2026-09-21**

**The owner decision this was waiting on had already been made by events.** The
entry below defers it because a worktree was "actively adding the missing call".
That branch's last commit is **2026-07-15** and it sits **1374 commits behind
`main`** — abandoned, not active. With that premise gone there is no decision
left: the outbox is what runs, and wiring the debouncer would have risked double
dispatch.

The module was *entirely* unreachable, not merely its entry point — including
`get_degraded_project_count`, which `/health` published, because the keys it
counted were written only inside `flush_pending`. So the ops dashboard showed
**two numbers structurally incapable of being non-zero** while the real backlog
went unreported.

Removed the module, its task, its beat entry and its two settings. `/health`
now reports `ai_pipeline.pending_dispatches` from `run_downstream_outbox` —
`None` rather than `0` when it cannot read.

Found by JR-03. `app/services/ai_pipeline_debouncer.enqueue_pipeline_for_run` has
**zero production callers** in the tracked tree (`git grep`: the only non-test
reference is a *comment* at `celery_app.py:169`). Yet:

- `config.py:304-309` states "``stream_service.close_session`` no longer fires
  ``run_agent_pipeline`` directly; the run lands in a Redis SortedSet" —
  `stream_service.py` references neither the debouncer nor `run_agent_pipeline`.
- `AI_PIPELINE_DEBOUNCE_ENABLED` defaults to **True**, so an operator would
  reasonably tune `AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS` expecting an effect.
- The `flush-ai-pipeline-queue` beat task runs **every 2 minutes** draining an
  empty set — 279 firings observed with the SortedSet empty while five
  `run_downstream_outbox` rows waited.

The live mechanism is the outbox: `run_downstream_outbox` carries an
`agent_pipeline` operation, and `claim_downstream_dispatches` was invoked
directly and claimed all 20 pending rows correctly (rolled back).

**Not fixed deliberately.** The worktree `agent-a08622553ca10f607`
(`feat/epic3-gitlab-integration`) *is* adding the missing call at
`stream_service.py:500`. Deleting the module or its beat entry here would
conflict with that active work. Decide which mechanism is canonical — outbox or
debouncer — then either wire it or remove it together with the beat entry and the
config comment. Wiring it as-is risks double dispatch alongside the outbox.

### BUG-011 — `/agents` pipeline-run dropdown still shows static content  ·  S2  ·  **FIXED 2026-09-20** on `fix/bug011-agents-run-selection-2026-09-20`

- Reported by: **user**, against `http://testlookup.local/agents`
- "The pipeline runs drop down, when any value is selected, the displayed content
  of pipelines are not refreshed or changed. It is static values."

**Previously filed as TL-2026-09-18-01-009 and marked FIXED** (derived selection
in `AgentStatusPage.tsx`, merged in PR #126, deployed at `build-20260918-224140`
and `build-20260919-043402`).

#### Root cause: the first fix caused the second report

Not a second cause, and not the wrong control — this tracker guessed both. The
TL-009 fix was **half of one**. It cleared the stale pipeline id so the panels
could not describe the previous run, and stopped there, leaving
`selectedPipeline` null. Every right-hand panel — AI report, agent stages,
compute graph, the whole "displayed content of pipelines" — then fell back to
*Select a pipeline run to see agent stages*, identically, for every run picked.
**Wrong content became no content**, which reads the same way from the user's
chair, and it made a second click mandatory on every selection.

#### Measured live, on the deployment, before touching anything

| Run selected | Pipelines | Left list | Right panel |
|---|---|---|---|
| Auth Service · Run #2 | 13 | 14 cards | placeholder |
| Auth Service · Run #1 | 0 | empty state | placeholder |
| CheckoutSuite · Run #3 | 2 | 3 cards | placeholder |
| CheckoutSuite · Run #2 | 2 | 3 cards | placeholder |

The dropdown navigated and refetched correctly the whole time — which is why
three passes over the source found nothing. Only the panel was static.

#### The fix

A run on the route now also opens that run's **newest** pipeline
(`AgentStatusPage.tsx`, derived from the already-sorted list, not an effect).
Runs carry 0, 2 and 13 pipelines here, so "the run's pipeline" is not a safe
assumption; newest-first is, and it matches what the "Run #N" label promises. A
run with no pipelines keeps the placeholder — honest there, since the left
column explains the absence. An explicitly clicked card still wins.

#### Evidence, both directions

`tests/probe-bug011-agents-trigger.spec.ts`, run against **both** builds:

| Build | `placeholders` | Result |
|---|---|---|
| homelab `build-20260919-165423` (old code) | `[true,true,true]` | **2 failed** |
| local dev, fixed | `[false,false,false]` | 3 passed, twice |
| **homelab `build-20260920-210311` (deployed fix)** | `[false,true,false]` | **3 passed, twice** |

A probe that has never been seen to fail is not evidence, so it was run red
first, on the deployment.

The single `true` in the deployed run is the fix behaving correctly, not a
partial pass: the three probed runs hold **13 / 0 / 2** pipelines, and the
placeholder appears exactly on the run that has none. A fix that blindly
selected something would have shown `[false,false,false]` there and been wrong.

Six unit cases in `AgentStatusPage.runswitch.test.tsx` survive five mutations
aimed at the *plausible wrong fixes* (no auto-select; unsorted `[0]`;
auto-select with no run on the route; auto-select overriding a click;
auto-selecting the pipeline but not its run).

**VERIFIED LIVE on `testlookup.local`**, 2026-09-20, `build-20260920-210311`
(DEPLOY_EXIT=0, frontend pod image
`sha256:7a0030acba94c2710969a83aa080125bc500f22146ebfae50c2b1a1ce326e87f`).
This is the gap that let the first fix look done — it was unit-tested only and
never checked against the deployment.

### BUG-012 — `/agents` still shows "awaiting review" after the pipeline is accepted on `/reviews`  ·  S2  ·  **FIXED 2026-09-19** on `fix/stale-state-bugs-2026-09-19`

- Reported by: **user**, against `http://testlookup.local/agents`
- "When a pipeline is accepted in the review in the page
  `http://testlookup.local/reviews`, then the AI pipeline status still shows
  'awaiting review'."

Two surfaces disagree about the same fact after a state change on one of them.
The likely shapes, in the order worth checking:

1. **The accept writes, but `/agents` reads a different field.** `/reviews`
   records an acceptance (review row / `requires_human_review`) while the agents
   page derives its badge from something else that was never updated.
2. **A cache or SWR key is not invalidated.** The accept commits, but `/agents`
   serves a stale payload until a hard reload — check whether the status corrects
   itself on refresh. If it does, it is invalidation, not persistence.
3. **The accept never persists.** Check the row directly before blaming the UI.

Distinguish these before changing anything: reload `/agents` after accepting and
see whether the badge corrects itself. That single observation separates (2) from
(1) and (3).

Note the precedent from BUG-011: the previous "same page, same symptom" report
turned out to be a *different control* from the one already fixed. Confirm which
status field the badge actually reads before assuming the accept path is wrong,
and add a live assertion so "fixed" is evidence rather than inference.

---

## BUG-012 — resolved: the accept path was never broken, the subject was missing

Diagnosed against live homelab data, not from source. The three shapes listed
above were all wrong, and the real cause was none of them.

`POST /reviews/{id}/accept` **does** transition `agent_pipeline_runs.status`
(`review_request_service.settle_review` → `guarded_transition`), and `/agents`
reads that same column. No stale field, no missing invalidation.

The review's *subject* had been deleted. `review_requests.pipeline_run_id` is an
FK with `ON DELETE SET NULL`; `subject_id` is a plain `varchar` with **no FK**.
Deleting a pipeline run nulls the first, leaves a dangling id in the second, and
the review row survives in the queue. `settle_review` guarded its transition
with a bare `if pipeline_run_id is not None`, so settling such a review skipped
the transition and reported success having changed nothing.

Measured on the deployment:

| `pipeline_run_id IS NULL` | subject is a real pipeline | count |
|---|---|---|
| false | yes | 106 |
| true | **no** | **12** |

No exceptions either way, and the single review a human had accepted was one of
the twelve — which is why the report was reproducible but the code looked right.

**Fixed** (owner decision): settling an orphan now fails loudly with
`409 subject_run_deleted`, and `list_reviews` filters orphans out of the queue.
This reverses a previously pinned behaviour — `test_a_review_whose_run_was_deleted_still_settles`
asserted the silent settle, with no rationale; the replacement carries one.

The 12 existing orphan rows are left in place. They no longer surface.

---

## Open findings from the 2026-09-19 stale-state sweep (not yet fixed)

Found while scanning for this bug class. Confirmed by tracing both sides;
**not** fixed on the BUG-012 branch, which was scoped to defects that never
self-heal.

**Backend**

- ~~Shared report links and compliance evidence bundles ignore snapshot
  staleness entirely~~ — **FIXED 2026-09-20** (PR #138). Both now read through
  `intelligence_snapshot_service.get_or_compute`, which recomputes rather than
  serving a stale or obsolete-schema row; the bundle no longer falls back to an
  empty payload, and its tenant scope is an explicit check rather than a join
  predicate that a mocked session ignored.
- **Correcting an AI classification never invalidates the run snapshot**
  (`feedback_service.py:46-54`). The corrected category shows on `/analyze` and
  nowhere else.
- **`PUT /feedback/{analysis_id}` is asymmetric with `POST`**
  (`feedback_service.py:284-304`): it writes the `AIFeedback` row only — never
  applies `corrected_category` to `AIAnalysis`, never resets
  `requires_human_review`, never evicts the semantic cache. Editing a correction
  makes the training label diverge from every product surface.
- **Semantic-cache eviction runs before the commit** (`feedback_service.py:50-54`
  vs the router commit at `feedback.py:318`) — the exact ordering this codebase
  documents and fixed at `app_settings.py:578-584`.
- `test_bl03_snapshot_freshness.py` is **vacuous** — e.g.
  `action = "mark_stale"; assert action == "mark_stale"`. It is the named guard
  for the snapshot-freshness contract and tests nothing, which is why the
  permanent-staleness defect survived.

**Frontend** (all self-heal on a poll or a route unmount, so they are a stale
first paint rather than a durable lie)

- Quarantine approve/reject/release refreshes the table but not the stat tiles
  above it (30 s poll).
- Two `useApiKeys`/`refreshApiKeys` pairs over disjoint key spaces for one
  `/api/v1/keys` resource — neither matcher can ever match the other's key.
- Per-row "Dismiss" refreshes the log list but not the unread badge, while the
  sibling `handleMarkAll` in the same component does it correctly.
- `/settings/ai-agents` renders one agent config twice under two keys; beyond
  the display mismatch the panel then sends a stale `If-Match`, so the **next**
  save is rejected on a precondition the user cannot see.
- `/settings/ai-config` saves into `useState`, bypassing SWR against the
  `frontend.swr-only-fetching` convention; the shared `settings/ai-config` key
  waits a 60 s poll.
- Reviews-tab actions leave the library's "N awaiting review" headline on a
  different `tm-cases` key.

