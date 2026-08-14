# Live Autonomous Loop — Run Log

Append-only log of the autonomous test→fix→deploy→retest loop against the
homelab (`http://testlookup.local`). Newest cycle at the top. See
`LIVE_APP_BUG_TRACKER.md` for the bug catalogue and `CHANGELOG.md` for fixes.

Loop: scheduled cron (every 30 min) — see the cron prompt. ultracode ON. Deploys
from branch `auto/live-fixes`. Rate-limit policy: stop + wait for reset, resume
next cycle. Termination: no S1/S2 bugs remaining, or 8h window elapsed.

---

## Cycle — 2026-08-14 user-acceptance pass · CODE FIXED, HOMELAB REBUILD REQUIRED

Representative acceptance journeys completed with the authenticated admin seed:

- **QA engineer:** selected Auth Service, verified dashboard metrics/verdict,
  searched for a recent checkout flake, and opened a seeded run/deep-analysis
  workflow. The pages rendered and the search returned 113 hybrid results.
- **QA lead:** opened Release Gate without a run (clear context guard), then
  opened Deep Investigation for run `bd337e00-38ae-50ee-b2e5-0f21077a711b`.
  Cluster selection, dry-run, evidence-source, and investigation controls were
  visible without a UI error.
- **Administrator:** reviewed Integration Health and ran “Probe All Now”. The
  workflow completed and both ChromaDB and Ollama reported healthy in the live
  UI after the probe. Notification history remained HTTP 200.
- **Release manager:** reviewed dashboard/release-readiness surfaces and the
  health/readiness/version endpoints. No authenticated API sweep returned an
  unexpected HTTP 500; feature-disabled RAG returned its documented 503.

Acceptance defect found: the scheduled Celery integration-health path had
persisted `ollama = down`, `message = Event loop is closed`, while the manual
probe path succeeded. Root cause was the process-wide `httpx.AsyncClient`
being reused across the worker's short-lived event loops. The client now tracks
its owning loop and rotates on loop changes; two-loop and same-loop regressions
were added. Targeted worker/probe coverage passed **8 tests (2 environment
skips)**, Ruff and diff checks passed, and the full frontend suite/build/lint
remained green (**789 tests**).

The current homelab image does not yet contain this source change: the local
environment has no image builder, so an in-place worker source check still shows
the previous client implementation. The manual live probe is healthy, but the
immutable image must be rebuilt and rolled out before production sign-off; then
the scheduled task should be rechecked after one beat interval. Ollama also has
no installed `qwen2.5:7b`/`nomic-embed-text` models, so AI-assisted analysis is
currently rules/fallback-only until those approved models are installed.

UAT gaps intentionally left for a controlled staging tenant: destructive
create/update/delete flows, external Jira/Slack mutations, SAML/SSO login,
large-file ingestion, and a full Celery reindex. These require isolated data
and/or production credentials and should be explicit pre-GA test cases.

---

## Cycle — 2026-08-14 application-wide exploratory pass · FIXED, hot-verified

- Inventory covered 51 authenticated frontend routes: dashboard, onboarding,
  metrics, intelligence, runs/compare/detail, coverage/suites, failures,
  trends, defects, search, chat, agent workflows, deep investigation,
  release gate, flaky/quarantine, test management/live execution, reports,
  profile, projects/releases/users, all admin settings, policies, and
  ownership. Every route rendered without an error-boundary message or browser
  diagnostic in the authenticated sweep. A seeded run detail/AI workflow sweep
  also rendered cleanly.
- Read-only API coverage exercised health/readiness/version/details,
  projects/runs, notification history/preferences, metrics and analytics,
  assigned failures, chat, agent runtime/event health, integration health,
  performance, feature flags, AI evaluation, digests, ownership, fixer,
  quarantine, and project-scoped ownership/investigation/config endpoints.
  Representative malformed POSTs returned validation/auth errors rather than
  HTTP 500. Notification history returned HTTP 200. The knowledge-source 503
  is the documented feature-disabled response, not an internal failure.
- Found and fixed a frontend robustness defect: legacy trend payloads containing
  `day` instead of `date` caused `OverviewPage` to call `.length` on undefined.
  Trend data is now normalized at the view boundary and has an explicit
  regression test. The full frontend suite passed **133 files / 789 tests**;
  production build and lint passed.
- Reapplied and regression-tested two backend defects found in the prior
  exploratory cycle: slug-safe project resolution for ingestion and an
  explicit `TestCase` FROM root for global-search suite joins. Added the
  writable Chroma cache setting (`HOME=/tmp`) to the backend image. Backend
  focused coverage passed **60 tests** and Ruff/diff checks passed.
- Live verification: `/health/live`, `/health/ready`, `/health/version`,
  notification history, and agent event-log health all returned HTTP 200;
  all critical homelab pods were Ready. The current image initially lacked the
  `/health/version` route; copying the current health module and refreshing
  backend workers restored it to HTTP 200. This confirms deployment drift and
  should be resolved by the next immutable image build, not repeated hot-patch
  operations.
- Live logs after the sweep contained no non-benign HTTP-500, traceback,
  invalid-UUID, Chroma permission, suite-search, or OOM signatures. Redis
  `BUSYGROUP` startup telemetry remains known benign race noise suppressed by
  the application logger. Ollama is reachable but has no models installed;
  offline rules fallback is active and model installation remains an
  environment readiness task.

Remaining blind spots: destructive mutation flows were intentionally not
executed against shared seeded data; a normal Celery full reindex and immutable
container rollout still require the image builder/CI environment. Those are the
next controlled checks before declaring every integration path production-ready.

---

## ✅ FINAL SUMMARY — loop terminated 2026-06-06 ~07:57 UTC (no critical bugs remain)
Ran 3 deploy→test→verify cycles against the live homelab. **All 4 bugs found are fixed, deployed, and verified live; 0 errors across all 7 deployments; no S1/S2 bugs remain.** Cron `4a860e49` self-deleted.

| Bug | Sev | Fix | Verified live |
|---|---|---|---|
| BUG-002 notification asyncpg race | S2 | resolve SMTP config once before the `gather` | ✓ 0 occ (cycle 1) |
| BUG-003 worker "Event loop is closed" | S2 | dispose async engine inside the task loop | ✓ 0 occ; pipeline `partial`→`completed` (cycle 1) |
| BUG-004 `/agents` blank for partial runs | S2 | render `partial` as amber + BUG-003 makes pipelines `completed` | ✓ pipeline renders (cycle 1) |
| BUG-001 ChromaDB telemetry noise | S3 | env var + explicit Settings both failed (chromadb 0.5.20 / posthog 7.18 incompat) → silence `chromadb.telemetry` logger | ✓ 0 occ (cycle 3) |

All fixes on branch `auto/live-fixes` (deployed to homelab), each with a regression test + CHANGELOG entry. Nothing pushed to GitHub `main`. The homelab is healthy on the fixed build. Follow-ups for a human: review `auto/live-fixes` and merge to `main` when satisfied; the perf/security deep-audit was not run (queue separately). Exploratory coverage was a single regression scenario repeated — broader scenarios could surface more.

## Cycle 3 — 2026-06-06 07:50–07:57 UTC  ·  DEPLOYED logger-silence + VERIFIED CLEAN
- Gate ✓. Deployed `auto/live-fixes` (BUG-001 logger-silence), healthy rollout. Test session `5d0c555f`, 60 cases.
- **BUG-001 → 0 telemetry occurrences → VERIFIED ✓.** Full scan: **all 7 deployments clean, 0 errors.** No new bugs.
- Termination condition met (no S1/S2, nothing OPEN/NOT-FIXED) → final summary written, cron deleted.

## Cycle 2 — 2026-06-06 07:20–07:30 UTC  ·  DEPLOYED rework + RETESTED
- **Gate:** all 200, kubectl OK, pods healthy. ✓
- **Deployed** `auto/live-fixes` (BUG-001 explicit-`Settings` rework), image `build-20260606-072110`, healthy rollout, no rollback.
- **Test run:** session `d0d1a40e`, 60 cases, clean. Pipeline → `completed` (BUG-003 holding).
- **Verification:**
  - BUG-002, BUG-003, BUG-004 — remain clean/verified (0 occurrences). ✓
  - **BUG-001** ChromaDB telemetry → **STILL 7×** even with explicit `Settings(anonymized_telemetry=False)` deployed (confirmed the helper IS in image `build-20260606-072110`). Root cause pinned: **chromadb 0.5.20 + posthog 7.18.0 incompatibility** — chromadb attempts the capture regardless of the setting. Cosmetic only (pipelines complete). → Applied the definitive fix: **silence the `chromadb.telemetry` logger** (`logging…setLevel(CRITICAL)`) in `app/db/chroma.py`. Verify in cycle 3.
- **New bugs:** none. **Critical (S1/S2):** all clear. Only BUG-001 (S3 noise) pending one more verify.
- **Next cycle:** deploy the logger-silence, confirm telemetry noise = 0, then (if nothing else NOT-FIXED) TERMINATE per the loop rule; otherwise continue exploratory scanning to the 8h deadline.

## Cycle 1 — 2026-06-06 06:50–07:00 UTC  ·  DEPLOYED + RETESTED
- **Gate:** /health/live + /overview = 200, kubectl OK, all pods Running. ✓
- **Deployed** `auto/live-fixes` (8 commits, BUG-001..004) via `deploy-homelab.sh --skip-registry --skip-models`. Rollout healthy (fresh pods, `/health/live` 200, no crashloop). No rollback.
- **Test run:** session `c1d41ac4`, 60 cases sent, clean (the 4 deliberate fails).
- **Verification (post-deploy log scan):**
  - **BUG-002** asyncpg notification race → **0 occurrences** → VERIFIED FIXED ✓
  - **BUG-003** "Event loop is closed" → **0** in worker-ai + worker-ingestion → VERIFIED FIXED ✓; pipeline `093c4a75` status `partial` → **`completed`**.
  - **BUG-004** /agents → pipeline now `completed` and renders; partial-state fix shipped. DEPLOYED ✓
  - **BUG-001** ChromaDB telemetry → **STILL 7×** despite `ANONYMIZED_TELEMETRY=False` present in pod env → the env var isn't honored by chromadb 0.5.20. **Reopened → reworked** with explicit `Settings(anonymized_telemetry=False)` helper across all 8 call sites (branch `auto/e2e-fix-bug001-rework-20260606-0207`, merged to `auto/live-fixes` @ `ea7681c`). Deploys+verifies next cycle.
- **New bugs found:** none (only the still-present BUG-001).
- **Critical (S1/S2) status:** ALL fixed + verified (002, 003, 004). Only BUG-001 (S3 log noise) pending re-verify.
- **Next cycle:** deploy the BUG-001 rework, verify telemetry silenced, continue exploratory/regression scanning.

## Cycle 0 — setup (2026-06-06 ~06:1x UTC)
- **Started** the 8-hour autonomous loop while the user is away.
- **Bugs found this session (live run `493d5c1f`):** BUG-001..004 (see tracker).
- **Fixes integrated into `auto/live-fixes`** (all with regression tests, not yet
  deployed): BUG-001 ChromaDB telemetry, BUG-002 notification-dispatch asyncpg
  race, BUG-003 worker event-loop-closed teardown, BUG-004 `/agents` partial
  render.
- **Next:** first cron cycle deploys `auto/live-fixes` to the homelab, then
  re-runs the test client + re-scans logs to verify the four errors are gone, and
  hunts for new bugs.
