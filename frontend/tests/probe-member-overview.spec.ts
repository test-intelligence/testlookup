/**
 * What does a project MEMBER (non-admin) see on Overview at first login?
 *
 * `metrics/summary` and `metrics/trends` deliberately return `{}` for a
 * non-admin who does not pin a project — the router comment says so, and the
 * reason is sound (the service trusts `project_id`, so an unverified one would
 * read another tenant's KPIs).
 *
 * But `projectStore` defaults a fresh session to ALL_PROJECTS. So a member with
 * real data may land on an empty dashboard. This probe logs in as a
 * QA_ENGINEER who belongs to exactly one project holding one run, and dumps
 * what the page actually renders — rather than inferring from the API.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('overview as a project member on a fresh session', async ({ page }) => {
  const failed: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failed.push(`${r.status()} ${r.url().replace(BASE, '')}`)
    }
  })

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'zz_scoped_probe')
  await page.fill('input[type="password"]', 'NBPCwQOR2sM2xgc3')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(8000)

  console.log('=== URL after login ===', page.url())

  const scope = await page.evaluate(
    () => localStorage.getItem('testlookup-active-project'),
  )
  console.log('=== persisted project scope ===', scope)

  await page.goto(`${BASE}/overview`)
  await page.waitForTimeout(7000)

  const body = await page.locator('body').innerText()
  console.log('=== FAILED API CALLS ===', JSON.stringify(failed, null, 1))
  console.log('=== OVERVIEW PAGE TEXT ===')
  console.log(body.slice(0, 2600))

  expect(body.length).toBeGreaterThan(0)
})
