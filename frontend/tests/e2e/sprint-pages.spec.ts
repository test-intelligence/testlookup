import { test, expect, type Page } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

const now = new Date('2026-05-15T12:00:00Z').toISOString()

async function mockMyFailuresApi(page: Page) {
  const listRequests: string[] = []

  await page.route('**/api/v1/me/assigned-failures**', async route => {
    const url = new URL(route.request().url())

    if (url.pathname.endsWith('/count')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 2 }),
      })
      return
    }

    listRequests.push(url.search)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            id: 'case-auth-1',
            test_run_id: 'run-101',
            test_name: 'test_login_redirects_after_sso',
            suite_name: 'AuthSuite',
            class_name: 'LoginSpec',
            status: 'FAILED',
            severity: 'major',
            failure_category: 'PRODUCT_BUG',
            duration_ms: 912,
            error_message: 'Expected /dashboard, got /login',
            created_at: now,
            project_id: 'project-1',
            project_name: 'Checkout',
            build_number: '101',
            navigation_url: '/runs/run-101/tests/case-auth-1',
          },
          {
            id: 'case-api-2',
            test_run_id: 'run-102',
            test_name: 'test_payment_contract',
            suite_name: 'PaymentAPI',
            class_name: 'PaymentContractSpec',
            status: 'BROKEN',
            severity: 'critical',
            failure_category: 'INFRASTRUCTURE',
            duration_ms: 1200,
            error_message: 'Schema mismatch',
            created_at: now,
            project_id: 'project-1',
            project_name: 'Checkout',
            build_number: '102',
            navigation_url: '/runs/run-102/tests/case-api-2',
          },
        ],
        total: 2,
        page: Number(url.searchParams.get('page') || 1),
        size: Number(url.searchParams.get('size') || 25),
        pages: 1,
        unresolved_total: 2,
      }),
    })
  })

  return { listRequests }
}

async function mockSuitesApi(page: Page) {
  await page.route('**/api/v1/suites**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname

    if (path === '/api/v1/suites') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'suite-auth',
              project_id: 'project-1',
              name: 'AuthSuite',
              description: 'Login and SSO coverage',
              is_default: true,
              tags: null,
              test_case_count: 12,
              created_at: now,
              updated_at: now,
            },
            {
              id: 'suite-payments',
              project_id: 'project-1',
              name: 'PaymentAPI',
              description: 'Contract tests',
              is_default: false,
              tags: null,
              test_case_count: 8,
              created_at: now,
              updated_at: now,
            },
          ],
          total: 2,
        }),
      })
      return
    }

    if (path === '/api/v1/suites/suite-auth') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'suite-auth',
          project_id: 'project-1',
          name: 'AuthSuite',
          description: 'Login and SSO coverage',
          is_default: true,
          tags: null,
          test_case_count: 12,
          created_at: now,
          updated_at: now,
        }),
      })
      return
    }

    if (path === '/api/v1/suites/suite-auth/test-cases') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'canonical-auth-1',
              project_id: 'project-1',
              test_suite_id: 'suite-auth',
              test_suite_name: 'AuthSuite',
              test_fingerprint: 'fp-auth-1',
              test_name: 'test_login_redirects_after_sso',
              class_name: 'LoginSpec',
              status: 'active',
              source: 'execution',
              first_seen_run_id: 'run-101',
              last_seen_run_id: 'run-101',
              last_seen_test_case_id: 'case-auth-1',
              deleted_at_run_id: null,
              managed_test_case_id: null,
              review_tag: null,
              tags: null,
              run_count: 5,
              created_at: now,
              updated_at: now,
            },
          ],
          total: 1,
        }),
      })
      return
    }

    await route.continue()
  })
}

/**
 * Smoke coverage for pages shipped over the last several sprints that
 * had no e2e tests yet. The bar is intentionally low: route loads inside
 * the auth shell + signature copy renders, tolerant of empty states on a
 * fresh DB. Deep behaviour (e.g. promoting a policy to active, claiming
 * a failure) is exercised by the per-page unit tests under
 * frontend/src/pages/*.test.tsx and backend integration tests.
 */
test.describe('Sprint pages — smoke', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  // ── /my-failures (action queue, shipped 2026-05-15) ─────────────────────

  test.describe('My Failures action queue', () => {
    test('renders assigned failures from the API and deep-links rows', async ({ page }) => {
      await mockMyFailuresApi(page)

      await page.goto('/my-failures')
      await expect(page).toHaveURL(/.*\/my-failures/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      await expect(page.getByText('test_login_redirects_after_sso')).toBeVisible()
      await expect(page.getByText('test_payment_contract')).toBeVisible()
      await expect(page.getByText('AuthSuite')).toBeVisible()
      await expect(page.getByText('PaymentAPI')).toBeVisible()
      await expect(page.getByText('2 assigned')).toBeVisible()

      await page.getByText('test_login_redirects_after_sso').click()
      await expect(page).toHaveURL(/.*\/runs\/run-101\/tests\/case-auth-1/)
    })

    test('changing the time window re-fetches with the selected days value', async ({ page }) => {
      const { listRequests } = await mockMyFailuresApi(page)

      await page.goto('/my-failures')
      await expect(page.getByText('test_login_redirects_after_sso')).toBeVisible()

      await page.getByRole('radio', { name: /^7d$/i }).click()
      await expect.poll(() => listRequests.some(search => search.includes('days=7')))
        .toBe(true)
      await expect(page.getByRole('radio', { name: /^7d$/i })).toHaveAttribute('aria-checked', 'true')
    })

    test('renders My Failures page with time-window controls', async ({ page }) => {
      await page.goto('/my-failures')
      await expect(page).toHaveURL(/.*\/my-failures/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // Either the populated queue (table) or the "You're caught up"
      // EmptyState renders — both are valid landing states.
      const signal = page
        .locator('text=/My Failures|You.?re caught up|Couldn.?t load/i')
        .first()
      await expect(signal).toBeVisible({ timeout: 10000 })

      // The window radiogroup is always present (controls live above
      // the table/empty-state).
      await expect(page.getByRole('radiogroup', { name: /time window/i })).toBeVisible()
    })

    test('switching the time window does not crash the page', async ({ page }) => {
      await page.goto('/my-failures')
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      const sevenDay = page.getByRole('radio', { name: /^7d$/i })
      if (await sevenDay.isVisible().catch(() => false)) {
        await sevenDay.click()
        // Page stays mounted; sidebar still rendered.
        await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
      }
    })
  })

  // ── /suites (project-scoped suite catalogue) ────────────────────────────

  test.describe('Suites', () => {
    test('renders suite rows and opens the suite detail page', async ({ page }) => {
      await mockSuitesApi(page)

      await page.goto('/suites')
      await expect(page).toHaveURL(/.*\/suites/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      await expect(page.getByText('AuthSuite')).toBeVisible()
      await expect(page.getByText('PaymentAPI')).toBeVisible()
      await expect(page.getByText('12')).toBeVisible()

      await page.getByText('AuthSuite').click()
      await expect(page).toHaveURL(/.*\/suites\/suite-auth/)
      await expect(page.getByRole('heading', { name: /AuthSuite/ })).toBeVisible()
      await expect(page.getByText('test_login_redirects_after_sso')).toBeVisible()
    })
  })

  // ── /value-metrics (Phase K) ────────────────────────────────────────────

  test.describe('Value Metrics', () => {
    test('renders Value Metrics page', async ({ page }) => {
      await page.goto('/value-metrics')
      await expect(page).toHaveURL(/.*\/value-metrics/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      const heading = page.locator('text=/Value Metrics|Select a project/i').first()
      await expect(heading).toBeVisible({ timeout: 10000 })
    })
  })

  // ── /agents (AI Pipeline) ───────────────────────────────────────────────

  test.describe('AI Pipeline', () => {
    test('renders AI Pipeline / Agents page', async ({ page }) => {
      await page.goto('/agents')
      await expect(page).toHaveURL(/.*\/agents/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // The page renders either the compute graph card list or an empty
      // state message. We only assert one of those is visible.
      const signal = page
        .locator('text=/agent|pipeline|stage|no.*pipeline|select.*run/i')
        .first()
      await expect(signal).toBeVisible({ timeout: 10000 })
    })
  })

  // ── /policies (Release Gate Policy editor) ──────────────────────────────

  test.describe('Release gate policies', () => {
    test('renders policy list page', async ({ page }) => {
      await page.goto('/policies')
      await expect(page).toHaveURL(/.*\/policies/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // List view header is fixed string. "New Policy" CTA is present
      // for the admin user the e2e suite logs in as.
      await expect(page.locator('text=Release Gate Policies').first())
        .toBeVisible({ timeout: 10000 })
      await expect(
        page.getByRole('button', { name: /new policy/i }).first(),
      ).toBeVisible({ timeout: 10000 })
    })

    test('opens the editor when New Policy is clicked', async ({ page }) => {
      await page.goto('/policies')
      await page.getByRole('button', { name: /new policy/i }).first().click()
      await expect(page).toHaveURL(/.*\/policies\/new/)
      // Editor mode header & at least one section heading.
      await expect(page.locator('text=/New Policy|Thresholds|Pass-?Rate Bands/i').first())
        .toBeVisible({ timeout: 10000 })
    })
  })

  // ── /ownership (Service Ownership editor) ───────────────────────────────

  test.describe('Service ownership', () => {
    test('renders ownership page', async ({ page }) => {
      await page.goto('/ownership')
      await expect(page).toHaveURL(/.*\/ownership/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // Either the editor or the read-only map view renders depending on
      // the page state — both share the "Service Ownership" prefix.
      await expect(page.locator('text=/Service Ownership/i').first())
        .toBeVisible({ timeout: 10000 })
    })
  })

  // ── /flaky-coach ───────────────────────────────────────────────────────

  test.describe('Flaky Coach', () => {
    test('renders Flaky Coach page', async ({ page }) => {
      await page.goto('/flaky-coach')
      await expect(page).toHaveURL(/.*\/flaky-coach/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // Header always renders; subtitle text differs based on whether a
      // project is selected. Tolerate both states.
      await expect(page.locator('text=/Flaky Coach/i').first())
        .toBeVisible({ timeout: 10000 })
      // Either coaching content, "Select a project", or "No flaky tests".
      const body = page
        .locator('text=/select a project|quarantine|flaky test|no flaky/i')
        .first()
      await expect(body).toBeVisible({ timeout: 10000 })
    })
  })

  // ── /getting-started (Onboarding) ───────────────────────────────────────

  test.describe('Onboarding', () => {
    test('renders the getting-started flow', async ({ page }) => {
      await page.goto('/getting-started')
      await expect(page).toHaveURL(/.*\/getting-started/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })

      // Page is built around stepped guidance — at least one
      // numbered/heading anchor must land.
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 })
    })
  })

  // ── /runs/compare (already covered in tier-0-2 but extended here) ───────

  test.describe('Run compare deep-link', () => {
    test('renders run-compare even with garbage query params', async ({ page }) => {
      await page.goto('/runs/compare?left=not-a-uuid&right=not-a-uuid')
      await expect(page).toHaveURL(/.*\/runs\/compare/)
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 })
      // Page must not crash to a blank screen with bad params.
      const signal = page
        .locator('text=/Run Compare|Pick two|select.*run/i')
        .first()
      await expect(signal).toBeVisible({ timeout: 10000 })
    })
  })
})
