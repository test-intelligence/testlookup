/**
 * LIVE probe (Wave 3, VIZ-205/501): the Trends suite x day heatmap against a
 * deployment. Read-only. Skipped unless `PROBE_W3=1` (`PROBE_HOWTO` in
 * `tests/lib/rollout.ts`); FAILS, never skips, when the credentials are
 * missing. Since Phase D (S6) the heatmap asks no flag, so the probe no
 * longer checks one.
 *
 * What only a live server can show: the bodies are the server's real wire
 * shape (FK0 finding 1: the hermetic fixtures once hid that), the Trends
 * chart-data sections draw from it too, and a cell's rows reconcile with the
 * cell on real data (`reconciliation.value` = the cell's n).
 */
import { expect, test, type Page } from '@playwright/test'
import {
  bringNear,
  captureResponses,
  DRAWN,
  expectWireChart,
  probeEnv,
  probeGet,
  PROBE_HOWTO,
  probeSignIn,
  rowsPanels,
  section,
  type ProbeEnv,
  walkToMark,
} from './lib/rollout'

const env = probeEnv()
test.skip(!env, `Live probe, not run in CI. ${PROBE_HOWTO}`)
test.use({ viewport: { width: 1280, height: 4000 } })

const frameIn = (page: Page, id: string) => section(page, id).locator('[data-chart-frame]')

interface Matrix {
  x_keys: string[]
  y_keys: string[]
  cells: { x: number; y: number; value: number | string | null; n: number }[]
}

test('Trends: the suite x day heatmap draws from a valid wire body; a cell\'s rows reconcile with it', async ({ page }) => {
  const live = env as ProbeEnv
  const heatmaps = captureResponses(page, /^\/api\/v1\/analytics\/heatmap$/)
  const chartData = captureResponses(page, /^\/api\/v1\/analytics\/chart-data$/)
  await probeSignIn(page, live)
  await page.goto(`${live.base}/trends`)
  await bringNear(page, 'trends-heatmap')
  await expect(frameIn(page, 'trends-heatmap'), 'the heatmap drew (pick a project with runs in the last 14 days)').toHaveAttribute(
    'data-chart-state',
    DRAWN,
    { timeout: 30_000 },
  )
  expect(heatmaps, 'one heatmap read').toHaveLength(1)
  expect(heatmaps[0].status).toBe(200)
  expect(heatmaps[0].url).toContain('kind=suite_day')
  expectWireChart(heatmaps[0].body, 'matrix', 'live suite_day')
  const matrix = heatmaps[0].body as Matrix & { unit?: string }
  expect(matrix.unit).toBe('percent')
  expect(matrix.cells.filter((c) => c.n === 0 && c.value !== null), 'a cell nobody ran is null, never 0').toEqual([])
  // The chart-data sections on the same page (the Wave 2.6 envelope fix, live).
  for (const response of chartData) {
    expect(response.status, response.url).toBe(200)
    expectWireChart(response.body, 'series', `live ${response.url}`)
  }
  for (const id of ['trends-multi-series', 'trends-duration']) {
    await bringNear(page, id)
    await expect(frameIn(page, id), id).toHaveAttribute('data-chart-state', /^(ready|truncated|filtered-empty|not-measured)$/)
  }

  // A cell's rows, through the UI (Enter on the first measured cell), reconcile with it.
  await bringNear(page, 'trends-heatmap')
  const rows = captureResponses(page, /^\/api\/v1\/analytics\/chart-data\/rows$/)
  await section(page, 'trends-heatmap').locator('[data-chart-keyboard="heatmap"]').focus()
  await walkToMark(page, 'trends-heatmap')
  await page.keyboard.press('Enter')
  await expect(rowsPanels(page)).toHaveCount(1)
  await expect.poll(() => rows.length, { timeout: 15_000 }).toBeGreaterThan(0)
  expect(rows[0].status).toBe(200)
  const query = new URLSearchParams(rows[0].url.split('?')[1])
  const y = matrix.y_keys.indexOf(query.get('bucket_suite') ?? '')
  const x = matrix.x_keys.indexOf(query.get('bucket_day') ?? '')
  const cell = matrix.cells.find((c) => c.x === x && c.y === y)
  expect(cell, 'the rows selectors are the cell\'s KEYS').toBeDefined()
  const page1 = rows[0].body as { total: number; reconciliation: { mark_field: string; value: number } }
  expect(page1.total, 'the rows behind a cell are its executions (n)').toBe(cell?.n)

  // The same through the API: read-only, no UI state.
  const direct = await probeGet(page, rows[0].url)
  expect(direct.status).toBe(200)
})
