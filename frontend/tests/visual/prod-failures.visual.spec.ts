/**
 * BEFORE/AFTER baselines of /failures (Wave 2.5, VIZ-104): the failure
 * verdict with its stability fill (G5), the "What's failing" card with its
 * 14-cell run strip (S4, fail drawn in `--gate-no-go` today) and the
 * failure timeline (S3, fail in `--status-failed`: two reds, one meaning).
 * Wave 3 C0 adds the failure category distribution, the categorisation card
 * of the page that gains the Wave 3 sections (failure groups and clusters,
 * the drill ladder, the project scatter): captured with every flag off,
 * before any Wave 3 code, and last, so the Wave 2.5 regions keep their
 * inputs.
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

    // Every canonical bucket is drawn, empty ones too; the fixture's 36
    // failures land in three of them (LOCATOR has no bucket: Unknown).
    const categories = cardByHeading(page, 'Failure category distribution')
    await expect(categories.getByRole('row')).toHaveCount(6)
    await expect(categories.getByRole('row', { name: 'Assertion / product: 58% (21 of 36)' })).toBeVisible()
    await expect(categories).toContainText('36 failures')
    await visualRegion(page, 'failures-categories', theme, categories)
    assertHermetic(api, errors)
  })
}
