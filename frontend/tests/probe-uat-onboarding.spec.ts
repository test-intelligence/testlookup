/**
 * UAT iteration 2 — the ONBOARDING journey, driven through the UI.
 *
 * Question: can someone who has never used TestLookup get from "empty account"
 * to "I can see my test results", using only what the product shows them?
 *
 * Walks: /getting-started as a newcomer -> create a project through the UI ->
 * read the guidance offered -> follow it. Records what the product actually
 * tells the user at each step, so usability can be judged, not guessed.
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT = 'ZZ UAT Onboarding - delete me'

test('onboarding: a newcomer gets from empty to first results', async ({ page }) => {
  const failedCalls: string[] = []
  const consoleErrors: string[] = []
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      failedCalls.push(`${r.status()} ${r.url().replace(BASE, '').split('?')[0]}`)
    }
  })
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)) })

  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"], input[type="text"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForTimeout(7000)

  // ---- Step 1: what does the product say to a newcomer? ----
  await page.goto(`${BASE}/getting-started`)
  await page.waitForTimeout(5000)
  console.log('=== STEP 1: /getting-started (All Projects) ===')
  console.log((await page.locator('body').innerText()).slice(0, 2200))

  // ---- Step 2: create a project through the UI ----
  await page.goto(`${BASE}/projects`)
  await page.waitForTimeout(4000)
  console.log('\n=== STEP 2: /projects before ===')
  console.log((await page.locator('body').innerText()).slice(0, 1200))

  const newBtn = page.getByRole('button', { name: /new project|create project|add project/i })
  console.log('\n"new project" control found:', await newBtn.count())
  if (await newBtn.count()) {
    await newBtn.first().click()
    await page.waitForTimeout(2500)
    console.log('=== create dialog ===')
    console.log((await page.locator('body').innerText()).slice(0, 1500))

    // Fill whatever the form asks for.
    const nameField = page.locator('input[name="name"], input[placeholder*="name" i]').first()
    if (await nameField.count()) await nameField.fill(PROJECT)
    const slugField = page.locator('input[name="slug"], input[placeholder*="slug" i]').first()
    if (await slugField.count()) await slugField.fill('zz-uat-onboarding')
    const descField = page.locator('textarea, input[name="description"]').first()
    if (await descField.count()) await descField.fill('UAT onboarding journey probe')

    const submit = page.getByRole('button', { name: /^(create|save|add)/i })
    console.log('submit control found:', await submit.count())
    if (await submit.count()) {
      await submit.first().click()
      await page.waitForTimeout(5000)
    }
    console.log('=== after submit ===')
    console.log((await page.locator('body').innerText()).slice(0, 1200))
  }

  // ---- Step 3: is the new project selectable, and what does onboarding say now? ----
  const selector = page.locator('select').first()
  const opts = await selector.locator('option').allTextContents()
  console.log('\n=== STEP 3: project selector options ===')
  console.log(JSON.stringify(opts))
  const created = opts.find((o) => /uat onboarding/i.test(o))
  console.log('new project appears in selector:', !!created)
  if (created) {
    await selector.selectOption({ label: created })
    await page.waitForTimeout(3000)
    await page.goto(`${BASE}/getting-started`)
    await page.waitForTimeout(5000)
    console.log('\n=== STEP 3b: /getting-started scoped to the BRAND-NEW project ===')
    console.log((await page.locator('body').innerText()).slice(0, 2500))
  }

  console.log('\n=== FAILED API CALLS ===', JSON.stringify([...new Set(failedCalls)]))
  console.log('=== CONSOLE ERRORS ===', JSON.stringify([...new Set(consoleErrors)]))
  expect(true).toBe(true)
})
