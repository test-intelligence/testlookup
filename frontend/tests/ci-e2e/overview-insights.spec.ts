/**
 * W23a FAIL-11 on Overview, hermetic (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §4.1, §6.3), the
 * Insights card — a QA engineer on ONE project:
 *
 *  - the card is below `[data-home-sections]` (outside the home tabs) and before "How this verdict is decided";
 *  - its rows come in the server's order (open first, high → low) with their server titles; a `<script>` sentinel
 *    suite name is text; the perf row links to `/trends?tab=durations` and `Latest run` to `/runs/<id>?tab=changes`;
 *  - `24 h` re-reads with `range=24h`; at most 5 rows, then `Showing 5 of 7 · Show all in the Inbox`;
 *  - Dismiss asks inline (`Dismiss for everyone in Checkout?`), POSTs, the row leaves and `Showing …` follows the
 *    server's new total; a scripted 404 reads `Insight not found.` inline;
 *  - a not-measured detector reads its sentence; a VIEWER sees no Dismiss;
 *  - All Projects (an ADMIN's view): no card and NO insights request;
 *  - no row or the card's header scrolls sideways at 1280 and 1440 (CI's wider DejaVu included: ≥ 40 px slack).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-insights-cadence.ts`
 * `insightsCadenceWorld` on `OVERVIEW_ON` (`page: 'overview'`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { expectMainFits, ownOverflow } from '../lib/inPageGeometry'
import { openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { ADMIN } from '../visual/production/fixtures-releases'
import {
  insightsCadenceWorld,
  notMeasured,
  WORLD_SUITE,
  worldDetectors,
  worldInsight,
  worldInsightRows,
  type InsightsCadenceWorldOptions,
} from '../visual/production/fixtures-insights-cadence'
import { NOT_MEASURED } from '../../src/types/insights'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const INSIGHTS = `/api/v1/projects/${PROJECT_ID}/insights`
const HOSTILE = '<script>window.__xss=1</script> sentinel'
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })
const card = (p: Page) => p.locator('[data-insights-card]')
const rows = (p: Page) => card(p).locator('[data-insight-row]')
const insightQueries = (api: Parameters<typeof requestsTo>[0]) =>
  requestsTo(api, INSIGHTS).map((line) => new URL(line.slice(line.indexOf(' ') + 1), 'http://overview.test').searchParams)

/** The world's three rows (perf high with a hostile title, flake medium, coverage resolved) and four low rows. */
function sevenRows() {
  const [perf, flake, coverage] = worldInsightRows()
  const hostile = { ...perf, title: `Suite ${HOSTILE} is 2.4× slower than its previous 20 runs` }
  const low = [1, 2, 3, 4].map((n) => worldInsight({
    kind: 'flake_rising', severity: 'low', run_path: null, title: `Flaky tests rising in suite Low sentinel ${n} (1 → 3)`,
    link: { path: '/flaky', label: 'Flaky tests' }, last_seen_at: new Date(NOW.getTime() - n * 3_600_000).toISOString(),
  }))
  return [hostile, flake, coverage, ...low]
}

/** A one-line flex row's free width: its content box minus its children and gaps (negative: they wrapped / overflow). */
async function rowSlack(row: Locator): Promise<number> {
  return row.evaluate((el: HTMLElement) => {
    const style = getComputedStyle(el)
    const inner = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    const kids = Array.from(el.children) as HTMLElement[]
    // One line = every child overlaps the first one vertically (the header centres its actions on the title block).
    const first = kids[0].getBoundingClientRect()
    const wrapped = kids.some((kid) => kid.getBoundingClientRect().top >= first.bottom)
    const gap = parseFloat(style.columnGap) || 0
    const used = kids.reduce((sum, kid) => sum + kid.getBoundingClientRect().width, 0) + gap * Math.max(0, kids.length - 1)
    return wrapped ? -1 : Math.round(inner - used)
  })
}

async function openOverview(page: Page, options: InsightsCadenceWorldOptions = {}) {
  const world = insightsCadenceWorld({ page: 'overview', role: 'QA_ENGINEER', insights: sevenRows(), ...options })
  const opened = await openRollout(page, '/overview', { handlers: world.handlers, ready, user: world.user })
  await card(page).scrollIntoViewIfNeeded()
  await expect(rows(page).first()).toBeVisible()
  await networkQuiet(page, opened.api)
  return { ...opened, world }
}

for (const [width, height] of [[1280, 900], [1440, 900]] as const) {
  test(`the card: below the home sections, server order, text-only titles, allow-listed links, no sideways scroll — ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    const { api, errors } = await openOverview(page)
    const follows = await page.evaluate(() => {
      const sections = document.querySelector('[data-home-sections]') as HTMLElement
      const insights = document.querySelector('[data-insights-card]') as HTMLElement
      const verdict = Array.from(document.querySelectorAll('button')).find((b) => /How this verdict is decided/.test(b.textContent ?? ''))
      return {
        after: Boolean(sections.compareDocumentPosition(insights) & Node.DOCUMENT_POSITION_FOLLOWING),
        inside: sections.contains(insights),
        beforeVerdict: verdict ? Boolean(insights.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING) : null,
      }
    })
    expect(follows).toEqual({ after: true, inside: false, beforeVerdict: true })
    await expect(card(page).getByRole('heading', { name: 'Insights', exact: true })).toBeVisible()
    await expect(card(page)).toContainText('Patterns across runs · checked every hour')

    await expect(rows(page)).toHaveCount(5)
    await expect(rows(page).locator('[data-insight-pill]')).toHaveText(['High', 'Medium', 'Low', 'Low', 'Low'])
    await expect(rows(page).nth(0).locator('[data-insight-title]')).toHaveText(`Suite ${HOSTILE} is 2.4× slower than its previous 20 runs`)
    await expect(rows(page).nth(1).locator('[data-insight-title]')).toHaveText(`Flaky tests rising in suite ${WORLD_SUITE} (1 → 4)`)
    await expect(card(page).locator('script')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expect(rows(page).nth(0).getByRole('link', { name: 'Trends › Execution time' })).toHaveAttribute('href', '/trends?tab=durations')
    await expect(rows(page).nth(0).getByRole('link', { name: 'Latest run' })).toHaveAttribute('href', `/runs/${RUN_ID}?tab=changes`)
    await expect(card(page).locator('[data-insights-showing]')).toHaveText('Showing 5 of 7 · Show all in the Inbox')
    expect(insightQueries(api).map((q) => [q.get('range'), q.get('include_dismissed'), q.get('limit')])).toEqual([['7d', 'false', null]])

    // Widths: the actions never push the card wider; the header keeps ≥ 40 px for CI's DejaVu.
    expect(await ownOverflow(card(page))).toBeLessThanOrEqual(0)
    for (let i = 0; i < 5; i += 1) expect(await ownOverflow(rows(page).nth(i))).toBeLessThanOrEqual(0)
    expect(await rowSlack(card(page).locator('[data-panel-header]'))).toBeGreaterThanOrEqual(40)
    await expectMainFits(page, `Overview at ${width}`)
    assertHermetic(api, errors)
  })
}

test.describe('at 1280 x 900', () => {
  test.use({ viewport: { width: 1280, height: 900 } })

  test('24 h re-reads with range=24h, the toggle pressed', async ({ page }) => {
    const { api, errors } = await openOverview(page)
    await card(page).getByRole('button', { name: '24 h', exact: true }).click()
    await expect.poll(() => insightQueries(api).map((q) => q.get('range'))).toEqual(['7d', '24h'])
    await expect(card(page).getByRole('button', { name: '24 h', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(card(page).getByRole('button', { name: '7 d', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('Dismiss asks inline, POSTs, the row leaves and Showing follows; a scripted 404 reads inline', async ({ page }) => {
    watchNativeDialogs(page)
    const { api, errors, world } = await openOverview(page)
    const first = rows(page).nth(0)
    const id = await first.getAttribute('data-insight-row')
    await first.getByRole('button', { name: 'Dismiss', exact: true }).click()
    const ask = card(page).getByRole('group', { name: 'Dismiss for everyone in Checkout?' })
    await expect(ask).toBeVisible()
    await expectNoOverlay(page)
    expect(world.writes, 'the first click sends nothing').toEqual([])
    await ask.getByRole('button', { name: 'Dismiss', exact: true }).click()
    await expect(card(page).locator(`[data-insight-row="${id}"]`)).toHaveCount(0)
    expect(world.writes.map((w) => w.line)).toEqual([`POST ${INSIGHTS}/${id}/dismiss`])
    await expect(card(page).locator('[data-insights-showing]')).toHaveText('Showing 5 of 6 · Show all in the Inbox')

    world.state.refusals.dismiss = { status: 404, code: 'insight_not_found' }
    const next = rows(page).nth(0)
    await next.getByRole('button', { name: 'Dismiss', exact: true }).click()
    await card(page).getByRole('group', { name: 'Dismiss for everyone in Checkout?' }).getByRole('button', { name: 'Dismiss', exact: true }).click()
    await expect(next.getByRole('alert')).toHaveText('Insight not found.')
    await expect(rows(page)).toHaveCount(5)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('a not-measured detector reads its sentence; a VIEWER sees no Dismiss', async ({ page }) => {
    const detectors = worldDetectors()
    detectors[2] = notMeasured('coverage_drop', 'coverage_no_snapshot')
    const { api, errors } = await openOverview(page, { role: 'VIEWER', detectors })
    await expect(card(page).locator('[data-insights-detectors]'))
      .toHaveText(`Coverage drop — not measured: ${NOT_MEASURED.coverage_no_snapshot}`)
    await expect(card(page).getByRole('button', { name: 'Dismiss' })).toHaveCount(0)
    await expect(rows(page)).toHaveCount(5)
    assertHermetic(api, errors)
  })

  test('All Projects: no card and NO insights request', async ({ page }) => {
    const world = insightsCadenceWorld({ page: 'overview', role: 'ADMIN', insights: sevenRows() })
    const { api, errors } = await openProductionPage(page, '/overview', {
      theme: 'signal', now: NOW, me: ADMIN, user: ADMIN, projectId: 'all', handlers: world.handlers, ready,
    })
    await networkQuiet(page, api)
    await expect(card(page)).toHaveCount(0)
    expect(requestsTo(api, /\/insights$/), 'All Projects asks for no insights').toEqual([])
    assertHermetic(api, errors)
  })
})
