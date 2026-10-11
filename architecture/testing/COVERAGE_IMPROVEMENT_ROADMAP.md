# Coverage Improvement Roadmap

## Now — blocking, deterministic evidence

1. Keep frontend coverage above 60/55/51/61 and investigate every decrease.
2. Keep the three hermetic authentication journeys blocking in Chromium.
3. Hermetic pending/accept/reject review journeys and producer/reviewer
   separation are implemented. Notification redaction remains because its
   security authority is the backend distribution gate.
4. The PostgreSQL service integration fixture spanning ingest, intelligence,
   defect promotion and release decision shipped in PR #119. Extend its shared
   lineage to a deployed UI/API journey using real Redis workers (M04).

Exit target: GAP-01 has blocking coverage for auth, review, release and defect
paths (release/defect browser expansion remains); GAP-03's service-level proof
is implemented, with deployed full-stack proof remaining.

## Error sweeps — every page, every action (owner request 2026-10-10)

Two test types that look for errors instead of asserting one behaviour:

1. **No page or GET endpoint answers 500.**
   - *CI, blocking:* `backend/tests/integration/test_every_get_route_answers_without_500_postgres.py`
     calls every `GET /api/v1` route of the real app, as an admin and as a
     viewer, against seeded PostgreSQL/Mongo/Redis; any 500 fails the build
     with the route named. 502/503/504 (a dependency the job does not run) are
     reported, not failed.
   - *Live:* `frontend/tests/sweeps/route-sweep.spec.ts` visits every route in
     `App.tsx` as every role in a fixtures file (`backend/scripts/sweep_fixtures.py`,
     tokens minted in the backend container) and fails on a 5xx, an uncaught
     page error or an error screen. `src/routing/sweepRoutes.test.ts` fails when
     `App.tsx` gains a route the sweep lacks.
2. **No UI action raises a UI or backend error.** `frontend/tests/sweeps/action-sweep.spec.ts`
   exercises each page's read-only controls (tabs, filters, disclosure and sort
   toggles, view/open buttons, one row per table) and fails on any 5xx, page
   error or error screen an action causes. It never clicks anything that
   writes, sends, runs, deletes or downloads.

Run: `SWEEP_BASE_URL=... SWEEP_FIXTURES=... npx playwright test -c playwright.sweep.config.ts`
(findings in `frontend/sweep-results/*.jsonl`).

First run (homelab, 444 page visits, 581 actions, 272 GET routes x 5 roles):
`GET /training/status` 500 for every role; viewers offered a QA-engineer-only
"Refresh" (403 + toast); a raw `/api/v1/...` path in the review banner;
duplicate error toasts; `must_change_password` enforced only in the browser.
All fixed with regression tests.

Next: run the live sweeps after every homelab deploy; add the remaining roles
to the action sweep; extend the action sweep to write actions in a disposable
project (create/edit/delete with cleanup), which the read-only allowlist skips.

## Next — risk-weighted expansion

1. Raise frontend branch/function floors only after tests cover the lifecycle,
   integration settings, RAG and analytics gaps; raise one point at a time.
2. MCP and CLI now have separate measured CI floors (41% and 62%). Raise each
   independently as its low-coverage tools and commands gain behavior tests.
3. The shell skip-link and project-dialog keyboard journeys are blocking.
   Add axe scans and extend keyboard coverage across the remaining critical
   user paths.
4. Version live fixtures and emit a machine-readable manifest of build, data,
   scenario, result, trace and correlation IDs.
5. Investigator narrative review subjects shipped in PR #119. Execute the full
   distribution matrix against real sinks, including retry after acceptance
   and rejected/superseded content (M07–M08).

Exit: no P0 module below 70% branch coverage without a documented reason; all
critical requirements have an asserted negative case.

## Later — operational confidence

1. Extend the blocking two-project run-list outage canary to production-safe
   list/detail/search/export probes in staging and production.
2. Schedule provider paired evals and drift review; never promote a tier from
   unit-test results.
3. Conduct quarterly broker/database/provider fault exercises and verify alerts
   both fire and clear.
4. Track escaped defects back to a requirement and missing test layer; add the
   smallest durable regression at that layer.

## Governance

The next execution programme is [the Sol exploratory package](EXPLORATORY_EXECUTION_PACKAGE.md),
prepared on 2026-09-16 without executing tests. It uses one consolidated branch,
one PR and one merge, with mandatory unit and real E2E tests for every defect.

- Coverage floors may increase independently; do not update all baselines in a
  single cleanup.
- New critical behavior needs a regression that fails when the behavior is
  reversed, plus authorization and boundary cases where relevant.
- Quarantined tests have an owner, issue and expiry. A retry is diagnostic, not
  evidence of stability.
- Review this map monthly and after each incident, new provider, new public
  status, new migration pattern, or new distribution channel.
