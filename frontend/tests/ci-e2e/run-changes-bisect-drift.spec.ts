/**
 * W23b FAIL-12 / FAIL-13 on `/runs/:id?tab=changes`, hermetic (`docs/deferred-scope-2026-10-13/work/W23b-contract.md`
 * §2.1 F-2, §2.2 U-1, §4, §6.3), at 1280 × 900 and 1440 × 900:
 *
 *  - the separate card `Configuration drift`: `Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9 …`
 *    and its groups, "Against build #238, the baseline above";
 *  - a run without a context: `Not recorded by this reporter: …`, never `No configuration drift`, the branch entry
 *    still listed;
 *  - `Narrow` on the second new failure → `?bisect=<fp>` (tab kept) and the box under its row; `?bisect=top` on load →
 *    the FIRST new failure's box, the URL rewritten to its fingerprint;
 *  - NO `/bisect` read without the param: opening the tab asks exactly the regression diff and the duration
 *    regressions (`fold-run.spec.ts`'s inventory);
 *  - the main card is unchanged by the drift (the drift is its own card; without `config_drift` no card renders).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-bisect-drift.ts` over `RUN_PAGE`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, normalisedMarkup, observed, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import {
  bisectDriftWorld,
  WORLD_BRANCH,
  worldDrift,
  worldDriftEntry,
  worldNarrowing,
  worldNewFailures,
  worldRegressionDiff,
  type BisectDriftWorldOptions,
} from '../visual/production/fixtures-bisect-drift'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const BISECT = /^\/api\/v1\/runs\/[^/]+\/bisect$/
const mainCard = (p: Page) => p.getByRole('region', { name: 'What changed since the last good run' })
const driftCard = (p: Page) => p.getByRole('region', { name: 'Configuration drift' })
const changesReady = (p: Page) => mainCard(p).getByText(/Baseline: Build #238/)
const failureRow = (p: Page, name: string) => mainCard(p).locator('li').filter({ hasText: name })
const params = (p: Page) => new URL(p.url()).searchParams

async function openChanges(page: Page, options: BisectDriftWorldOptions = {}, query = 'tab=changes') {
  const world = bisectDriftWorld(options)
  const opened = await openRollout(page, `/runs/${RUN_ID}?${query}`, { handlers: world.handlers, ready: changesReady, user: world.user })
  return { ...opened, world }
}

for (const [width, height] of [[1280, 900], [1440, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`the Configuration drift card: the acceptance line and its groups — ${width}`, async ({ page }) => {
      const { api, errors } = await openChanges(page)
      const card = driftCard(page)
      await expect(card.getByRole('heading', { level: 2 })).toHaveText('Configuration drift')
      await expect(card).toContainText('Against build #238, the baseline above')
      await expect(card.locator('[data-config-drift-summary]')).toContainText(
        'Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9',
      )
      const sections = await card.locator('[data-config-drift-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-config-drift-section')))
      expect(sections).toEqual(['dependencies', 'runtime', 'env', 'run'])
      await expect(card.locator('[data-config-drift-section="run"]')).toContainText(`branch main → ${WORLD_BRANCH}`)
      await expect(card).not.toContainText('No configuration drift')
      await expect(card.locator('[data-config-drift-context]')).toHaveCount(0)
      // A separate card after the main one; nothing narrowed on its own.
      expect(await mainCard(page).evaluate((el, other) => Boolean(el.compareDocumentPosition(other as Node) & Node.DOCUMENT_POSITION_FOLLOWING), await card.elementHandle())).toBe(true)
      await networkQuiet(page, api)
      expect(requestsTo(api, BISECT)).toEqual([])
      await expectMainFits(page, `drift @${width}`)
      inPageLine('/runs/:id?tab=changes drift', width, { card: await minContentSlack(card) })
      assertHermetic(api, errors)
    })

    test(`a run without a context: "Not recorded by this reporter", the branch entry still listed — ${width}`, async ({ page }) => {
      const branch = worldDriftEntry('run', 'branch', 'changed', 'main', WORLD_BRANCH)
      const { api, errors } = await openChanges(page, {
        regressionDiff: worldRegressionDiff({ config_drift: worldDrift({ context_state: 'run_missing', entries: [branch], entries_total: 1 }) }),
      })
      const card = driftCard(page)
      await expect(card.locator('[data-config-drift-context]')).toHaveText(
        'Not recorded by this reporter: this run sent no runtime or dependency context.',
      )
      await expect(card).not.toContainText('No configuration drift')
      await expect(card.locator('[data-config-drift-section="run"]')).toContainText(`branch main → ${WORLD_BRANCH}`)
      await expect(card.locator('[data-config-drift-only-run]')).toHaveText('Runtime and dependency drift: not recorded by this reporter.')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`Narrow on the second new failure: ?bisect=<fp> and the box under its row — ${width}`, async ({ page }) => {
      const failures = worldNewFailures()
      const second = failures[1]
      const { api, errors, world } = await openChanges(page, { answers: { [second.test_fingerprint]: worldNarrowing() } })
      await networkQuiet(page, api)
      expect(requestsTo(api, BISECT), 'no read before Narrow').toEqual([])
      const row = failureRow(page, second.test_name)
      const narrow = row.getByRole('button', { name: 'Narrow' })
      await expect(mainCard(page).getByRole('button', { name: 'Narrow' })).toHaveCount(failures.length)
      await narrow.click()
      await expect(narrow).toHaveAttribute('aria-expanded', 'true')
      const box = row.locator('[data-narrow-box]')
      const block = box.getByRole('region', { name: 'Narrowed range' })
      await expect(block.getByRole('heading', { level: 3 })).toHaveText(`Narrowed range for ${second.test_name}`)
      await expect(block.locator('[data-bisect-state]')).toHaveText('Last passed in build #236 (goodsen), first failed in build #239 (badsent).')
      await expect.poll(() => params(page).get('bisect')).toBe(second.test_fingerprint)
      expect(params(page).get('tab')).toBe('changes')
      await networkQuiet(page, api)
      expect(requestsTo(api, BISECT)).toEqual([`GET /api/v1/runs/${RUN_ID}/bisect?fingerprint=${second.test_fingerprint}`])
      expect(world.seen).toEqual([second.test_fingerprint])
      await expectMainFits(page, `narrow box @${width}`)
      inPageLine('/runs/:id?tab=changes narrow-box', width, { box: await minContentSlack(box) })
      assertHermetic(api, errors)
    })

    test(`?bisect=top on load opens the first new failure and rewrites the URL — ${width}`, async ({ page }) => {
      const first = worldNewFailures()[0]
      const { api, errors, world } = await openChanges(page, { answers: { [first.test_fingerprint]: worldNarrowing() } }, 'tab=changes&bisect=top')
      const box = failureRow(page, first.test_name).locator('[data-narrow-box]')
      await expect(box.getByRole('region', { name: 'Narrowed range' }).locator('[data-bisect-state]')).toBeVisible()
      await expect.poll(() => params(page).get('bisect')).toBe(first.test_fingerprint)
      expect(params(page).get('tab')).toBe('changes')
      await networkQuiet(page, api)
      expect(world.seen).toEqual([first.test_fingerprint])
      await expect(page.locator('[data-narrow-box]')).toHaveCount(1)
      assertHermetic(api, errors)
    })
  })
}

test('opening the Changes tab asks exactly the regression diff and the duration regressions — no /bisect', async ({ page }) => {
  const world = bisectDriftWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: world.handlers, ready: (p) => p.locator('[data-primary] table'), user: world.user })
  await networkQuiet(page, api)
  const before = observed(api).length
  await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Changes' }).click()
  await expect(driftCard(page)).toBeVisible()
  await networkQuiet(page, api)
  expect([...observed(api).slice(before)].sort()).toEqual([
    `GET /api/v1/runs/${RUN_ID}/duration-regressions`,
    `GET /api/v1/runs/${RUN_ID}/regression-diff`,
  ])
  expect(world.seen).toEqual([])
  assertHermetic(api, errors)
})

test('the main card is unchanged by the drift; without config_drift no drift card renders', async ({ page }) => {
  const withDrift = await openChanges(page)
  await networkQuiet(page, withDrift.api)
  const markupWith = await normalisedMarkup(mainCard(page))
  const without = await openChanges(page, { regressionDiff: worldRegressionDiff({ config_drift: undefined }) })
  await networkQuiet(page, without.api)
  await expect(driftCard(page)).toHaveCount(0)
  expect(await normalisedMarkup(mainCard(page))).toBe(markupWith)
  assertHermetic(without.api, without.errors)
})
