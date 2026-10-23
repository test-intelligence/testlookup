/**
 * W10 FAIL-09 on `/runs/:id?tab=changes`, hermetic (`docs/deferred-scope-2026-10-13/work/W10-contract.md` §2.6, §4),
 * at 1440 x 900 and 1280:
 *
 *  - offline: one reason line in the card, `Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).`, no
 *    commits;
 *  - a project without GitHub: `Commit list unavailable: this project has no GitHub integration.` with its link,
 *    `Connect GitHub in Settings › GitHub` → `/settings/github` (whole value);
 *  - a listed range counted from a weak anchor: `Commits since build #239 (3)`, the weak-anchor line, each short sha a
 *    link to its server-built `commit_url` (whole value, a new tab, `noopener noreferrer`), and a `javascript:` URL
 *    rendered as text, never a link;
 *  - opening the tab asks exactly the regression diff and the duration regressions (`fold-run.spec.ts`'s inventory);
 *  - in the card (no dialog), `#main-content` never scrolls sideways (printed `INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-integrations-runtime.ts` over
 * `fixtures-run.ts` `RUN_PAGE`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { RANGE_BASE_BUILD, RANGE_COMMITS, runChangesWorld } from '../visual/production/fixtures-integrations-runtime'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const OFFLINE_LINE = 'Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).'
const NO_INTEGRATION_LINE = 'Commit list unavailable: this project has no GitHub integration.'
const WEAK_ANCHOR_LINE = 'Counted from the last completed run, not the last green one: some of these may predate the failure.'
const card = (page: Page) => page.getByRole('region', { name: 'What changed since the last good run' })
const changesReady = (p: Page) => card(p).getByText(/Baseline: Build #238/)

async function expectCardFits(page: Page, width: number, what: string) {
  await expectMainFits(page, `${what} @${width}`)
  inPageLine(`/runs/:id?tab=changes ${what}`, width, { card: await minContentSlack(card(page)) })
}

for (const [width, height] of [[1440, 900], [1280, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`offline: the reason line, no commits — ${width}`, async ({ page }) => {
      const world = runChangesWorld('offline')
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, { handlers: world.handlers, ready: changesReady })
      const reason = card(page).locator('[data-commit-range-reason]')
      await expect(reason).toHaveText(OFFLINE_LINE)
      await expect(reason.getByRole('link')).toHaveCount(0)
      await expect(card(page).getByText(/^Commits since/)).toHaveCount(0)
      await expect(card(page).getByRole('link'), 'no commit link, no settings link').toHaveCount(0)
      await expectNoOverlay(page)
      await expectCardFits(page, width, 'offline')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`no GitHub integration: the line and its link to Settings › GitHub — ${width}`, async ({ page }) => {
      const world = runChangesWorld('no_integration')
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, { handlers: world.handlers, ready: changesReady })
      const reason = card(page).locator('[data-commit-range-reason]')
      await expect(reason).toContainText(NO_INTEGRATION_LINE)
      await expect(reason.getByRole('link', { name: 'Connect GitHub in Settings › GitHub' })).toHaveAttribute('href', '/settings/github')
      await expect(card(page).getByText(/^Commits since/)).toHaveCount(0)
      await expectCardFits(page, width, 'no integration')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`a listed range: its own build, the weak-anchor line, each sha linking to its commit — ${width}`, async ({ page }) => {
      const world = runChangesWorld('listed', { hostileUrl: true })
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, { handlers: world.handlers, ready: changesReady })
      const c = card(page)
      // The heading names the build the range starts after (239), not the diff's baseline (238); 3 + the hostile one.
      await expect(c.getByText(`Commits since build #${RANGE_BASE_BUILD} (4)`, { exact: true })).toBeVisible()
      await expect(c.getByText(WEAK_ANCHOR_LINE, { exact: true })).toBeVisible()
      await expect(c.locator('[data-commit-range-reason]')).toHaveCount(0)
      for (const commit of RANGE_COMMITS) {
        const link = c.getByRole('link', { name: commit.sha, exact: true })
        await expect(link).toHaveAttribute('href', commit.commit_url)
        await expect(link).toHaveAttribute('target', '_blank')
        await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
        await expect(c).toContainText(commit.message)
      }
      // A commit_url that is not http(s) renders as text.
      await expect(c.getByText('bad0bad0', { exact: true })).toBeVisible()
      await expect(c.getByRole('link', { name: 'bad0bad0' })).toHaveCount(0)
      await expect(c.getByRole('link'), 'the three http(s) commits only').toHaveCount(RANGE_COMMITS.length)
      await expectNoOverlay(page)
      await expectCardFits(page, width, 'listed')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  })
}

test('opening the Changes tab asks exactly the regression diff and the duration regressions', async ({ page }) => {
  const world = runChangesWorld('listed')
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: world.handlers, ready: (p) => p.locator('[data-primary] table') })
  await networkQuiet(page, api)
  const before = observed(api).length
  await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Changes' }).click()
  await expect(card(page).getByText(`Commits since build #${RANGE_BASE_BUILD} (3)`, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect([...observed(api).slice(before)].sort()).toEqual([
    `GET /api/v1/runs/${RUN_ID}/duration-regressions`,
    `GET /api/v1/runs/${RUN_ID}/regression-diff`,
  ])
  assertHermetic(api, errors)
})
