/**
 * Exploratory UI probe against the live homelab.
 *
 * Not a pass/fail regression suite — it walks the real SPA and REPORTS what it
 * observes, so a human (or the next loop iteration) can judge it. It captures
 * the evidence class this codebase's known bugs actually leave behind:
 *
 *   * console errors / unhandled rejections
 *   * failed network requests (4xx / 5xx) with the URL that produced them
 *   * pages that render no meaningful content (the documented "empty page,
 *     no error" failure) — distinguishing an honest empty state from a break
 *
 * Auth is seeded directly into localStorage (the shapes zustand-persist uses)
 * rather than driven through the login form, so a login-page change cannot
 * silently block the whole exploration.
 */
import { test, expect, type ConsoleMessage, type Request } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT_ID = '2aefa4fa-8917-448b-9895-9cd41911ef95'
const PROJECT_NAME = 'Checkout Service'

/** Pages worth walking, in the order a QA lead would. */
const ROUTES: Array<{ path: string; label: string }> = [
  { path: '/overview', label: 'Overview' },
  { path: '/runs', label: 'Runs' },
  { path: '/failures', label: 'Failure analysis' },
  { path: '/test-management', label: 'Test management' },
  { path: '/flaky-coach', label: 'Flaky coach' },
  { path: '/quarantine', label: 'Quarantine' },
  { path: '/coverage', label: 'Coverage' },
  { path: '/trends', label: 'Trends' },
  { path: '/reports/summary', label: 'Summary report' },
  { path: '/value-metrics', label: 'Value metrics (ROI)' },
  { path: '/search', label: 'Search' },
  { path: '/intelligence', label: 'Intelligence hub' },
  { path: '/defects', label: 'Defects' },
  { path: '/settings', label: 'Settings index' },
  { path: '/settings/ai', label: 'Settings — AI config' },
  { path: '/settings/retention', label: 'Settings — Retention' },
  { path: '/settings/mfa-policy', label: 'Settings — MFA policy' },
  { path: '/settings/gitlab', label: 'Settings — GitLab' },
]

/** Noise that is not evidence of a defect. */
function isIgnorableConsole(text: string): boolean {
  return (
    text.includes('Download the React DevTools') ||
    text.includes('[vite]') ||
    text.includes('favicon')
  )
}

test('exploratory walk of the live UI', async ({ page, request }) => {
  // ── authenticate via the API, then seed the stores the SPA reads ──────────
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(login.ok(), 'login must succeed before exploring').toBeTruthy()
  const auth = await login.json()

  await page.addInitScript(
    ([token, refresh, pid, pname]) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({
          state: {
            token,
            refreshToken: refresh,
            user: { username: 'admin', role: 'ADMIN', email: 'admin@testlookup.local' },
            isAuthenticated: true,
          },
          version: 0,
        }),
      )
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({
          state: { activeProjectId: pid, activeProject: { id: pid, name: pname } },
          version: 0,
        }),
      )
    },
    [auth.access_token, auth.refresh_token ?? '', PROJECT_ID, PROJECT_NAME] as const,
  )

  const report: string[] = []

  for (const route of ROUTES) {
    const consoleErrors: string[] = []
    const failedRequests: string[] = []
    const pageErrors: string[] = []

    const onConsole = (m: ConsoleMessage) => {
      if (m.type() === 'error' && !isIgnorableConsole(m.text())) {
        consoleErrors.push(m.text().slice(0, 160))
      }
    }
    const onPageError = (e: Error) => pageErrors.push(String(e.message).slice(0, 160))
    const onResponse = async (r: { status: () => number; url: () => string }) => {
      const s = r.status()
      if (s >= 400 && r.url().includes('/api/')) {
        failedRequests.push(`${s} ${r.url().replace(BASE, '').slice(0, 120)}`)
      }
    }
    const onRequestFailed = (r: Request) => {
      if (r.url().includes('/api/')) {
        failedRequests.push(`NETFAIL ${r.url().replace(BASE, '').slice(0, 120)}`)
      }
    }

    page.on('console', onConsole)
    page.on('pageerror', onPageError)
    page.on('response', onResponse)
    page.on('requestfailed', onRequestFailed)

    let bodyLen = 0
    let heading = ''
    let navError = ''
    try {
      await page.goto(`${BASE}${route.path}`, { waitUntil: 'networkidle', timeout: 45_000 })
      // Let SWR settle: most pages fetch on mount.
      await page.waitForTimeout(1500)
      bodyLen = (await page.locator('main, body').first().innerText().catch(() => '')).trim().length
      heading = (await page.locator('h1, h2').first().innerText().catch(() => '')).trim().slice(0, 60)
    } catch (e) {
      navError = String((e as Error).message).split('\n')[0].slice(0, 120)
    }

    page.off('console', onConsole)
    page.off('pageerror', onPageError)
    page.off('response', onResponse)
    page.off('requestfailed', onRequestFailed)

    const flags: string[] = []
    if (navError) flags.push(`NAV_ERROR(${navError})`)
    if (pageErrors.length) flags.push(`PAGE_ERROR x${pageErrors.length}`)
    if (consoleErrors.length) flags.push(`CONSOLE x${consoleErrors.length}`)
    if (failedRequests.length) flags.push(`HTTP_FAIL x${failedRequests.length}`)
    if (!navError && bodyLen < 200) flags.push(`THIN_RENDER(${bodyLen} chars)`)

    report.push(
      `${flags.length ? '⚠' : '·'} ${route.label.padEnd(26)} ${route.path.padEnd(24)} ` +
        `chars=${String(bodyLen).padStart(5)} h="${heading}" ${flags.join(' ') || 'ok'}`,
    )
    for (const e of pageErrors.slice(0, 3)) report.push(`      PAGEERR: ${e}`)
    for (const e of consoleErrors.slice(0, 3)) report.push(`      CONSOLE: ${e}`)
    for (const f of [...new Set(failedRequests)].slice(0, 5)) report.push(`      HTTP:    ${f}`)
  }

  console.log('\n===== EXPLORATORY WALK =====\n' + report.join('\n') + '\n============================\n')
})
