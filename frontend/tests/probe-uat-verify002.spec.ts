import { test, expect } from '@playwright/test'
const BASE = 'http://testlookup.local'
test('run header reconciles on the deployed build', async ({ page }) => {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)
  await page.goto(`${BASE}/runs/65d54ffc-f8d2-402a-afaf-5668f259ce5b`)
  await page.waitForTimeout(8000)
  const body = await page.locator('body').innerText()
  const i = body.indexOf('passed')
  console.log('=== HEADER REGION ===')
  console.log(body.slice(Math.max(0, i - 120), i + 200))
  const m = [...body.matchAll(/(\d+)\s+(passed|failed|skipped|broken|unrecognised)\b/gi)]
  const buckets: Record<string, number> = {}
  for (const x of m) buckets[x[2].toLowerCase()] = Number(x[1])
  const total = Number((body.match(/\/\s*(\d+)\s*total/i) ?? [])[1] ?? 0)
  const sum = Object.values(buckets).reduce((a, b) => a + b, 0)
  console.log('buckets:', JSON.stringify(buckets), 'total:', total, 'sum:', sum)
  console.log('RECONCILES:', sum === total)
  expect(sum).toBe(total)
})
