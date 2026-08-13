/**
 * The three settings pages with zero prior exploratory coverage:
 * settings/agent-activity, settings/integration-health, settings/performance.
 *
 * Drives each tab rather than trusting the API, per the standing rule: check
 * what the PAGE renders. Also pins a project first, since AgentActivityPage
 * shows a "select a project" notice in All-Projects mode and would otherwise
 * look empty for the wrong reason.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(6000)
}

test('the three zero-coverage settings pages', async ({ page }) => {
  const failed: string[] = []
  const consoleErrors: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failed.push(`${r.status()} ${r.url().replace(BASE, '')}`)
    }
  })
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200))
  })

  await login(page)

  // Pin a real project via the actual <select>, not the zustand envelope.
  const selector = page.locator('select').first()
  if (await selector.count()) {
    const opts = await selector.locator('option').allTextContents()
    const target = opts.find((o) => /checkout/i.test(o))
    if (target) await selector.selectOption({ label: target })
    await page.waitForTimeout(2500)
  }

  for (const [route, tabs] of [
    ['settings/agent-activity', []],
    ['settings/integration-health', ['Current Status', 'Trends (7d)', 'Probe History']],
    ['settings/performance', ['Latency Budgets', 'Search Config', 'Scale Scenarios']],
  ] as [string, string[]][]) {
    await page.goto(`${BASE}/${route}`)
    await page.waitForTimeout(5000)
    console.log(`\n=========== ${route} ===========`)
    console.log((await page.locator('body').innerText()).slice(0, 1800))

    for (const label of tabs) {
      const tab = page.getByRole('button', { name: label, exact: true })
      if (await tab.count()) {
        await tab.first().click()
        await page.waitForTimeout(3000)
        console.log(`\n----- ${route} :: ${label} -----`)
        console.log((await page.locator('body').innerText()).slice(0, 1500))
      } else {
        console.log(`\n----- ${route} :: ${label} -- TAB NOT FOUND -----`)
      }
    }
  }

  console.log('\n=== FAILED API CALLS ===', JSON.stringify(failed, null, 1))
  console.log('=== CONSOLE ERRORS ===', JSON.stringify(consoleErrors, null, 1))
  expect(true).toBe(true)
})
