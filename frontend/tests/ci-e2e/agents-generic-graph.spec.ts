/**
 * W14 AGT-06, hermetic (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.2 test 5):
 *
 *  - an investigation pipeline draws its real topology from `/agentic-runtime`: seven nodes — the plan, the
 *    five hypotheses side by side in ONE column, the synthesis — each coloured by its own status;
 *  - an offline pipeline (every stage on the hand canvas, `offline@1`) still draws the hand canvas;
 *  - the inventory adds `GET /agents/pipelines/<id>/agentic-runtime` for the investigation only (the
 *    offline pipeline's runtime is not answered at all: asking for it would fail the spec closed).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agent-ops.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY } from '../visual/production/fixtures-run'
import {
  HYPOTHESES,
  investigationGraphWorld,
  INVESTIGATION_PIPELINE_ID,
  OFFLINE_PIPELINE_ID,
} from '../visual/production/fixtures-agent-ops'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const report = (p: Page) => p.getByRole('region', { name: 'AI report' })
const ready = (p: Page) => report(p).getByText(AI_REPORT_SUMMARY)
const node = (p: Page, id: string) => p.locator(`[data-stage-id="${id}"]`)
const added = (before: string[], after: string[]) => [...new Set(after)].filter((line) => !before.includes(line)).sort()

test('an investigation draws plan → five hypotheses in one column → synthesis, coloured by status', async ({ page }) => {
  const world = investigationGraphWorld()
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  const atLoad = observed(api)
  // Nothing of the graph is asked before Agent stages opens.
  expect(world.runtimeReads).toEqual([])

  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  const graph = page.locator('[data-generic-graph]')
  await expect(graph).toBeVisible()
  await expect(graph.locator('[data-stage-id]')).toHaveCount(7)

  const plan = await node(page, 'investigator_plan').boundingBox()
  const synthesis = await node(page, 'investigator_synthesis').boundingBox()
  const hypotheses = await Promise.all(HYPOTHESES.map(async (h) => node(page, h).boundingBox()))
  expect(plan && synthesis && hypotheses.every(Boolean)).toBeTruthy()
  const xs = new Set(hypotheses.map((box) => Math.round(box?.x ?? -1)))
  expect(xs.size, 'the five hypotheses share one column').toBe(1)
  const ys = new Set(hypotheses.map((box) => Math.round(box?.y ?? -1)))
  expect(ys.size, 'side by side: five rows').toBe(5)
  const column = [...xs][0]
  expect(column).toBeGreaterThan(Math.round(plan?.x ?? 0))
  expect(Math.round(synthesis?.x ?? 0)).toBeGreaterThan(column)

  await expect(node(page, 'investigator_plan')).toHaveAttribute('data-stage-status', 'done')
  await expect(node(page, 'hypothesis_commit')).toHaveAttribute('data-stage-status', 'running')
  await expect(node(page, 'hypothesis_environment')).toHaveAttribute('data-stage-status', 'failed')
  await expect(node(page, 'investigator_synthesis')).toHaveAttribute('data-stage-status', 'pending')
  // Ten drawn dependencies (plan → 5, 5 → synthesis).
  await expect(graph.locator('path[data-edge-kind="dependency"]')).toHaveCount(10)

  await networkQuiet(page, api)
  expect(added(atLoad, observed(api))).toEqual([`GET /api/v1/agents/pipelines/${INVESTIGATION_PIPELINE_ID}/agentic-runtime`])
  assertHermetic(api, errors)
})

test('an offline pipeline still draws the hand canvas, and its runtime is never asked', async ({ page }) => {
  const world = investigationGraphWorld()
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: world.handlers, ready })
  await page.locator(`[data-testid="pipeline-row"][data-pipeline-id="${OFFLINE_PIPELINE_ID}"]`)
    .getByRole('button', { name: /offline pipeline/i }).click()
  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  await expect(page.getByRole('tablist', { name: 'Pipeline views' })).toBeVisible()
  await expect(page.locator('[data-generic-graph]')).toHaveCount(0)
  // The hand canvas's own cards (it pre-seeds every node of the offline topology).
  await expect(page.getByText('Flaky Sentinel', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.runtimeReads).toEqual([])
  assertHermetic(api, errors)
})
