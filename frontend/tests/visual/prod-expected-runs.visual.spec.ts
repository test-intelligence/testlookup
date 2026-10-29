/**
 * W23a TRND-03 baselines for Settings › Expected runs (`docs/deferred-scope-2026-10-13/work/W23a-contract.md`
 * §6.3, §6.5), the three NEW regions, in signal, lab and paper (`THEMES_WITH_PAPER`), a QA lead:
 *
 *  - `expected-runs-table`: the table with two cadences — `nightly-regression` (Mon–Fri by 06:00, Europe/Berlin,
 *    its last 14 days with two misses) and `smoke-suite` PAUSED (the warning pill, until, the reason, who);
 *  - `expected-runs-form`: the Add form, weekly, filled (Runs of suite, Weekdays, by 06:00, Europe/Berlin);
 *  - `expected-runs-pause-inline`: the pause row form under the first row (until and reason typed).
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The 2400 px
 * viewport (`PINNED`) keeps every region on screen with nothing scrolled, as `visualRegion` requires. World:
 * `production/fixtures-insights-cadence.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux);
 * nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import {
  insightsCadenceWorld,
  pausedCadence,
  worldCadence,
  worldCadenceStatus,
  worldMissedWindow,
  worldStatusItem,
} from './production/fixtures-insights-cadence'

test.use({ ...PINNED })

const h1 = (page: Page) => page.getByRole('heading', { level: 1, name: 'Expected runs', exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const table = (page: Page) => page.locator('table[data-expected-runs]')

/** Two cadences, the second paused; the first missed two of its last 14 days. */
function world() {
  const nightly = worldCadence()
  const smoke = pausedCadence(worldCadence({ suite_name: 'smoke-suite' }))
  const status = worldCadenceStatus([
    worldStatusItem(nightly, {
      measured: true, expected: 10, met: 8, missed: 2,
      missed_windows: [worldMissedWindow(2), worldMissedWindow(3)],
    }),
    worldStatusItem(smoke, { measured: true, expected: 6, met: 6, missed: 0, paused: 2 }),
  ])
  return { nightly, ...insightsCadenceWorld({ page: 'settings', cadences: [nightly, smoke], status }) }
}

async function openPage(page: Page, theme: (typeof THEMES_WITH_PAPER)[number]) {
  const w = world()
  const opened = await openRollout(page, '/settings/expected-runs', { theme, handlers: w.handlers, ready: h1, user: w.user })
  await expect(table(page).locator('tr[data-cadence-id]')).toHaveCount(2)
  await networkQuiet(page, opened.api)
  return { w, ...opened }
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the expected-runs table — ${theme}`, async ({ page }) => {
    const { api, errors } = await openPage(page, theme)
    await expect(table(page).locator('[data-cadence-state="paused"]')).toContainText('Paused')
    await blur(page)
    await visualRegion(page, 'expected-runs-table', theme, table(page))
    assertHermetic(api, errors)
  })

  test(`the Add form, weekly — ${theme}`, async ({ page }) => {
    const { api, errors } = await openPage(page, theme)
    await page.getByRole('button', { name: 'Add expected cadence' }).first().click()
    const form = page.getByRole('region', { name: 'Add expected cadence' })
    await form.getByLabel('Runs of suite').check()
    await form.getByLabel('Suite name').fill('nightly-regression-eu')
    await form.getByRole('button', { name: 'Weekdays' }).click()
    await form.getByLabel('Expected by').fill('06:00')
    await form.getByLabel('Time zone').selectOption('Europe/Berlin')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'expected-runs-form', theme, form)
    assertHermetic(api, errors)
  })

  test(`the pause row form — ${theme}`, async ({ page }) => {
    const { w, api, errors } = await openPage(page, theme)
    await table(page).locator(`tr[data-cadence-id="${w.nightly.id}"]`).getByRole('button', { name: /^Pause / }).click()
    const pause = page.locator(`[data-cadence-pause="${w.nightly.id}"]`)
    await pause.getByLabel('Pause until').fill('2026-09-25T23:59')
    await pause.getByLabel('Reason').fill('release freeze')
    await blur(page)
    await visualRegion(page, 'expected-runs-pause-inline', theme, pause)
    assertHermetic(api, errors)
  })
}
