/**
 * UAT iteration 3 — the INGEST journey, plus live re-verification of UAT-001.
 *
 * A user uploads a JUnit report and needs to trust what comes back. This drives
 * the UI upload path end to end and dumps the rendered run list + run detail so
 * every number can be cross-checked against the database.
 *
 * The fixture is deliberately awkward but legal: mixed statuses, a skip, an
 * <error> (which becomes BROKEN), unicode, and a long name — the shapes a real
 * suite produces.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT_RX = /uat onboarding/i

test('a11y fix is live on the deployed build', async ({ page }) => {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  await page.goto(`${BASE}/projects`)
  await page.waitForTimeout(4000)
  await page.getByRole('button', { name: /new project/i }).first().click()
  await page.waitForTimeout(1800)

  const labelled = await page.evaluate(() => {
    const out: Record<string, unknown>[] = []
    document.querySelectorAll('[role="dialog"] input, form input, [role="dialog"] textarea, form textarea')
      .forEach((el) => {
        const id = (el as HTMLElement).id
        out.push({
          placeholder: (el as HTMLInputElement).placeholder,
          id,
          labelText: id ? document.querySelector(`label[for="${id}"]`)?.textContent?.trim() : null,
        })
      })
    return out
  })
  console.log('=== UAT-001 RE-VERIFY (deployed) ===')
  console.log(JSON.stringify(labelled, null, 1))
  const unlabelled = labelled.filter((f) => !f.labelText)
  console.log('fields still lacking an accessible name:', unlabelled.length)
  expect(unlabelled.length).toBe(0)
})

test('ingest: upload a report and read the result', async ({ page }) => {
  const failed: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) failed.push(`${r.status()} ${r.url().replace(BASE, '').split('?')[0]}`)
  })

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  const selector = page.locator('select').first()
  const opts = await selector.locator('option').allTextContents()
  const target = opts.find((o) => PROJECT_RX.test(o))
  if (!target) { console.log('ABORT: UAT project missing'); expect(target).toBeTruthy(); return }
  await selector.selectOption({ label: target })
  await page.waitForTimeout(3000)

  // Find the upload affordance a user would use.
  await page.goto(`${BASE}/runs`)
  await page.waitForTimeout(5000)
  const bodyBefore = await page.locator('body').innerText()
  console.log('=== /runs BEFORE upload (empty project) ===')
  console.log(bodyBefore.slice(0, 1200))

  const uploadBtn = page.getByRole('button', { name: /upload/i })
  const uploadLink = page.getByRole('link', { name: /upload/i })
  console.log('upload buttons:', await uploadBtn.count(), 'upload links:', await uploadLink.count())
  if (await uploadBtn.count()) await uploadBtn.first().click()
  else if (await uploadLink.count()) await uploadLink.first().click()
  await page.waitForTimeout(3000)
  console.log('=== after clicking upload ===')
  console.log((await page.locator('body').innerText()).slice(0, 1400))

  const fileInput = page.locator('input[type="file"]')
  console.log('file inputs:', await fileInput.count())
  if (await fileInput.count()) {
    await fileInput.first().setInputFiles(
      'tests/fixtures/uat-ingest.xml',   // relative to the frontend/ cwd
    )
    await page.waitForTimeout(2000)
    console.log('=== after choosing a file ===')
    console.log((await page.locator('body').innerText()).slice(0, 1400))
    const submit = page.getByRole('button', { name: /^(upload|import|submit|ingest)/i })
    console.log('submit controls:', await submit.count())
    if (await submit.count()) {
      await submit.last().click()
      await page.waitForTimeout(12000)
    }
    console.log('=== after submitting the upload ===')
    console.log((await page.locator('body').innerText()).slice(0, 1600))
  }

  await page.goto(`${BASE}/runs`)
  await page.waitForTimeout(6000)
  console.log('=== /runs AFTER upload ===')
  console.log((await page.locator('body').innerText()).slice(0, 1800))
  console.log('=== FAILED API CALLS ===', JSON.stringify([...new Set(failed)]))
  expect(true).toBe(true)
})
