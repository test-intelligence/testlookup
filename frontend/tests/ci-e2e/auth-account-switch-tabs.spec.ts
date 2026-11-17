/**
 * D-0019 (GPT-6): one browser profile, two tabs. Tab A is signed in as an admin; tab B signs in as a viewer. Tab A
 * kept the admin session (its menus, its role checks, its admin token), and a write from A after the switch put the
 * admin back in the shared `auth-storage` entry, so the next tab or reload opened as the admin.
 *
 * Now one account per browser profile (owner decision Q3): a sign-in as someone else, or a sign-out, in any tab
 * reloads the other tabs onto it, and a tab whose account was replaced never writes it back. Same-account token
 * changes are adopted without a reload. No server revoke.
 *
 * The harness's `seedSession` re-seeds `auth-storage` on every load of a page, which would undo the switch on A's
 * reload, so this spec seeds the context once (only while nothing is stored) with tokens that name the account
 * (`admin-access`, `viewer-access`), and answers `/auth/me` by bearer, like the server.
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock), opened by hand for the one-shot seed.
 */
import { expect, test, type Browser, type BrowserContext, type Page, type Request } from '@playwright/test'
import { assertHermetic, freezeClock, mockApi, respond, stillMotion, type ApiHandlers, type MockedApi } from '../lib/production-pages'
import { LAYOUT, NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import { ADMIN, VIEWER, flagsHandlers } from '../visual/production/fixtures-agents-billing-flags'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The other tab follows: the reload and the new account's first paint (an assertion budget, not a sleep). */
const FOLLOWS_WITHIN_MS = 5_000
const ACCOUNTS: Record<string, typeof ADMIN> = { 'Bearer admin-access': ADMIN, 'Bearer viewer-access': VIEWER }

const account = (p: Page) => p.getByRole('button', { name: /Account menu/ })

/** One session for the whole context, written only while none is stored: a reload keeps what the tabs wrote. */
async function seedOnce(context: BrowserContext) {
  await context.addInitScript(({ admin, projectId }) => {
    if (localStorage.getItem('auth-storage') === null) {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: { token: 'admin-access', refreshToken: 'admin-refresh', user: admin, isAuthenticated: true }, version: 0,
      }))
    }
    localStorage.setItem('testlookup-active-project', JSON.stringify({ state: { activeProjectId: projectId }, version: 0 }))
    localStorage.setItem('testlookup-time-window', JSON.stringify({ state: { days: 30 }, version: 3 }))
    localStorage.setItem('testlookup-theme', JSON.stringify({ state: { theme: 'signal' }, version: 0 }))
  }, { admin: ADMIN, projectId: PROJECT_ID })
}

/** Every request a tab made, in order, with the bearer it carried. */
interface Sent { tab: string; line: string; bearer: string | null }

function server(sent: Sent[], options: { holdAdminMe?: Promise<void> } = {}) {
  const flags = flagsHandlers()
  let held = options.holdAdminMe
  const handlers: ApiHandlers = [
    ['/api/v1/auth/me', async ({ route }) => {
      const bearer = route.request().headers().authorization ?? ''
      if (bearer === 'Bearer admin-access' && held) { const gate = held; held = undefined; await gate }
      return ACCOUNTS[bearer] ?? respond(401, { detail: 'Could not validate credentials' })
    }],
    ['/api/v1/auth/login', () => ({ access_token: 'viewer-access', refresh_token: 'viewer-refresh', token_type: 'bearer' }), 'POST'],
    ['/api/v1/auth/logout', () => respond(204), 'POST'],
    // The sign-in page's two public reads.
    ['/api/v1/sso/status', () => ({ sso_enabled: false, has_active_config: false, enforcement_mode: 'OPTIONAL' })],
    ['/api/v1/auth/options', () => ({ registration_enabled: false, password_reset_email: false, demo_mode: false })],
    ...flags.handlers,
    ...OVERVIEW_ON,
    ...LAYOUT,
  ]
  return { handlers, sent }
}

async function openTab(page: Page, name: string, path: string, world: ReturnType<typeof server>) {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(String(error)))
  page.on('request', (request: Request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) {
      world.sent.push({ tab: name, line: `${request.method()} ${url.pathname}`, bearer: request.headers().authorization ?? null })
    }
  })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, ADMIN, world.handlers)
  await page.goto(path)
  return { api, errors }
}

async function signInAsViewer(page: Page) {
  await page.getByLabel('Email or Username').fill('viewer')
  await page.getByLabel('Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: 'Log In' }).click()
  await expect(account(page)).toContainText('Viewer')
}

const storedUsername = (p: Page) =>
  p.evaluate(() => JSON.parse(localStorage.getItem('auth-storage') ?? '{}')?.state?.user?.username ?? null)

function hermetic(...tabs: { api: MockedApi; errors: string[] }[]) {
  for (const tab of tabs) assertHermetic(tab.api, tab.errors)
}

test('D-0019: a sign-in as someone else in one tab moves the other tab to that account', async ({ page, context }) => {
  await seedOnce(context)
  const world = server([])
  const a = await openTab(page, 'A', '/settings/feature-flags', world)
  await expect(page.getByRole('heading', { level: 1, name: 'Feature Flags' })).toBeVisible()
  await expect(account(page)).toContainText('Admin')

  const tabB = await context.newPage()
  const b = await openTab(tabB, 'B', '/login', world)
  const reloadedA = page.waitForEvent('framenavigated', { predicate: (frame) => frame === page.mainFrame() })
  await signInAsViewer(tabB)
  const signedInAt = world.sent.length

  // A reloads onto the viewer: its account menu, and the role guard sends it off the admin page.
  await reloadedA
  await expect(account(page)).toContainText('Viewer', { timeout: FOLLOWS_WITHIN_MS })
  await expect(page).toHaveURL(/\/overview$/, { timeout: FOLLOWS_WITHIN_MS })
  await expect(page.getByText('Another tab of this browser signed in as viewer, so this tab reloaded.')).toBeVisible()
  await page.goto('/settings/feature-flags')
  await expect(page).toHaveURL(/\/overview$/)
  const adminAfter = world.sent.slice(signedInAt).filter((s) => s.tab === 'A' && s.bearer === 'Bearer admin-access')
  // At most a request A had already started; nothing after its reload, and never the flag inventory.
  expect(adminAfter.filter((s) => s.line === 'GET /api/v1/feature-flags')).toEqual([])
  expect(await storedUsername(page)).toBe('viewer')
  hermetic(a, b)
})

test('D-0019: a late write from the old tab does not put the old account back for the next tab', async ({ page, context }) => {
  await seedOnce(context)
  let release = () => {}
  const world = server([], { holdAdminMe: new Promise<void>((resolve) => { release = resolve }) })
  const a = await openTab(page, 'A', '/overview', world)  // A's mount-time /auth/me (admin) is held

  const tabB = await context.newPage()
  const b = await openTab(tabB, 'B', '/login', world)
  await signInAsViewer(tabB)
  release()
  await expect(account(page)).toContainText('Viewer', { timeout: FOLLOWS_WITHIN_MS })
  expect(await storedUsername(tabB)).toBe('viewer')

  // A third tab opened now hydrates the viewer and verifies with the viewer's token.
  const tabC = await context.newPage()
  const c = await openTab(tabC, 'C', '/overview', world)
  await expect(account(tabC)).toContainText('Viewer')
  // The tab draws the stored account first and verifies it just after: wait for that first /auth/me.
  await expect
    .poll(() => world.sent.find((s) => s.tab === 'C' && s.line === 'GET /api/v1/auth/me')?.bearer ?? null)
    .toBe('Bearer viewer-access')
  await expect(account(tabB)).toContainText('Viewer')
  hermetic(a, b, c)
})

test('D-0019: a sign-out in one tab signs the other tab out', async ({ page, context }) => {
  await seedOnce(context)
  const world = server([])
  const a = await openTab(page, 'A', '/overview', world)
  await expect(account(page)).toContainText('Admin')
  const tabB = await context.newPage()
  const b = await openTab(tabB, 'B', '/overview', world)
  await expect(account(tabB)).toContainText('Admin')

  await account(tabB).click()
  await tabB.locator('[data-account-menu]').getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(tabB).toHaveURL(/\/login/)
  await expect(page).toHaveURL(/\/login/, { timeout: FOLLOWS_WITHIN_MS })
  hermetic(a, b)
})

async function isolated(browser: Browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })
  await seedOnce(context)
  return { context, page: await context.newPage() }
}

test('two separate browser profiles keep their own accounts', async ({ browser }) => {
  const world = server([])
  const one = await isolated(browser)
  const two = await isolated(browser)
  try {
    const a = await openTab(one.page, 'A', '/overview', world)
    await expect(account(one.page)).toContainText('Admin')
    const b = await openTab(two.page, 'B', '/login', world)
    await signInAsViewer(two.page)
    await expect(account(one.page)).toContainText('Admin')
    expect(await storedUsername(one.page)).toBe('admin')
    hermetic(a, b)
  } finally {
    await one.context.close()
    await two.context.close()
  }
})
