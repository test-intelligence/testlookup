/**
 * W12 SRCH-05 search usage (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.5, §4.1, §4.2, D-9), hermetic:
 *
 *  - a QA lead or an admin sees ONE strip of three tiles under the verdict ribbon (`data-search-usage`):
 *    **Searches today** `3` (`UTC day · kept 30 days`), **Zero-result rate** `33 %` (`1 of 3 found nothing`),
 *    **p95 latency** `120 ms` (`p50 40 ms`), from ONE `GET /api/v1/search/usage?project_id=<p>&days=1`
 *    (All Projects, an admin: no `project_id`);
 *  - with no search today each tile reads `—` and `Not measured yet: no search ran today.` — never `0 %`;
 *  - a QA engineer and a viewer see no strip and send NO usage request;
 *  - the strip fits at 1280 and 1440 (each caption printed with its slack).
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { boxOf, inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, landmark, openProductionPage } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, queryOf, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  SEARCH_TEXT,
  searchWorld,
  USAGE_API,
  USAGE_MEASURED,
  USAGE_NOT_MEASURED,
  type SearchRole,
  type SearchWorldOptions,
} from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1280, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const rail = (p: Page) => p.getByTestId('searchable-data-summary')
const strip = (p: Page) => p.locator('[data-search-usage]')
const tile = (p: Page, key: 'searches' | 'zeroRate' | 'p95') => strip(p).locator(`[data-usage-tile="${key}"]`)

async function open(page: Page, options: SearchWorldOptions & { projectId?: string } = {}) {
  const world = searchWorld(options)
  return openProductionPage(page, '/search', {
    theme: 'signal',
    now: NOW,
    me: world.user,
    user: world.user,
    projectId: options.projectId ?? P,
    handlers: world.handlers,
    ready: rail,
  })
}

test('a QA lead: three tiles under the ribbon from one usage read of today', async ({ page }) => {
  const { api, errors } = await open(page, { usage: USAGE_MEASURED })
  await expect(strip(page)).toBeVisible()
  await expect(strip(page).locator('[data-usage-tile]')).toHaveCount(3)
  const expected = [
    { key: 'searches', title: 'Searches today', value: '3', caption: 'UTC day · kept 30 days' },
    { key: 'zeroRate', title: 'Zero-result rate', value: '33 %', caption: '1 of 3 found nothing' },
    { key: 'p95', title: 'p95 latency', value: '120 ms', caption: 'p50 40 ms' },
  ] as const
  for (const t of expected) {
    await expect(tile(page, t.key)).toContainText(t.title)
    await expect(tile(page, t.key)).toContainText(t.value)
    await expect(tile(page, t.key)).toContainText(t.caption)
  }
  // Under the verdict ribbon.
  const [ribbon, tiles] = [await boxOf(landmark(page, 'Index verdict')), await boxOf(strip(page))]
  expect(tiles.top, 'under the ribbon').toBeGreaterThan(ribbon.top)
  await networkQuiet(page, api)
  const asked = queryOf(api, USAGE_API)
  expect(asked).toHaveLength(1)
  expect(Object.fromEntries(asked[0].entries())).toEqual({ project_id: P, days: '1' })
  assertHermetic(api, errors)
})

test('no search today: every tile reads — and "Not measured yet", never 0 %', async ({ page }) => {
  const { api, errors } = await open(page, { usage: USAGE_NOT_MEASURED })
  await expect(strip(page)).toBeVisible()
  for (const key of ['searches', 'zeroRate', 'p95'] as const) {
    await expect(tile(page, key)).toContainText('—')
    await expect(tile(page, key)).toContainText(SEARCH_TEXT.usageNotMeasured)
  }
  await expect(strip(page)).not.toContainText('0 %')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('an admin in All Projects: the usage of every project (no project_id)', async ({ page }) => {
  const { api, errors } = await open(page, { role: 'ADMIN', usage: USAGE_MEASURED, projectId: 'all' })
  await expect(strip(page)).toBeVisible()
  await expect(tile(page, 'searches')).toContainText('3')
  await networkQuiet(page, api)
  const asked = queryOf(api, USAGE_API)
  expect(asked.map((q) => Object.fromEntries(q.entries()))).toEqual([{ days: '1' }])
  assertHermetic(api, errors)
})

for (const role of ['QA_ENGINEER', 'VIEWER'] as SearchRole[]) {
  test(`a ${role}: no strip and no usage request`, async ({ page }) => {
    const { api, errors } = await open(page, { role, usage: USAGE_MEASURED })
    await expect(rail(page)).toBeVisible()
    await networkQuiet(page, api)
    await expect(strip(page)).toHaveCount(0)
    expect(requestsTo(api, USAGE_API)).toEqual([])
    await expect(page.getByText(SEARCH_TEXT.usageRefused), 'nothing was refused either').toHaveCount(0)
    assertHermetic(api, errors)
  })
}

for (const width of [1280, 1440]) {
  test(`@${width}: the strip's three tiles fit, captions on one line`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const { api, errors } = await open(page, { usage: USAGE_MEASURED })
    await expect(strip(page)).toBeVisible()
    await networkQuiet(page, api)
    await expectNoHorizontalOverflow(page, `/search usage @${width}`)
    const numbers: Record<string, number> = { strip: (await boxOf(strip(page))).width }
    for (const key of ['searches', 'zeroRate', 'p95'] as const) {
      numbers[key] = (await boxOf(tile(page, key))).width
      numbers[`${key}Slack`] = await minContentSlack(tile(page, key))
    }
    inPageLine('/search usage-strip', width, numbers)
    for (const key of ['searches', 'zeroRate', 'p95'] as const) expect(numbers[`${key}Slack`], `${key} fits its tile`).toBeGreaterThanOrEqual(0)
    assertHermetic(api, errors)
  })
}
