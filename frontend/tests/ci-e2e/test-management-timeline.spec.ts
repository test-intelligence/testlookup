/**
 * W13 TCL-05 "one timeline per test case" (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §2.6, §4.2),
 * hermetic, 1440 × 900:
 *
 *  - an authored case's pane: **Timeline** is the SECOND tab, Details stays the default, and nothing asks for the
 *    timeline until the tab opens; then ONE `GET /cases/<id>/timeline?limit=50` (no `kinds`: All);
 *  - the entries newest first, each with its time, title, `by <actor>` / `by not recorded` / `by automation`, its
 *    status; the links by id only (`/runs/<run>/tests/<result>`, `/test-management?tab=plans`, `/defects?defect=<id>`,
 *    compared whole); a `<img …>` detail rendered as text;
 *  - **Load more** sends the page's `next_cursor`; the stream then ends (`Start of the timeline.`);
 *  - the chips send their kinds (`Reviews` → `kinds=review_requested&kinds=review_decided`); Executions with no
 *    automation identity says so; a failed read says `Could not load the timeline.` with Retry.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `timelineWorld()` (`fixtures-test-library.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  LIBRARY_TEXT,
  LIBRARY_USERS,
  TIMELINE_CASE,
  TIMELINE_CURSOR,
  TIMELINE_DEFECT_ID,
  TIMELINE_RESULT_ID,
  TIMELINE_RUN_ID,
  timelineApi,
  timelineWorld,
  tmCaseId,
  tmCaseTitle,
  type TimelineWorldOptions,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const pane = (p: Page) => p.locator('[data-case-panel]')
const timeline = (p: Page) => pane(p).locator('[data-case-timeline]')
const API = timelineApi(tmCaseId(TIMELINE_CASE))
const timelineQueries = (world: ReturnType<typeof timelineWorld>) =>
  world.reads.filter((r) => r.line.startsWith(`GET ${API}`)).map((r) => new URL(r.line.slice(4), 'http://x').searchParams)

async function openCase(page: Page, options: TimelineWorldOptions = {}) {
  const world = timelineWorld(options)
  const opened_ = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady, user: LIBRARY_USERS.QA_LEAD })
  await page.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(TIMELINE_CASE)}"]:not([data-in-page-row])`).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(TIMELINE_CASE) })).toBeVisible()
  return { world, ...opened_ }
}

test('TCL-05 Timeline is the second tab, read only once opened; entries, actors, links by id, text-only details; Load more → the cursor → the start', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await openCase(page)
  const tabs = pane(page).getByRole('button').filter({ hasText: /^(Details|Timeline|History|Reviews|Comments|AI Review)$/ })
  await expect(tabs.nth(0)).toHaveText('Details')
  await expect(tabs.nth(1)).toHaveText('Timeline')
  await networkQuiet(page, api)
  expect(requestsTo(api, API), 'nothing asks for the timeline before the tab opens').toEqual([])

  await pane(page).getByRole('button', { name: 'Timeline', exact: true }).click()
  await expect(timeline(page).locator('[data-timeline-entry]')).toHaveCount(6)
  await networkQuiet(page, api)
  const first = timelineQueries(world)
  expect(first, 'ONE read, All kinds, the default limit').toHaveLength(1)
  expect([...first[0].entries()]).toEqual([['limit', '50']])

  const kinds = await timeline(page).locator('[data-timeline-entry]').evaluateAll((els) => els.map((e) => e.getAttribute('data-kind')))
  expect(kinds).toEqual(['automated_execution', 'defect', 'review_decided', 'comment', 'plan_execution', 'version'])
  await expect(timeline(page).locator(`[data-timeline-entry="${TIMELINE_RESULT_ID}"]`)).toContainText('by automation')
  await expect(timeline(page).locator('[data-kind="review_decided"]')).toContainText('by Lena Lead')
  await expect(timeline(page).locator('[data-kind="defect"]')).toContainText('by not recorded')
  await expect(timeline(page).locator('[data-kind="defect"] [data-timeline-detail]')).toHaveText('<img src=x onerror="window.__xss=1"> retried twice')
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss), 'the detail is text').toBeUndefined()

  const href = (type: string) => timeline(page).locator(`[data-timeline-link="${type}"]`).first().getAttribute('href')
  expect(await href('run_result')).toBe(`/runs/${TIMELINE_RUN_ID}/tests/${TIMELINE_RESULT_ID}`)
  expect(await href('plan')).toBe('/test-management?tab=plans')
  expect(await href('defect')).toBe(`/defects?defect=${TIMELINE_DEFECT_ID}`)

  await timeline(page).getByRole('button', { name: 'Load more', exact: true }).click()
  await expect(timeline(page).locator('[data-timeline-entry]')).toHaveCount(9)
  await expect(timeline(page).locator('[data-timeline-end]')).toHaveText(LIBRARY_TEXT.timelineEnd)
  await networkQuiet(page, api)
  const all = timelineQueries(world)
  expect(all).toHaveLength(2)
  expect(Object.fromEntries(all[1].entries()), 'page 2 sends page 1\'s cursor').toEqual({ cursor: TIMELINE_CURSOR, limit: '50' })
  assertHermetic(api, errors)
})

test('TCL-05 the chips send their kinds; Executions without an automation identity says so', async ({ page }) => {
  const { world, api, errors } = await openCase(page, { noAutomation: true })
  await pane(page).getByRole('button', { name: 'Timeline', exact: true }).click()
  await expect(timeline(page).locator('[data-timeline-entry]')).toHaveCount(6)

  await timeline(page).getByRole('button', { name: 'Reviews', exact: true }).click()
  await expect(timeline(page).locator('[data-timeline-entry]')).toHaveCount(2)
  await timeline(page).getByRole('button', { name: 'Executions', exact: true }).click()
  await expect(timeline(page).getByRole('note')).toHaveText(LIBRARY_TEXT.timelineNoAutomation)
  await networkQuiet(page, api)
  const sent = timelineQueries(world).map((q) => q.getAll('kinds'))
  expect(sent).toEqual([[], ['review_requested', 'review_decided'], ['plan_execution', 'automated_execution']])
  assertHermetic(api, errors)
})

test('TCL-05 a failed read says so with Retry', async ({ page }) => {
  const { world, api, errors } = await openCase(page, { fails: true })
  await pane(page).getByRole('button', { name: 'Timeline', exact: true }).click()
  await expect(timeline(page).getByRole('alert')).toContainText('Could not load the timeline.')
  await networkQuiet(page, api)
  const before = timelineQueries(world).length
  await timeline(page).getByRole('button', { name: 'Retry', exact: true }).click()
  await expect.poll(() => timelineQueries(world).length).toBeGreaterThan(before)
  assertHermetic(api, errors)
})
