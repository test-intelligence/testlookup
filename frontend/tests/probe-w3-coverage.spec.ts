/**
 * LIVE probe (Wave 3, VIZ-206/502 + VIZ-205/501 env/release kinds): the
 * Coverage page's coverage map and suite x environment heatmap against a
 * deployment. Read-only. Skipped unless `PROBE_W3=1` (`PROBE_HOWTO` in
 * `tests/lib/rollout.ts`). Since Phase D (S6) the sections ask no flag, so
 * the probe no longer checks one.
 *
 * Live-only questions: the tree's real node ids (`all`, `s:<key>`) drill to a
 * level the server answers; the environment `(none)` bucket's rows (FK1 open
 * issue: BE1 and BE4 must use the same environment expression) reconcile
 * with its cell.
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
  cells: { x: number; y: number; value: number | null; n: number }[]
}

test('Coverage: the map and the environment heatmap draw from valid wire bodies; Enter drills a suite', async ({ page }) => {
  const live = env as ProbeEnv
  const maps = captureResponses(page, /^\/api\/v1\/analytics\/coverage-map$/)
  const heatmaps = captureResponses(page, /^\/api\/v1\/analytics\/heatmap$/)
  const project = await probeSignIn(page, live)
  await page.goto(`${live.base}/coverage`)
  await bringNear(page, 'coverage-map')
  await expect(frameIn(page, 'coverage-map'), 'the map drew (pick a project with tests)').toHaveAttribute('data-chart-state', DRAWN, {
    timeout: 30_000,
  })
  await expect(frameIn(page, 'coverage-map')).toContainText('Test execution coverage — not code coverage')
  expect(maps[0]?.status).toBe(200)
  expectWireChart(maps[0].body, 'tree', 'live coverage-map depth 1')
  const nodes = (maps[0].body as { nodes: { id: string; parent_id: string | null }[] }).nodes
  expect(nodes[0]?.id).toBe('all')
  expect(nodes.slice(1).every((n) => n.id.startsWith('s:') || n.id.startsWith('other:'))).toBe(true)

  await bringNear(page, 'heatmap-suite_environment')
  await expect(frameIn(page, 'heatmap-suite_environment')).toHaveAttribute('data-chart-state', /^(ready|truncated|filtered-empty)$/, {
    timeout: 30_000,
  })
  const env1 = heatmaps.find((r) => r.url.includes('kind=suite_environment'))
  expect(env1?.status).toBe(200)
  expectWireChart(env1?.body, 'matrix', 'live suite_environment')

  // The (none) environment bucket: its rows reconcile with its cell (same expression on both routes).
  const matrix = env1?.body as Matrix
  const none = matrix.x_keys.indexOf('(none)')
  const cell = none >= 0 ? matrix.cells.find((c) => c.x === none && c.n > 0) : undefined
  if (cell) {
    const suite = matrix.y_keys[cell.y]
    const query = new URLSearchParams({ metric: 'executions', bucket_suite: suite, bucket_environment: '(none)', project_id: project, days: '30' })
    query.append('group_by', 'suite')
    query.append('group_by', 'environment')
    const rows = await probeGet(page, `/api/v1/analytics/chart-data/rows?${query}`)
    expect(rows.status).toBe(200)
    expect((rows.body as { total: number }).total, `(none) x ${suite}: rows = the cell's n`).toBe(cell.n)
  } else {
    test.info().annotations.push({ type: 'note', description: 'no (none) environment cell with executions in this project: not checked' })
  }

  // Enter on a suite drills: one depth-2 read the server answers with that suite as its root.
  await bringNear(page, 'coverage-map')
  await section(page, 'coverage-map').locator('[data-chart-keyboard="treemap"]').focus()
  await walkToMark(page, 'coverage-map')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/[?&]drill=suite/)
  await expect.poll(() => maps.filter((r) => r.url.includes('depth=2')).length, { timeout: 15_000 }).toBe(1)
  const depth2 = maps.find((r) => r.url.includes('depth=2'))
  expect(depth2?.status).toBe(200)
  expectWireChart(depth2?.body, 'tree', 'live coverage-map depth 2')
  const suiteKey = new URLSearchParams(depth2?.url.split('?')[1]).get('suite')
  const root = (depth2?.body as { nodes: { id: string }[] }).nodes[0]
  if (root) expect(root.id, 'the level\'s root is the drilled suite').toBe(`s:${suiteKey}`)
})
