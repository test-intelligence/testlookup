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
| Settings pages (11 routes) | `settings.spec.ts` | smoke |
| My-failures / suites / policies / ownership / value-metrics | `sprint-pages.spec.ts` | medium (mocked) |
| Search / projects / releases / live / intelligence | `features.spec.ts` | smoke |
| **API Keys CRUD (list / generate / revoke)** | **`api-keys.spec.ts`** ✨ | **deep, mocked** |
| **Project Data danger zone (typed-name reset)** | **`project-data-danger-zone.spec.ts`** ✨ | **deep, mocked** |
| **Release-gate policy CRUD (list / new / validate / create)** | **`policies.spec.ts`** ✨ | **deep, mocked** |
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
  / create round-trip. _Still open:_ verify a created policy's **application on
  `/release-gate`** (needs `getEffectivePolicy` mock + reading `ReleaseGatePage`).
- Run **detail → test-case drill-down** — deeper assertions on
  `/runs/:runId` + `/runs/:runId/tests/:testId` with a mocked run payload
  (status counts, failure message, stack trace, history).

**Tier 2 — core flows, shallow today**
- Settings **form save** round-trips (profile edit, AI config mode switch) —
  mock the `PUT` + assert the success toast and persisted value.
- Failure-investigation workflow — `/failures` drill-down → tag → assign.
- Flaky quarantine — `/flaky-coach` recommendation → `/quarantine` approve/deny.
- Faceted **search** — entity-type tabs + result navigation (mocked results).

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
