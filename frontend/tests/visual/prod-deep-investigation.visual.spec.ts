/**
 * BEFORE/AFTER baseline of /deep-investigate (Wave 2.5, VIZ-104): the five
 * "Investigation inputs" KPI slots (a severity distribution bar, two
 * literal polylines, a target line and the spend-vs-budget bars, with
 * $18.42 of a $50 budget spent).
 *
 * The page focuses its newest run in `?run=` (UX redesign P4: the old
 * `/deep-investigate/<run>` redirects to the Run page's Analysis tab), so
 * the spec opens that run's `?run=` URL directly: the same page, without a
 * redirect race.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { DEEP_INVESTIGATION, NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`deep investigation regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/deep-investigate?run=${RUN_ID}`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: DEEP_INVESTIGATION,
      ready: (p) => landmark(p, 'Investigation inputs'),
    })

    const kpis = landmark(page, 'Investigation inputs')
    await expect(kpis).toContainText('$18.42')
    await expect(kpis).toContainText('of $50 budget')

    await visualRegion(page, 'deep-kpis', theme, kpis)
    assertHermetic(api, errors)
  })
}
