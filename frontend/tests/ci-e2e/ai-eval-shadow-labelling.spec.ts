/**
 * W16 AGT-13 (`docs/deferred-scope-2026-10-13/work/W16-contract.md` §2.2 test 7, §4.1, §4.2), hermetic, on
 * `/settings/ai-eval?tab=tiers` (clock pinned at 2026-09-18 12:00 UTC):
 *
 *  - a QA lead on the pinned project sees `3` on the Tiers tab and `Label live pairs (3)`, ONE shadow-pairs read for
 *    the badge and the queue (the same key), the incumbent / candidate columns as text and the `Run #…` link;
 *    `Candidate correct` labels the first pair: the card leaves and both counts read 2; Run comparison (Include live
 *    labels and Record the result ON by default) answers `1 paired samples · 1 live labels` and `Not enough
 *    evidence`, and the recorded comparison joins the history;
 *  - a project that never produced a pair says `No shadow pairs: no live model tier.`;
 *  - widths (§4.1, planned for CI's DejaVu fonts with ≥ 40 px slack): the two new tables' scrollers and the queue's
 *    cards at 1280 and 1440, printed as `INPAGE` lines (scrollWidth vs clientWidth), with the system fonts and with
 *    every font forced to Verdana (the DejaVu Sans width proxy: no DejaVu on a Windows host).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-ai-eval.ts` (G-A).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectMainFits, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { AI_EVAL_PATHS, SHADOW_PAIR_IDS, aiEvalWorld } from '../visual/production/fixtures-ai-eval'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tabBar = (p: Page) => p.getByRole('tablist', { name: 'AI evaluation' })
const tiersTab = (p: Page) => tabBar(p).getByRole('tab', { name: /^Tiers/ })
const queue = (p: Page) => p.getByTestId('shadow-pair-queue')
const pairCard = (p: Page, id: string) => p.locator(`[data-shadow-pair="${id}"]`)
const runPanel = (p: Page) => p.getByTestId('tier-run-panel')

async function open(page: Page, world: ReturnType<typeof aiEvalWorld>) {
  return openRollout(page, '/settings/ai-eval?tab=tiers', {
    handlers: world.handlers, user: world.user, ready: (p) => p.getByTestId('ai-eval-tiers-tab'),
  })
}

test('a QA lead labels a live pair and runs G2 with the live labels', async ({ page }) => {
  const world = aiEvalWorld({ role: 'qa_lead' })
  const { api, errors } = await open(page, world)
  await expect(tiersTab(page)).toHaveAttribute('aria-selected', 'true')
  await expect(tiersTab(page).locator('[data-tab-count]')).toHaveText('3')
  await expect(queue(page).getByRole('heading', { name: 'Label live pairs (3)' })).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, AI_EVAL_PATHS.shadowPairs), 'the badge and the queue share ONE read').toHaveLength(1)

  const first = pairCard(page, SHADOW_PAIR_IDS[0])
  await expect(first.getByText('Incumbent · llm')).toBeVisible()
  await expect(first.getByText('Candidate · slm')).toBeVisible()
  await expect(first.locator('pre[data-pair-output]').first()).toContainText('The order total ignores the coupon discount.')
  await expect(first.locator('pre[data-pair-output]').nth(1)).toContainText('The fixture prices are stale.')
  const link = first.getByRole('link', { name: 'Run #128' })
  await expect(link).toHaveAttribute('href', '/runs/aa1d0001-0001-4abc-8def-a1dbea7e0001?tab=tests')
  await expect(pairCard(page, SHADOW_PAIR_IDS[2])).toContainText('Source no longer available.')

  await first.getByRole('button', { name: 'Candidate correct' }).click()
  await expect(first).toHaveCount(0)
  await expect(queue(page).getByRole('heading', { name: 'Label live pairs (2)' })).toBeVisible()
  await expect(tiersTab(page).locator('[data-tab-count]')).toHaveText('2')

  const panel = runPanel(page)
  await expect(panel.getByRole('checkbox', { name: 'Include live labels' })).toBeChecked()
  await expect(panel.getByRole('checkbox', { name: 'Record the result' })).toBeChecked()
  await panel.getByRole('button', { name: 'Run comparison' }).click()
  const result = panel.locator('[data-tier-result]')
  await expect(result.locator('[data-tier-samples]')).toHaveText('1 paired samples · 1 live labels')
  await expect(result.locator('[data-verdict]')).toHaveText('Not enough evidence')
  await expect(result).toContainText('Not enough evidence yet: G2 needs 100 paired samples for this agent.')
  await expect(page.locator('[data-tier-run]')).toHaveCount(2)

  await networkQuiet(page, api)
  expect(world.writes.map((w) => w.line)).toEqual([
    `POST ${AI_EVAL_PATHS.shadowPairs}/${SHADOW_PAIR_IDS[0]}/label`,
    `POST ${AI_EVAL_PATHS.tierComparison}`,
  ])
  expect(world.writes[0].body).toEqual({ verdict: 'candidate' })
  expect(world.writes[1].body).toEqual({
    project_id: PROJECT_ID, agent_id: 'agent.root_cause_analysis.v1', capability: 'root_cause_analysis',
    incumbent_tier: 'llm', candidate_tier: 'slm', delta: 0.05, pairs: [], include_live_labels: true, persist: true,
  })
  expect(requestsTo(api, AI_EVAL_PATHS.candidateOptions), 'a QA lead makes no ADMIN read').toEqual([])
  assertHermetic(api, errors)
})

test('a project that never produced a pair says there is no live model tier', async ({ page }) => {
  const world = aiEvalWorld({ role: 'qa_lead', pairs: [] })
  const { api, errors } = await open(page, world)
  await expect(queue(page)).toContainText('No shadow pairs: no live model tier.')
  await expect(queue(page).locator('[data-shadow-pair]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

/** Every font forced to Verdana (as wide as CI's DejaVu Sans, which this host lacks). */
async function verdana(page: Page) {
  await page.addStyleTag({ content: '*, *::before, *::after { font-family: Verdana, sans-serif !important; }' })
  await page.evaluate(() => document.fonts.ready)
}

/** A scroller's own overflow and its content slack; asserted to fit. */
async function measureScroller(scroller: Locator, what: string) {
  await expect(scroller, `${what} is on the page`).toBeVisible()
  const box = await scroller.evaluate((el: HTMLElement) => ({ client: el.clientWidth, scroll: el.scrollWidth }))
  expect(await ownOverflow(scroller), `${what} scrolls sideways`).toBeLessThanOrEqual(0)
  return { ...box, slack: await minContentSlack(scroller) }
}

for (const width of [1280, 1440]) {
  test.describe(`widths at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`the Tiers tables and the queue cards fit the settings column, system fonts and Verdana — ${width}`, async ({ page }) => {
      test.setTimeout(60_000)
      const world = aiEvalWorld({ role: 'qa_lead' })
      const { api, errors } = await open(page, world)
      await runPanel(page).getByRole('button', { name: 'Run comparison' }).click()
      await expect(runPanel(page).locator('[data-tier-result]')).toBeVisible()

      const measureAll = async (fonts: string) => {
        await expectMainFits(page, `${fonts} tiers @${width}`)
        const numbers: Record<string, unknown> = {
          'tier-metrics': await measureScroller(page.locator('[data-table-scroller="tier-metrics"]'), `${fonts} tier-metrics @${width}`),
          'tier-history': await measureScroller(page.locator('[data-table-scroller="tier-history"]'), `${fonts} tier-history @${width}`),
        }
        const cards = queue(page).locator('[data-shadow-pair]')
        await expect(cards).toHaveCount(3)
        for (let i = 0; i < 3; i += 1) {
          const card = cards.nth(i)
          expect(await ownOverflow(card), `${fonts} queue card ${i} overflows @${width}`).toBeLessThanOrEqual(0)
          const pres = card.locator('pre[data-pair-output]')
          for (let j = 0; j < 2; j += 1) {
            expect(await ownOverflow(pres.nth(j)), `${fonts} card ${i} output ${j} scrolls sideways @${width}`).toBeLessThanOrEqual(0)
          }
        }
        numbers['queue-card'] = await cards.first().evaluate((el: HTMLElement) => {
          const pres = Array.from(el.querySelectorAll<HTMLElement>('pre[data-pair-output]'))
          return { client: el.clientWidth, scroll: el.scrollWidth, pre: pres.map((p) => Math.round(p.getBoundingClientRect().width)) }
        })
        inPageLine(`/settings/ai-eval?tab=tiers ${fonts}`, width, numbers)
      }

      await measureAll('system')
      await verdana(page)
      await measureAll('verdana')
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.line)).toEqual([`POST ${AI_EVAL_PATHS.tierComparison}`])
      assertHermetic(api, errors)
    })
  })
}
