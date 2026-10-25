/**
 * W14 AGT-05, hermetic (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.1 test 8):
 *
 *  - `/agents?run=` → Agent stages → Graph · Audit · Compare at the top of the graph frame (`?ptab=`);
 *  - Audit on an old pipeline: the gaps panel (data, the warning tint, never an alert) and the stage table;
 *    a replayable one: `Replayable ✓` and no panel;
 *  - Compare on a rerun pair: the picker offers exactly the run's other pipeline (its origin, the default),
 *    the table marks the changed stage and the line says `Output changed in 1 stage`;
 *  - the request inventory: opening Agent stages on an offline@1 pipeline adds nothing; Audit adds exactly
 *    the replay read; Compare exactly the compare read (polls repeat lines the page already made).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agent-ops.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY } from '../visual/production/fixtures-run'
import { auditWorld, compareWorld, ORIGIN_PIPELINE_ID, RERUN_PIPELINE_ID } from '../visual/production/fixtures-agent-ops'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const report = (p: Page) => p.getByRole('region', { name: 'AI report' })
const ready = (p: Page) => report(p).getByText(AI_REPORT_SUMMARY)
const strip = (p: Page) => p.getByRole('tablist', { name: 'Pipeline views' })
const viewTab = (p: Page, name: RegExp) => strip(p).getByRole('tab', { name })

/** The distinct request lines made since `before` (a poll repeats a line the page already made). */
const added = (before: string[], after: string[]) => [...new Set(after)].filter((line) => !before.includes(line)).sort()

test('Audit on an old pipeline: the gaps as data, the stage table, and exactly one new read', async ({ page }) => {
  const { handlers } = auditWorld({ replayable: false })
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers, ready })
  await networkQuiet(page, api)
  const atLoad = observed(api)

  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  await expect(strip(page).getByRole('tab')).toHaveText(['Graph', 'Audit · 4 gaps', 'Compare'])
  await expect(viewTab(page, /^Graph$/)).toHaveAttribute('aria-selected', 'true')
  // An offline@1 pipeline whose stages all map: the hand canvas, nothing new asked.
  await expect(page.locator('[data-generic-graph]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(added(atLoad, observed(api))).toEqual([])

  await viewTab(page, /^Audit/).click()
  await expect(page).toHaveURL(new RegExp(`[?&]ptab=audit(&|$)`))
  await expect(page).toHaveURL(new RegExp(`[?&]run=${RUN_ID}(&|$)`))
  const gaps = page.getByRole('region', { name: 'Audit gaps' })
  await expect(gaps.getByRole('listitem')).toHaveText([
    'Missing start events: summary',
    'Missing checkpoints: anomaly_detection, root_cause_analysis',
    'No final-state checksum recorded.',
  ])
  await expect(page.locator('[data-audit-header]')).toContainText('Not fully replayable · 4 gaps')
  await expect(page.locator('[data-audit-stages] tbody tr')).toHaveCount(5)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(added(atLoad, observed(api))).toEqual([`GET /api/v1/agents/pipelines/${ORIGIN_PIPELINE_ID}/replay`])

  // Graph is the clean URL again.
  await viewTab(page, /^Graph$/).click()
  await expect(page).not.toHaveURL(/ptab=/)
  assertHermetic(api, errors)
})

test('Audit on a replayable pipeline: Replayable ✓ and no gaps panel', async ({ page }) => {
  const { handlers } = auditWorld({ replayable: true })
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}&ptab=audit`, { handlers, ready })
  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  await expect(viewTab(page, /^Audit/)).toHaveText('Audit · replayable ✓')
  await expect(viewTab(page, /^Audit/)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-audit-header]')).toContainText('Replayable ✓')
  await expect(page.getByRole('region', { name: 'Audit gaps' })).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Compare a rerun pair: only the run\'s other pipeline is offered, one stage changed, exactly one new read', async ({ page }) => {
  const world = compareWorld()
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  const atLoad = observed(api)

  await report(page).getByRole('button', { name: 'Agent stages' }).click()
  await viewTab(page, /^Compare$/).click()
  await expect(page).toHaveURL(/[?&]ptab=compare(&|$)/)
  const picker = page.getByLabel('Compare with')
  // The rerun is on screen; its origin (`rerun_of`) is the one other pipeline, and the default.
  await expect(picker.locator('option')).toHaveCount(1)
  await expect(picker).toHaveValue(ORIGIN_PIPELINE_ID)
  await expect(page.locator('[data-compare-output]')).toHaveText('Output changed in 1 stage')
  const rca = page.locator('[data-compare-row="root_cause_analysis"]')
  await expect(rca.locator('[data-changed]')).toHaveCount(2)
  await expect(page.locator('[data-compare-row="summary"] [data-changed]')).toHaveCount(0)
  await expect(page.locator('[data-release-verdict]')).toHaveText(['—', '—'])
  await networkQuiet(page, api)
  expect(world.compared).toContainEqual({ a: RERUN_PIPELINE_ID, b: ORIGIN_PIPELINE_ID })
  expect(added(atLoad, observed(api))).toEqual([
    `GET /api/v1/agents/pipelines/compare?a=${RERUN_PIPELINE_ID}&b=${ORIGIN_PIPELINE_ID}`,
  ])
  assertHermetic(api, errors)
})
