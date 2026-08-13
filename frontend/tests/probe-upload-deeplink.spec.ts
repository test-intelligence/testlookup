/**
 * Live probe: the sidebar's "Upload Report" deep link must actually upload.
 *
 * Reported: /runs?upload=1 "displays the runs page". The earlier manual-upload
 * probe tested the *header button* after explicitly selecting a project, and
 * never followed the sidebar link — which is the entry point a user actually
 * clicks. So the feature was called verified while its primary path was untested.
 */
import { expect, test } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill('admin')
  await page.locator('input[type="password"]').fill('Admin@2026!')
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
}

const drawer = (page: import('@playwright/test').Page) =>
  page.locator('input[type="file"]')

test('DIAGNOSTIC: what state is the app in when the deep link lands', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs?upload=1`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)

  const projectSelect = page.locator('select[aria-label="Select project"]')
  const scope = await projectSelect.inputValue().catch(() => '(no selector)')
  const options = await projectSelect
    .locator('option')
    .evaluateAll((os) => os.map((o) => `${(o as HTMLOptionElement).value}=${o.textContent}`))
    .catch(() => [])
  const uploadBtn = page.getByRole('button', { name: /upload report/i }).first()

  console.log('--- deep-link diagnostic ---')
  console.log('url after load :', page.url())
  console.log('active scope   :', scope)
  console.log('project options:', JSON.stringify(options))
  console.log('upload button  :', (await uploadBtn.count()) ? 'present' : 'ABSENT (flag off?)')
  if (await uploadBtn.count()) {
    console.log('  disabled     :', await uploadBtn.isDisabled())
  }
  console.log('file input     :', (await drawer(page).count()) ? 'drawer OPEN' : 'drawer CLOSED')
})

test('the sidebar deep link opens the upload drawer', async ({ page }) => {
  await login(page)
  // Pick a concrete project first — the honest precondition for an upload.
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  const select = page.locator('select[aria-label="Select project"]')
  const values = await select
    .locator('option')
    .evaluateAll((os) =>
      os.map((o) => (o as HTMLOptionElement).value).filter((v) => v && v !== 'all'),
    )
  test.skip(!values.length, 'no projects on this deployment — nothing to upload into')
  await select.selectOption(values[0])
  await page.waitForTimeout(1000)

  await page.goto(`${BASE}/runs?upload=1`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  await expect(
    drawer(page).first(),
    'deep link did not open the upload drawer — the user just sees the runs list',
  ).toBeAttached()
})

test('clicking the sidebar entry (not typing the URL) opens the drawer', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  const select = page.locator('select[aria-label="Select project"]')
  const values = await select
    .locator('option')
    .evaluateAll((os) =>
      os.map((o) => (o as HTMLOptionElement).value).filter((v) => v && v !== 'all'),
    )
  test.skip(!values.length, 'no projects on this deployment')
  await select.selectOption(values[0])
  await page.waitForTimeout(1000)

  const link = page.getByRole('link', { name: /upload report/i }).first()
  test.skip(!(await link.count()), 'sidebar entry not rendered (group collapsed / flag off)')
  await link.click()
  await page.waitForTimeout(1500)

  await expect(
    drawer(page).first(),
    'clicking the sidebar Upload Report entry left the user on the runs list',
  ).toBeAttached()
})

test('On All Projects the link still LETS YOU UPLOAD', async ({ page }) => {
  // Reported twice. First the link silently did nothing; then it explained why
  // and still would not upload. Explaining a dead end is not a fix — the panel
  // must open and ask for the project.
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  const select = page.locator('select[aria-label="Select project"]')
  if (await select.count()) await select.selectOption('all').catch(() => {})
  await page.waitForTimeout(800)

  await page.goto(`${BASE}/runs?upload=1`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  await expect(
    drawer(page).first(),
    'the upload panel did not open under All Projects — the user still cannot upload',
  ).toBeAttached()

  const picker = page.locator('select option', { hasText: /Select a project/i })
  await expect(
    picker.first(),
    'the panel opened without asking which project to upload into',
  ).toBeAttached()
})

test('DIAGNOSTIC: does picking a project afterwards honour the pending intent', async ({ page }) => {
  await login(page)
  const select = page.locator('select[aria-label="Select project"]')
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  if (await select.count()) await select.selectOption('all').catch(() => {})
  await page.waitForTimeout(600)

  await page.goto(`${BASE}/runs?upload=1`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  console.log('before pick — drawer:', (await drawer(page).count()) ? 'OPEN' : 'closed',
              '| url:', page.url())

  const values = await select.locator('option').evaluateAll((os) =>
    os.map((o) => (o as HTMLOptionElement).value).filter((v) => v && v !== 'all'))
  if (!values.length) { console.log('no projects'); return }
  await select.selectOption(values[0])
  await page.waitForTimeout(1500)
  console.log('after  pick — drawer:', (await drawer(page).count()) ? 'OPEN' : 'closed',
              '| url:', page.url())
})
