/**
 * Select the project through the real UI control, then read back what the app
 * itself persisted — instead of guessing at zustand's storage envelope.
 *
 * Two hand-written fixtures already failed silently here (`activeProjectId` as
 * a bare key; then a hand-built `{state, version}` envelope). Driving the
 * actual control removes the guess, and dumping localStorage afterwards
 * records the true shape for later probes.
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

test('select a project via the UI, then load getting-started', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/getting-started`)
  await page.waitForTimeout(3000)

  // What does the project control actually look like?
  const selects = await page.locator('select').count()
  console.log('=== <select> count ===', selects)
  for (let i = 0; i < selects; i++) {
    const opts = await page.locator('select').nth(i).locator('option').allTextContents()
    console.log(`  select[${i}] options:`, JSON.stringify(opts))
  }

  // Prefer a real <select>; fall back to a combobox/button pattern.
  let switched = false
  for (let i = 0; i < selects; i++) {
    const opts = await page.locator('select').nth(i).locator('option').allTextContents()
    if (opts.some((o) => o.includes('Checkout Service'))) {
      await page.locator('select').nth(i).selectOption({ label: 'Checkout Service' })
      switched = true
      break
    }
  }
  console.log('=== switched via <select>? ===', switched)

  if (!switched) {
    const combo = page.getByRole('combobox').first()
    if (await combo.count()) {
      await combo.click()
      await page.waitForTimeout(500)
      const opt = page.getByText('Checkout Service', { exact: false }).first()
      if (await opt.count()) {
        await opt.click()
        switched = true
      }
    }
    console.log('=== switched via combobox? ===', switched)
  }

  await page.waitForTimeout(5000)

  const storage = await page.evaluate(() => {
    const out: Record<string, string> = {}
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)!
      if (k.toLowerCase().includes('project')) out[k] = localStorage.getItem(k) ?? ''
    }
    return out
  })
  console.log('=== project-related localStorage AFTER selecting ===')
  console.log(JSON.stringify(storage, null, 1))

  const body = await page.locator('body').innerText()
  console.log('=== PAGE TEXT ===')
  console.log(body)

  expect(body.length).toBeGreaterThan(0)
})
