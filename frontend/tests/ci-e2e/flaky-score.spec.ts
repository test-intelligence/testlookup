/**
 * W09 FLK-03 (contract §2.7, §4.3), hermetic: the four-signal flakiness score on `/flaky` › Detected.
 *
 *  - the table gains ONE column, **Flakiness score** with `(4 signals)` under it (its header `title` names both
 *    detectors), after Fail %; every cell is one line: a scored test `0.81` + its band chip (`high`), below the
 *    evidence floor `insufficient` (title `3 of 5 observations`), not swept yet `not scored yet` (title
 *    `Scores are computed nightly`), a failed enrichment `—` (title `Score not available`); Fail %,
 *    Recommendation and Impact stay;
 *  - **Sort** (in the chip row, not a new row) → `Flakiness score`: the scored tests by score, highest first,
 *    then every unscored one in the server's order; back to `Impact`: the server's order;
 *  - a row's EXISTING details panel opens with the section `Flakiness score (4 signals)`: the four signals with
 *    value × weight and `Weights sum to 1.00`; an insufficient row reads `insufficient: 3 of 5 observations`;
 *  - the load asks nothing new (the score travels on the coach response); the table fits its box at 1280 and
 *    1440 (the slack printed as a WIDTH line).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `flakyHandlers` (`FLAKY_SCORES`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectInventory, networkQuiet, openRollout, primaryOverflow, SHELL_BASE } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { flakyHandlers } from '../visual/production/fixtures-pages'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const table = (page: Page) => page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })
const rowOf = (page: Page, fingerprint: string) => table(page).locator(`tr[data-flaky-row="${fingerprint}"]`)

/** fold-flaky's Detected inventory (unchanged by W09: no new request on Detected). */
const DETECTED_LOAD = [
  ...SHELL_BASE,
  `GET /api/v1/projects/${P}/flaky-coach?days=30&limit=50`,
  `GET /api/v1/quarantine/stats?project_id=${P}`,
  `GET /api/v1/quarantine?project_id=${P}&live_only=true&limit=200`,
  `GET /api/v1/feature-flags/flaky_auto_quarantine/status?project_id=${P}`,
]

const SERVER_ORDER = Array.from({ length: 12 }, (_, i) => `fp-${String(i).padStart(2, '0')}`)
/** Scored by score (0.81, 0.73, 0.62, 0.46, 0.35, 0.27), then the unscored in the server's order. */
const SCORE_ORDER = ['fp-02', 'fp-08', 'fp-04', 'fp-00', 'fp-10', 'fp-06', 'fp-01', 'fp-03', 'fp-05', 'fp-07', 'fp-09', 'fp-11']

async function rowOrder(page: Page): Promise<string[]> {
  return table(page).locator('tbody tr[data-flaky-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-flaky-row') ?? ''))
}

/** The cell under the "Flakiness score" header, in a row. */
async function scoreCell(page: Page, fingerprint: string) {
  const headers = await table(page).locator('thead th').evaluateAll((ths) => ths.map((th) => (th.textContent ?? '').trim()))
  const index = headers.findIndex((text) => text.startsWith('Flakiness score'))
  expect(index, `a "Flakiness score" column in ${JSON.stringify(headers)}`).toBeGreaterThan(-1)
  return rowOf(page, fingerprint).locator('td').nth(index)
}

test('the score column: one line per cell for a scored, an insufficient, a pending and an unavailable score', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await expect(rowOf(page, 'fp-00')).toBeVisible()

  // The header: after Fail %, "(4 signals)" under it, a title naming both detectors; the other columns stay.
  const headers = await table(page).locator('thead th').evaluateAll((ths) => ths.map((th) => (th.textContent ?? '').trim()))
  const score = headers.findIndex((text) => text.startsWith('Flakiness score'))
  expect(headers[score - 1], 'right after Fail %').toBe('Fail %')
  expect(headers).toEqual(expect.arrayContaining(['Test', 'History', 'Fail %', 'Recommendation', 'Impact']))
  const header = table(page).locator('thead th').nth(score)
  await expect(header).toContainText('(4 signals)')
  await expect(header).toHaveAttribute(
    'title',
    'Result volatility, retries, environment instability and duration variance, from the nightly score. Separate from Fail %, which is the failure-rate detector.',
  )

  const scored = await scoreCell(page, 'fp-02')
  await expect(scored).toContainText('0.81')
  await expect(scored).toContainText('high')
  const medium = await scoreCell(page, 'fp-04')
  await expect(medium).toContainText('0.62')
  await expect(medium).toContainText('medium')
  const low = await scoreCell(page, 'fp-06')
  await expect(low).toContainText('0.27')
  await expect(low).toContainText('low')
  const insufficient = await scoreCell(page, 'fp-01')
  await expect(insufficient).toHaveText('insufficient')
  await expect(insufficient.locator('[title="3 of 5 observations"]')).toHaveCount(1)
  const pending = await scoreCell(page, 'fp-03')
  await expect(pending).toHaveText('not scored yet')
  await expect(pending.locator('[title="Scores are computed nightly"]')).toHaveCount(1)
  const unavailable = await scoreCell(page, 'fp-07')
  await expect(unavailable).toHaveText('—')
  await expect(unavailable.locator('[title="Score not available"]')).toHaveCount(1)
  // Never a zero for a test the sweep could not score.
  for (const fingerprint of ['fp-01', 'fp-03', 'fp-05', 'fp-07', 'fp-09', 'fp-11']) {
    await expect(await scoreCell(page, fingerprint)).not.toContainText('0.00')
  }

  // ONE line per cell: every row is as tall as every other (a wrapped score would add a ~16 px line; the
  // last row is a pixel shorter, its border).
  const heights = await table(page).locator('tbody tr').evaluateAll((rows) => rows.map((row) => row.getBoundingClientRect().height))
  expect(Math.max(...heights) - Math.min(...heights), `row heights ${JSON.stringify(heights)}`).toBeLessThanOrEqual(2)
  for (const cell of [scored, insufficient, pending]) {
    expect(await cell.evaluate((td) => getComputedStyle(td.firstElementChild ?? td).whiteSpace), 'the score cell does not wrap').toBe('nowrap')
  }

  await networkQuiet(page, api)
  expectInventory(api, errors, DETECTED_LOAD, '/flaky, Detected (with the score column)')
})

test('Sort › Flakiness score puts the most flaky first, unscored after in the server\'s order; Impact restores it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await expect(rowOf(page, 'fp-00')).toBeVisible()
  expect(await rowOrder(page)).toEqual(SERVER_ORDER)

  const sort = page.getByLabel('Sort')
  await expect(sort).toHaveValue('impact')
  // The control sits in the chip row (the Recommendation chips), not on a row of its own.
  const chips = await page.getByRole('radiogroup', { name: 'Recommendation' }).boundingBox()
  const control = await sort.boundingBox()
  expect(chips && control && Math.abs(chips.y + chips.height / 2 - (control.y + control.height / 2)) < 12, 'Sort is on the chip row').toBe(true)

  await sort.selectOption({ label: 'Flakiness score' })
  expect(await rowOrder(page)).toEqual(SCORE_ORDER)
  await sort.selectOption({ label: 'Impact' })
  expect(await rowOrder(page)).toEqual(SERVER_ORDER)
  await networkQuiet(page, api)
  expectInventory(api, errors, DETECTED_LOAD, '/flaky, Detected (sorting asks nothing)')
})

test('a row\'s details panel opens with the four signals and their weights; an insufficient row says so', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
  await page.getByRole('button', { name: 'test_checkout_flow_4', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'test_checkout_flow_4' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Flakiness score (4 signals)')).toBeVisible()
  for (const line of [
    'Result volatility (pass/fail flips) 0.70 × 0.45',
    'Retries 0.60 × 0.25',
    'Environment instability 0.55 × 0.20',
    'Duration variance 0.45 × 0.10',
  ]) {
    await expect(panel.getByText(line, { exact: true }), line).toBeVisible()
  }
  await expect(panel.getByText('Weights sum to 1.00')).toBeVisible()
  await expect(panel).toContainText('0.62')
  await expect(panel).toContainText('medium · 12 observations')
  // The existing details stay below it.
  await expect(panel.getByText('Total Runs')).toBeVisible()
  await panel.getByRole('button', { name: 'Close panel' }).click()
  await expect(panel).toHaveCount(0)

  await page.getByRole('button', { name: 'test_checkout_flow_1', exact: true }).click()
  const thin = page.getByRole('complementary', { name: 'test_checkout_flow_1' })
  await expect(thin).toContainText('insufficient: 3 of 5 observations')
  await expect(thin.getByText('Weights sum to 1.00')).toHaveCount(0)
  await thin.getByRole('button', { name: 'Close panel' }).click()

  await page.getByRole('button', { name: 'test_checkout_flow_3', exact: true }).click()
  await expect(page.getByRole('complementary', { name: 'test_checkout_flow_3' })).toContainText(
    'Not scored yet: the score is computed nightly from the last 30 days.',
  )
  await networkQuiet(page, api)
  expectInventory(api, errors, DETECTED_LOAD, '/flaky, Detected (the panel asks nothing)')
})

for (const width of [1280, 1440]) {
  test(`the Detected table, with its score column, fits its box at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/flaky', { handlers: flakyHandlers(), ready })
    await expect(rowOf(page, 'fp-11')).toBeAttached()
    await networkQuiet(page, api)
    const o = await primaryOverflow(page)
    const tableWidth = await table(page).evaluate((t) => Math.round(t.getBoundingClientRect().width))
    const naturalWidth = await table(page).evaluate((t) => {
      // The table's own content width: laid out without the 100 % stretch.
      const clone = t.cloneNode(true) as HTMLTableElement
      clone.style.width = 'auto'
      clone.style.position = 'absolute'
      clone.style.visibility = 'hidden'
      t.parentElement?.appendChild(clone)
      const w = Math.round(clone.getBoundingClientRect().width)
      clone.remove()
      return w
    })
    console.log(`WIDTH /flaky @${width} ${JSON.stringify({ scrollWidth: o.scrollWidth, clientWidth: o.clientWidth, tableWidth, naturalWidth, slack: o.clientWidth - naturalWidth, columns: o.columns })}`)
    expect(o.scrollWidth, 'the table is no wider than its box').toBeLessThanOrEqual(o.clientWidth)
    expectInventory(api, errors, DETECTED_LOAD, `/flaky @${width}`)
  })
}
