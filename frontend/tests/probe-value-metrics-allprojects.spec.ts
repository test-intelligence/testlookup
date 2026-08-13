/**
 * F-057: does the ROI page RENDER component counts in All-Projects scope,
 * even though `available: false`?
 *
 * The API returns available:false ("select a project to compute the
 * hours-saved model") but still ships component counts, and unscoped those
 * counts include soft-deleted projects — measured: flaky_tests_identified = 3
 * = 2 live + 1 from `ZZ Probe (multi-day trends) — delete me` (is_active=false).
 *
 * Reading the source says only the hero number is gated on `available`, and
 * the MetricCards render unconditionally. F-059 taught me not to trust that
 * reading — check what the page actually paints.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(overview|dashboard|$)/, { timeout: 20000 })
}

test('ROI page in All-Projects scope: are component counts rendered?', async ({ page }) => {
  await login(page)
  await page.evaluate(() => {
    localStorage.setItem('activeProjectId', '"__ALL__"')
  })

  await page.goto(`${BASE}/value-metrics`)
  await page.waitForTimeout(6000)

  const body = await page.locator('body').innerText()

  const hasFlakyCard = body.includes('Flaky Tests Found')
  // The measured unscoped figure. 2 is the live-only truth.
  const flakyMatch = body.match(/Flaky Tests Found[\s\S]{0,80}/)

  console.log('=== "Flaky Tests Found" card present? ===', hasFlakyCard)
  console.log('=== card region ===', JSON.stringify(flakyMatch?.[0] ?? null))
  console.log('=== PAGE TEXT (first 2200) ===')
  console.log(body.slice(0, 2200))

  expect(body.length).toBeGreaterThan(0)
})
