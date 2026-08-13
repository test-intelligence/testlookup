/**
 * /getting-started with a REAL project selected.
 *
 * Checkout Service is not a fresh project: it has runs, test cases, suites,
 * AI analysis and a release. If the onboarding checklist still reports 0%
 * complete, the page is telling an established user they have done nothing.
 *
 * Also captures what the "No workflow stages found / This workflow has not
 * emitted any stage data yet" block does when scoped — that wording is
 * pipeline-stage language, and seeing it on an onboarding page in
 * All-Projects scope is worth pinning down.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'
const CHECKOUT = '2aefa4fa-8917-448b-9895-9cd41911ef95'

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(overview|dashboard|$)/, { timeout: 20000 })
}

test('getting-started scoped to a project with real data', async ({ page }) => {
  const failedRequests: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failedRequests.push(`${r.status()} ${r.url().replace(BASE, '')}`)
    }
  })

  await login(page)
  // Pin the project the way the app's own store does. zustand's persist
  // middleware writes an envelope under its `name`, NOT a bare key — setting
  // `activeProjectId` directly is a no-op the page silently ignores, which is
  // exactly what made the first run of this probe look like a scope bug.
  await page.evaluate((id) => {
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({ state: { activeProjectId: id }, version: 0 }),
    )
  }, CHECKOUT)

  await page.goto(`${BASE}/getting-started`)
  await page.waitForTimeout(7000)

  const body = await page.locator('body').innerText()
  console.log('=== FAILED API REQUESTS ===', JSON.stringify(failedRequests, null, 1))
  console.log('=== PAGE TEXT (scoped) ===')
  console.log(body)

  expect(body.length).toBeGreaterThan(0)
})
