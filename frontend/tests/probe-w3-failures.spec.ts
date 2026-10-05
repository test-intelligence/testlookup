/**
 * LIVE probe (Wave 3, VIZ-207/504 + VIZ-602 + VIZ-506): the Failures page's
 * failure groups, systemic clusters, drill ladder and project scatter against
 * a deployment. Read-only (a drill is a URL change, a rows panel a read).
 * Skipped unless `PROBE_W3=1` (`PROBE_HOWTO` in `tests/lib/rollout.ts`).
 * Since Phase D (S6) the sections ask no flag, so the probe no longer checks
 * one.
 *
 * Live-only questions: the groups' signature SQL (BE3) and the rows'
 * `error_signature` selector (BE4) are the SAME expression (a group's rows =
 * its failure_count); the clusters carry `membership_key` after the nightly
 * sweep (migration 0193); every body is the real wire shape.
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
} from './lib/rollout'

const env = probeEnv()
test.skip(!env, `Live probe, not run in CI. ${PROBE_HOWTO}`)
test.use({ viewport: { width: 1280, height: 4000 } })

const frameIn = (page: Page, id: string) => section(page, id).locator('[data-chart-frame]').first()

test('Failures: groups, ladder and scatter draw from valid wire bodies; a group\'s rows are its failures; clusters keyed', async ({
  page,
}) => {
  const live = env as ProbeEnv
  const groups = captureResponses(page, /^\/api\/v1\/analytics\/failure-groups$/)
  const ladder = captureResponses(page, /^\/api\/v1\/analytics\/chart-data$/)
  const scatter = captureResponses(page, /^\/api\/v1\/analytics\/test-scatter$/)
  const clusters = captureResponses(page, /^\/api\/v1\/analytics\/systemic-clusters$/)
  const project = await probeSignIn(page, live)
  await page.goto(`${live.base}/failures`)

  await bringNear(page, 'failures-groups')
  await expect(frameIn(page, 'failures-groups'), 'groups drew (pick a project with failures)').toHaveAttribute('data-chart-state', DRAWN, {
    timeout: 30_000,
  })
  expect(groups[0]?.status).toBe(200)
  expect(groups[0].url).toContain('include=edges')
  expectWireChart(groups[0].body, 'graph', 'live failure-groups')
  const body = groups[0].body as {
    groups: { signature: string; failure_count: number }[]
    no_message: { id: string }
    singletons: { id: string }
  }
  expect(body.no_message.id).toBe('__NO_MESSAGE__')
  expect(body.singletons.id).toBe('__SINGLETONS__')
  await expect(section(page, 'failures-groups')).not.toContainText(/\bAI\b/)

  // The largest group's rows, read-only through the API: the same signature expression on both routes.
  const largest = body.groups[0]
  if (largest) {
    const query = new URLSearchParams({
      metric: 'failures',
      group_by: 'error_signature',
      bucket_error_signature: largest.signature,
      project_id: project,
      days: '30',
    })
    const rows = await probeGet(page, `/api/v1/analytics/chart-data/rows?${query}`)
    expect(rows.status).toBe(200)
    expect((rows.body as { total: number }).total, 'a group\'s rows are its failure_count').toBe(largest.failure_count)
  }

  // Clusters: asked only when the tab opens, without days; keyed by membership_key.
  expect(clusters).toEqual([])
  await section(page, 'failures-groups').getByRole('tab', { name: 'Systemic flake clusters' }).click()
  await expect.poll(() => clusters.length, { timeout: 15_000 }).toBe(1)
  expect(clusters[0].status).toBe(200)
  expect(new URLSearchParams(clusters[0].url.split('?')[1]).has('days')).toBe(false)
  const items = (clusters[0].body as { items: { membership_key: string | null; members: unknown[] }[] }).items
  for (const item of items) {
    if (item.members.length > 0) expect(item.membership_key, 'a cluster with members has its key (0193 + the sweep)').toMatch(/^[0-9a-f]{32}$/)
  }

  await bringNear(page, 'failures-drill')
  await expect(frameIn(page, 'failures-drill')).toHaveAttribute('data-chart-state', /^(ready|truncated|filtered-empty)$/, { timeout: 30_000 })
  const level0 = ladder.find((r) => r.url.includes('group_by=suite') && r.url.includes('group_by=status'))
  expect(level0?.status).toBe(200)
  expectWireChart(level0?.body, 'series', 'live ladder level 0')

  await bringNear(page, 'scatter-project')
  await expect(frameIn(page, 'scatter-project')).toHaveAttribute('data-chart-state', /^(ready|truncated|filtered-empty|not-measured)$/, {
    timeout: 30_000,
  })
  expect(scatter[0]?.status).toBe(200)
  expectWireChart(scatter[0].body, 'points', 'live test-scatter (project)')
})
