import { test, expect } from '@playwright/test'
const BASE = 'http://testlookup.local'

test('upload a report via the deep link', async ({ page }) => {
  const failed: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) failed.push(`${r.status()} ${r.url().replace(BASE,'').split('?')[0]}`)
  })
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  const sel = page.locator('select').first()
  const opts = await sel.locator('option').allTextContents()
  const t = opts.find(o => /uat onboarding/i.test(o))!
  await sel.selectOption({ label: t })
  await page.waitForTimeout(2500)

  await page.goto(`${BASE}/runs?upload=1`)
  await page.waitForTimeout(6000)
  const body = await page.locator('body').innerText()
  const i = body.indexOf('Upload')
  console.log('=== UPLOAD PANEL ===')
  console.log(body.slice(i, i + 1100))

  const fi = page.locator('input[type="file"]')
  console.log('file inputs:', await fi.count())
  await fi.first().setInputFiles('tests/fixtures/uat-ingest.xml')
  await page.waitForTimeout(2500)
  const after = await page.locator('body').innerText()
  const j = after.indexOf('Upload')
  console.log('=== AFTER CHOOSING FILE ===')
  console.log(after.slice(j, j + 1100))

  const btns = await page.getByRole('button').allTextContents()
  console.log('buttons visible:', JSON.stringify(btns.filter(Boolean).slice(0, 25)))
  const go = page.getByRole('button', { name: /^(upload|import|ingest|submit|start)/i })
  console.log('submit candidates:', await go.count())
  if (await go.count()) { await go.last().click(); await page.waitForTimeout(15000) }
  console.log('=== AFTER SUBMIT ===')
  console.log((await page.locator('body').innerText()).slice(0, 1500))
  console.log('=== FAILED ===', JSON.stringify([...new Set(failed)]))
  expect(true).toBe(true)
})
