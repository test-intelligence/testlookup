/**
 * BEFORE/AFTER baselines of /failures (Wave 2.5, VIZ-104): the failure
 * verdict with its stability fill (G5), the "What's failing" card with its
 * 14-cell run strip (S4, fail drawn in `--gate-no-go` today) and the
 * failure timeline (S3, fail in `--status-failed`: two reds, one meaning).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  assertHermetic,
  cardByHeading,
  landmark,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
} from '../lib/production-pages'
import { FAILURES, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`failures regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/failures', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: FAILURES,
      ready: (p) => landmark(p, 'Failure verdict'),
    })

    const whatsFailing = cardByHeading(page, "What's failing")
    await expect(whatsFailing.getByRole('img', { name: /^Run strip:/ })).toBeVisible()
    const timeline = cardByHeading(page, 'Failure timeline')

    await visualRegion(page, 'failures-verdict', theme, landmark(page, 'Failure verdict'))
    await visualRegion(page, 'failures-whats-failing', theme, whatsFailing)
    await visualRegion(page, 'failures-timeline', theme, timeline)
    assertHermetic(api, errors)
  })
}
