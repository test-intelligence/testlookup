/**
 * W23d AGT-14 / AGT-15 — the decision report's `Specialist evidence` row on the run's Evidence tab
 * (`docs/deferred-scope-2026-10-13/work/W23d-contract.md` §0.20, §4.1, §6.3), hermetic, at 1280 and 1440:
 *
 *  - with the findings world: the API contract card carries ``field `price` missing from GET /api/products/{id};
 *    drift since build 41``, the Log intelligence card a spark (`role="img"` named `p95 latency for checkout …`) and
 *    "p95 latency for checkout rose 3.1× in the failure window";
 *  - the offline world: "Prometheus lookup disabled: offline mode" and no spark;
 *  - the default world (no findings): no row, and the existing evidence groups unchanged;
 *  - the AI report's stage list shows `API Contract Validation` / `Log Intelligence` when the pipeline carries them;
 *  - nothing scrolls sideways (each card's slack printed).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-payload-metrics.ts` (G-A).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { expectNoHorizontalOverflow, networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { runAnalysisWorld } from '../visual/production/fixtures-in-page'
import { runSpecialistWorld } from '../visual/production/fixtures-payload-metrics'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const DRIFT = 'field price missing from GET /api/products/{id}; drift since build 41'
const ROSE = 'p95 latency for checkout rose 3.1× in the failure window'
const OFFLINE = 'Prometheus lookup disabled: offline mode'
const PATH = `/runs/${RUN_ID}?tab=evidence`

const ready = (p: Page) => p.getByRole('heading', { name: 'Decision intelligence' })
const row = (p: Page) => p.locator('[data-specialist-evidence]')

for (const width of [1280, 1440]) {
  test(`@${width}: the findings world — the drift sentence, the spark and its sentence`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const world = runSpecialistWorld({ findings: 'drift' })
    const { api, errors } = await openRollout(page, PATH, { handlers: world.handlers, ready })
    await expect(row(page)).toBeVisible()
    await expect(row(page).getByRole('heading', { name: 'Specialist evidence' })).toBeVisible()
    const contract = row(page).locator('[data-contract-findings]')
    const violation = contract.locator('[data-contract-violation]').first()
    await expect(violation).toHaveText(DRIFT)
    await expect(violation.locator('code')).toHaveText('price')
    const log = row(page).locator('[data-log-findings]')
    const spark = log.getByRole('img', { name: /^p95 latency for checkout: / })
    await expect(spark).toBeVisible()
    await expect(log.getByText(ROSE, { exact: true }).first()).toBeVisible()
    await expectNoOverlay(page)
    await expectNoHorizontalOverflow(page, `specialist evidence @${width}`)
    inPageLine('/runs/:id?tab=evidence specialist evidence', width, {
      contract: await minContentSlack(contract),
      log: await minContentSlack(log),
    })
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

test('the offline world: the outcome sentence, and no spark', async ({ page }) => {
  const world = runSpecialistWorld({ findings: 'offline' })
  const { api, errors } = await openRollout(page, PATH, { handlers: world.handlers, ready })
  const log = row(page).locator('[data-log-findings]')
  await expect(log.getByText(OFFLINE, { exact: true })).toBeVisible()
  await expect(log.getByRole('img')).toHaveCount(0)
  await expect(log.locator('[data-metric-spark]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the default world (no findings): no row, the existing evidence groups as before', async ({ page }) => {
  const world = runAnalysisWorld({ decisionReport: true })
  const { api, errors } = await openRollout(page, PATH, { handlers: world.handlers, ready })
  for (const group of ['Failure clusters', 'Flaky tests', 'Test health']) {
    await expect(page.getByRole('heading', { level: 4, name: new RegExp(`^${group} · `) })).toBeVisible()
  }
  await expect(row(page)).toHaveCount(0)
  await expect(page.getByText('Specialist evidence', { exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the AI report\'s stage list names the two specialists when the pipeline carries them', async ({ page }) => {
  const world = runSpecialistWorld({ findings: 'drift' })
  const { api, errors } = await openRollout(page, PATH, { handlers: world.handlers, ready })
  // The stage cards sit behind the collapsed "Agent stages" disclosure and its "Raw stage cards" section.
  await page.getByRole('button', { name: /^Agent stages/ }).click()
  const raw = page.locator('details').filter({ has: page.locator('summary', { hasText: 'Raw stage cards' }) })
  await raw.locator('summary').click()
  await expect(raw.getByText('API Contract Validation', { exact: true })).toBeVisible()
  await expect(raw.getByText('Log Intelligence', { exact: true })).toBeVisible()
  await expect(raw.getByText('Compare captured REST responses with earlier runs', { exact: true })).toBeVisible()
  await expect(raw.getByText('Trace, log-rate and application-metric evidence', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
