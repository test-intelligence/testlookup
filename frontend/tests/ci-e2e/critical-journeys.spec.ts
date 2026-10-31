import { expect, test, type Page, type Route } from '@playwright/test'

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'qa@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installHermeticApi(page: Page, authMeStatus = 200) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/v1/auth/login' && request.method() === 'POST') {
      return json(route, { access_token: 'access', refresh_token: 'refresh', token_type: 'bearer' })
    }
    if (path === '/api/v1/auth/me') {
      return authMeStatus === 200
        ? json(route, user)
        : json(route, { detail: 'expired' }, authMeStatus)
    }
    if (path === '/api/v1/sso/status') {
      return json(route, { sso_enabled: false, has_active_config: false, enforcement_mode: 'OPTIONAL' })
    }
    if (path === '/api/v1/projects') return json(route, [])
    if (path === '/api/v1/saved-views') return json(route, [])
    if (path === '/api/v1/notifications/history') return json(route, [])
    if (path === '/api/v1/notifications/history/unread-count') return json(route, { unread: 0 })
    if (path === '/api/v1/analytics/dashboard') return json(route, {})
    if (path === '/api/v1/analytics/trends') return json(route, [])
    if (path === '/api/v1/analytics/failure-categories') return json(route, [])
    if (path === '/api/v1/runs') return json(route, { items: [], total: 0, page: 1, size: 100 })

    // The app shell has optional badges and health probes. Returning a stable
    // empty object keeps this lane focused on routing/auth contracts while the
    // page/service suites own each endpoint's payload semantics.
    return json(route, {})
  })
}

test.describe('hermetic critical journeys', () => {
  test.beforeEach(async ({ context, page }) => {
    await context.clearCookies()
    await installHermeticApi(page)
  })

  test('an unauthenticated deep link is returned to sign-in', async ({ page }) => {
    await page.goto('/runs')
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: 'Sign in to TestLookup' })).toBeVisible()
  })

  test('password sign-in returns to the requested deep link', async ({ page }) => {
    await page.goto('/reviews')
    await expect(page).toHaveURL(/\/login$/)

    await page.getByLabel('Email or Username').fill('qa_lead')
    await page.getByLabel('Password').fill('correct horse battery staple')
    await page.getByRole('button', { name: 'Log In' }).click()

    // Back to the deep link, which since the UX redesign P4 (D4) redirects to
    // Inbox › Approvals: the review queue's new home.
    await expect(page).toHaveURL(/\/my-failures\?tab=approvals$/)
    // Named: since the UX redesign P1 a section's tabs are a <nav> too.
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
  })

  // W25a HYG-07: the sign-in's own `GET /auth/me` IS the verification (the
  // protected shell does not ask again within 30 s), and the shell's project
  // list is one shared read (the dev server's StrictMode double effect shares
  // the in-flight promise). Counted on the wire, not in the stores.
  test('a sign-in reads the session once and the project list once before Overview paints', async ({ page }) => {
    const reads = { me: 0, projects: 0 }
    page.on('request', (request) => {
      if (request.method() !== 'GET') return
      const path = new URL(request.url()).pathname
      if (path === '/api/v1/auth/me') reads.me += 1
      if (path === '/api/v1/projects') reads.projects += 1
    })

    await page.goto('/login')
    await page.getByLabel('Email or Username').fill('qa_lead')
    await page.getByLabel('Password').fill('correct horse battery staple')
    await page.getByRole('button', { name: 'Log In' }).click()

    await expect(page).toHaveURL(/\/overview$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible({ timeout: 20_000 })
    const atPaint = { ...reads }
    // A late second read would still be a second read: give the shell a moment.
    await page.waitForTimeout(1_500)

    expect(atPaint, 'the session and the project list, read by the time Overview paints').toEqual({ me: 1, projects: 1 })
    expect(reads, 'no second read after the paint').toEqual({ me: 1, projects: 1 })
  })

  test('an explicitly rejected persisted token cannot expose a protected route', async ({ page }) => {
    await page.addInitScript((seedUser) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: {
          token: 'expired',
          refreshToken: null,
          user: seedUser,
          isAuthenticated: true,
        },
        version: 0,
      }))
    }, user)
    await page.unroute('**/api/v1/**')
    await installHermeticApi(page, 401)

    await page.goto('/runs')
    await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 })
  })
})
