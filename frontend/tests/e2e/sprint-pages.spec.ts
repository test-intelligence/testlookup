import { test, expect } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

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
    test('renders My Failures page with time-window controls', async ({ page }) => {
      await page.goto('/my-failures')
      await expect(page).toHaveURL(/.*\/my-failures/)
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

      const sevenDay = page.getByRole('radio', { name: /^7d$/i })
      if (await sevenDay.isVisible().catch(() => false)) {
        await sevenDay.click()
        // Page stays mounted; sidebar still rendered.
        await expect(page.locator('aside')).toBeVisible()
      }
    })
  })

  // ── /value-metrics (Phase K) ────────────────────────────────────────────

  test.describe('Value Metrics', () => {
    test('renders Value Metrics page', async ({ page }) => {
      await page.goto('/value-metrics')
      await expect(page).toHaveURL(/.*\/value-metrics/)
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

      const heading = page.locator('text=/Value Metrics|Select a project/i').first()
      await expect(heading).toBeVisible({ timeout: 10000 })
    })
  })

  // ── /agents (AI Pipeline) ───────────────────────────────────────────────

  test.describe('AI Pipeline', () => {
    test('renders AI Pipeline / Agents page', async ({ page }) => {
      await page.goto('/agents')
      await expect(page).toHaveURL(/.*\/agents/)
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })

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
      await expect(page.locator('aside')).toBeVisible({ timeout: 10000 })
      // Page must not crash to a blank screen with bad params.
      const signal = page
        .locator('text=/Run Compare|Pick two|select.*run/i')
        .first()
      await expect(signal).toBeVisible({ timeout: 10000 })
    })
  })
})
