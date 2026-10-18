/**
 * W28 CHAT-01 (OR-05), hermetic, `ask_ai_chat` ON (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §2.6, §4.4):
 * the starter prompts stay available DURING a conversation, as a row of chips right above the question box.
 *
 *  - the welcome screen is unchanged: its five `chat-starter` cards, and no `chat-suggestion` (nor Show);
 *  - after a question the row (`group` "Suggested questions", five `chat-suggestion` chips showing the labels, each
 *    chip's title its full prompt) is the next thing above the question box; a chip asks its prompt (the stream
 *    request's WHOLE body) while what the reader typed stays in the box; the chips are disabled while that answer is
 *    being written and come back after;
 *  - Hide suggestions leaves one `Show suggestions` in its place, is remembered in this browser
 *    (`localStorage['tl.chat.hideSuggestions'] = '1'`) across a reload (the conversation reopened from the list),
 *    and Show brings the row back (`'0'`);
 *  - in the page: no overlay, `#main-content` never scrolls sideways at 1280 and 1440, the row's width, lines and
 *    slack printed (`INPAGE /chat suggestions-row`).
 *
 * `characterize-chat.spec.ts` and `chat-conversation.spec.ts` stay green UNEDITED (the chips carry their own test
 * id, so `chat-starter` filtered by prompt still finds only the welcome's cards).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `chatSuggestionsWorld()`
 * (`fixtures-test-case-editing.ts`, over `chatInPageWorld()`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, followedBy, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { CHAT_PROMPTS, chatSuggestionsWorld, HIDE_SUGGESTIONS_KEY } from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Ask about Checkout' })
const CHAT_ON = { ask_ai_chat: true }
const row = (p: Page) => p.getByRole('group', { name: 'Suggested questions' })
const chips = (p: Page) => p.getByTestId('chat-suggestion')
const input = (p: Page) => p.getByTestId('chat-input')
const stored = (p: Page) => p.evaluate((key) => window.localStorage.getItem(key), HIDE_SUGGESTIONS_KEY)

/** How many lines a wrapping row's children take (a child starts a new line when it begins below the line's bottom). */
async function wrappedLines(row: Locator): Promise<number> {
  return row.evaluate((el: HTMLElement) => {
    const rects = Array.from(el.children).map((child) => child.getBoundingClientRect()).sort((a, b) => a.top - b.top)
    let lines = 0
    let bottom = -Infinity
    for (const r of rects) {
      if (r.top >= bottom - 1) {
        lines += 1
        bottom = r.bottom
      } else bottom = Math.max(bottom, r.bottom)
    }
    return lines
  })
}

/** Ask one question from the box; its answer is on screen. */
async function askFirst(page: Page) {
  await input(page).fill('Why did build 105 fail?')
  await input(page).press('Enter')
  await expect(page.getByTestId('chat-assistant-message').first()).toContainText('Answer 1: two tests failed in the Auth suite.')
}

/** The five chips, in order, by label, each titled with its full prompt. */
async function expectFiveChips(scope: Locator) {
  const all = scope.getByTestId('chat-suggestion')
  await expect(all).toHaveText(CHAT_PROMPTS.map((p) => p.label))
  for (const [i, p] of CHAT_PROMPTS.entries()) await expect(all.nth(i), `${p.label}: its prompt is the title`).toHaveAttribute('title', p.prompt)
}

test('the welcome is unchanged; during a conversation the chips sit right above the box; a chip asks its prompt and keeps the typed draft', async ({ page }) => {
  watchNativeDialogs(page)
  const world = chatSuggestionsWorld({ holdTurn: 2 })
  const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
  await networkQuiet(page, api)

  // The welcome screen: its five starter cards and no chip row.
  await expect(page.getByTestId('chat-starter')).toHaveCount(5)
  await expect(chips(page), 'no chip on the welcome screen').toHaveCount(0)
  await expect(page.getByTestId('chat-suggestions-show'), 'nor a Show').toHaveCount(0)

  await askFirst(page)
  await expect(row(page)).toBeVisible()
  await expectFiveChips(row(page))
  await expect(page.getByTestId('chat-starter'), 'the welcome cards are gone').toHaveCount(0)
  expect(await followedBy(row(page), input(page)), 'the row is right above the question box').toBe(true)
  await expect(row(page).getByTestId('chat-suggestions-hide')).toHaveText('Hide suggestions')
  await expectNoOverlay(page)

  // A chip asks its prompt; the reader's draft stays in the box; the chips wait while the answer is written.
  await input(page).fill('draft text')
  await chips(page).filter({ hasText: 'Flaky tests' }).click()
  await expect.poll(() => world.posted.length, 'the chip sent its question').toBe(2)
  for (let i = 0; i < 5; i++) await expect(chips(page).nth(i), 'disabled while an answer is written').toBeDisabled()
  await expect(input(page), 'the draft stays').toHaveValue('draft text')
  world.release()
  await expect(page.getByTestId('chat-assistant-message').nth(1)).toContainText('Answer 2: two tests failed in the Auth suite.')
  await expect(page.getByTestId('chat-user-message').nth(1)).toContainText(CHAT_PROMPTS[2].prompt)
  for (let i = 0; i < 5; i++) await expect(chips(page).nth(i), 'enabled again').toBeEnabled()
  await expect(input(page), 'the draft is still there after the answer').toHaveValue('draft text')
  await networkQuiet(page, api)
  expect(world.posted).toEqual([{ message: 'Why did build 105 fail?' }, { message: CHAT_PROMPTS[2].prompt }])
  expect(world.writes, 'no suggested-action write').toEqual([])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('Hide suggestions is remembered in this browser across a reload; Show brings the row back', async ({ page }) => {
  watchNativeDialogs(page)
  const world = chatSuggestionsWorld()
  const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
  await networkQuiet(page, api)
  expect(await stored(page), 'nothing remembered before').toBeNull()
  await askFirst(page)

  await row(page).getByTestId('chat-suggestions-hide').click()
  await expect(row(page), 'the row hides').toHaveCount(0)
  await expect(chips(page)).toHaveCount(0)
  const show = page.getByTestId('chat-suggestions-show')
  await expect(show).toHaveText('Show suggestions')
  expect(await stored(page)).toBe('1')
  expect(await followedBy(show, input(page)), 'Show sits where the row was').toBe(true)

  // A reload, the conversation reopened: still hidden.
  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(page.getByTestId('chat-starter'), 'a reload opens on the welcome').toHaveCount(5)
  await page.getByTestId('chat-session').first().click()
  await expect(page.getByTestId('chat-assistant-message').first()).toContainText('Answer 1:')
  await expect(chips(page), 'still hidden after the reload').toHaveCount(0)
  await expect(show).toBeVisible()

  await show.click()
  await expect(row(page)).toBeVisible()
  await expectFiveChips(row(page))
  await expect(show).toHaveCount(0)
  expect(await stored(page)).toBe('0')
  await networkQuiet(page, api)
  expect(world.posted).toEqual([{ message: 'Why did build 105 fail?' }])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 } })

    test(`@${width}: the chip row wraps inside the question box's column; nothing scrolls sideways`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = chatSuggestionsWorld()
      const { api, errors } = await openRollout(page, '/chat', { handlers: world.handlers, ready, flags: CHAT_ON })
      await askFirst(page)
      const group = row(page)
      await expect(group).toBeVisible()
      await networkQuiet(page, api)
      await expectNoOverlay(page)
      await expectMainFits(page, `/chat suggestions @${width}`)
      expect(await ownOverflow(group), `the row @${width} scrolls sideways`).toBeLessThanOrEqual(1)
      // The question box's column (the composer's `max-w-3xl` wrapper) holds the row and the box.
      const column = group.locator('xpath=..')
      await expect(column.getByTestId('chat-input'), 'the row and the box share one column').toHaveCount(1)
      const [rowBox, columnBox] = [await boxOf(group), await boxOf(column)]
      expect(rowBox.left, 'the row starts inside the column').toBeGreaterThanOrEqual(columnBox.left - 1)
      expect(rowBox.right, 'and ends inside it').toBeLessThanOrEqual(columnBox.right + 1)
      inPageLine('/chat suggestions-row', width, { width: rowBox.width, column: columnBox.width, lines: await wrappedLines(group), slack: await minContentSlack(group) })
      assertHermetic(api, errors)
    })
  })
}
