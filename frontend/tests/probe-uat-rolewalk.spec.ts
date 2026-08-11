import { test, expect } from '@playwright/test'
const BASE = 'http://testlookup.local'
test('VIEWER sees scoped/empty states, never a blank page', async ({ page }) => {
  await page.goto(`${BASE}/login`)
  // Credentials come from the environment: the VIEWER is a throwaway created
  // per-run and deactivated afterwards, so its server-generated password is
  // never committed. Create one via POST /api/v1/users (it returns
  // `temp_password`) and export UAT_VIEWER_USER / UAT_VIEWER_PASS.
  await page.fill('input[name="username"], input[type="text"]', process.env.UAT_VIEWER_USER ?? 'zz_uat_viewer')
  await page.fill('input[type="password"]', process.env.UAT_VIEWER_PASS ?? '')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(8000)
  console.log('URL after login:', page.url())
  const report: Record<string, unknown>[] = []
  for (const r of ['overview','runs','failures','coverage','test-management','releases','my-failures','settings']) {
    await page.goto(`${BASE}/${r}`)
    await page.waitForTimeout(4000)
    const b = await page.locator('body').innerText()
    const low = b.toLowerCase()
    report.push({
      route: r, chars: b.length,
      leaksCheckout: low.includes('checkout service'),
      hasGuidance: /no |empty|select a project|not have|permission|access/i.test(b),
    })
  }
  console.log('=== VIEWER PAGE WALK ===')
  console.log(JSON.stringify(report, null, 1))
  console.log('ANY LEAK:', report.some(r => r.leaksCheckout))
  console.log('ANY BLANK (<400 chars):', report.filter(r => (r.chars as number) < 400).map(r => r.route))
  expect(report.some(r => r.leaksCheckout)).toBe(false)
})
