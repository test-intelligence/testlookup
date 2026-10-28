/**
 * W21d ACT-02, hermetic (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §2.4, §4.1, §4.2), on `/activity`
 * as a QA lead: the Live toggle.
 *
 *  - OFF by default: a fresh browser asks for no stream at all;
 *  - ON: ONE GET of the project's stream with `Authorization: Bearer` and `Accept: text/event-stream`, and nothing
 *    but the path in its URL (no token, no query); the streamed row lands on top of the feed and the dot says live;
 *    the world's body then ENDS, the client stays live through its first backoff step (5 s) and reconnects with
 *    `Last-Event-ID` = the last frame's id; the world answers that with 503, and the line under the filter row says
 *    "Live updates are unavailable right now; …" (absence of a stream is never shown as a quiet project);
 *  - a category filter keeps a streamed row that does not match it off the feed;
 *  - the choice survives a reload, and OFF stops the stream: no reconnect follows;
 *  - a refusal (429) shows the server's own sentence and is never retried;
 *  - no dialog anywhere.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-activity-live.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { activityLiveWorld, defaultStreamedRow, streamId } from '../visual/production/fixtures-activity-live'
import { STREAM_ERROR_SENTENCES } from '../../src/types/activityLive'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const STREAM_PATH = `/api/v1/projects/${PROJECT_ID}/activity/stream`
/** The client's first reconnect step (RETRY_MILLISECONDS) and a margin. */
const PAST_FIRST_BACKOFF_MS = 6_500

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Activity' })
const toggle = (p: Page) => p.locator('[data-activity-live]')
const statusLine = (p: Page) => p.locator('[data-activity-live-status]')
const rows = (p: Page) => p.getByRole('feed').getByTestId('activity-row')
const pathAndQuery = (url: string) => {
  const parsed = new URL(url)
  return `${parsed.pathname}${parsed.search}`
}

test('OFF by default; ON opens ONE bearer stream with no token in the URL, the row lands on top, the reconnect resumes, a 503 says so', async ({ page }) => {
  const world = activityLiveWorld()
  const { api, errors } = await openRollout(page, '/activity', { handlers: world.handlers, ready })
  await expect(rows(page)).toHaveCount(3)
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await networkQuiet(page, api)
  expect(requestsTo(api, STREAM_PATH), 'Live is OFF in a fresh browser: no stream').toEqual([])

  await toggle(page).click()
  const streamed = defaultStreamedRow()
  await expect(rows(page).first()).toContainText(streamed.summary)
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'live')
  await expect(statusLine(page)).toHaveCount(0)
  await expect(rows(page)).toHaveCount(4)
  const [first] = world.streamRequests
  expect(pathAndQuery(first.url)).toBe(STREAM_PATH)
  expect(first.authorization).toBe('Bearer access')
  expect(first.accept).toBe('text/event-stream')
  expect(first.lastEventId).toBeNull()

  await expect(statusLine(page)).toHaveText(STREAM_ERROR_SENTENCES.live_unavailable, { timeout: 15_000 })
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'unavailable')
  expect(world.streamRequests).toHaveLength(2)
  expect(pathAndQuery(world.streamRequests[1].url)).toBe(STREAM_PATH)
  expect(world.streamRequests[1].lastEventId).toBe(streamId(1))
  expect(world.streamRequests[1].authorization).toBe('Bearer access')
  await expect(rows(page).first()).toContainText(streamed.summary)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('a category filter keeps a streamed row that does not match it off the feed', async ({ page }) => {
  const world = activityLiveWorld()
  const { api, errors } = await openRollout(page, '/activity?category=release', { handlers: world.handlers, ready })
  await expect(rows(page)).toHaveCount(1)
  await toggle(page).click()
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'live')
  await networkQuiet(page, api)
  expect(world.streamRequests).toHaveLength(1)
  await expect(rows(page)).toHaveCount(1)
  await expect(page.getByRole('feed')).not.toContainText(defaultStreamedRow().summary)
  assertHermetic(api, errors)
})

test('Live is remembered across a reload, and OFF stops the stream: no reconnect follows', async ({ page }) => {
  const world = activityLiveWorld({ stream: { after: 'repeat' } })
  const { api, errors } = await openRollout(page, '/activity', { handlers: world.handlers, ready })
  await toggle(page).click()
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'live')

  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'live')
  expect(world.streamRequests.length).toBeGreaterThanOrEqual(2)

  await toggle(page).click()
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(statusLine(page)).toHaveCount(0)
  const opened = world.streamRequests.length
  await page.waitForTimeout(PAST_FIRST_BACKOFF_MS)
  expect(world.streamRequests, 'no reconnect after Live was turned off').toHaveLength(opened)
  assertHermetic(api, errors)
})

test('a refusal shows the server sentence and is never retried', async ({ page }) => {
  const world = activityLiveWorld({ stream: { refuse: { status: 429, code: 'too_many_streams' } } })
  const { api, errors } = await openRollout(page, '/activity', { handlers: world.handlers, ready })
  await toggle(page).click()
  await expect(statusLine(page)).toHaveText(STREAM_ERROR_SENTENCES.too_many_streams)
  await expect(toggle(page)).toHaveAttribute('data-live-status', 'refused')
  await page.waitForTimeout(PAST_FIRST_BACKOFF_MS)
  expect(world.streamRequests).toHaveLength(1)
  await expect(rows(page)).toHaveCount(3)
  assertHermetic(api, errors)
})
