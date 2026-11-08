/**
 * W39 STRAT-07 on `/runs/:runId?tab=timing`, hermetic: the "Observability evidence" card at the bottom of the Timing
 * tab — the Splunk / OpenShift / Prometheus evidence the AI analysis already fetched, read from what is stored
 * (`GET /runs/{id}/observability-timeline`, mocked to the contract §1.2 shape):
 *
 *  - evidence: below every timing card; one axis; a Prometheus line with its sentence, Splunk dots, an OpenShift bar
 *    and an instant, the item on hover; the "Without a time" lists; the analysis and the earlier one not shown;
 *  - mixed honest states: each lane its state and the server's sentence, only the lane with evidence draws, offline
 *    mode labelled "now";
 *  - not analysed: no axis, the lanes' sentences only;
 *  - a failed read is one sentence and the timing cards stay; the evidence is read once, only when the tab opens.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, UTC). Answers: `fixtures-observability.ts`, with the
 * timed run of `fixtures-timing.ts` (`TIMED`).
 */
import { expect, test, type Page } from '@playwright/test'
import { respond } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import {
  OBSERVABILITY_EVIDENCE,
  OBSERVABILITY_MIXED,
  OBSERVABILITY_NOT_ANALYSED,
  OBSERVABILITY_PATH,
  SENTENCE,
} from '../visual/production/fixtures-observability'
import { TIMED, TIMING_PATH, withTiming } from '../visual/production/fixtures-timing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const card = (page: Page) => page.getByRole('region', { name: 'Observability evidence' })
const lane = (page: Page, source: string) => card(page).locator(`[data-lane="${source}"]`)
const open = (page: Page, observability: unknown) =>
  openRollout(page, `/runs/${RUN_ID}?tab=timing`, {
    handlers: withTiming(TIMED, observability),
    ready: (p) => card(p).locator('[data-observability-state="ready"]'),
  })

test('evidence: the last card of the tab, one axis, a metric line, Splunk dots, an OpenShift bar, the untimed lists', async ({ page }) => {
  const { api, errors } = await open(page, OBSERVABILITY_EVIDENCE)
  const region = card(page)
  await expect(region).toHaveAttribute('data-axis-state', 'timed')
  // Below every timing card: the tab's last section, after Duration regressions.
  const regressions = await page.getByRole('region', { name: 'Duration regressions' }).boundingBox()
  const box = await region.boundingBox()
  expect(box && regressions ? box.y - (regressions.y + regressions.height) : -1).toBeGreaterThan(0)
  await expect(region).toContainText('Evidence the AI analysis already fetched — nothing is fetched when you open this card.')
  await expect(region.locator('[data-observability-analysed]')).toHaveText(/^From the analysis of Sep 18, 10:35\.$/)
  await expect(region.locator('[data-observability-others]')).toHaveText('1 earlier analysis not shown.')
  await expect(region.locator('[data-now="configured"]')).toHaveText('Now Splunk, OpenShift and Prometheus are configured for this project.')
  await expect(region.locator('[data-now="offline"]')).toHaveCount(0)

  await expect(region.locator('[data-lane]')).toHaveCount(5)
  await expect(region.locator('[data-lane][data-lane-state="evidence"]')).toHaveCount(5)
  await expect(region.locator('[data-evidence-axis]')).toHaveCount(1)
  await expect(region.locator('[data-evidence-axis] [data-evidence-tick]').first()).toHaveText('10:24:00')

  const series = lane(page, 'prometheus').locator('[data-evidence-series="p95 latency"]')
  await expect(series).toHaveAttribute('data-series-rose', 'true')
  await expect(series.getByRole('img', { name: 'p95 latency: 9 points' })).toBeVisible()
  await expect(series).toContainText('p95 latency for checkout rose 3.4× in the failure window')
  await expect(lane(page, 'splunk_trace').locator('[data-evidence-mark="instant"]')).toHaveCount(2)
  await expect(lane(page, 'ocp_events').locator('[data-evidence-mark="span"]')).toHaveCount(1)
  await expect(lane(page, 'ocp_events').locator('[data-evidence-mark="instant"]')).toHaveCount(1)
  await expect(region.locator('[data-evidence-run-window]').first()).toBeVisible()

  // Hover names the item (the time is the run's clock, here UTC).
  await lane(page, 'splunk_trace').locator('[data-evidence-mark="instant"]').last().hover()
  await expect(region.locator('[data-evidence-tip]')).toHaveText('10:30:04 [ERROR] — gateway: upstream timeout after 5000 ms')
  await lane(page, 'ocp_events').locator('[data-evidence-mark="span"]').hover()
  await expect(region.locator('[data-evidence-tip]')).toHaveText('10:27:00 → 10:31:30 — BackOff — Back-off restarting failed container — Seen 4×')

  // Items without a readable / stored time are listed, never placed.
  await expect(lane(page, 'splunk_per_test').locator('[data-untimed-item="time_unreadable"]')).toContainText('[REDACTED_PHONE] [ERROR] retry budget spent')
  await expect(lane(page, 'ocp_per_test').locator('[data-untimed-item="no_time_stored"]')).toContainText('Pod: checkout-sentinel')
  await expect(lane(page, 'ocp_per_test').locator('svg')).toHaveCount(0)

  // Fits its column: nothing scrolls sideways.
  expect(await region.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  await networkQuiet(page, api)
  expect(requestsTo(api, OBSERVABILITY_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('mixed honest states: each lane its state and sentence, only the lane with evidence draws; offline "now"', async ({ page }) => {
  const { api, errors } = await open(page, OBSERVABILITY_MIXED)
  const states = await card(page).locator('[data-lane]').evaluateAll((els) => els.map((el) => `${el.getAttribute('data-lane')}:${el.getAttribute('data-lane-state')}`))
  expect(states).toEqual(['prometheus:unavailable', 'splunk_trace:unavailable', 'ocp_events:none_found', 'splunk_per_test:evidence', 'ocp_per_test:not_recorded'])
  await expect(lane(page, 'prometheus').locator('[data-lane-sentence]')).toHaveText(SENTENCE.prometheus_unavailable)
  await expect(lane(page, 'splunk_trace').locator('[data-lane-sentence]')).toHaveText(SENTENCE.splunk_trace_unavailable)
  await expect(lane(page, 'ocp_events').locator('[data-lane-sentence]')).toHaveText(SENTENCE.ocp_events_none_found)
  for (const source of ['prometheus', 'splunk_trace', 'ocp_events', 'ocp_per_test']) {
    await expect(lane(page, source).locator('svg'), source).toHaveCount(0)
  }
  await expect(lane(page, 'splunk_per_test').locator('[data-evidence-mark]')).toHaveCount(1)
  await expect(card(page).locator('[data-now="offline"]')).toHaveText('Now AI offline mode is on, so analyses fetch no observability evidence.')
  await expect(card(page).locator('[data-now="configured"]')).toHaveText('Now Splunk and Prometheus are configured for this project; OpenShift is not.')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('not analysed: no axis, every lane its sentence only', async ({ page }) => {
  const { api, errors } = await open(page, OBSERVABILITY_NOT_ANALYSED)
  await expect(card(page)).toHaveAttribute('data-axis-state', 'none')
  await expect(card(page).locator('[data-observability-analysis]')).toHaveText('No deep analysis of this run is stored.')
  await expect(card(page).locator('[data-lanes] svg')).toHaveCount(0)
  await expect(card(page).locator('[data-evidence-axis]')).toHaveCount(0)
  await expect(lane(page, 'ocp_events')).toHaveAttribute('data-lane-state', 'not_recorded')
  await expect(lane(page, 'ocp_events').locator('[data-lane-sentence]')).toHaveText(SENTENCE.ocp_events_not_recorded)
  await expect(lane(page, 'splunk_per_test').locator('[data-lane-sentence]')).toHaveText(SENTENCE.splunk_per_test_not_analysed)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a failed read is one sentence and the timing stays; the evidence is read once, only when the tab opens', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, {
    handlers: [[OBSERVABILITY_PATH, () => respond(500, { detail: 'boom' })], ...withTiming(TIMED)],
    ready: (p) => p.locator('[data-primary] table'),
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, OBSERVABILITY_PATH)).toEqual([])
  await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Timing' }).click()
  await expect(page).toHaveURL(/[?&]tab=timing/)
  await expect(card(page).locator('[data-observability-state="error"]')).toHaveText('Could not load the observability evidence of this run.')
  await expect(page.locator('[data-chart="execution-timeline"] [data-timeline-bar]')).toHaveCount(12)
  await networkQuiet(page, api)
  expect(requestsTo(api, OBSERVABILITY_PATH)).toHaveLength(1)
  expect(requestsTo(api, TIMING_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})
