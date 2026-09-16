# EXP-BUG-013 — release detail deep links lost scope and targets

**Mission / requirement / severity:** M04, connected release lineage across UI
reloads, P1.

Release cards and page tests use `/releases/:releaseId`, but the production
router registered only `/releases`. A direct release link therefore matched the
catch-all route and sent the operator to Dashboard. Registering the path exposed
a second defect: `ReleasesPage` did not read `releaseId`, so the portfolio
rendered while the requested release's linked runs remained collapsed.

The production router now maps `/releases/:releaseId` to `ReleasesPage`.
`ReleasesPage` resolves that route with the authorized detail endpoint rather
than the persisted project's release list, renders the requested release with
its own project label, and initializes its inline expansion. Phase rows expose
the IDs already emitted by the production “Open phase” links and scroll after
the async detail response renders. Routed errors retry the detail request, and
the authorized project inventory supplies the name omitted by the detail API.

**Regression tests:** `frontend/src/App.routes.test.tsx` navigates through the
real route table and requires the Releases page. Releases page tests require
the inline detail, independently fetched target instead of the persisted
project's list, correct project label, real phase ID, and delayed scroll. The
deployed M04 journey persists an unrelated project and still requires the
release heading, `Linked Test Runs (1)`, and linked build in all three browsers.
It pins only the project-picker inventory so that selection survives refresh;
the release and lineage assertions remain live homelab requests.

**Red evidence:** deployed revision `e657d28c6a48d7e2b4ff964ddec2c76dbe22eb2c`
fell through to Dashboard. Deployed revision
`d04e5bb882dc7ff41f8c00fc7cba367314819abc` rendered the Releases portfolio but
did not open the requested detail. Independent review of `7e2630ed` found that
the requested release still disappeared under a different persisted project
and the production phase hash had no DOM target or delayed scroll. The red runs
reproduced the first two failures in all three browsers; retained trace
directories are from the final green run.

**Green evidence:** exact revision
`49ce63a18245292be95804cf2ae7c2a6ca28066e` served as
`build-20260916-224030` with healthy dependencies and schema `0189`. Focused
Vitest passed 5 tests in 2 files, TypeScript and targeted ESLint were clean,
all 43 quality guards passed, and the unrelated-project live journey passed in
Chromium, Firefox, and WebKit.

**Mutation:** each harness asserted its mutation was present before executing
the regression. Changing the route and initializing `expandedId` to null were
killed by the original tests. Replacing the route-independent detail result
with the persisted-project list and removing the `phase-` DOM target were
killed by the final regression. Mutations that retried the unrelated list and
dropped project-name resolution also failed. Source files were restored
byte-for-byte.

**Independent review:** APPROVE on HEAD
`49ce63a18245292be95804cf2ae7c2a6ca28066e`; reviewed tracked diff
`5b777d527761de4ac18a4129143f6572e2c6ef7d`, M04 evidence blob
`dba49b52d08202ef9dc6fa5f13298d300ecfe26e`, and this pre-annotation evidence
blob `2709857b35e447cdae7d56be69879fd454615d67`.
