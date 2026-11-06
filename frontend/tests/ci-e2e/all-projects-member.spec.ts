/**
 * W35 STRAT-01, hermetic (`docs/deferred-scope-2026-10-13/work/W35-contract.md` §1, §3a): "All Projects" for a
 * non-admin who belongs to more than one project, over ONLY their projects.
 *
 *  - a QA lead (not an admin) in Checkout (A) and Payments (B): `GET /projects` lists those two; the top bar offers
 *    All Projects and it stays selected across Overview → Runs → Trends (no auto-select to the first project);
 *  - every read on the three pages goes out with NO `project_id` and no `/projects/<id>/` path; each page shows the
 *    A + B aggregate the fixtures answer for such a read (the backend's member-only aggregate), never one project's;
 *  - no toast (a 403 or 422 raises one) and no request names the foreign project C;
 *  - a tester in ONE project: no All Projects option; a stored All Projects moves to their project, which the page's
 *    reads then pin.
 *
 * The scoped fixtures answer as the API does: no `project_id` → the caller's aggregate; one of their projects → that
 * project alone; the literal `all` → 422 (the API rejects it); any other project → 403. Harness: `tests/lib/rollout.ts`
 * / `production-pages.ts` (fail closed, pinned clock). World: `OVERVIEW_ON`, `RUNS_PAGE` and `TRENDS_ON`
 * (`fixtures.ts`) under the scoped handlers below; the two-project list is `LIVE_PROJECTS` (`fixtures-activity-live.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import {
  landmark,
  openProductionPage,
  respond,
  type ApiHandler,
  type ApiHandlers,
  type ApiRequest,
  type MockedApi,
} from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, requestsTo } from '../lib/rollout'
import {
  isoAgo,
  NOW,
  OVERVIEW_ON,
  PROJECT_ID,
  RUNS_PAGE,
  runListOf,
  runs,
  TREND_SUMMARY_PATH,
  TRENDS_ON,
  trendPoints,
  USER,
  type TrendPoint,
} from '../visual/production/fixtures'
import { LIVE_PROJECTS, SECOND_PROJECT } from '../visual/production/fixtures-activity-live'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const A = PROJECT_ID
const B = SECOND_PROJECT.id
/** A project the QA lead is NOT a member of: `GET /projects` never lists it, and no request may name it. */
const FOREIGN = '31313131-3131-4131-8131-313131313131'

/** The fixture QA lead (`USER`, role QA_LEAD): a member of A and B. */
const QA_LEAD = USER
const TESTER = { ...USER, id: '00000000-0000-4000-8000-0000000000e5', username: 'tester', full_name: 'Tester', role: 'TESTER' }

type Scope = readonly string[]

/** Answer as the API does: no project → the caller's aggregate; a member project → it alone; `all` → 422; else 403. */
function scoped(members: Scope, body: (projects: Scope, request: ApiRequest) => unknown): ApiHandler {
  return (request) => {
    const asked = request.url.searchParams.get('project_id')
    if (asked === null) return body(members, request)
    if (asked === 'all') return respond(422, { detail: 'project_id: Input should be a valid UUID' })
    if (members.includes(asked)) return body([asked], request)
    return respond(403, { detail: 'You do not have access to this project' })
  }
}

/** The world's own handler for `path` (first exact match), wrapped below. */
function handlerOf(handlers: ApiHandlers, path: string): ApiHandler {
  const hit = handlers.find(([matcher]) => matcher === path)
  if (!hit) throw new Error(`the world has no handler for ${path}`)
  return hit[1]
}

// ── Per-project numbers: the aggregate is their sum ────────────────────────

/** The Overview KPIs of each project (`/metrics/summary`). */
const KPI: Record<string, { total: number; newFailures: number; flaky: number; defects: number }> = {
  [A]: { total: 1_240, newFailures: 6, flaky: 3, defects: 4 },
  [B]: { total: 760, newFailures: 3, flaky: 2, defects: 3 },
}
const kpiSum = (projects: Scope, key: keyof (typeof KPI)[string]) => projects.reduce((s, p) => s + KPI[p][key], 0)

const baseSummary = handlerOf(OVERVIEW_ON, '/api/v1/metrics/summary')
function summaryOf(projects: Scope, request: ApiRequest) {
  const base = baseSummary(request) as Record<string, Record<string, unknown>>
  return {
    ...base,
    total_executions_7d: { ...base.total_executions_7d, value: kpiSum(projects, 'total') },
    new_failures_24h: { ...base.new_failures_24h, value: kpiSum(projects, 'newFailures') },
    flaky_test_count: { ...base.flaky_test_count, value: kpiSum(projects, 'flaky') },
    active_defects: { ...base.active_defects, value: kpiSum(projects, 'defects') },
  }
}

/** B's day: a steady 40 passed, 2 failed, 1 skipped on every day A ran (`trendPoints`). */
function paymentsDay(date: string): TrendPoint {
  return { date, passed: 40, failed: 2, skipped: 1, broken: 0, total: 43, pass_rate: 95.24 }
}

/** `/metrics/trends` day series over `projects`, per day the sum of each project's day. */
function trendsOf(projects: Scope, days: number): TrendPoint[] {
  return trendPoints(days).map((a) => {
    const parts = projects.map((p) => (p === A ? a : paymentsDay(a.date)))
    const add = (key: 'passed' | 'failed' | 'skipped' | 'broken' | 'total') => parts.reduce((s, d) => s + d[key], 0)
    const passed = add('passed')
    const evaluated = passed + add('failed') + add('broken')
    return {
      date: a.date,
      passed,
      failed: add('failed'),
      skipped: add('skipped'),
      broken: add('broken'),
      total: add('total'),
      pass_rate: Math.round((passed / evaluated) * 10_000) / 100,
    }
  })
}
const daysOf = (request: ApiRequest, fallback: number) => Number(request.url.searchParams.get('days') ?? fallback)

/** B's three builds (`payments-ci`, Run #512-#510), between A's; the aggregate list is A's thirteen and these, newest first. */
function paymentsRuns() {
  return runs().slice(0, 3).map((run, i) => {
    const start = isoAgo(i * 2, 9)
    return {
      ...run,
      id: `b5555555-5555-4555-8555-00000000000${i}`,
      project_id: B,
      project_name: 'Payments',
      build_number: 512 - i,
      jenkins_job: 'payments-ci',
      run_seq: 512 - i,
      primary_suite_name: 'Payments',
      suite_names: ['Payments'],
      wall_clock_ms: null,
      timing_source: 'derived_sum',
      exec_started_at: null,
      exec_finished_at: null,
      start_time: start,
      end_time: new Date(Date.parse(start) + run.duration_ms).toISOString(),
      created_at: start,
    }
  })
}
function runsOf(projects: Scope) {
  const all = [...(projects.includes(A) ? runs() : []), ...(projects.includes(B) ? paymentsRuns() : [])]
  return all.sort((x, y) => Date.parse(y.created_at) - Date.parse(x.created_at))
}

/** Overview, Runs and Trends for a caller in `members`: `GET /projects` lists them; every scoped read is wrapped. */
function memberWorld(members: Scope): ApiHandlers {
  const projects = LIVE_PROJECTS.filter((p) => members.includes(p.id))
  const trendSummary = handlerOf(TRENDS_ON, TREND_SUMMARY_PATH)
  return [
    ['/api/v1/projects', () => projects],
    ['/api/v1/metrics/summary', scoped(members, summaryOf)],
    ['/api/v1/metrics/trends', scoped(members, (ps, r) => ({ data: trendsOf(ps, daysOf(r, 30)), period_days: daysOf(r, 30) }))],
    ['/api/v1/runs', scoped(members, (ps, r) => runListOf(() => runsOf(ps), () => null)(r))],
    [TREND_SUMMARY_PATH, scoped(members, (_, r) => trendSummary(r))],
    ...OVERVIEW_ON,
    ...RUNS_PAGE,
    ...TRENDS_ON,
  ]
}

const picker = (p: Page) => p.getByRole('combobox', { name: 'Select project' })
const nav = (p: Page, name: RegExp) => p.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name })
const kpi = (p: Page, title: string) =>
  p.locator('[data-kpi-strip] [data-metric-card="compact"]').filter({ has: p.getByText(title, { exact: true }) }).locator('p.tabular-nums')
/** react-hot-toast's toasts (its fixed container), never a page's own status lines (`in-page-sweep.spec.ts`). */
const toasts = (p: Page) => p.locator('div[style*="z-index: 9999"] [role="status"]')
const storedProject = (p: Page) =>
  p.evaluate(() => JSON.parse(localStorage.getItem('testlookup-active-project') ?? '{}')?.state?.activeProjectId ?? null)

/** Every request line that names a project: a `project_id` parameter or a `/projects/<id>` path. */
const namingAProject = (api: MockedApi) =>
  observed(api).filter((line) => /[?&]project_id=/.test(line) || /\/projects\/[^/?]+/.test(line.split('?')[0]))

function open(page: Page, user: typeof USER, members: Scope, path: string, ready: (p: Page) => ReturnType<Page['locator']>) {
  return openProductionPage(page, path, { theme: 'signal', now: NOW, me: user, user, projectId: 'all', handlers: memberWorld(members), ready })
}

test('a QA lead in two projects: All Projects stays selected; Overview, Runs and Trends read A + B with no project_id', async ({ page }) => {
  const { api, errors } = await open(page, QA_LEAD, [A, B], '/overview', (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }))
  await networkQuiet(page, api)

  // The top bar offers All Projects to a member of two projects, and keeps it.
  await expect(picker(page).locator('option')).toHaveText(['— Select project —', 'All Projects', 'Checkout', 'Payments'])
  await expect(picker(page)).toHaveValue('all')
  // Overview: the KPIs are A + B (1 240 + 760 executions, 6 + 3 new failures, 3 + 2 flaky, 4 + 3 defects).
  await expect(kpi(page, 'Total executions')).toHaveText('2000')
  await expect(kpi(page, 'New failures · 24h')).toHaveText('9')
  await expect(kpi(page, 'Flaky tests')).toHaveText('5')
  await expect(kpi(page, 'Active defects')).toHaveText('7')

  // Runs (in the app, not a reload): A's thirteen builds and B's three (Run #512-#510) in one list.
  await nav(page, /^Runs/).click()
  await expect(page.locator('[data-status-banner]')).toBeVisible()
  const table = page.locator('[data-primary]').getByRole('table')
  await expect(table.locator('tbody tr')).toHaveCount(runs().length + paymentsRuns().length)
  for (const seq of ['Run #60', 'Run #512', 'Run #511', 'Run #510']) await expect(table).toContainText(seq)
  await expect(picker(page)).toHaveValue('all')

  // Trends (14 days): the Executions tile sums the A + B day series — A's 1 205 and B's 7 days × 43.
  await nav(page, /^Trends/).click()
  await expect(landmark(page, 'Trend metrics')).toBeVisible()
  const executions = (ps: Scope) => trendsOf(ps, 14).reduce((s, d) => s + d.total, 0)
  expect([executions([A]), executions([A, B])]).toEqual([1_205, 1_506])
  await expect(kpi(page, 'Executions')).toHaveText('1506')
  await networkQuiet(page, api)

  // Every read of the three pages: the aggregate ones were asked with no project, and nothing named one.
  for (const path of ['/api/v1/metrics/summary', '/api/v1/metrics/trends', '/api/v1/runs', TREND_SUMMARY_PATH]) {
    expect(requestsTo(api, path).length, `${path} was read`).toBeGreaterThan(0)
  }
  await expect(picker(page)).toHaveValue('all')
  expect(await storedProject(page)).toBe('all')
  expect(namingAProject(api), 'no read names a project in All Projects').toEqual([])
  expect(observed(api).filter((line) => line.includes(FOREIGN)), 'no request names the foreign project').toEqual([])
  await expect(toasts(page), 'no toast (a 403 or 422 raises one)').toHaveCount(0)
  assertHermetic(api, errors)
})

test('a tester in ONE project: no All Projects option; a stored All Projects moves to their project, which the reads pin', async ({ page }) => {
  const { api, errors } = await open(page, TESTER, [A], '/overview', (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }))

  await expect(picker(page)).toHaveValue(A)
  await expect(picker(page).locator('option')).toHaveText(['— Select project —', 'Checkout'])
  await expect.poll(() => storedProject(page)).toBe(A)
  // Overview: Checkout's own KPIs, from a read pinned to it.
  await expect(kpi(page, 'Total executions')).toHaveText('1240')
  await expect(kpi(page, 'Active defects')).toHaveText('4')
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/metrics/summary').filter((line) => line.includes(`project_id=${A}`)).length).toBeGreaterThan(0)
  expect(observed(api).filter((line) => line.includes(B) || line.includes(FOREIGN)), 'no request names another project').toEqual([])
  await expect(toasts(page), 'no toast').toHaveCount(0)
  assertHermetic(api, errors)
})
