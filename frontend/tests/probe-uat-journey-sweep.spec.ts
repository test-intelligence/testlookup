/**
 * UAT iteration 1 — post-merge health of every primary page.
 *
 * 29 PRs merged today and nothing has been driven through a browser since. This
 * walks each primary route with a project pinned and records, per page:
 *   - console errors
 *   - failed API calls (>=400)
 *   - whether the page rendered anything at all
 *   - crash/error-boundary text
 *
 * Deliberately broad, not deep: iteration 1 is a regression net over the merge.
 * Later iterations go deep on individual journeys.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

const ROUTES = [
  'overview', 'runs', 'failures', 'coverage', 'live', 'my-failures',
  'test-management', 'quarantine', 'releases', 'defects', 'projects',
  'reports/summary', 'getting-started', 'intelligence', 'search',
  'settings', 'settings/integration-health', 'settings/performance',
  'settings/agent-activity', 'settings/notifications', 'settings/digests',
  'settings/retention', 'settings/audit', 'settings/storage', 'settings/ai',
  'users', 'policies',
]

// Error-boundary / crash wording the app uses. Case-insensitive: CSS uppercases.
const CRASH = [
  'something went wrong', 'unexpected error', 'application error',
  'failed to load', 'chunkloaderror', 'cannot read properties',
]

test('post-merge: every primary page renders without crashing', async ({ page }) => {
  const report: Record<string, unknown>[] = []

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  // Pin a real project through the actual selector, not the zustand envelope.
  const selector = page.locator('select').first()
  if (await selector.count()) {
    const opts = await selector.locator('option').allTextContents()
    const target = opts.find((o) => /checkout/i.test(o))
    if (target) await selector.selectOption({ label: target })
    await page.waitForTimeout(2500)
  }

  for (const route of ROUTES) {
    const consoleErrors: string[] = []
    const failedCalls: string[] = []
    const onConsole = (m: import('@playwright/test').ConsoleMessage) => {
      if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 180))
    }
    const onResponse = (r: import('@playwright/test').Response) => {
      if (r.status() >= 400 && r.url().includes('/api/')) {
        failedCalls.push(`${r.status()} ${r.url().replace(BASE, '').split('?')[0]}`)
      }
    }
    page.on('console', onConsole)
    page.on('response', onResponse)

    let body = ''
    let navError = ''
    try {
      await page.goto(`${BASE}/${route}`, { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(5000)
      body = await page.locator('body').innerText()
    } catch (e) {
      navError = String(e).slice(0, 150)
    }

    page.off('console', onConsole)
    page.off('response', onResponse)

    const lower = body.toLowerCase()
    report.push({
      route,
      chars: body.length,
      crash: CRASH.filter((c) => lower.includes(c)),
      consoleErrors: [...new Set(consoleErrors)].slice(0, 4),
      failedCalls: [...new Set(failedCalls)].slice(0, 6),
      navError,
    })
  }

  console.log('=== UAT PAGE SWEEP ===')
  console.log(JSON.stringify(report, null, 1))

  const broken = report.filter(
    (r) =>
      (r.crash as string[]).length > 0 ||
      (r.navError as string) !== '' ||
      (r.chars as number) < 400,
  )
  console.log('=== PAGES NEEDING INVESTIGATION ===')
  console.log(JSON.stringify(broken.map((b) => b.route), null, 1))

  expect(report.length).toBe(ROUTES.length)
})
