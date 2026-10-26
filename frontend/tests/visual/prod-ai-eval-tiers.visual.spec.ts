/**
 * W16 G-D baselines (`docs/deferred-scope-2026-10-13/work/W16-contract.md` §6.5, three NEW regions), signal, lab and
 * paper (`THEMES_WITH_PAPER`), on `/settings/ai-eval?tab=tiers` as a QA lead with the fixture project pinned:
 *
 *  - `ai-eval-tier-result`: a G2 run over 3 live labels — `Not enough evidence`, `3 paired samples · 3 live labels`,
 *    the floor line, the metrics table with its Wilson bounds — and the recorded comparisons below it;
 *  - `ai-eval-shadow-queue`: the labelling queue with two pending root-cause pairs, the second without a source;
 *  - `ai-eval-shadow-empty`: the honest empty state of a project that never produced a pair.
 *
 * No existing region changes (no region shot `/settings/ai-eval` before W16). Each region is the container itself,
 * with focus blurred. Hermetic world: `production/fixtures-ai-eval.ts` (G-A). PNGs are rendered by CI only (Linux);
 * nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import { isoAgo } from './production/fixtures'
import { aiEvalWorld, SHADOW_PAIR_IDS, shadowPairRow } from './production/fixtures-ai-eval'

test.use(PINNED)

const tiersReady = (p: Page) => p.getByTestId('ai-eval-tiers-tab')
const queue = (p: Page) => p.getByTestId('shadow-pair-queue')
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

/** Three labelled live pairs of the root-cause agent (llm → slm): the evidence the run reads. */
function labelledPairs() {
  const verdicts = ['incumbent', 'both', 'neither'] as const
  return verdicts.map((verdict, i) => shadowPairRow({
    id: `a5a1ab00-000${i + 1}-4abc-8def-5ad0ba1eab0${i + 1}`,
    label_status: i === 2 ? 'golden_match' : 'human_labelled',
    label: { verdict, source: i === 2 ? 'feedback' : 'human' },
  }))
}

for (const theme of THEMES_WITH_PAPER) {
  test(`a G2 run over three live labels and the recorded comparisons — ${theme}`, async ({ page }) => {
    const world = aiEvalWorld({ role: 'qa_lead', pairs: labelledPairs() })
    const { api, errors } = await openRollout(page, '/settings/ai-eval?tab=tiers', { theme, handlers: world.handlers, user: world.user, ready: tiersReady })
    const comparison = page.locator('[data-tier-comparison]')
    await comparison.getByRole('button', { name: 'Run comparison' }).click()
    await expect(comparison.locator('[data-tier-samples]')).toHaveText('3 paired samples · 3 live labels')
    await expect(comparison.locator('[data-tier-result] [data-verdict]')).toHaveText('Not enough evidence')
    await expect(comparison.locator('[data-tier-run]')).toHaveCount(2)
    await blur(page)
    await visualRegion(page, 'ai-eval-tier-result', theme, comparison)
    expect(world.writes.map((w) => w.line)).toEqual(['POST /api/v1/ai-eval/tier-comparison'])
    assertHermetic(api, errors)
  })

  test(`the labelling queue: two pending pairs, one without a source — ${theme}`, async ({ page }) => {
    const pairs = [
      shadowPairRow({ id: SHADOW_PAIR_IDS[0] }),
      shadowPairRow({ id: SHADOW_PAIR_IDS[2], sample_key: 'root_cause:ca5ec0de-0003-4abc-8def-7e57ca5e0003', source: null,
        created_at: isoAgo(12) }),
    ]
    const world = aiEvalWorld({ role: 'qa_lead', pairs })
    const { api, errors } = await openRollout(page, '/settings/ai-eval?tab=tiers', { theme, handlers: world.handlers, user: world.user, ready: tiersReady })
    await expect(queue(page).locator('[data-shadow-pair]')).toHaveCount(2)
    await expect(queue(page).getByRole('heading', { name: 'Label live pairs (2)' })).toBeVisible()
    await expect(queue(page)).toContainText('Source no longer available.')
    await blur(page)
    await visualRegion(page, 'ai-eval-shadow-queue', theme, queue(page))
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`no live model tier: the honest empty queue — ${theme}`, async ({ page }) => {
    const world = aiEvalWorld({ role: 'qa_lead', pairs: [] })
    const { api, errors } = await openRollout(page, '/settings/ai-eval?tab=tiers', { theme, handlers: world.handlers, user: world.user, ready: tiersReady })
    await expect(queue(page)).toContainText('No shadow pairs: no live model tier.')
    await blur(page)
    await visualRegion(page, 'ai-eval-shadow-empty', theme, queue(page))
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })
}
