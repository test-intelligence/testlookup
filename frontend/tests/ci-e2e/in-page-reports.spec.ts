/**
 * W27b PAN-05b + PAN-07 IN-PAGE SHAPE, analytics and reports (`docs/deferred-scope-2026-10-13/work/W27b-contract.md`
 * §2.2, §1.3, §1.4, §4.1), hermetic, on the CONVERTED pages at 1280 AND 1440. It FAILS on the modal tree by design.
 * For each container: no overlay (`expectNoOverlay`), its DOM position as §1.4 places it, `#main-content` never
 * scrolling sideways, no clipped table, and one `INPAGE <route> <container> @<w> {…}` line (< 40 px of slack at 1280
 * is a finding for G-B).
 *
 *  - `/trends`: the note form right after the card that opened it (the cadence card, the run-gap callout, the hero
 *    chart for Edit); one form at a time, moving there after the discard question when text was typed; the email form
 *    right under the page header, its reads only once it opens;
 *  - `/activity`: an event's detail as the list item right after its row, `?event=` pushed (Back closes it), another
 *    row replaces it, a reload reopens it, an event the feed has not loaded opens right above the list, a malformed id
 *    shows `That is not a valid event link.` with NO request, Escape (focus inside) closes it and focus returns;
 *  - `/value-metrics`: the methodology right after the hero;
 *  - the catalogue: a heatmap cell's rows (`/trends?tab=heatmap`) inside the heatmap's section after its frame, as
 *    wide as the section, `rows=` pushed on open (Back closes it); `/failures` Groups: `Failure group #1` after the
 *    frame, its View rows opening the rows section; no `[data-side-panel]` anywhere;
 *  - Customise: `/explore` under the header, above the panels; Trends › By suite › Compare inside its section after
 *    the chart frame;
 *  - `/coverage` Fix tagging right after the Untagged executions callout, in the same column;
 *  - a report page's Views: a view's Delete strip right under that view's row INSIDE the popover; Escape in the strip
 *    closes the strip only.
 *
 * The flows themselves are the `characterize-trends-activity / -catalogue` specs and the rollout / report specs.
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { boxOf, clippedTables, expectMainFits, followedBy, inPageLine, minContentSlack, ownOverflow, precedes } from '../lib/inPageGeometry'
import { cardByHeading, landmark, openProductionPage, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, expectDrawn, networkQuiet, openRollout, requestsTo, rowsPanels, section, sectionFrame } from '../lib/rollout'
import {
  CHART_DATA_PATH,
  COVERAGE_ON,
  daysAgo,
  FAILURES_ON,
  NOW,
  PROJECT_ID,
  TRENDS_ON,
  USER,
  VALUE_METRICS,
} from '../visual/production/fixtures'
import {
  ACTIVITY_EVENTS,
  ACTIVITY_OLD_EVENT,
  activityDeepLinkWorld,
  METHODOLOGY_HANDLERS,
  MY_VIEW,
  savedViewsDeleteWorld,
} from '../visual/production/fixtures-in-page-b'
import {
  compareCountChartData,
  EMAIL_AVAILABILITY_PATH,
  EXPLORE,
  fixTaggingWorld,
  note,
  notesWorld,
  QA_ENGINEER,
  trendsReportWorld,
} from '../visual/production/fixtures-trends-coverage'

const P = PROJECT_ID
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const header = (page: Page) => page.locator('[data-page-header]')
const pathAndSearch = (page: Page) => {
  const url = new URL(page.url())
  return `${url.pathname}${url.search}`
}
const historyLength = (page: Page) => page.evaluate(() => window.history.length)

/** A container in the page's flow: no overlay, main fits, its slack printed. */
async function expectSection(page: Page, what: string, width: number, container: Locator, extra: Record<string, unknown> = {}) {
  await expectNoOverlay(page)
  await expect(page.locator('[data-side-panel]'), `${what}: no side panel`).toHaveCount(0)
  await expectMainFits(page, `${what} @${width}`)
  expect(await clippedTables(page.locator('#main-content')), `${what} @${width}: a clipped table`).toEqual([])
  expect(await ownOverflow(container), `${what} @${width}: scrolls sideways`).toBeLessThanOrEqual(1)
  const box = await boxOf(container)
  inPageLine(what, width, { width: box.width, slack: await minContentSlack(container), ...extra })
  return box
}

/** `/analytics/coverage` with one untagged group, so the callout (and its Fix tagging) shows (`coverage-fix-tagging.spec.ts`). */
function withUntaggedGroup(): ApiHandlers {
  const fixture = COVERAGE_ON.find(([m]) => m === '/api/v1/analytics/coverage')?.[1]
  if (!fixture) throw new Error('COVERAGE_ON answers /analytics/coverage')
  return [
    [
      '/api/v1/analytics/coverage',
      (request: ApiRequest) => {
        const body = fixture(request) as { summary: Record<string, unknown>; suites: Record<string, unknown>[] }
        const untagged = { suite_name: 'Unknown suite', unique_tests: 5, passed: 30, failed: 9, skipped: 4, pass_rate: 76.92, last_run_at: null, last_run_id: null }
        return { ...body, suites: [...body.suites, untagged] }
      },
    ],
  ]
}

const EDITABLE = note('99999999-9999-4999-8999-0000000000f1', daysAgo(11), daysAgo(5), 'CI runners paused for the datacentre move')

for (const width of [1280, 1440]) {
  test.describe(`${width} x 1400`, () => {
    test.use({ viewport: { width, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`/trends @${width}: the note form after the card that opened it (cadence, run gap, hero), one at a time`, async ({ page }) => {
      watchNativeDialogs(page)
      const notes = notesWorld([EDITABLE])
      const { api, errors } = await openRollout(page, '/trends', { handlers: [...notes.handlers, ...TRENDS_ON], ready: trendsReady, user: QA_ENGINEER })
      const cadence = cardByHeading(page, /^Run cadence — last/)
      const gap = page.getByRole('region', { name: 'Run gap in this window' })
      const hero = page.locator('[data-catalogue-section="trends-pass-rate"]')
      await expect(page.locator('[data-pin-note-open="cadence"]')).toBeVisible()
      await networkQuiet(page, api)
      await expectNoOverlay(page)

      await page.locator('[data-pin-note-open="cadence"]').click()
      let form = opened(page, 'Pin a note')
      await expect(form).toBeVisible()
      expect(await followedBy(cadence, form), 'the note form right after the cadence card').toBe(true)
      await expectSection(page, '/trends pin-note (cadence)', width, form)

      // Typed text, then the gap's Pin: the discard question, then the form moves to the gap callout.
      await form.locator('[data-pin-note="text"]').fill('typed on the cadence card')
      await page.locator('[data-pin-note-open="gap"]').click()
      const ask = page.locator('[data-inline-confirm]').filter({ hasText: 'Discard your changes to "Pin a note"?' })
      await expect(ask).toBeVisible()
      await ask.getByRole('button', { name: 'Discard changes' }).click()
      form = opened(page, 'Pin a note')
      await expect(form).toHaveCount(1)
      expect(await followedBy(gap, form), 'now right after the run-gap callout').toBe(true)
      await expect(form.locator('[data-pin-note="from"]'), 'with the gap\'s days').toHaveValue(daysAgo(11))
      await expectSection(page, '/trends pin-note (gap)', width, form)
      await form.getByRole('button', { name: 'Close note dialog' }).click()
      await expect(form).toHaveCount(0)

      // A note's Edit: right after the hero chart.
      await hero.locator('[data-timeline-notes-chip]').click()
      await page.locator(`[data-timeline-note-edit="${EDITABLE.id}"]`).click()
      const edit = opened(page, 'Edit note')
      await expect(edit).toBeVisible()
      expect(await followedBy(hero, edit), 'the edit form right after the hero chart').toBe(true)
      await expectSection(page, '/trends edit-note', width, edit)
      await edit.getByRole('button', { name: 'Cancel' }).click()
      await networkQuiet(page, api)
      expect(notes.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/trends @${width}: Email report… opens under the page header and reads its availability only then`, async ({ page }) => {
      watchNativeDialogs(page)
      const report = trendsReportWorld()
      const { api, errors } = await openRollout(page, '/trends', { handlers: [...report.handlers, ...TRENDS_ON], ready: trendsReady })
      await networkQuiet(page, api)
      expect(requestsTo(api, EMAIL_AVAILABILITY_PATH), 'nothing about email before it opens').toEqual([])
      await header(page).getByRole('button', { name: 'More actions' }).click()
      await page.getByRole('menuitem', { name: 'Email report…' }).click()
      const email = opened(page, /Email report/)
      await expect(email).toBeVisible()
      expect(await followedBy(header(page), email), 'right under the page header').toBe(true)
      await networkQuiet(page, api)
      expect(requestsTo(api, EMAIL_AVAILABILITY_PATH), 'read once it opens').toHaveLength(1)
      await expectSection(page, '/trends email', width, email)
      await email.getByRole('button', { name: 'Close email dialog' }).click()
      await expect(email).toHaveCount(0)
      assertHermetic(api, errors)
    })

    test(`/activity @${width}: the detail under its row, ?event= pushed (Back closes), replaced on a switch, reopened on a reload; an unloaded event above the list; a malformed id sends nothing`, async ({ page }) => {
      watchNativeDialogs(page)
      const { handlers } = activityDeepLinkWorld()
      const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Activity' })
      const { api, errors } = await openRollout(page, '/activity?category=configuration', { handlers, ready })
      const [FLAG, RETENTION] = ACTIVITY_EVENTS
      const feed = page.getByRole('feed')
      const rowOf = (summary: string) => feed.locator('li:not([data-in-page-row])').filter({ has: page.getByRole('button', { name: summary, exact: true }) })
      await expect(rowOf(FLAG.summary)).toBeVisible()
      await networkQuiet(page, api)

      const before = await historyLength(page)
      await rowOf(FLAG.summary).getByRole('button', { name: FLAG.summary, exact: true }).click()
      let detail = opened(page, FLAG.summary)
      await expect(detail).toBeVisible()
      await expect.poll(() => pathAndSearch(page)).toBe(`/activity?category=configuration&event=${FLAG.id}`)
      expect(await historyLength(page), 'opening pushes an entry').toBe(before + 1)
      const next = rowOf(FLAG.summary).locator('xpath=following-sibling::li[1]')
      await expect(next, 'the detail is the list item after its row').toHaveAttribute('data-in-page-row', '')
      await expect(next.locator(detail)).toHaveCount(1)
      await expect(detail.getByRole('button', { name: 'Close activity details' }), 'focus on its X').toBeFocused()
      await expectSection(page, '/activity detail', width, detail, { feed: (await boxOf(feed)).width })

      // Another row replaces it (no new entry).
      await rowOf(RETENTION.summary).getByRole('button', { name: RETENTION.summary, exact: true }).click()
      detail = opened(page, RETENTION.summary)
      await expect(detail).toBeVisible()
      await expect(opened(page, FLAG.summary)).toHaveCount(0)
      await expect.poll(() => pathAndSearch(page)).toBe(`/activity?category=configuration&event=${RETENTION.id}`)
      expect(await historyLength(page), 'a switch replaces').toBe(before + 1)
      await expect(rowOf(RETENTION.summary).locator('xpath=following-sibling::li[1]').locator(detail)).toHaveCount(1)

      // Back closes it; the category survives.
      await page.goBack()
      await expect(opened(page, RETENTION.summary)).toHaveCount(0)
      await expect.poll(() => pathAndSearch(page)).toBe('/activity?category=configuration')

      // A reload with ?event= reopens it under its row, once.
      await page.goto(`/activity?category=configuration&event=${RETENTION.id}`)
      detail = opened(page, RETENTION.summary)
      await expect(detail).toHaveCount(1)
      await expect(rowOf(RETENTION.summary).locator('xpath=following-sibling::li[1]').locator(detail)).toHaveCount(1)
      // Escape with focus inside closes it; focus returns to the row's button.
      await detail.getByRole('button', { name: 'Close activity details' }).focus()
      await page.keyboard.press('Escape')
      await expect(detail).toHaveCount(0)

      // An event the feed has not loaded (the rail's link to an older one): right above the list.
      await page.goto(`/activity?event=${ACTIVITY_OLD_EVENT.id}`)
      detail = opened(page, ACTIVITY_OLD_EVENT.summary)
      await expect(detail).toBeVisible()
      expect(await precedes(detail, feed), 'above the feed list').toBe(true)
      await expectSection(page, '/activity detail (deep link)', width, detail)

      // A malformed id: the line, and no request carries it.
      const from = api.seen.length + api.unhandled.length
      await page.goto('/activity?event=../../x')
      await expect(page.getByText('That is not a valid event link.')).toBeVisible()
      await networkQuiet(page, api)
      const lines = [...api.seen, ...api.unhandled].slice(from)
      // The page's own reads come again (a fresh load: the feed, `/activity/event-types`); no event read.
      expect(lines.filter((l) => /\/projects\/[^/]+\/activity\/|\/api\/v1\/projects\/x|\.\.%2F|\.\.\//.test(l)), 'no request for the malformed id').toEqual([])
      expect(requestsTo(api, `/api/v1/projects/${P}/activity/${ACTIVITY_OLD_EVENT.id}`)).toHaveLength(1)
      assertHermetic(api, errors)
    })

    test(`/value-metrics @${width}: the methodology right after the hero`, async ({ page }) => {
      watchNativeDialogs(page)
      const { api, errors } = await openProductionPage(page, '/value-metrics', {
        theme: 'signal',
        now: NOW,
        me: USER,
        user: USER,
        projectId: P,
        handlers: [...METHODOLOGY_HANDLERS, ...VALUE_METRICS],
        ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
      })
      await networkQuiet(page, api)
      await page.getByRole('button', { name: 'How is this calculated?' }).click()
      const methodology = opened(page, 'How hours saved is calculated')
      await expect(methodology).toBeVisible()
      expect(await followedBy(page.locator('[data-value-hero]').first(), methodology), 'right after the hero').toBe(true)
      await expectSection(page, '/value-metrics methodology', width, methodology)
      await methodology.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(methodology).toHaveCount(0)
      assertHermetic(api, errors)
    })

    test(`the catalogue @${width}: a heatmap's rows inside its section after the frame (rows= pushed, Back closes)`, async ({ page }) => {
      watchNativeDialogs(page)
      const HEAT = { id: 'trends-heatmap', title: 'Suite pass rate by day' }
      const { api, errors } = await openRollout(page, '/trends?tab=heatmap', { handlers: TRENDS_ON, ready: trendsReady })
      const frame = sectionFrame(page, HEAT.id, HEAT.title)
      await expectDrawn(frame, HEAT.id)
      const before = await historyLength(page)
      const grid = section(page, HEAT.id).locator('[data-chart-keyboard="heatmap"]')
      await grid.focus()
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('Enter')
      const rows = rowsPanels(page)
      await expect(rows).toHaveCount(1)
      await expect(section(page, HEAT.id).locator(rows), 'inside the heatmap section').toHaveCount(1)
      expect(await precedes(frame, rows), 'after the chart frame').toBe(true)
      await expect(page).toHaveURL(/[?&]rows=/)
      expect(await historyLength(page), 'rows= is pushed').toBe(before + 1)
      const [sectionBox, rowsBox] = [await boxOf(section(page, HEAT.id).first()), await boxOf(rows)]
      expect(rowsBox.width, 'as wide as its section').toBeGreaterThanOrEqual(sectionBox.width - 2)
      await expect(rows.locator('[data-rows-state]')).toHaveAttribute('data-rows-state', /^(ready|empty)$/, { timeout: 15_000 })
      await expectSection(page, '/trends heatmap rows', width, rows, { section: sectionBox.width })
      await page.goBack()
      await expect(rows).toHaveCount(0)
      await expect(page).not.toHaveURL(/[?&]rows=/)
      assertHermetic(api, errors)
    })

    test(`the catalogue @${width}: /failures Groups opens a group after its frame; View rows opens the rows section`, async ({ page }) => {
      watchNativeDialogs(page)
      const GROUPS = { id: 'failures-groups', title: 'Failures grouped by error message' }
      const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict') })
      const groupsFrame = sectionFrame(page, GROUPS.id, GROUPS.title)
      await expectDrawn(groupsFrame, GROUPS.id)
      await section(page, GROUPS.id).locator('[data-group-plot]').focus()
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('Enter')
      const group = page.getByRole('complementary', { name: 'Failure group #1' })
      await expect(group).toBeVisible()
      await expect(section(page, GROUPS.id).locator(group), 'inside the groups section').toHaveCount(1)
      expect(await precedes(groupsFrame, group), 'after the groups frame').toBe(true)
      await expectSection(page, '/failures group', width, group)
      await group.locator('[data-group-view-rows]').click()
      await expect(group).toHaveCount(0)
      await expect(rowsPanels(page)).toHaveCount(1)
      await expect(section(page, GROUPS.id).locator(rowsPanels(page))).toHaveCount(1)
      await expectNoOverlay(page)
      expect(api.unhandled).toEqual([])
      assertHermetic(api, errors)
    })

    test(`Customise @${width}: /explore opens under the header, above the panels`, async ({ page }) => {
      watchNativeDialogs(page)
      const { api, errors } = await openRollout(page, '/explore', { handlers: EXPLORE, ready: (p) => p.getByRole('heading', { level: 1, name: 'Explorer' }) })
      const firstPanel = page.locator('[data-explore-panel]').first()
      await expect(firstPanel).toBeVisible()
      await networkQuiet(page, api)
      await page.getByRole('button', { name: 'Customise', exact: true }).click()
      const customise = page.locator('[data-chart-customise-panel]')
      await expect(customise).toBeVisible()
      expect(await precedes(header(page), customise), 'under the header').toBe(true)
      expect(await precedes(customise, firstPanel), 'above the panels').toBe(true)
      await expectSection(page, '/explore customise', width, customise)
      await customise.getByRole('button', { name: 'Close customise panel' }).click()
      await expect(customise).toHaveCount(0)
      assertHermetic(api, errors)
    })

    test(`Customise @${width}: Trends › By suite › Compare opens inside its section, after the chart frame`, async ({ page }) => {
      watchNativeDialogs(page)
      const shared = TRENDS_ON.find(([m]) => m === CHART_DATA_PATH)?.[1]
      if (!shared) throw new Error('TRENDS_ON answers chart-data')
      const { api, errors } = await openRollout(page, '/trends?tab=by-suite', {
        handlers: [[CHART_DATA_PATH, compareCountChartData((request: ApiRequest) => shared(request))], ...TRENDS_ON],
        ready: trendsReady,
      })
      const compare = page.locator('[data-catalogue-section="trends-compare"]')
      const frame = compare.locator('[data-chart-frame]')
      await expect(frame).toHaveAttribute('data-chart-state', /^(ready|truncated)$/, { timeout: 20_000 })
      await compare.locator('[data-chart-customise]').click()
      const customise = page.locator('[data-chart-customise-panel]')
      await expect(compare.locator(customise), 'inside the Compare section').toHaveCount(1)
      expect(await precedes(frame, customise), 'after its chart frame').toBe(true)
      await expectSection(page, '/trends compare customise', width, customise, { section: (await boxOf(compare)).width })
      await customise.getByRole('button', { name: 'Close customise panel' }).click()
      await expect(customise).toHaveCount(0)
      assertHermetic(api, errors)
    })

    test(`/coverage Fix tagging @${width}: right after the Untagged executions callout, in the same column`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = fixTaggingWorld()
      const { api, errors } = await openRollout(page, '/coverage', { handlers: [...withUntaggedGroup(), ...world.handlers, ...COVERAGE_ON], ready: (p) => landmark(p, 'Run cadence') })
      const callout = page.getByRole('region', { name: 'Untagged executions' })
      await expect(callout).toBeVisible()
      await networkQuiet(page, api)
      await page.locator('[data-fix-tagging-open]').click()
      const panel = page.getByRole('complementary', { name: 'Fix tagging' })
      await expect(panel).toBeVisible()
      await expect(panel.locator('[data-untagged-test]').first()).toBeVisible()
      expect(await followedBy(callout, panel), 'right after the callout').toBe(true)
      const [calloutBox, panelBox] = [await boxOf(callout), await boxOf(panel)]
      expect(Math.abs(panelBox.left - calloutBox.left), 'the same column').toBeLessThanOrEqual(1)
      await expectSection(page, '/coverage fix-tagging', width, panel, { column: calloutBox.width })
      await panel.getByRole('button', { name: 'Close the fix tagging panel' }).click()
      await expect(panel).toHaveCount(0)
      assertHermetic(api, errors)
    })

    test(`Views @${width}: a view's Delete strip right under its row inside the popover; Escape closes only the strip`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = savedViewsDeleteWorld()
      const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON], ready: trendsReady })
      await networkQuiet(page, api)
      await page.locator('[data-saved-views-trigger]').first().click()
      const popover = page.locator('[data-saved-views-panel]')
      const row = popover.locator(`li[data-saved-view="${MY_VIEW.id}"]`)
      await row.locator('[data-saved-view-delete]').click()
      const strip = popover.locator('[data-inline-confirm]')
      await expect(strip).toContainText(`Delete the view "${MY_VIEW.name}"?`)
      const next = row.locator('xpath=following-sibling::li[1]')
      await expect(next, 'the strip is the list item after the view').toHaveAttribute('data-in-page-row', '')
      // A plain selector: `next.locator(strip)` would look for the whole popover-rooted chain inside the item.
      await expect(next.locator('[data-inline-confirm]')).toHaveCount(1)
      const box = await boxOf(strip)
      expect(box.right, 'inside the popover').toBeLessThanOrEqual((await boxOf(popover)).right + 1)
      inPageLine('/trends views-delete', width, { width: box.width, slack: await minContentSlack(strip) })
      await page.keyboard.press('Escape')
      await expect(strip, 'Escape cancels the strip').toHaveCount(0)
      await expect(popover, 'the popover stays open').toBeVisible()
      await page.keyboard.press('Escape')
      await expect(popover).toHaveCount(0)
      await expectNoOverlay(page)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })
  })
}
