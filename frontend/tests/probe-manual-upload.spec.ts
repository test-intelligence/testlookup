import { expect, test } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill('admin')
  await page.locator('input[type="password"]').fill('Admin@2026!')
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
}

/** Upload targets ONE project, so the control is correctly disabled while the
 *  project selector is on "All Projects". Pick a real project first. */
async function selectAProject(page: import('@playwright/test').Page) {
  const select = page.locator('select[aria-label="Select project"]')
  const values = await select.locator('option').evaluateAll((os) =>
    os.map((o) => (o as HTMLOptionElement).value).filter((v) => v && v !== 'all'),
  )
  if (values.length) await select.selectOption(values[0])
  await page.waitForTimeout(1200)
}

test('the manual-upload entry point is visible on /runs', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  const trigger = page.getByRole('button', { name: /upload/i })
  await expect(trigger.first(), 'no Upload control on /runs — the flag gates this button').toBeVisible()
})

test('upload is disabled on All Projects and enabled once a project is picked', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  const trigger = page.getByRole('button', { name: /upload report/i }).first()
  // A run is ingested into one project, so "All Projects" must not offer upload.
  await expect(trigger, 'upload should be disabled while scope is All Projects').toBeDisabled()
  await selectAProject(page)
  await expect(trigger, 'upload should enable once a single project is active').toBeEnabled()
})

test('the upload modal opens and accepts a file input', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  await selectAProject(page)
  await page.getByRole('button', { name: /upload report/i }).first().click()
  await page.waitForTimeout(800)

  // The modal must offer a real file input, not just a heading.
  const fileInput = page.locator('input[type="file"]')
  await expect(fileInput.first(), 'modal opened without a file input').toBeAttached()
})
