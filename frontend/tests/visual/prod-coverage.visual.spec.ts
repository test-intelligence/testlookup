/**
 * BEFORE/AFTER baselines of /coverage (Wave 2.5, VIZ-104): the 5-level
 * run-cadence intensity strip (S2), 30 days with a 7-day gap.
 * Wave 3 C0 adds the suite coverage breakdown, the suite-level card of the
 * page that gains the Wave 3 sections (coverage treemap, suite x environment
 * and suite x release heatmaps): captured with every flag off, before any
 * Wave 3 code, so the flag-off page after the wave is proved unchanged. It
 * is captured last, after the Wave 2.5 regions, so their inputs are unchanged.
 * Phase D, S4: the Wave 3 sections mount unconditionally (no flag is asked),
 * so the page is opened with their answers (`COVERAGE_ON`).
 *
 * UX redesign P3 (the page template): the verdict card is gone — its health
 * score, its dimensions and the coverage gaps are a collapsed disclosure
 * below the suite table — so the `coverage-verdict` region is DROPPED (its
 * PNGs go with it). The suite table is the page's primary content, right
 * under the KPI strip (new pixels: a gap column appears only when a suite
 * has a gap, and this fixture's suites have none); the cadence card keeps
 * its place beside it.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { COVERAGE_ON, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`coverage regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/coverage', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: COVERAGE_ON,
      ready: (p) => landmark(p, 'Run cadence'),
    })

    const cadence = landmark(page, 'Run cadence')
    await expect(cadence.getByRole('img').first()).toBeVisible()

    await visualRegion(page, 'coverage-cadence', theme, cadence)

    // The fixture's five suites, in its order, and its footer: a page that
    // drew an empty or loading state fails here, not as a screenshot of it.
    const breakdown = landmark(page, 'Suite coverage breakdown')
    await expect(breakdown.getByRole('row')).toHaveCount(5)
    await expect(breakdown.getByRole('row', { name: 'Auth: 610 passed, 38 failed, 12 skipped' })).toBeVisible()
    await expect(breakdown).toContainText('Showing 5 suites')
    await visualRegion(page, 'coverage-suite-breakdown', theme, breakdown)
    assertHermetic(api, errors)
  })
}
