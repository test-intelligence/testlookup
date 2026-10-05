/**
 * BEFORE/AFTER baselines of /overview (Wave 2.5, VIZ-104): the KPI cards
 * with their three real sparklines and the verdict card with its pass-rate
 * mini meter (G7).
 *
 * Phase D S1: the Execution-trend card (and its `overview-trend` shot) is
 * gone; the catalogue mounts on every load, so the page is opened with the
 * catalogue's answers (`OVERVIEW_ON`) and every flag off, and the regions
 * are captured once the catalogue has drawn (as `prod-overview-on` captures
 * the same two names). The pass-rate trend that replaced the card is
 * `overview-on-trend` there.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import {
  ancestorWithClass,
  assertHermetic,
  cardAround,
  openProductionPage,
  PINNED,
  THEMES,
  visualRegion,
  waitForCharts,
} from '../lib/production-pages'
import { expectDrawn, sectionFrame } from '../lib/rollout'
import { NOW, OVERVIEW_ON, PROJECT_ID, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`overview regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/overview', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: OVERVIEW_ON,
      ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    })

    const kpis = ancestorWithClass(page.getByText('Total executions', { exact: true }), 'grid')
    // The three KPI cards with a day series draw a sparkline (not a caption).
    await expect(kpis.locator('svg path')).not.toHaveCount(0)
    await expect(kpis.getByText(/no trend line|no executions recorded/)).toHaveCount(0)

    const verdict = cardAround(page.getByText('RELEASE READINESS', { exact: true }))
    await expect(verdict.getByText('Conditional', { exact: false }).first()).toBeVisible()

    for (const [id, title] of [
      ['overview-trend', 'Pass rate trend'],
      ['overview-donut', 'Status breakdown'],
      ['overview-top-failing', 'Top failing tests'],
      ['overview-categories', 'Failure categories'],
    ] as const) {
      const frame = sectionFrame(page, id, title)
      await expectDrawn(frame, id)
      await waitForCharts(frame)
    }

    await visualRegion(page, 'overview-kpis', theme, kpis)
    await visualRegion(page, 'overview-verdict', theme, verdict)
    assertHermetic(api, errors)
  })
}
