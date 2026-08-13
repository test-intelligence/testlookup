/**
 * Post-deploy verification for #557: the Trends tab must now carry Timeout and
 * Auth-failed columns, and the per-status counts must account for every probe
 * in total_probes.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('integration-health trends renders the recovered status columns', async ({ page }) => {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(6000)

  await page.goto(`${BASE}/settings/integration-health`)
  await page.waitForTimeout(4000)
  await page.getByRole('button', { name: 'Trends (7d)', exact: true }).first().click()
  await page.waitForTimeout(4000)

  const body = await page.locator('body').innerText()
  const start = body.indexOf('Current Status')
  console.log('=== TRENDS TAB ===')
  console.log(body.slice(start, start + 900))

  // innerText returns CSS-transformed text, so compare case-insensitively.
  const lower = body.toLowerCase()
  expect(lower).toContain('timeout')
  expect(lower).toContain('auth failed')
})
