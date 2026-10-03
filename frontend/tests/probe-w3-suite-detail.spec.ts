/**
 * LIVE probe (Wave 3, VIZ-205/501 test x run + VIZ-506 scatter): Suite
 * detail's two sections against a deployment with both flags on. Read-only.
 * Skipped unless `PROBE_W3=1` (`PROBE_HOWTO` in `tests/lib/rollout.ts`);
 * FAILS when a flag is off. The suite: `PROBE_SUITE`, else the project's
 * first suite (`/api/v1/suites`).
 *
 * Live-only questions: the status matrix and the points body are the real
 * wire shape; a scatter point's rows reconcile with its n (the evaluated
 * executions of a failure rate); exactly one rows panel opens.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  bringNear,
  captureResponses,
  DRAWN,
  expectProbeFlagsOn,
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

const frameIn = (page: Page, id: string) => section(page, id).locator('[data-chart-frame]').first()

test('Suite detail: test x run and the scatter draw from valid wire bodies; a point\'s rows are its n', async ({ page }) => {
  const live = env as ProbeEnv
  const heatmaps = captureResponses(page, /^\/api\/v1\/analytics\/heatmap$/)
  const scatter = captureResponses(page, /^\/api\/v1\/analytics\/test-scatter$/)
  const rows = captureResponses(page, /^\/api\/v1\/analytics\/chart-data\/rows$/)
  const project = await probeSignIn(page, live)
  await expectProbeFlagsOn(page, project)
  let suite = process.env.PROBE_SUITE ?? ''
  if (!suite) {
    const suites = await probeGet(page, `/api/v1/suites?project_id=${project}`)
    expect(suites.status).toBe(200)
    suite = (suites.body as { items?: { name: string }[] }).items?.[0]?.name ?? ''
  }
  expect(suite, `no suite to probe: set PROBE_SUITE. ${PROBE_HOWTO}`).toBeTruthy()
  await page.goto(`${live.base}/coverage/suite?name=${encodeURIComponent(suite)}&days=30`)

  await bringNear(page, 'heatmap-test_run')
  await expect(frameIn(page, 'heatmap-test_run')).toHaveAttribute('data-chart-state', /^(ready|truncated|filtered-empty|not-measured)$/, {
    timeout: 30_000,
  })
  expect(heatmaps[0]?.status).toBe(200)
  expect(heatmaps[0].url).toContain('kind=test_run')
  expectWireChart(heatmaps[0].body, 'matrix', 'live test_run')
  expect((heatmaps[0].body as { value_type: string }).value_type).toBe('status')

  await bringNear(page, 'scatter-suite')
  await expect(frameIn(page, 'scatter-suite'), 'the scatter drew (pick a suite with 5+ executions per test)').toHaveAttribute(
    'data-chart-state',
    DRAWN,
    { timeout: 30_000 },
  )
  expect(scatter[0]?.status).toBe(200)
  expectWireChart(scatter[0].body, 'points', 'live test-scatter (suite)')
  const points = (scatter[0].body as { points: { id: string; n: number }[] }).points

  await section(page, 'scatter-suite').locator('[data-chart-keyboard="scatter"]').focus()
  await walkToMark(page, 'scatter-suite')
  await page.keyboard.press('Enter')
  await expect(rowsPanels(page), 'exactly one rows panel').toHaveCount(1)
  await expect.poll(() => rows.length, { timeout: 15_000 }).toBeGreaterThan(0)
  expect(rows[0].status).toBe(200)
  const test_ = new URLSearchParams(rows[0].url.split('?')[1]).get('bucket_test')
  const point = points.find((p) => p.id === test_)
  expect(point, 'the rows selector is a point id').toBeDefined()
  expect((rows[0].body as { total: number }).total, 'a point\'s rows are its evaluated executions (n)').toBe(point?.n)
})
