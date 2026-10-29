/**
 * W23a baselines for the four existing pages (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §6.3, §6.5),
 * the seven NEW G-D regions, in signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `overview-insights-card`: the Overview card with three rows (perf suite `High`, flake `Medium`, a coverage
 *    row resolved 2 h ago) and one not-measured detector line;
 *  - `overview-insights-empty`: the card with no row — the empty sentence and the ✓ line, one check "not measured";
 *  - `overview-insight-dismiss-inline`: the first row with its inline question open under it;
 *  - `inbox-insights`: Inbox › Insights with `Show dismissed (1)` in its toolbar;
 *  - `trends-callout-expected-missed`: the Trends › Volume callout, a configured cadence with two misses beside a
 *    paused one (the exact tile, the two lines, `Action needed`);
 *  - `trends-callout-no-cadence`: the callout over the shared set's gap with the Configure line (a QA lead);
 *  - `coverage-score-cadence`: Coverage's opened score disclosure — the `Run cadence` dimension and the miss row.
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The 4000 px
 * viewport (`PINNED_TALL`) keeps every region on screen with nothing scrolled, as `visualRegion` requires. World:
 * `production/fixtures-insights-cadence.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody
 * regenerates them locally.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { NOW } from './production/fixtures'
import {
  insightsCadenceWorld,
  notMeasured,
  pausedCadence,
  worldCadence,
  worldCadenceStatus,
  worldDetectors,
  worldInsight,
  worldInsightRows,
  worldMissedWindow,
  worldStatusItem,
  type InsightsCadenceWorldOptions,
} from './production/fixtures-insights-cadence'

test.use({ ...PINNED_TALL, timezoneId: 'UTC', locale: 'en-US' })

type Theme = (typeof THEMES_WITH_PAPER)[number]

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const overviewReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })
const inboxReady = (p: Page) => p.locator('[data-page-header] h1')
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const coverageReady = (p: Page) => landmark(p, 'Run cadence')
const card = (p: Page) => p.locator('[data-insights-card]')
const callout = (p: Page) => p.locator('section[aria-labelledby="schedpaused"]')

/** Coverage drop not measured (no snapshot yet); the other three measured. */
function oneNotMeasured() {
  const detectors = worldDetectors()
  detectors[2] = notMeasured('coverage_drop', 'coverage_no_snapshot')
  return detectors
}

async function open(page: Page, theme: Theme, path: string, ready: (p: Page) => Locator, options: InsightsCadenceWorldOptions) {
  const world = insightsCadenceWorld(options)
  const opened = await openRollout(page, path, { theme, handlers: world.handlers, ready, user: world.user })
  await networkQuiet(page, opened.api)
  return opened
}

/** Two misses (Tue 15, Wed 16 Sep) on nightly-regression, and a paused any-run cadence (release freeze). */
function missedAndPaused() {
  const nightly = worldCadence()
  const anyRun = pausedCadence(worldCadence({ suite_name: null }), { paused_reason: 'release freeze', paused_by_name: 'Alice Lead' })
  return worldCadenceStatus([
    worldStatusItem(nightly, { expected: 5, met: 3, missed: 2, missed_windows: [worldMissedWindow(2), worldMissedWindow(3)] }),
    worldStatusItem(anyRun, { expected: 0, met: 0, missed: 0, paused: 3 }),
  ])
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the Overview Insights card — ${theme}`, async ({ page }) => {
    const { api, errors } = await open(page, theme, '/overview', overviewReady,
      { page: 'overview', role: 'QA_ENGINEER', insights: worldInsightRows(), detectors: oneNotMeasured() })
    await expect(card(page).locator('[data-insight-row]')).toHaveCount(3)
    await blur(page)
    await visualRegion(page, 'overview-insights-card', theme, card(page))
    assertHermetic(api, errors)
  })

  test(`the Overview Insights card, empty — ${theme}`, async ({ page }) => {
    const { api, errors } = await open(page, theme, '/overview', overviewReady,
      { page: 'overview', role: 'QA_ENGINEER', insights: [], detectors: oneNotMeasured() })
    await expect(card(page).locator('[data-insights-empty]')).toBeVisible()
    await blur(page)
    await visualRegion(page, 'overview-insights-empty', theme, card(page))
    assertHermetic(api, errors)
  })

  test(`the Overview dismiss question, inline — ${theme}`, async ({ page }) => {
    const { api, errors } = await open(page, theme, '/overview', overviewReady,
      { page: 'overview', role: 'QA_ENGINEER', insights: worldInsightRows() })
    const row = card(page).locator('[data-insight-row]').first()
    await row.getByRole('button', { name: 'Dismiss', exact: true }).click()
    await expect(row.locator('[data-inline-confirm]')).toBeVisible()
    await blur(page)
    await visualRegion(page, 'overview-insight-dismiss-inline', theme, row)
    assertHermetic(api, errors)
  })

  test(`Inbox › Insights with Show dismissed (1) — ${theme}`, async ({ page }) => {
    const dismissed = worldInsight({
      kind: 'cost_budget', severity: 'medium', run_path: null, title: 'AI spend is at 85 % of the $10 monthly cap',
      subject_label: 'budget', link: { path: '/settings/billing', label: 'LLM cost budget' },
      dismissed_at: new Date(NOW.getTime() - 3_600_000).toISOString(), dismissed_by_name: 'Alice Lead',
    })
    const { api, errors } = await open(page, theme, '/my-failures?tab=insights', inboxReady,
      { page: 'inbox', role: 'QA_ENGINEER', insights: [...worldInsightRows(), dismissed] })
    const tab = page.locator('[data-inbox-insights]')
    await expect(tab.getByRole('button', { name: 'Show dismissed (1)' })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'inbox-insights', theme, tab)
    assertHermetic(api, errors)
  })

  test(`the Trends callout, expected runs missed — ${theme}`, async ({ page }) => {
    const { api, errors } = await open(page, theme, '/trends', trendsReady,
      { page: 'trends', role: 'QA_LEAD', status: missedAndPaused() })
    await expect(callout(page).getByRole('heading', { name: 'Expected runs missed' })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'trends-callout-expected-missed', theme, callout(page))
    assertHermetic(api, errors)
  })

  test(`the Trends callout, no cadence — ${theme}`, async ({ page }) => {
    const { api, errors } = await open(page, theme, '/trends', trendsReady, { page: 'trends', role: 'QA_LEAD' })
    await expect(callout(page).getByRole('link', { name: 'Configure' })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'trends-callout-no-cadence', theme, callout(page))
    assertHermetic(api, errors)
  })

  test(`the Coverage score with a configured cadence — ${theme}`, async ({ page }) => {
    const status = worldCadenceStatus([worldStatusItem(worldCadence(), {
      expected: 5, met: 4, missed: 1, missed_windows: [worldMissedWindow(2)],
    })], 30)
    const { api, errors } = await open(page, theme, '/coverage', coverageReady, { page: 'coverage', role: 'QA_LEAD', status })
    await page.getByRole('button', { name: /How this score is computed/ }).click()
    const verdict = page.getByRole('region', { name: 'Coverage verdict' })
    await expect(verdict).toContainText('4 of 5 expected runs')
    await blur(page)
    await visualRegion(page, 'coverage-score-cadence', theme, verdict)
    assertHermetic(api, errors)
  })
}
