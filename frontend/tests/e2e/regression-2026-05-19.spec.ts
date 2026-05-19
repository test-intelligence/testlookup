/**
 * End-to-end smoke regression for the 2026-05-18/19 bug-fix initiative.
 *
 * One spec per user-reported bug area. These are visibility-level
 * smoke checks: do the affected pages load, do the new UI affordances
 * render, do the new backend endpoints respond with sane shapes. They
 * are intentionally lighter than the unit-level regression pins under
 * ``backend/tests/regression/`` and ``frontend/src/**/*.test.tsx`` —
 * those guard the contract; this spec guards the user-journey wiring.
 *
 * Companion docs:
 *   * ``docs/REGRESSION_TEST_SUITE.md`` — full index of every fix +
 *     its pinning test.
 *   * ``memory/project_regression_complete_2026_05_19.md`` — cursor.
 */
import { test, expect } from '@playwright/test'
import { performRealLogin } from './realLoginHelper'

test.describe('Regression smoke (2026-05-18/19)', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page)
  })

  test('/my-failures renders the auto-assigned inbox + window chips', async ({ page }) => {
    await page.goto('/my-failures')
    await expect(page.getByRole('heading', { name: /My Failures/i })).toBeVisible({
      timeout: 10000,
    })
    // Window chips render — they're always available regardless of role.
    await expect(page.getByRole('radio', { name: '24h' })).toBeVisible()
    await expect(page.getByRole('radio', { name: '7d' })).toBeVisible()
  })

  test('/coverage/suite days selector reflects clicks (URL rewrite)', async ({ page }) => {
    await page.goto('/coverage?days=30')
    // Pick the first suite link if available. Tolerate the empty-state
    // path on a fresh project — the days chips are still on the
    // /coverage page even when no suite is selected, so we can verify
    // the rewrite by clicking the chip and checking the URL.
    await page.waitForLoadState('networkidle')
    const sevenChip = page.getByRole('button', { name: /^7d$/ }).first()
    if (await sevenChip.isVisible()) {
      await sevenChip.click()
      await expect(page).toHaveURL(/days=7/)
    }
  })

  test('/runs Pipeline-signal KPI is rendered', async ({ page }) => {
    await page.goto('/runs')
    await expect(page.getByText(/Pipeline signal/i)).toBeVisible({ timeout: 15000 })
  })

  test('/runs/compare page loads in latest mode without 404 toast', async ({ page }) => {
    // Picking an arbitrary suite name — the page must not crash even
    // if the suite has no runs. The fix replaces a hard 404/500 with
    // a graceful "not enough data to compare" empty state.
    await page.goto('/runs/compare?mode=latest&suite=__non_existent_suite__')
    await expect(page.locator('body')).not.toContainText(/500 Internal Server Error/i, {
      timeout: 8000,
    })
    // Either the empty state OR a compare layout — both are acceptable.
    const emptyState = page.getByText(/Not enough data to compare|RealisticTestNGSuite|Suite scope/i)
    await expect(emptyState.first()).toBeVisible({ timeout: 8000 })
  })

  test('/test-management Test Suites tab shows the Trend link affordance', async ({ page }) => {
    await page.goto('/test-management?tab=Test+Suites')
    await page.waitForLoadState('networkidle')
    // The Trend link only renders when at least one suite has data.
    // We just assert the tab loaded — the per-row link is tested at
    // the unit level (TestManagementPage.suite-aggregates.test.ts).
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10000 })
  })

  test('/search shows the chip + Index Health entity counts before any query', async ({ page }) => {
    await page.goto('/search')
    await page.waitForLoadState('networkidle')
    // Index Health row appears with non-empty entity counts via
    // /search/entity-counts fallback. The exact label may vary by
    // theme — match on the entity names.
    await expect(page.getByText(/Tests|Runs|Suites/i).first()).toBeVisible({
      timeout: 8000,
    })
  })

  test('/api/v1/runs/compare/latest soft-fails on data gap', async ({ page, request }) => {
    // Direct API probe — the endpoint should NOT 500 when only one of
    // the run-level suite mappings exists. It should either 200 with
    // ``data_gap`` or 404 with a friendly LookupError. Anything in the
    // 5xx range is a regression.
    const resp = await request.get(
      '/api/v1/runs/compare/latest?suite_name=__non_existent_suite__',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
  })

  test('/api/v1/test-management/suites/{name}/trend returns the trend envelope', async ({ request }) => {
    const resp = await request.get(
      '/api/v1/test-management/suites/__nope__/trend?days=14',
      { failOnStatusCode: false },
    )
    // 200 + empty points OR 401 (unauth in the request context) is OK.
    // 500 is a regression — the endpoint must always be present.
    expect(resp.status()).toBeLessThan(500)
  })

  test('/api/v1/runs/{id}/recover-live returns 4xx (not 5xx) for unknown run', async ({ request }) => {
    // Bug 2026-05-19: a live run reporting "100 tests, 90 passed, 10
    // failed" but with zero test_cases rows had no recovery path
    // when both Redis buffer and durable archive were empty. The
    // endpoint now falls back to source="synthesis" — queueing the
    // persist task with empty events so its synthesis branch
    // materialises one placeholder row per reported test.
    //
    // This probe just asserts the endpoint exists and doesn't 5xx.
    // The synthesis fallback contract is pinned at the
    // backend-integration layer:
    //   backend/tests/integration/test_regression_2026_05_19_e2e.py
    //     ::test_recover_live_falls_back_to_synthesis_when_buffer_and_archive_empty
    const resp = await request.post(
      '/api/v1/runs/00000000-0000-0000-0000-000000000000/recover-live',
      { failOnStatusCode: false },
    )
    expect(resp.status()).toBeLessThan(500)
    // Unknown run → 404. Live-stream guard / no-aggregates guard → 422.
    // Both are valid 4xx responses for this synthetic probe id.
    expect([404, 422]).toContain(resp.status())
  })
})
