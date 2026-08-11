/**
 * Isolates the create-project form: is it broken, or was the previous probe's
 * selector wrong? Dumps every input the dialog actually renders, fills by
 * label/order, submits, and reports what came back.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('create project through the UI', async ({ page }) => {
  const calls: string[] = []
  page.on('response', (r) => {
    if (r.url().includes('/api/v1/projects')) calls.push(`${r.request().method()} ${r.status()} ${r.url().replace(BASE, '')}`)
  })

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  await page.goto(`${BASE}/projects`)
  await page.waitForTimeout(4000)
  await page.getByRole('button', { name: /new project/i }).first().click()
  await page.waitForTimeout(2000)

  // What inputs does the dialog actually have?
  const inputs = await page.locator('form input, form textarea, [role="dialog"] input, [role="dialog"] textarea').all()
  console.log('=== INPUTS IN DIALOG ===', inputs.length)
  for (const el of inputs) {
    console.log(JSON.stringify({
      tag: await el.evaluate((n) => n.tagName),
      name: await el.getAttribute('name'),
      id: await el.getAttribute('id'),
      placeholder: await el.getAttribute('placeholder'),
      aria: await el.getAttribute('aria-label'),
      required: await el.getAttribute('required'),
      type: await el.getAttribute('type'),
    }))
  }

  // Fill positionally: Name, Slug, Description, Jira key, OCP namespace.
  if (inputs.length >= 2) {
    await inputs[0].fill('ZZ UAT Onboarding - delete me')
    await inputs[1].fill('zz-uat-onboarding')
    if (inputs.length >= 3) await inputs[2].fill('UAT onboarding journey probe')
  }
  await page.waitForTimeout(500)

  const create = page.getByRole('button', { name: /^create project$/i })
  console.log('create button count:', await create.count(),
              'disabled:', await create.first().isDisabled().catch(() => 'n/a'))
  await create.first().click()
  await page.waitForTimeout(6000)

  console.log('=== /api/v1/projects CALLS ===', JSON.stringify(calls))
  const body = await page.locator('body').innerText()
  console.log('=== PAGE AFTER SUBMIT (first 900) ===')
  console.log(body.slice(0, 900))
  console.log('=== project now listed:', /uat onboarding/i.test(body))
  expect(true).toBe(true)
})
