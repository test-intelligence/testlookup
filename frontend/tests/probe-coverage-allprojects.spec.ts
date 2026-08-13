/**
 * F-059 verification: what does Coverage actually RENDER in All-Projects scope?
 *
 * The API returns `project_name` per suite row, derived with `MAX(p.name)` over
 * a GROUP BY on suite name alone — so a row aggregating two projects is
 * labelled with just one of them. I filed that as a user-visible mislabel
 * without checking the consumer. `CoverageSuite` (types/analytics.ts) has no
 * `project_name` field at all, which says the UI never sees it.
 *
 * This probe settles it against the running app rather than by reading types:
 * dump every rendered suite row and look for any project attribution.
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

test('Coverage in All-Projects scope: what is rendered per suite row', async ({ page }) => {
  await login(page)

  // Force All-Projects scope through the store the app itself uses.
  await page.evaluate(() => {
    localStorage.setItem('activeProjectId', '"__ALL__"')
  })

  await page.goto(`${BASE}/coverage`)
  await page.waitForTimeout(6000)

  const body = await page.locator('body').innerText()

  // Every suite name the API returns for this deployment.
  const suiteNames = ['api', 'regression', 'smoke', 'UploadDeepLinkE2E', 'auth']
  const rendered = suiteNames.filter((s) => body.includes(s))

  // The two live project names — is EITHER shown next to a suite?
  const showsZZ = body.includes('ZZ Probe (volume/pagination)')
  const showsCheckout = body.includes('Checkout Service')

  console.log('=== RENDERED SUITE NAMES ===', rendered)
  console.log('=== shows "ZZ Probe" anywhere on the page? ===', showsZZ)
  console.log('=== shows "Checkout Service" anywhere on the page? ===', showsCheckout)
  console.log('=== PAGE TEXT (first 3000 chars) ===')
  console.log(body.slice(0, 3000))

  expect(rendered.length).toBeGreaterThan(0)
})
