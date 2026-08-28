import { expect, test } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill('admin')
  await page.locator('input[type="password"]').fill('Admin@2026!')
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
}

/** Upload targets ONE project. The control used to be DISABLED while the
 *  selector sat on "All Projects"; it is now enabled, and the modal asks which
 *  project instead -- "a question to ask, not a reason to send the user away
 *  from the thing they just clicked" (UploadReportModal). Pick a real project
 *  when the test needs the pre-scoped path. */
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

test('upload stays available in All-Projects scope and asks which project', async ({ page }) => {
  // This probe used to assert the button was DISABLED on All Projects. That
  // behaviour was superseded: RunsPage gates the button on role only
  // (`disabled={!isQaEngineer}`) and passes `projectId={null}`, and the modal
  // renders a required Project chooser. The old assertion failed against a
  // deployment that was working exactly as designed -- a stale probe that
  // reports a false defect trains people to ignore the suite, so it asserts
  // the current contract instead.
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  // Precondition -- the old probe never checked this, and without it the test
  // says nothing about which scope it measured.
  const scope = page.locator('select[aria-label="Select project"]')
  await expect(scope, 'the project selector must be on All Projects for this test')
    .toHaveValue('all')

  const trigger = page.getByRole('button', { name: /upload report/i }).first()
  await expect(trigger, 'upload is gated on role, not on scope').toBeEnabled()

  await trigger.click()
  // The modal root is a plain div -- no role="dialog" -- so anchor on its
  // heading rather than a role that is not there.
  const heading = page.getByRole('heading', { name: /upload test report/i })
  await expect(heading).toBeVisible({ timeout: 10_000 })
  const dialog = page.locator('div.fixed.inset-0').last()

  // The load-bearing part: opening it in All-Projects scope must ASK for the
  // project, or a report would land nowhere (or somewhere arbitrary).
  const chooser = dialog.locator('select').first()
  await expect(chooser,
    'All-Projects upload must ask which project the report lands in').toBeVisible()
  const options = await chooser.locator('option').count()
  expect(options, 'the project chooser must offer real projects').toBeGreaterThan(0)
})

test('upload is pre-scoped when a single project is already active', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await selectAProject(page)

  const trigger = page.getByRole('button', { name: /upload report/i }).first()
  await expect(trigger, 'upload should stay enabled once a project is active').toBeEnabled()
})
