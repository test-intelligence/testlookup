/**
 * W14 AGT-07 (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.5 test 8, §4.2), hermetic, on
 * `/deep-investigate?run=`: the page reads three server answers and computes none of them.
 *
 *  - on load exactly `GET …/pre-scan?limit=10`, `GET /deep-investigate/history?project_id=&page=1&size=10` and
 *    `GET …/estimate` with NO query: Pre-scan `3 groups`, Cost & time `$0.00 · ~4 min · local model — no charge`;
 *  - "Investigated runs · 4" switches the Recent runs panel to the history;
 *  - two groups ticked → Estimate asks the estimate with `selected_failures=12` (a read, nothing queued) → Run on
 *    selection posts exactly `{ mode: 'deep', failure_ids: <the two groups' ids> }`;
 *  - a world whose estimate gate resolves to Rules (configured Auto, no model) → no Run CTA anywhere, the gate's
 *    sentence in their place, nothing posted.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `deepConfigWorld` (`fixtures-agent-ops-b.ts`)
 * over G-A's `DEEP_INVESTIGATION` answers.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { deepPreScanBody, PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { deepConfigWorld, RULES_SENTENCE } from '../visual/production/fixtures-agent-ops-b'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const DEEP_PAGE = `/deep-investigate?run=${RUN_ID}`
const verdict = (p: Page) => p.getByRole('region', { name: 'Investigation verdict' })
const config = (p: Page) => p.getByTestId('deep-run-configuration')
const estimateCard = (p: Page) => p.locator('[data-deep-estimate]')

test('the pre-scan, the history and the measured estimate; Estimate re-asks; Run on selection posts the ids', async ({ page }) => {
  const world = deepConfigWorld()
  const { api, errors } = await openRollout(page, DEEP_PAGE, { handlers: world.handlers, ready: verdict })
  await expect(verdict(page)).toContainText('Pre-scan3 groupsby signature')
  await expect(verdict(page).getByTitle('15 failing · by error signature · clusters shown')).toHaveText('by signature')
  await expect(estimateCard(page)).toContainText('$0.00')
  await expect(estimateCard(page).locator('[data-estimate-minutes]')).toHaveText('~4 min')
  await expect(estimateCard(page)).toContainText('local model — no charge')
  await expect(estimateCard(page)).toContainText('estimate from 12 past runs')
  await networkQuiet(page, api)
  expect(requestsTo(api, `/api/v1/deep-investigate/${RUN_ID}/pre-scan`)).toEqual([`GET /api/v1/deep-investigate/${RUN_ID}/pre-scan?limit=10`])
  expect(requestsTo(api, '/api/v1/deep-investigate/history'))
    .toEqual([`GET /api/v1/deep-investigate/history?project_id=${PROJECT_ID}&page=1&size=10`])
  expect(requestsTo(api, `/api/v1/deep-investigate/${RUN_ID}/estimate`)).toEqual([`GET /api/v1/deep-investigate/${RUN_ID}/estimate`])

  // The history: four investigated runs, in the Recent runs panel.
  await page.getByRole('button', { name: 'Investigated runs · 4' }).click()
  await expect(page.getByRole('heading', { level: 3, name: /^Investigated runs/ })).toBeVisible()
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(4)
  await page.getByRole('button', { name: 'Investigated runs · 4' }).click()
  await expect(page.getByRole('heading', { level: 3, name: /^Recent runs/ })).toBeVisible()

  // Two groups → Estimate (a GET with the count) → Run on selection (a POST with the ids).
  const groups = deepPreScanBody(RUN_ID).groups
  await config(page).getByRole('checkbox', { name: /^Select 7 failures/ }).check()
  await config(page).getByRole('checkbox', { name: /^Select 5 failures/ }).check()
  await expect(config(page)).toContainText('2 groups · 12 failures selected')
  await config(page).getByRole('button', { name: 'Estimate' }).click()
  await expect.poll(() => world.state.estimateQueries.at(-1)?.get('selected_failures') ?? null).toBe('12')
  expect(world.state.writes, 'Estimate queues nothing').toEqual([])
  await config(page).getByRole('button', { name: 'Run on selection' }).click()
  await expect(config(page)).toContainText('Deep analysis queued for the 12 selected failures.')
  expect(world.state.writes).toEqual([{
    line: `POST /api/v1/deep-investigate/${RUN_ID}`,
    body: { mode: 'deep', failure_ids: [...groups[0].member_result_ids, ...groups[1].member_result_ids] },
  }])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('the estimate\'s gate resolves to Rules: no Run CTA anywhere, the gate\'s sentence, nothing posted', async ({ page }) => {
  const world = deepConfigWorld({ gate: 'rules' })
  const { api, errors } = await openRollout(page, DEEP_PAGE, { handlers: world.handlers, ready: verdict })
  await expect(page.getByTestId('deep-disabled-note')).toHaveText([RULES_SENTENCE, RULES_SENTENCE])
  await expect(page.getByRole('button', { name: 'Run Deep Analysis' })).toHaveCount(0)
  await expect(verdict(page).getByRole('button', { name: /Run on all/ })).toHaveCount(0)
  await expect(config(page).getByRole('button', { name: 'Run on selection' })).toHaveCount(0)
  await expect(config(page).getByTestId('deep-config-gate-note')).toHaveText(RULES_SENTENCE)
  // Estimating stays possible: it is a read.
  await expect(config(page).getByRole('button', { name: 'Estimate' })).toBeEnabled()
  await networkQuiet(page, api)
  expect(world.state.writes).toEqual([])
  assertHermetic(api, errors)
})
