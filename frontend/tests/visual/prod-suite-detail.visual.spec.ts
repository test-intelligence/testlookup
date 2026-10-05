/**
 * BEFORE/AFTER baselines of /coverage/suite (Wave 2.5, VIZ-104): the
 * "Run history" stacked daily bars (A2: Skipped and Broken shared one colour
 * before the migration) and the pass-rate trend (A3: a per-run area before,
 * a per-day kit time series after, "Pass rate trend — last N days", OD-4).
 *
 * Phase D S3: the page asks no flag; the pass-rate frame is always the
 * analysis frame, so its region is `prod-suite-detail-on`'s
 * `suite-on-pass-rate` (the flag-off `suite-pass-rate` shot is deleted). The
 * page is opened with the catalogue's answers (`SUITE_DETAIL_ON`) and every
 * flag off; the run history above keeps its PNG.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { test } from '@playwright/test'
import {
  assertHermetic,
  frameByHeading,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { expectDrawn, sectionFrame } from '../lib/rollout'
import { NOW, PROJECT_ID, SUITE, SUITE_DETAIL_ON, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`suite detail regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/coverage/suite?name=${SUITE}&days=30`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: SUITE_DETAIL_ON,
      ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    })

    const history = frameByHeading(page, /^Run history/)
    await waitForCharts(history)
    // The analysis frame below has drawn: nothing moves after this.
    const passRate = sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/)
    await expectDrawn(passRate, 'pass rate')
    await waitForCharts(passRate)

    await visualRegion(page, 'suite-run-history', theme, history)
    assertHermetic(api, errors)
  })
}
