/**
 * BEFORE/AFTER baseline of /release-gate/<run> (Wave 2.5, VIZ-104): the
 * recommendation card with its semicircle risk arc, for a NO_GO floored to
 * 60 by the pass rate. Today the arc's track is `#334155` and its score is
 * literal white, which is theme-blind on `lab`.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, cardAround, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RELEASE_GATE, RUN_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`release gate regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/release-gate/${RUN_ID}`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RELEASE_GATE,
      ready: (p) => p.getByText('Risk Score', { exact: true }),
    })

    const card = cardAround(page.getByText('Risk Score', { exact: true }))
    await expect(card).toContainText('Recommendation')
    // The kit ring draws its number as HTML; the meter carries the reading.
    await expect(card.getByRole('meter', { name: 'Risk Score' })).toHaveAttribute('aria-valuenow', '60')

    await visualRegion(page, 'gate-risk-gauge', theme, card)
    assertHermetic(api, errors)
  })
}
