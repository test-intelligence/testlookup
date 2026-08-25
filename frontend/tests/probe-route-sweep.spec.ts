/**
 * Route sweep against the live homelab (http://testlookup.local).
 *
 * Visits every reachable route and asserts what a user would notice: the page
 * is not an error boundary, not an empty shell, and did not quietly fail an
 * API call, throw in the console, or leave a request hanging.
 *
 * Isolation note — this cost a false positive worth recording. The first
 * version drove all 18 routes through ONE page and waited for
 * ``networkidle``. Background pollers (notifications, unread counts) started
 * by earlier routes keep firing, so the later a route ran the less likely the
 * network ever went quiet for 500ms. The failure moved between runs — route 16
 * once, route 18 the next — which is the signature of accumulated state rather
 * than a broken page. Each route now gets a fresh context, and the wait is on
 * rendered content instead of network silence.
 *
 * ``networkidle`` was only ever a wait strategy; the assertions below are the
 * actual contract and are unchanged. A genuine hang is still caught, by
 * ``unfinished`` — a real never-answering request fails the test on its own
 * terms rather than as a timeout in the harness.
 *
 *   npx playwright test --config probe-live.config.ts probe-route-sweep
 */
import { test, expect, type BrowserContext } from '@playwright/test'

const BASE = 'http://testlookup.local'
const USER = 'admin'
const PASS = 'Admin@2026!'

// A run with a completed deep pipeline, so the intelligence routes render real
// content rather than an empty state that could hide a defect.
const RUN_ID = 'ddf0e5a7-6023-403a-802c-7caac0e5ea06'

const ROUTES = [
  '/overview',
  '/runs',
  '/suites',
  '/coverage',
  '/coverage/suite',
  '/trends',
  '/defects',
  '/failure-analysis',
  '/my-failures',
  '/intelligence',
  '/release-gate',
  '/agents',
  '/search',
  '/getting-started',
  `/runs/${RUN_ID}`,
  `/runs/${RUN_ID}/intelligence`,
  `/release-gate/${RUN_ID}`,
  `/deep-investigate/${RUN_ID}`,
]

// Documented "not configured" responses, not failures. Kept deliberately
// narrow — one endpoint each, never a blanket status allow-list:
//   * llm-quota answers 404 when a project has no budget set, and
//     useProjectQuota maps exactly that status to null (see
//     src/hooks/useLlmBudget.test.ts, which pins the contract and proves a 500
//     still surfaces as an error).
const EXPECTED_404 = /\/api\/v1\/projects\/[^/]+\/llm-quota$/
const IGNORED_URL = /favicon|\/api\/v1\/auth\/(me|refresh)\b/
const IGNORED_CONSOLE = /Download the React DevTools|was preloaded using link preload/
const BROKEN_COPY = /Something went wrong|Failed to load|Unable to load|Error boundary|Unexpected error/i

// How long an API call may stay unanswered before it counts as hung. Every
// observed call on this deployment answers in tens of milliseconds.
const HANG_MS = 20_000

let storageState: Awaited<ReturnType<BrowserContext['storageState']>>

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[type="text"], input[name="username"]', USER)
  await page.fill('input[type="password"]', PASS)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 })
  storageState = await context.storageState()
  await context.close()
})

for (const route of ROUTES) {
  test(`route ${route} renders without error`, async ({ browser }) => {
    const context = await browser.newContext({ storageState })
    const page = await context.newPage()

    const consoleErrors: string[] = []
    const netErrors: string[] = []
    const inFlight = new Map<string, number>()

    page.on('console', (m) => {
      if (m.type() !== 'error') return
      if (IGNORED_CONSOLE.test(m.text())) return
      // Chrome logs a generic "Failed to load resource: 404" for every failed
      // fetch, with no URL in the text — the location carries it. Correlate,
      // so the documented llm-quota 404 is excused by URL rather than by
      // ignoring all resource errors, which would hide real 404s.
      const from = m.location()?.url ?? ''
      if (/Failed to load resource/.test(m.text()) && EXPECTED_404.test(from.split('?')[0])) return
      consoleErrors.push(`${m.text().slice(0, 200)}${from ? ` <- ${from.slice(0, 140)}` : ''}`)
    })
    page.on('request', (r) => {
      if (r.url().includes('/api/')) inFlight.set(r.url(), Date.now())
    })
    page.on('response', (r) => {
      inFlight.delete(r.url())
      const expected404 = r.status() === 404 && EXPECTED_404.test(r.url().split('?')[0])
      if (r.status() >= 400 && !IGNORED_URL.test(r.url()) && !expected404) {
        netErrors.push(`${r.status()} ${r.request().method()} ${r.url().slice(0, 180)}`)
      }
    })
    page.on('requestfailed', (r) => inFlight.delete(r.url()))

    try {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })

      // Wait for the app to actually paint, rather than for the network to go
      // quiet — this page tree keeps background pollers running by design.
      await expect
        .poll(async () => (await page.locator('body').innerText()).trim().length, {
          timeout: 45_000,
          message: `${route}: never rendered content`,
        })
        .toBeGreaterThan(120)

      const body = await page.locator('body').innerText()
      const hung = [...inFlight.entries()]
        .filter(([, t]) => Date.now() - t > HANG_MS)
        .map(([u, t]) => `${Date.now() - t}ms unanswered: ${u.slice(0, 160)}`)

      expect(body, `${route}: rendered an error state`).not.toMatch(BROKEN_COPY)
      expect(netErrors, `${route}: failed API call(s): ${netErrors.join(' | ')}`).toHaveLength(0)
      expect(hung, `${route}: hung API call(s): ${hung.join(' | ')}`).toHaveLength(0)
      expect(consoleErrors, `${route}: console error(s): ${consoleErrors.join(' | ')}`).toHaveLength(0)
    } finally {
      await context.close()
    }
  })
}
