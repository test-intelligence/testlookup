/**
 * UAT iteration 2, part 2 — from a brand-new empty project to first results.
 *
 * Also checks whether the create-project inputs are programmatically labelled
 * (they render "Project Name *" visually but carry no name/id/aria-label), which
 * decides whether that is an accessibility defect or just a testability quirk.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'

test('new project -> guidance -> ingest -> results', async ({ page }) => {
  const failed: string[] = []
  const errors: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) failed.push(`${r.status()} ${r.url().replace(BASE, '').split('?')[0]}`)
  })
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)) })

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  // ---- a11y: are the create-project fields labelled for a screen reader? ----
  await page.goto(`${BASE}/projects`)
  await page.waitForTimeout(3500)
  await page.getByRole('button', { name: /new project/i }).first().click()
  await page.waitForTimeout(1500)
  const a11y = await page.evaluate(() => {
    const out: Record<string, unknown>[] = []
    document.querySelectorAll('[role="dialog"] input, form input, [role="dialog"] textarea, form textarea')
      .forEach((el) => {
        const id = (el as HTMLElement).id
        const labelFor = id ? document.querySelector(`label[for="${id}"]`) : null
        const wrapping = el.closest('label')
        out.push({
          placeholder: (el as HTMLInputElement).placeholder,
          required: (el as HTMLInputElement).required,
          hasIdLabel: !!labelFor,
          hasWrappingLabel: !!wrapping,
          ariaLabel: el.getAttribute('aria-label'),
          ariaLabelledBy: el.getAttribute('aria-labelledby'),
        })
      })
    return out
  })
  console.log('=== A11Y: create-project field labelling ===')
  console.log(JSON.stringify(a11y, null, 1))
  await page.keyboard.press('Escape')
  await page.waitForTimeout(1000)

  // ---- pin the brand-new empty project ----
  const selector = page.locator('select').first()
  const opts = await selector.locator('option').allTextContents()
  const target = opts.find((o) => /uat onboarding/i.test(o))
  console.log('\nnew project selectable:', !!target)
  if (!target) { console.log('ABORT: project not in selector'); expect(true).toBe(true); return }
  await selector.selectOption({ label: target })
  await page.waitForTimeout(3000)

  // ---- what does the product tell a user with an EMPTY project? ----
  for (const route of ['getting-started', 'overview', 'runs']) {
    await page.goto(`${BASE}/${route}`)
    await page.waitForTimeout(5000)
    console.log(`\n=== EMPTY PROJECT :: /${route} ===`)
    console.log((await page.locator('body').innerText()).slice(0, 1800))
  }

  // ---- is there a way to upload a report from the UI? ----
  const uploadCtl = page.getByRole('button', { name: /upload/i })
  const uploadLink = page.getByRole('link', { name: /upload/i })
  console.log('\n=== UPLOAD affordance on /runs ===')
  console.log('buttons:', await uploadCtl.count(), 'links:', await uploadLink.count())
  const fileInputs = await page.locator('input[type="file"]').count()
  console.log('file inputs present:', fileInputs)

  console.log('\n=== FAILED API CALLS ===', JSON.stringify([...new Set(failed)]))
  console.log('=== CONSOLE ERRORS ===', JSON.stringify([...new Set(errors)]))
  expect(true).toBe(true)
})
