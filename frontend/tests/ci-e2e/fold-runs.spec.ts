/**
 * UX redesign P3 (`02-design-spec.md` §2, the above-the-fold contract), on
 * /runs, hermetic: at 1440 x 900 the page's primary content (the runs table,
 * `[data-primary]`) starts at most 300 px below the top of `#main-content`
 * (the shell's scroller: its top padding and the P1 section tabs count).
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after the template can be compared: measured on the unchanged page
 * first (P3 agent B's report, `docs/viz-work/p3-agent-B.md`: 1748 px, the
 * table at 652 px).
 *
 * Also held here, in a browser: the failure signatures open in the side panel
 * (beside the page, not under the table), and the two Disclosures below the
 * table are closed on arrival.
 *
 * UX redesign P4 (D2, the `/intelligence` list retired into the table): the
 * AI-verdict column. Each run on screen is asked for its verdict once (no
 * batch endpoint exists), the verdict opens the run's Analysis tab, a run
 * with none reads "—", the separate "Intel" link is gone, and the column did
 * not push the table wider than its card: the table's scroller has no
 * horizontal overflow at 1280 and 1440 px (before P4 it overflowed by 6 px at
 * 1280: `minWidth: 980` in a 974 px card).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the visual specs: 13 builds in 30 days, the newest two red).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID, RUNS_PAGE } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.locator('[data-status-banner]')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<{ scrollHeight: number; primaryTop: number | null; primaries: number }> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
    }
  }, MAIN)
}

test('the runs table starts within the fold budget at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /runs ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // It is the runs table, and its first build is on screen.
  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('table')).toBeVisible()
  await expect(primary.getByRole('row').nth(1)).toBeInViewport()
  // Above it: the verdict in one line, not a card.
  await expect(page.locator('[data-status-banner]')).toBeInViewport()
  await expect(page.locator('section[aria-label="Pipeline verdict"]')).toHaveCount(0)
  // Below it: the gauge and the build history, collapsed.
  await expect(page.locator('[data-disclosure][data-open="false"]')).toHaveCount(2)
  await expect(page.getByRole('meter', { name: 'Pipeline health' })).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

/** The runs table's horizontal scroller: its content width against its box. */
async function tableOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number; columns: [string, number][] }> {
  // Fonts first: a fallback face measures another table (CI's DejaVu is wider
  // than Windows' Segoe: the P4 table fitted at 974 = 974 here and was 1,040 there).
  await page.evaluate(() => document.fonts.ready)
  return page.locator('[data-primary] [data-runs-table-scroller]').evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
    // Each column's width, for the log: where the room went when this fails.
    columns: Array.from(el.querySelectorAll('thead th'), (th) => [
      (th.textContent ?? '').trim() || '(blank)',
      Math.round(th.getBoundingClientRect().width),
    ]) as [string, number][],
  }))
}

for (const width of [1280, 1440]) {
  test(`the runs table fits its card at ${width} px (no horizontal scroll)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
    await networkQuiet(page, api)
    const overflow = await tableOverflow(page)
    console.log(`OVERFLOW /runs @${width} ${JSON.stringify(overflow)}`)
    expect(overflow.scrollWidth, 'the table is no wider than its card').toBeLessThanOrEqual(overflow.clientWidth)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}

test('each run on screen carries its AI verdict, and the verdict opens the run’s Analysis tab', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  const table = page.locator('[data-primary]').getByRole('table')
  await expect(table.getByRole('columnheader', { name: 'AI verdict' })).toBeVisible()
  // The newest build: a No-Go nobody has reviewed yet.
  const newest = table.locator(`#run-row-${RUN_ID}`)
  const verdict = newest.locator('[data-ai-verdict-cell] a')
  await expect(verdict).toHaveText(/No-Go\s*draft/)
  await expect(verdict).toHaveAttribute('href', `/runs/${RUN_ID}?tab=analysis`)
  // Reviewed, conditional, and none: what the fixture says, nothing invented.
  await expect(table.locator('[data-ai-verdict="GO"]')).toHaveCount(1)
  await expect(table.locator('[data-ai-verdict="CONDITIONAL_GO"]')).toHaveCount(1)
  await expect(table.locator('[data-ai-verdict="none"]')).toHaveCount(10)
  await expect(table.locator('[data-ai-verdict="none"]').first()).toHaveText('—')
  // The separate Intel link is gone, and no row links to the old route.
  await expect(table.getByRole('link', { name: /Intel/ })).toHaveCount(0)
  await expect(table.locator('a[href$="/intelligence"]')).toHaveCount(0)
  await networkQuiet(page, api)
  // One verdict request per run on screen (13 rows), never more.
  const asked = requestsTo(api, /^\/api\/v1\/runs\/[^/]+\/intelligence$/)
  expect(asked).toHaveLength(13)
  expect(new Set(asked).size, 'no run asked twice').toBe(13)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the failure signatures open beside the page, from the header and from a row', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  const panel = page.getByRole('complementary', { name: 'Failure signatures' })
  await expect(panel).toHaveCount(0)
  await page.getByRole('button', { name: /^Failure signatures/ }).click()
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('meter', { name: 'Failed builds by signature' })).toBeVisible()
  // Non-modal: the table stays usable beside it (the panel reserves its width, never covers it).
  const [panelBox, primaryBox] = [await panel.boundingBox(), await page.locator('[data-primary]').boundingBox()]
  expect(panelBox && primaryBox && primaryBox.x + primaryBox.width <= panelBox.x + 1, 'the table ends where the panel begins').toBe(true)
  await panel.getByRole('button', { name: 'Close panel' }).click()
  await expect(panel).toHaveCount(0)
  // A row's signature chip opens it too.
  await page.locator('[data-signature-chip]').first().click()
  await expect(panel).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
