/**
 * BEFORE/AFTER baseline of the run's AI analysis (Wave 2.5, VIZ-104): the
 * composite-risk meter with its gradient fill (G6), at risk 58 (a
 * conditional go), and the dimension grid beside it.
 *
 * UX redesign P4: the analysis is the Run page's Analysis tab
 * (`/runs/<run>?tab=analysis`; `/runs/<run>/intelligence` only redirects
 * there), the verdict is a one-line StatusBanner, and the meter + dimensions
 * moved into the "How this score is computed" disclosure below What failed,
 * so the spec opens it before the capture. The region keeps its name.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { RUN_PAGE } from './production/fixtures-run'

test.use(PINNED)

for (const theme of THEMES) {
  test(`run intelligence regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}?tab=analysis`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RUN_PAGE,
      ready: (p) => p.locator('[data-status-banner]'),
    })

    await page.getByRole('button', { name: /^How this score is computed/ }).click()
    const score = page.locator('[data-disclosure]').filter({ has: page.getByText('Composite risk score', { exact: true }) })
    await expect(score).toContainText('58')

    await visualRegion(page, 'intelligence-verdict', theme, score)
    assertHermetic(api, errors)
  })
}
