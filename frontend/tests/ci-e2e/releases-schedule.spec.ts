/**
 * W11 REL-06 on `/releases`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.3, §4.1, §4.2), at 1280
 * and 1440:
 *
 *  - **⋯ → Export schedule (.ics)** and **(.csv)** download through the signed-in session: one authenticated
 *    `GET /api/v1/releases/schedule.{ics,csv}?project_id=…` each (the whole target compared: the project and nothing
 *    else, no token in the URL; the `Authorization` header carries it), saved under the server's filename
 *    (`testlookup-releases-checkout-20260918.ics`); the ICS starts `BEGIN:VCALENDAR` with CRLF lines and stable UIDs,
 *    the CSV starts with a BOM and its header row; a refused export toasts `Could not export the release schedule.`;
 *  - **List | Calendar** (two pressed-state buttons): Calendar replaces the cards in the list column with no new
 *    request (the page's own release list), rides the URL (`?view=calendar`, then `&month=2026-10` from **Next ›**),
 *    shows 2026.11 on its planned day and its phases as bars (Payments soak across 14-18 Sep), states
 *    `Dates are shown as UTC days.`, and a bar opens the release in the pane beside it (`open=` kept beside `view`);
 *    List drops the key again;
 *  - in the page (no dialog), `#main-content` never scrolls sideways; the day cell's width is printed (`INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, observed } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  BOM,
  QA_LEAD,
  RELEASE_ACTIVE_ID,
  releasePlanningWorld,
  SCHEDULE_CSV_HEADER,
  scheduleFilename,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const header = (page: Page) => page.locator('[data-page-header]')
const pane = (page: Page, name: string) => page.getByRole('complementary', { name, exact: true })
const searchOf = (page: Page) => [...new URL(page.url()).searchParams.entries()]

async function openMenu(page: Page) {
  await header(page).getByRole('button', { name: 'More actions' }).click()
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`Export schedule (.ics) and (.csv): authenticated downloads under the server's filename — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releasePlanningWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      await expectMainFits(page, `header @${width}`)

      await openMenu(page)
      const [ics] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: 'Export schedule (.ics)' }).click()])
      expect(ics.suggestedFilename()).toBe(scheduleFilename('ics'))
      const icsText = await readFile(await ics.path(), 'utf8')
      expect(icsText.startsWith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n')).toBe(true)
      expect(icsText).toContain(`UID:release-${RELEASE_ACTIVE_ID}@testlookup\r\n`)
      expect(icsText.trimEnd().endsWith('END:VCALENDAR')).toBe(true)

      await openMenu(page)
      const [csv] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: 'Export schedule (.csv)' }).click()])
      expect(csv.suggestedFilename()).toBe(scheduleFilename('csv'))
      const csvText = await readFile(await csv.path(), 'utf8')
      expect(csvText.startsWith(`${BOM}${SCHEDULE_CSV_HEADER}\r\n`), 'a BOM, then the header row').toBe(true)

      // Each export is ONE authenticated read: the whole target is the path and the project, no token in the URL.
      expect(world.downloads).toEqual([
        { target: `/api/v1/releases/schedule.ics?project_id=${P}`, authorization: 'Bearer access' },
        { target: `/api/v1/releases/schedule.csv?project_id=${P}`, authorization: 'Bearer access' },
      ])
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })

    test(`Calendar: no new request, the chips and bars, a bar opens the pane, the month rides the URL — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releasePlanningWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      const list = header(page).getByRole('button', { name: 'List', exact: true })
      const calendar = header(page).getByRole('button', { name: 'Calendar', exact: true })
      await expect(list).toHaveAttribute('aria-pressed', 'true')
      await expect(calendar).toHaveAttribute('aria-pressed', 'false')
      expect(searchOf(page), 'List is the default and writes no key').toEqual([])

      const before = observed(api).length
      await calendar.click()
      await expect(calendar).toHaveAttribute('aria-pressed', 'true')
      expect(searchOf(page)).toEqual([['view', 'calendar']])
      const primary = page.locator('[data-primary]')
      await expect(primary.getByText('September 2026')).toBeVisible()
      await expect(primary.getByText('Dates are shown as UTC days.', { exact: true })).toBeVisible()
      await expect(page.getByRole('article', { name: /^2026\.11 / }), 'the cards gave way to the calendar').toHaveCount(0)
      // 2026.11 on its planned day (23 Sep), 2026.10 on its release day (8 Sep), and 2026.11's phases as bars.
      await expect(primary.getByRole('button', { name: /2026\.11/ }).first()).toBeVisible()
      const soak = primary.getByRole('button', { name: /Payments soak/ })
      await expect(soak).toHaveCount(1)
      await expect(soak).toHaveAttribute('title', /Payments soak/)
      await networkQuiet(page, api)
      expect(observed(api).length, 'the calendar is drawn from the list the page holds').toBe(before)

      const day = await boxOf(primary.getByText('Dates are shown as UTC days.', { exact: true }).locator('xpath=ancestor::*[1]'))
      const grid = await boxOf(soak.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " grid-cols-7 ")][1]'))
      inPageLine('/releases calendar', width, { grid: grid.width, day: Math.round(grid.width / 7), legend: day.width })
      await expectMainFits(page, `calendar @${width}`)

      await soak.click()
      await expect(pane(page, '2026.11'), 'a bar opens its release beside the calendar').toBeVisible()
      expect(searchOf(page)).toEqual([['view', 'calendar'], ['open', RELEASE_ACTIVE_ID]])
      await expectNoOverlay(page)
      await page.getByRole('button', { name: 'Close release' }).click()
      await expect(pane(page, '2026.11')).toHaveCount(0)

      await primary.getByRole('button', { name: /Next/ }).click()
      await expect(primary.getByText('October 2026')).toBeVisible()
      expect(searchOf(page)).toEqual([['view', 'calendar'], ['month', '2026-10']])
      await expect(primary.getByRole('button', { name: /2026\.12/ }).first(), 'the planned release on 18 Oct').toBeVisible()

      await list.click()
      await expect(list).toHaveAttribute('aria-pressed', 'true')
      expect(searchOf(page), 'List drops the view and month').toEqual([])
      await expect(page.getByRole('article', { name: /^2026\.11 / })).toBeVisible()
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  })
}

test('a refused export says so: "Could not export the release schedule."', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const world = releasePlanningWorld()
  const handlers: ApiHandlers = [['/api/v1/releases/schedule.ics', () => respond(500, { detail: 'Internal Server Error' })], ...world.handlers]
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready, user: QA_LEAD })
  await networkQuiet(page, api)
  await openMenu(page)
  await page.getByRole('menuitem', { name: 'Export schedule (.ics)' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Could not export the release schedule.' })).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
