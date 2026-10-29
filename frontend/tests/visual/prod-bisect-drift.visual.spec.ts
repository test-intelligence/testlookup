/**
 * W23b "history bisect + config and dependency drift" baselines (`docs/deferred-scope-2026-10-13/work/W23b-contract.md`
 * §6.3, §6.5: the five NEW regions), three themes each (`THEMES_WITH_PAPER`: signal, lab, paper), each region the
 * in-page container itself, at `PINNED_TALL` (1280 × 4000, nothing scrolled, as `visualRegion` requires):
 *
 *  - `failures-suspects-narrowed`: `/failures` › a row's Suspects section, offline — the two builds with their short
 *    SHAs, the runs-in-between line, the offline reason, `SuspectsPanel`'s empty state below (FAIL-12);
 *  - `failures-suspects-narrowed-online`: the same section with a fetched compare — three ranked commits, the
 *    run-level comparison, the monorepo and merge caveats;
 *  - `run-changes-config-drift`: the run page's Changes tab, the Configuration drift card, recorded — dependencies,
 *    runtime, an env var and a branch entry (FAIL-13);
 *  - `run-changes-config-drift-not-recorded`: the card for a run whose reporter sent no context — the sentence and
 *    the branch entry;
 *  - `run-changes-narrow-inline`: a new failure's row with its Narrow box open — the main-fallback wording and the
 *    same-commit line.
 *
 * No EXISTING region changes (§6.5): no existing region opens Suspects, and no shared fixture sends `config_drift`
 * or `test_fingerprint`. Hermetic world: `production/fixtures-bisect-drift.ts`. PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import {
  bisectDriftWorld,
  WORLD_BRANCH,
  WORLD_GOOD_SHA,
  worldCommit,
  worldDrift,
  worldDriftEntry,
  worldFetchedCompare,
  worldNarrowing,
  worldNewFailures,
  worldRegressionDiff,
  type BisectDriftWorldOptions,
} from './production/fixtures-bisect-drift'

test.use(PINNED_TALL)

/** The top-failing row whose Suspects opens (`fixtures.ts` `TOP_FAILING`, fingerprint `fp-top-1`). */
const ROW = 'checkout total includes tax'
const FP = 'fp-top-1'

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const suspects = (p: Page): Locator => p.getByRole('complementary', { name: `Suspects: ${ROW}` })
const driftCard = (p: Page): Locator => p.getByRole('region', { name: 'Configuration drift' })
const changesReady = (p: Page) => p.getByRole('region', { name: 'What changed since the last good run' }).getByText(/Baseline: Build #238/)

/** The fetched compare on the server's 0-100 score scale (`score_commits`: `round(100 * …, 1)`). */
function fetchedOnServerScale() {
  return worldFetchedCompare({
    commits: [
      worldCommit({ score: 82 }),
      worldCommit({ sha: 'fadesentinel02', author: 'Lee Sentinel', message: 'Rename the cart sentinel helper', score: 41,
        commit_url: 'https://github.example.com/acme/payments/commit/fadesentinel02' }),
      worldCommit({ sha: 'beadsentinel03', author: 'Kim Sentinel', message: 'Docs: checkout sentinel notes', score: 12,
        commit_url: 'https://github.example.com/acme/payments/commit/beadsentinel03' }),
    ],
  })
}

async function openSuspects(page: Page, theme: (typeof THEMES_WITH_PAPER)[number], options: BisectDriftWorldOptions) {
  const world = bisectDriftWorld(options)
  const { api, errors } = await openRollout(page, '/failures', { theme, handlers: world.handlers, ready: (p) => landmark(p, 'Failure verdict'), user: world.user })
  await networkQuiet(page, api)
  await page.locator('[data-primary]').getByRole('button', { name: `Suspects for ${ROW}` }).click()
  const section = suspects(page)
  await expect(section.getByRole('region', { name: 'Narrowed range' }).locator('[data-bisect-state]')).toBeVisible()
  await expect(section.getByTestId('suspects-panel')).toBeVisible()
  await blur(page)
  await networkQuiet(page, api)
  return { api, errors, section }
}

async function openChanges(page: Page, theme: (typeof THEMES_WITH_PAPER)[number], options: BisectDriftWorldOptions, query = 'tab=changes') {
  const world = bisectDriftWorld(options)
  const opened = await openRollout(page, `/runs/${RUN_ID}?${query}`, { theme, handlers: world.handlers, ready: changesReady, user: world.user })
  await networkQuiet(page, opened.api)
  return opened
}

for (const theme of THEMES_WITH_PAPER) {
  // ── /failures › Suspects (FAIL-12) ──
  test(`failures-suspects-narrowed: offline — ${theme}`, async ({ page }) => {
    const { api, errors, section } = await openSuspects(page, theme, { answers: { [FP]: worldNarrowing() } })
    await expect(section.locator('[data-bisect-compare-reason]')).toHaveText('Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).')
    await expect(section.locator('[data-bisect-untested] summary')).toHaveText('2 run(s) in between did not run this test.')
    await visualRegion(page, 'failures-suspects-narrowed', theme, section)
    assertHermetic(api, errors)
  })

  test(`failures-suspects-narrowed-online: three ranked commits — ${theme}`, async ({ page }) => {
    const { api, errors, section } = await openSuspects(page, theme, { answers: { [FP]: worldNarrowing({ compare: fetchedOnServerScale() }) } })
    await expect(section.locator('[data-bisect-narrowed-heading]')).toHaveText('Narrowed to 3 commits (the run-level range since the last green run has 17)')
    await expect(section.locator('[data-bisect-commit]')).toHaveCount(3)
    await expect(section.locator('[data-bisect-merge-caveat]')).toBeVisible()
    await visualRegion(page, 'failures-suspects-narrowed-online', theme, section)
    assertHermetic(api, errors)
  })

  // ── /runs/:id?tab=changes (FAIL-13, FAIL-12) ──
  test(`run-changes-config-drift: recorded — ${theme}`, async ({ page }) => {
    const { api, errors } = await openChanges(page, theme, {})
    const card = driftCard(page)
    await expect(card.locator('[data-config-drift-summary]')).toContainText('Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9')
    await expect(card.locator('[data-config-drift-section]')).toHaveCount(4)
    await visualRegion(page, 'run-changes-config-drift', theme, card)
    assertHermetic(api, errors)
  })

  test(`run-changes-config-drift-not-recorded: run_missing — ${theme}`, async ({ page }) => {
    const branch = worldDriftEntry('run', 'branch', 'changed', 'main', WORLD_BRANCH)
    const { api, errors } = await openChanges(page, theme, {
      regressionDiff: worldRegressionDiff({ config_drift: worldDrift({ context_state: 'run_missing', entries: [branch], entries_total: 1 }) }),
    })
    const card = driftCard(page)
    await expect(card.locator('[data-config-drift-context]')).toHaveText('Not recorded by this reporter: this run sent no runtime or dependency context.')
    await expect(card.locator('[data-config-drift-section="run"]')).toBeVisible()
    await visualRegion(page, 'run-changes-config-drift-not-recorded', theme, card)
    assertHermetic(api, errors)
  })

  test(`run-changes-narrow-inline: main fallback and the same commit — ${theme}`, async ({ page }) => {
    const first = worldNewFailures()[0]
    const base = worldNarrowing()
    const answer = worldNarrowing({
      anchor: 'main_fallback',
      fallback_branch: 'main',
      good: base.good ? { ...base.good, branch: 'main' } : null,
      bad: base.bad ? { ...base.bad, commit_hash: WORLD_GOOD_SHA } : null,
      untested_runs: [],
      untested_count: 0,
      compare: { ...base.compare, state: 'not_attempted', reason: 'same_commit' },
    })
    const { api, errors } = await openChanges(page, theme, { answers: { [first.test_fingerprint]: answer } }, `tab=changes&bisect=${first.test_fingerprint}`)
    const row = page.getByRole('region', { name: 'What changed since the last good run' }).locator('li[data-new-failure-narrowable]').filter({ hasText: first.test_name })
    const box = row.locator('[data-narrow-box]')
    await expect(box.locator('[data-bisect-state]')).toContainText('Never passed on feature/checkout-sentinel in the runs read; last passed on main')
    await expect(box.locator('[data-bisect-compare-reason]')).toContainText('so no code change explains this failure')
    await blur(page)
    await visualRegion(page, 'run-changes-narrow-inline', theme, row)
    assertHermetic(api, errors)
  })
}
