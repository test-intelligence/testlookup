/**
 * /coverage/suite with the catalogue ON (Wave 2.6, VIZ-408; plan 2.2 "Suite
 * detail", 5.3). The flag buys one chart's trend analysis and zoom: the
 * existing pass-rate frame gains both, drawn from the page's own points, so
 * the flag adds NO request beyond the seam's lookup. Independently of the
 * flag, both frames grow from 220 to the 240 px floor (VIZ-106).
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { frameByHeading } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectInventory,
  expectNoErrorFrame,
  expectNoTextEscapes,
  networkQuiet,
  openRollout,
  section,
  sectionFrame,
  SHELL_ON,
} from '../lib/rollout'
import { CATALOGUE_ON, PROJECT_ID, SUITE, SUITE_DETAIL, SUITE_DETAIL_ON } from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => p.getByRole('heading', { name: /^Run history/ })
const PATH = `/coverage/suite?name=${SUITE}&days=30`

/** The flag-off list plus the seam's lookup: nothing else. */
const INVENTORY_ON = [
  ...SHELL_ON,
  `GET /api/v1/analytics/suite-detail?project_id=${P}&suite_name=${SUITE}&days=30`,
  `GET /api/v1/test-management/suites/${SUITE}/trend?project_id=${P}&days=30`,
  `GET /api/v1/suites?project_id=${P}`,
]

async function bodyHeight(frame: Locator): Promise<number> {
  return (await frame.locator('[data-chart-body]').boundingBox())?.height ?? 0
}

test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('catalogue on: the pass-rate frame gains trend analysis and zoom, and nothing is asked for it', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, flags: CATALOGUE_ON, ready })
  const passRate = sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/)
  await expectDrawn(passRate, 'suite-pass-rate')
  await expect(passRate.locator('[data-trend-controls]')).toBeVisible()
  await expect(passRate.locator('[data-chart-brush]')).toBeVisible()
  // Only that frame: Run history is untouched and outside any section.
  const history = frameByHeading(page, /^Run history/)
  await expectDrawn(history, 'run history')
  await expect(history.locator('[data-trend-controls], [data-chart-brush]')).toHaveCount(0)
  await expect(page.locator('[data-catalogue-section]')).toHaveCount(1)
  // The VIZ-106 floor.
  expect(await bodyHeight(history), 'run history body').toBeGreaterThanOrEqual(240)
  expect(await bodyHeight(passRate), 'pass-rate body').toBeGreaterThanOrEqual(240)
  await expectNoErrorFrame(page)
  await expectNoTextEscapes(page, 'Suite detail at 1280')
  await networkQuiet(page, api)
  expectInventory(api, errors, INVENTORY_ON, 'Suite detail, catalogue on')
})

// Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
// catalogue section (the pass-rate frame with its overlays and brush), in
// both themes the harness renders. No allowlist: R2 measured 0 violations.
for (const theme of ['signal', 'lab'] as const) {
  test(`axe: the catalogue section, every impact, no violation (${theme})`, async ({ page }) => {
    const { api } = await openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, flags: CATALOGUE_ON, ready, theme })
    await expectDrawn(sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/), 'suite-pass-rate')
    await networkQuiet(page, api)
    await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
  })
}

test('catalogue off: no section, no overlays, no brush; both frames still at the 240 px floor', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: SUITE_DETAIL, ready })
  const passRate = frameByHeading(page, /^Pass rate trend/)
  await expectDrawn(passRate, 'pass rate')
  await expectDrawn(frameByHeading(page, /^Run history/), 'run history')
  await expect(section(page, 'suite-pass-rate')).toHaveCount(0)
  await expect(page.locator('[data-trend-controls], [data-chart-brush]')).toHaveCount(0)
  for (const frame of await page.locator('[data-chart-frame]').all()) {
    expect(await bodyHeight(frame), 'frame body').toBeGreaterThanOrEqual(240)
  }
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('?days=365 in the URL: the page falls back to its own window, and nothing on the wire asks for more than 90', async ({
  page,
}) => {
  const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=365`, {
    handlers: SUITE_DETAIL_ON,
    flags: CATALOGUE_ON,
    ready,
  })
  await expectDrawn(sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/), 'suite-pass-rate')
  await networkQuiet(page, api)
  expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  expectInventory(api, errors, INVENTORY_ON, 'Suite detail at ?days=365')
})
