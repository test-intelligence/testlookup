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
 * Phase D, S5: the Wave 3 sections mount unconditionally (no flag is asked),
 * so the page is opened with their answers (`FAILURES_ON`); they sit below
 * the body grid, so the four regions are the same pixels.
 * UX redesign P3 (page template), same region names, new contents:
 * `failures-verdict` is the one-line StatusBanner (the "Failure verdict"
 * landmark); `failures-whats-failing` is the Top failing table, the run strip
 * in its header (the What's-failing and Flakiness cards merged into it); the
 * category card is the Categories tab, opened first; the timeline is a
 * Disclosure, opened first. NEW region `failures-score`: the stability meter
 * (G5) and its four dimensions left the verdict for the "How this score is
 * computed" Disclosure, captured open.
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
import { FAILURES_ON, NOW, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`failures regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/failures', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: FAILURES_ON,
      ready: (p) => landmark(p, 'Failure verdict'),
    })

    const whatsFailing = cardByHeading(page, 'Top failing tests')
    await expect(whatsFailing.getByRole('img', { name: /^Run strip:/ })).toBeVisible()

    await visualRegion(page, 'failures-verdict', theme, landmark(page, 'Failure verdict'))
    await visualRegion(page, 'failures-whats-failing', theme, whatsFailing)

    // The categories are a tab (P3): open it, then the two Disclosures below the tabs.
    await page.getByRole('tablist', { name: 'Failure analysis sections' }).getByRole('tab', { name: 'Categories' }).click()
    await expect(page).toHaveURL(/[?&]tab=categories(&|$)/)
    const disclosure = (title: string) =>
      page.locator('[data-disclosure]').filter({ has: page.getByRole('button', { name: new RegExp(`^${title}`) }) })
    for (const title of ['Failure timeline', 'How this score is computed']) {
      await disclosure(title).getByRole('button', { name: new RegExp(`^${title}`) }).click()
      await expect(disclosure(title)).toHaveAttribute('data-open', 'true')
    }
    const timeline = disclosure('Failure timeline')
    await expect(timeline.getByRole('img', { name: /^Failure timeline:/ })).toBeVisible()
    await visualRegion(page, 'failures-timeline', theme, timeline)
    const score = disclosure('How this score is computed')
    await expect(score.getByRole('meter', { name: 'Stability score' })).toBeVisible()
    await visualRegion(page, 'failures-score', theme, score)

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
