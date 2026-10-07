/**
 * Baselines of the suite page and the suites list (UX redesign P4, one page
 * for one suite; `03-implementation-plan.md` § P4 item 3):
 *  - `suite-page-kpis`: the suite page's KPI strip (the window's tests run,
 *    executions, pass rate, failed, average duration);
 *  - `suite-page-tests`: its Tests tab, the catalog joined with the window's
 *    analytics (pass rate, executions, duration, latest result, the flaky pill,
 *    the last error; a test that did not run reads "—");
 *  - `suites-list`: the suites list with its run columns (pass rate, last
 *    run, executions, failing, owner; the default suite no run reports by name
 *    reads "—").
 * New regions: their PNGs come from CI (Linux fonts), not from this machine.
 * The Charts tab's frames keep their regions in `prod-suite-detail*.visual`.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { SUITE_DETAIL_ON, SUITE_ID } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`suite page regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, `/suites/${SUITE_ID}`, {
      theme,
      handlers: SUITE_DETAIL_ON,
      ready: (p) => p.locator('[data-suite-tests]'),
    })
    await networkQuiet(page, api)
    await expect(page.locator('[data-kpi-strip] [data-metric-card]')).toHaveCount(5)
    await expect(page.locator('[data-test-row="fp-auth-2"]')).toContainText('Flaky')
    await visualRegion(page, 'suite-page-kpis', theme, page.locator('[data-kpi-strip]'))
    await visualRegion(page, 'suite-page-tests', theme, page.locator('[data-suite-tests]'))
    assertHermetic(api, errors)
  })

  test(`suites list region — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/suites', {
      theme,
      handlers: SUITE_DETAIL_ON,
      ready: (p) => p.locator('[data-primary] table'),
    })
    await networkQuiet(page, api)
    await expect(page.locator('[data-col="owner"]').first()).toHaveText('QA Lead')
    await visualRegion(page, 'suites-list', theme, page.locator('[data-primary]'))
    assertHermetic(api, errors)
  })
}
