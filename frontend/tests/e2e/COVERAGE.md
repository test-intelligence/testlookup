# E2E Coverage Map & Roadmap

Playwright e2e suite for the TestLookup SPA. Runs against a **live** stack
(frontend :3000 + backend :8000); `globalSetup` authenticates once via
`/auth/dev-login` and stores the JWT in `storageState`. Per-test auth uses
`performRealLogin()` (fast-path + dev-login/form fallbacks). Deterministic
flows (CRUD, error paths, modals) intercept the API via `apiMock.ts`.

```bash
make test-e2e                 # docker compose exec frontend npm run test:e2e
npm run test:e2e              # local (needs the stack up + dev-login enabled)
npx playwright test --list    # compile/collect only (no server/browser)
```

## Test infrastructure
| File | Purpose |
|------|---------|
| `global-setup.ts` | one-time dev-login → `storageState` |
| `realLoginHelper.ts` | `performRealLogin(page)` per-test auth |
| `mockHelper.ts` | legacy login/route mocks |
| `apiMock.ts` | `mockJson` / `mockError` / `seedActiveProject` (deterministic flows) |

## Coverage status by area

| Area | Spec | Depth |
|------|------|-------|
| Login happy-path + dev-login | `auth.spec.ts` | medium |
| **Login error / register validation / reset-password / logout / protected-route redirect** | **`auth-flows.spec.ts`** ✨ | **deep, deterministic** |
| Sidebar navigation | `navigation.spec.ts` | smoke |
| Overview / dashboard | `dashboard.spec.ts` | smoke |
| Runs list + run detail | `test-runs.spec.ts` | shallow |
| Defect intake modal + failures | `defects-failures.spec.ts` | medium |
| **Failure investigation (defect create round-trip + failed-test reassign)** | **`failure-investigation.spec.ts`** ✨ | **deep, mocked** |
| Settings pages (11 routes) | `settings.spec.ts` | smoke |
| **Settings form saves (profile PATCH + AI-config mode PUT)** | **`settings-form-save.spec.ts`** ✨ | **deep, mocked** |
| **Flaky quarantine review (approve / reject / release)** | **`flaky-quarantine.spec.ts`** ✨ | **deep, mocked** |
| My-failures / suites / policies / ownership / value-metrics | `sprint-pages.spec.ts` | medium (mocked) |
| Search / projects / releases / live / intelligence | `features.spec.ts` | smoke |
| **Faceted search (scope facet re-query + result nav + mode facet)** | **`search-faceted.spec.ts`** ✨ | **deep, mocked** |
| **API Keys CRUD (list / generate / revoke)** | **`api-keys.spec.ts`** ✨ | **deep, mocked** |
| **Project Data danger zone (typed-name reset)** | **`project-data-danger-zone.spec.ts`** ✨ | **deep, mocked** |
| **Release-gate policy CRUD (list / new / validate / create)** | **`policies.spec.ts`** ✨ | **deep, mocked** |
| **Release-gate policy *application* (badge + per-rule + hardcoded fallback)** | **`release-gate-policy.spec.ts`** ✨ | **deep, mocked** |
| **Run detail → test-case drill-down (summary / table / filter / stack trace)** | **`run-detail-drilldown.spec.ts`** ✨ | **deep, mocked** |
| Tier 0-2 feature pages | `tier-0-2-features.spec.ts` | smoke |
| Workflow timeline component | `workflow-visual-language.spec.ts` | smoke |
| 2026-05-19 regressions | `regression-2026-05-19.spec.ts` | medium |
| Backlog API contracts | `backlog-pending-contracts.spec.ts` | API probe |

✨ = added/enhanced in this pass.

## Prioritized roadmap (remaining gaps)

Built with `apiMock.ts` helpers; ordered by risk × frequency.

**Tier 1 — high-risk**
- ✅ `project-data` danger zone — done (`project-data-danger-zone.spec.ts`).
- ✅ Release **policy CRUD** — done (`policies.spec.ts`): list / new / validation
  / create round-trip.
- ✅ Release **policy application** — done (`release-gate-policy.spec.ts`): the
  policy badge (level + version), the per-rule "Policy Rules Evaluated"
  breakdown, and the hardcoded-fallback → "system defaults" label. Mocks
  `GET /api/v1/release-readiness/:runId`.
- ✅ Run **detail → test-case drill-down** — done (`run-detail-drilldown.spec.ts`):
  run summary header + per-test table, FAILED status-filter re-query, and the
  row-click through to `/runs/:runId/tests/:testId` (stack trace + AI panel).
  One catch-all mock over `/api/v1/runs**` honouring the `?status=` filter.

**Tier 2 — core flows, shallow today**
- ✅ Settings **form save** round-trips — done (`settings-form-save.spec.ts`):
  profile name + avatar `PATCH /api/v1/auth/me`, AI-config analysis-mode switch
  `PUT /api/v1/settings/ai`, each asserting the success toast + the persisted
  value forwarded to the backend.
- ✅ Failure-investigation workflow — done (`failure-investigation.spec.ts`):
  the real mutations behind triage — defect intake **create** round-trip
  (`POST /api/v1/analytics/defects` with severity + failure_category) and
  failed-test **reassign** (`PUT /api/v1/me/assigned-failures/:id/reassign`,
  QA_LEAD+). The `/failures` analytics page itself stays read-only (Phase-2
  placeholder CTAs), so those surfaces carry the workflow.
- ✅ Flaky quarantine — done (`flaky-quarantine.spec.ts`): the QA_LEAD review
  state machine on `/quarantine` — approve / reject a proposal and release an
  active quarantine, each asserting the notes prompt + the POST to
  `/api/v1/quarantine/:id/{approve,reject,release}` and the success toast.
- ✅ Faceted **search** — done (`search-faceted.spec.ts`): the scope chip
  re-queries `GET /api/v1/search/global` with an `entity_types` filter (the
  result set demonstrably narrows), a result row navigates to its entity, and
  the mode chip updates the retrieval provenance footer.

**Tier 3 — breadth/edge**
- Role-based access (VIEWER/QA_ENGINEER vs ADMIN) — management routes hidden /
  redirected; use `dev-login?role=` to mint each role's `storageState`.
- Table filter / sort / pagination on `/runs`, `/suites`, `/defects`.
- Real-time polling freshness on `/live` and `/my-failures`.

## Conventions for new specs
- Auth with `performRealLogin(page)` in `beforeEach`.
- Deterministic data → mock via `apiMock.ts`; never assert on live row counts.
- Project-gated pages → `seedActiveProject(page)` before navigating, and stay
  tolerant of the "Select a project" gate (don't false-fail when seeding
  doesn't engage).
- Prefer role/text/`#id` selectors over CSS classes (theme classes churn).
