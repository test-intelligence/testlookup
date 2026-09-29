/**
 * BEFORE/AFTER baselines of /coverage/suite (Wave 2.5, VIZ-104): the
 * "Run history" stacked daily bars (A2: Skipped and Broken shared one colour
 * before the migration) and the pass-rate trend (A3: a per-run area before,
 * a per-day kit time series after, "Pass rate trend — last N days", OD-4).
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
import { NOW, PROJECT_ID, SUITE, SUITE_DETAIL, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`suite detail regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/coverage/suite?name=${SUITE}&days=30`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: SUITE_DETAIL,
      ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    })

    const history = frameByHeading(page, /^Run history/)
    await waitForCharts(history)
    const passRate = frameByHeading(page, /^Pass rate trend/)
    await waitForCharts(passRate)

    await visualRegion(page, 'suite-run-history', theme, history)
    await visualRegion(page, 'suite-pass-rate', theme, passRate)
    assertHermetic(api, errors)
  })
}
