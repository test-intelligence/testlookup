/**
 * W12 SRCH-01 Similar failures (`docs/deferred-scope-2026-10-13/work/W12-contract.md` §2.1, §0.5, §4.1, §4.2, D-3),
 * hermetic, on `/runs/:runId/tests/:testId` (the FULL page):
 *
 *  - a FAILED or BROKEN result has a fourth tab, History · Steps · Details · **Similar failures** (`?tab=similar`); the
 *    page load asks nothing new (no `GET /search/similar` until the tab opens); opening it asks
 *    `GET /api/v1/search/similar/<testId>?limit=5` ONCE and lists up to five results of the same project, each a link
 *    to `/runs/<run>/tests/<id>` with `<suite> · <status> · rel 0.82 · <relative last run>`;
 *  - the empty / degraded states by `semantic_status` (`ok` with none, `not_configured`, `unavailable`) and an HTTP
 *    error each read their own sentence;
 *  - a PASSED or SKIPPED result has no such tab, and `?tab=similar` falls back to History with no request;
 *  - the tab strip stays ONE line with the fourth tab (the fold of `fold-test-case.spec.ts` holds), nothing scrolls
 *    sideways at 1280 and 1440, and nothing opens an overlay.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-search.ts` (`similarWorld`, over
 * `fixtures-pages.ts`' `TEST_CASE`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { expectNoHorizontalOverflow, MAIN, networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { TEST_CASE_API, TEST_CASE_ID, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import { SEARCH_TEXT, similarApi, SIMILAR_ITEMS, similarWorld, type SimilarOutcome } from '../visual/production/fixtures-search'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const SIMILAR = similarApi(TEST_CASE_ID)
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_CASE_NAME })
const tabs = (p: Page) => p.getByRole('tablist', { name: 'Test case sections' })
const tab = (p: Page, name: string) => tabs(p).getByRole('tab', { name: new RegExp(`^${name}`) })
const panel = (p: Page) => p.locator('[data-test-case-tab="similar"]')

async function open(page: Page, path: string, world: ReturnType<typeof similarWorld>) {
  watchNativeDialogs(page)
  return openRollout(page, path, { handlers: world.handlers, ready })
}

test('a failed result: four tabs, nothing asked on load; opening Similar failures asks once and lists five links of the project', async ({ page }) => {
  const { api, errors } = await open(page, TEST_CASE_PATH, similarWorld())
  await expect(tab(page, 'History')).toHaveAttribute('aria-selected', 'true')
  const names = await tabs(page).getByRole('tab').allTextContents()
  expect(names.map((n) => n.trim().replace(/\s*\d+$/, ''))).toEqual(['History', 'Steps', 'Details', 'Similar failures'])
  await networkQuiet(page, api)
  expect(requestsTo(api, SIMILAR), 'no similar read on load').toEqual([])

  await tab(page, 'Similar failures').click()
  await expect(page).toHaveURL(/[?&]tab=similar(&|$)/)
  await expect(tab(page, 'Similar failures')).toHaveAttribute('aria-selected', 'true')
  const links = panel(page).getByRole('link')
  await expect(links).toHaveCount(5)
  for (const [i, item] of SIMILAR_ITEMS.entries()) {
    const link = panel(page).getByRole('link', { name: item.test_name, exact: true })
    await expect(link, `row ${i + 1}`).toHaveCount(1)
    // A whole-value compare (never a substring): the link is exactly this result's page.
    expect(await link.getAttribute('href')).toBe(`/runs/${item.test_run_id}/tests/${item.test_case_id}`)
  }
  const first = SIMILAR_ITEMS[0]
  await expect(panel(page)).toContainText(`${first.suite_name} · ${first.status} · rel ${first.relevance_score?.toFixed(2)}`)
  // A result with no suite still reads as one line of metadata.
  await expect(panel(page)).toContainText(`${SIMILAR_ITEMS[4].status} · rel ${SIMILAR_ITEMS[4].relevance_score?.toFixed(2)}`)
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  const asked = queryOf(api, SIMILAR)
  expect(asked, 'one similar read').toHaveLength(1)
  expect(Object.fromEntries(asked[0].entries())).toEqual({ limit: '5' })
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('?tab=similar opens the tab directly with one read; History unmounts it', async ({ page }) => {
  const { api, errors } = await open(page, `${TEST_CASE_PATH}?tab=similar`, similarWorld())
  await expect(tab(page, 'Similar failures')).toHaveAttribute('aria-selected', 'true')
  await expect(panel(page).getByRole('link')).toHaveCount(5)
  await networkQuiet(page, api)
  expect(requestsTo(api, SIMILAR)).toHaveLength(1)
  await tab(page, 'History').click()
  await expect(page).not.toHaveURL(/[?&]tab=/)
  await expect(panel(page), 'mounted only while open').toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

const EMPTY: { outcome: SimilarOutcome; sentence: string }[] = [
  { outcome: 'empty', sentence: SEARCH_TEXT.similarEmpty.ok },
  { outcome: 'not_configured', sentence: SEARCH_TEXT.similarEmpty.not_configured },
  { outcome: 'unavailable', sentence: SEARCH_TEXT.similarEmpty.unavailable },
  { outcome: 'error', sentence: SEARCH_TEXT.similarEmpty.error },
]

for (const { outcome, sentence } of EMPTY) {
  test(`Similar failures, ${outcome}: "${sentence}" and no link`, async ({ page }) => {
    const { api, errors } = await open(page, `${TEST_CASE_PATH}?tab=similar`, similarWorld({ outcome }))
    await expect(panel(page).getByText(sentence, { exact: true })).toBeVisible()
    await expect(panel(page).getByRole('link')).toHaveCount(0)
    for (const other of EMPTY.filter((e) => e.sentence !== sentence)) {
      await expect(panel(page).getByText(other.sentence, { exact: true })).toHaveCount(0)
    }
    await networkQuiet(page, api)
    expect(requestsTo(api, SIMILAR).length, 'asked (SWR may retry an error)').toBeGreaterThanOrEqual(1)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

test('a broken result has the tab too', async ({ page }) => {
  const { api, errors } = await open(page, TEST_CASE_PATH, similarWorld({ status: 'BROKEN' }))
  await expect(tab(page, 'Similar failures')).toHaveCount(1)
  await networkQuiet(page, api)
  expect(requestsTo(api, SIMILAR)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

for (const status of ['PASSED', 'SKIPPED'] as const) {
  test(`a ${status.toLowerCase()} result has no Similar failures tab; ?tab=similar falls back to History with no read`, async ({ page }) => {
    const { api, errors } = await open(page, `${TEST_CASE_PATH}?tab=similar`, similarWorld({ status }))
    await expect(tab(page, 'History')).toHaveAttribute('aria-selected', 'true')
    await expect(tab(page, 'Similar failures')).toHaveCount(0)
    await expect(tabs(page).getByRole('tab')).toHaveCount(3)
    await expect(panel(page)).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, SIMILAR)).toEqual([])
    expect(requestsTo(api, `${TEST_CASE_API}/history`), 'History loads instead').toHaveLength(1)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

for (const width of [1280, 1440]) {
  test(`@${width}: the four tabs stay one line, the answer block keeps its fold, and the open tab fits`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await open(page, TEST_CASE_PATH, similarWorld())
    await networkQuiet(page, api)
    const boxes = await tabs(page).getByRole('tab').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON() as DOMRect))
    expect(boxes).toHaveLength(4)
    const tops = new Set(boxes.map((b) => Math.round(b.top)))
    expect([...tops], 'the tab strip is one line').toHaveLength(1)
    const strip = await tabs(page).boundingBox()
    const right = Math.max(...boxes.map((b) => b.right))
    const slack = Math.round((strip ? strip.x + strip.width : 0) - right)
    // The fold-test-case budget: the primary block's top within 300 px of the scroller's top.
    const primaryTop = await page.evaluate((selector) => {
      const main = document.querySelector(selector) as HTMLElement
      main.scrollTop = 0
      const primary = document.querySelector('[data-primary]') as HTMLElement
      return Math.round(primary.getBoundingClientRect().top - main.getBoundingClientRect().top)
    }, MAIN)
    console.log(`SIMILAR tabs @${width} ${JSON.stringify({ tabsRight: Math.round(right), stripWidth: Math.round(strip?.width ?? 0), slack, primaryTop })}`)
    expect(primaryTop).toBeLessThanOrEqual(300)
    await tab(page, 'Similar failures').click()
    await expect(panel(page).getByRole('link')).toHaveCount(5)
    await expectNoHorizontalOverflow(page, `similar failures @${width}`)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

test('the similar links stay on this origin and point at exactly the listed results, in relevance order', async ({ page }) => {
  const { api, errors } = await open(page, `${TEST_CASE_PATH}?tab=similar`, similarWorld())
  await expect(panel(page).getByRole('link')).toHaveCount(5)
  const hrefs = await panel(page).getByRole('link').evaluateAll((els) => els.map((el) => (el as HTMLAnchorElement).href))
  const origin = new URL(page.url()).origin
  // Whole values only (never a substring or a prefix of a URL).
  expect(hrefs.map((href) => new URL(href).origin)).toEqual(hrefs.map(() => origin))
  expect(hrefs.map((href) => new URL(href).pathname)).toEqual(SIMILAR_ITEMS.map((item) => `/runs/${RUN_ID}/tests/${item.test_case_id}`))
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
