/**
 * VIZ-303 / VIZ-306 cross-page flow, with `viz_multi_filters` ON:
 *
 *   select 2 releases + 2 suites on Trends → navigate to Coverage by an in-app
 *   link → the same selection applies (URL carries it, requests carry it) →
 *   reload restores it.
 *
 * The selection is made through the shareable URL — the multi-select filter
 * bar is the presentation half of Epic 3 and this spec must not depend on it.
 * The backend is stubbed at the network edge exactly like the other ci-e2e
 * specs (seeded auth + project in localStorage, `page.route` for /api/v1).
 */
import { expect, test, type Page, type Request, type Route } from '@playwright/test'

const PROJECT = '00000000-0000-4000-8000-000000000101'
const R1 = '00000000-0000-4000-8000-0000000000a1'
const R2 = '00000000-0000-4000-8000-0000000000a2'

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'lead@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

const projects = [
  { id: PROJECT, name: 'Alpha Project', slug: 'alpha-project', description: '', is_active: true, created_at: '2026-09-16T03:00:00Z' },
]

const releases = [
  { id: R1, project_id: PROJECT, name: '2026.09', version: null, description: null, status: 'active' },
  { id: R2, project_id: PROJECT, name: '2026.10', version: null, description: null, status: 'active' },
]

function run(id: string, suite: string) {
  return {
    id,
    project_id: PROJECT,
    project_name: 'Alpha Project',
    build_number: id,
    branch: 'main',
    status: 'PASSED',
    passed_tests: 10,
    failed_tests: 0,
    skipped_tests: 0,
    broken_tests: 0,
    total_tests: 10,
    pass_rate: 100,
    duration_ms: 1000,
    created_at: '2026-09-16T03:00:00Z',
    start_time: '2026-09-16T03:00:00Z',
    end_time: '2026-09-16T03:00:01Z',
    ingestion_source: 'upload',
    primary_suite_name: suite,
    suite_names: [suite],
    run_seq: 1,
  }
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function scopeOf(request: Request) {
  const url = new URL(request.url())
  return {
    path: url.pathname,
    releases: url.searchParams.getAll('release_id').sort(),
    suites: url.searchParams.getAll('suite_name').sort(),
    raw: url.search,
  }
}

async function stub(page: Page, analyticsCalls: ReturnType<typeof scopeOf>[]) {
  await page.addInitScript(({ seedUser, projectId }) => {
    if (!localStorage.getItem('auth-storage')) {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: { token: 'access', refreshToken: 'refresh', user: seedUser, isAuthenticated: true },
        version: 0,
      }))
    }
    if (!localStorage.getItem('testlookup-active-project')) {
      localStorage.setItem('testlookup-active-project', JSON.stringify({
        state: { activeProjectId: projectId },
        version: 0,
      }))
    }
  }, { seedUser: user, projectId: PROJECT })

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/auth/me') return json(route, user)
    if (path === '/api/v1/projects') return json(route, projects)
    if (path === '/api/v1/feature-flags/viz_multi_filters/status') {
      return json(route, { key: 'viz_multi_filters', enabled: true })
    }
    if (path.startsWith('/api/v1/feature-flags/')) return json(route, { key: path.split('/')[4], enabled: false })
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === '/api/v1/test-management/suites') return json(route, [])
    if (path === '/api/v1/releases') return json(route, { items: releases, total: 2, page: 1, size: 100 })
    if (path === '/api/v1/me/assigned-failures/count') return json(route, { count: 0 })
    if (path === '/api/v1/runs') {
      return json(route, { items: [run('run-1', 'payments'), run('run-2', 'cart')], total: 2, page: 1, size: 100, pages: 1 })
    }
    if (path.startsWith('/api/v1/analytics/') || path.startsWith('/api/v1/metrics/')) {
      analyticsCalls.push(scopeOf(route.request()))
    }
    return json(route, {})
  })
}

test('two releases and two suites follow the user from Trends to Coverage and survive a reload', async ({ page }) => {
  const analyticsCalls: ReturnType<typeof scopeOf>[] = []
  await stub(page, analyticsCalls)

  // ── Trends, selection carried by the link ──────────────────────────────
  await page.goto(`/trends?release=${R2}&release=${R1}&suites=payments&suites=cart&window=14`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  // The top-bar picker reports the multi-selection instead of naming one —
  // as a read-only summary button, not a <select> (a11y M5).
  await expect(page.getByRole('button', { name: /^Filter by release/ })).toHaveText(/2 releases/)
  await expect.poll(() =>
    analyticsCalls.some(c => c.releases.join() === [R1, R2].sort().join() && c.suites.join() === 'cart,payments'),
  ).toBe(true)

  // ── In-app link to Coverage: the link does not carry the filter ───────
  // (Not reset here: Trends and Coverage share SWR keys for coverage/trends,
  // so an identical scope is served from cache rather than re-requested —
  // which is itself the evidence that the scope is the same.)
  await page.getByRole('link', { name: 'Coverage', exact: true }).first().click()
  await expect(page).toHaveURL(/\/coverage\?/)

  const coverageUrl = new URL(page.url())
  expect(coverageUrl.searchParams.getAll('release').sort()).toEqual([R1, R2].sort())
  expect(coverageUrl.searchParams.getAll('suites').sort()).toEqual(['cart', 'payments'])
  expect(coverageUrl.searchParams.get('window')).toBe('14')

  // The requests Coverage makes carry the same scope — repeated bare keys,
  // never `release_id[]`.
  await expect.poll(() =>
    analyticsCalls.some(c => c.path === '/api/v1/analytics/coverage'
      && c.releases.join() === [R1, R2].sort().join()
      && c.suites.join() === 'cart,payments'),
  ).toBe(true)
  expect(analyticsCalls.every(c => !c.raw.includes('%5B%5D') && !c.raw.includes('[]'))).toBe(true)

  // ── Reload restores it ─────────────────────────────────────────────────
  analyticsCalls.length = 0
  await page.reload()
  await expect(page).toHaveURL(/\/coverage\?/)
  const reloaded = new URL(page.url())
  expect(reloaded.searchParams.getAll('release').sort()).toEqual([R1, R2].sort())
  expect(reloaded.searchParams.getAll('suites').sort()).toEqual(['cart', 'payments'])
  await expect.poll(() =>
    analyticsCalls.some(c => c.path === '/api/v1/analytics/coverage'
      && c.releases.join() === [R1, R2].sort().join()
      && c.suites.join() === 'cart,payments'),
  ).toBe(true)
})

test('a keystroke on the header controls never collapses a multi-selection (a11y M5)', async ({ page }) => {
  const analyticsCalls: ReturnType<typeof scopeOf>[] = []
  await stub(page, analyticsCalls)
  await page.goto(`/trends?release=${R2}&release=${R1}&suites=payments&suites=cart&window=14`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

  // Located by LABEL, not role: the assertion is about what a keystroke does
  // to the selection, whatever element the control is (a native <select>
  // commits the next option on ArrowDown — the bug).
  const release = page.getByLabel(/^Filter by release/)
  await expect(release).toBeVisible()
  await release.focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('2')

  const suite = page.getByLabel(/^Test suite/)
  await expect(suite).toBeVisible()
  await suite.focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('c')

  // Give any (wrong) store -> URL write time to land.
  await page.waitForTimeout(600)
  const url = new URL(page.url())
  expect(url.searchParams.getAll('release').sort()).toEqual([R1, R2].sort())
  expect(url.searchParams.getAll('suites').sort()).toEqual(['cart', 'payments'])
  await expect(release).toHaveText(/2 releases/)
  await expect(suite).toHaveText(/2 suites/)
})
