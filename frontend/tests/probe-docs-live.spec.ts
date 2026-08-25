/**
 * Live probe: the user documentation renders at /docs on the deployment.
 *
 * The point is the DIRECT URL. The page was always reachable by clicking the
 * sidebar — client-side routing never hits the server — while a hard load of
 * /docs returned Swagger UI. So this navigates straight to the URL, the way a
 * shared link or a bookmark does.
 *
 *   npx playwright test --config probe-live.config.ts probe-docs-live
 */
import { test, expect, type Page } from '@playwright/test'

const BASE = 'http://testlookup.local'

let page: Page

test.describe.configure({ mode: 'serial' })

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext()
  page = await context.newPage()
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[type="text"], input[name="username"]', 'admin')
  await page.fill('input[type="password"]', 'Admin@2026!')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 })
})

test('a direct load of /docs renders the guide, not Swagger', async () => {
  await page.goto(`${BASE}/docs`, { waitUntil: 'domcontentloaded' })

  await expect
    .poll(async () => (await page.locator('body').innerText()).length, { timeout: 30_000 })
    .toBeGreaterThan(400)

  const body = await page.locator('body').innerText()
  expect(body, 'the API reference must not be served here').not.toMatch(/Swagger/i)
  expect(body).toMatch(/Documentation/i)
  expect(body, 'the default topic should render').toMatch(/What TestLookup is/i)
})

test('the navigation lists every documentation group', async () => {
  // Navigates itself: depending on a previous test's navigation makes a
  // failure here mean 'the test before me moved the page', not 'the nav is
  // broken'.
  await page.goto(`${BASE}/docs`, { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () => (await page.locator('body').innerText()).length, { timeout: 30_000 })
    .toBeGreaterThan(400)

  // Case-insensitive: the group headers carry `text-transform: uppercase`, so
  // innerText reports "START HERE". Asserting the source casing would fail on a
  // page that is rendering perfectly.
  const body = (await page.locator('body').innerText()).toLowerCase()
  for (const group of ['start here', 'using testlookup', 'how it decides', 'reference']) {
    expect(body, `nav group "${group}"`).toContain(group)
  }
})

test('a deep link opens the topic directly', async () => {
  // The behaviour that was broken: a URL someone can paste into a ticket.
  await page.goto(`${BASE}/docs/flaky`, { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () => (await page.locator('body').innerText()).includes('0.45'), {
      timeout: 30_000,
      message: 'the flaky topic did not render its weights',
    })
    .toBe(true)

  const body = await page.locator('body').innerText()
  expect(body).toMatch(/Fewer than 5/)
  expect(body, 'quarantine must not be described as an action').toMatch(
    /does not change your test suite/i,
  )
})

test('diagrams render with their written description', async () => {
  await page.goto(`${BASE}/docs/architecture`, { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () => (await page.locator('body').innerText()).includes('In words:'), {
      timeout: 30_000,
    })
    .toBe(true)
})

test('the API reference is still served, at /api-docs', async () => {
  const res = await page.request.get(`${BASE}/api-docs`)
  expect(res.status()).toBe(200)
  expect(await res.text()).toMatch(/Swagger/i)

  // The spec itself — previously unrouted, so Swagger could never load it.
  const spec = await page.request.get(`${BASE}/api-docs/openapi.json`)
  expect(spec.status()).toBe(200)
  expect(spec.headers()['content-type'] ?? '').toContain('json')
})
