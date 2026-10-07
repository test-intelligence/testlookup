/**
 * BEFORE/AFTER baseline of the Run page (`/runs/<run>`, UX redesign P4): one
 * page with four tabs (Tests · Analysis · Changes · Evidence).
 *
 *   run-header            the one PageHeader: build, job · branch · date, the
 *                         status pill, suite and release chips, ⋯, and the
 *                         page's tab bar (Tests counted);
 *   run-status-chips      the Tests tab's counts as filter chips, failures first
 *                         (12 failed, 3 broken, 180 passed, 2 skipped);
 *   run-analysis-verdict  the Analysis tab's verdict line (StatusBanner: a
 *                         conditional go at risk 58) with its reasoning.
 *
 * New in P4: their baselines come from CI. The composite-risk meter of the
 * same analysis is `prod-run-intelligence` (`intelligence-verdict`).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`; fixtures:
 * `production/fixtures-run.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { RUN_PAGE } from './production/fixtures-run'

test.use(PINNED)

for (const theme of THEMES) {
  test(`run page regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RUN_PAGE,
      ready: (p) => p.locator('[data-primary] table'),
    })

    const header = page.locator('[data-page-header]')
    await expect(header.getByRole('heading', { level: 1 })).toHaveText('Run #240')
    await visualRegion(page, 'run-header', theme, header)

    const chips = page.getByRole('group', { name: 'Filter by status' })
    await expect(chips.getByRole('button')).toHaveText(['All 197', '12 failed', '3 broken', '180 passed', '2 skipped'])
    await visualRegion(page, 'run-status-chips', theme, chips)

    await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Analysis' }).click()
    const verdict = page.getByRole('region', { name: 'Release verdict' })
    await expect(verdict.locator('[data-status-banner]')).toHaveAttribute('data-status-banner', 'conditional')
    await expect(verdict).toContainText('58/100')
    await visualRegion(page, 'run-analysis-verdict', theme, verdict)
    assertHermetic(api, errors)
  })
}
