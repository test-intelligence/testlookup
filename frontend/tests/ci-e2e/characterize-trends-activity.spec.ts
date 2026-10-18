/**
 * W27b PAN-05b CHARACTERIZATION, `/trends` notes, `/activity`, `/value-metrics`
 * (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.2, rows N3, N4, N6, N7), hermetic. Written against the
 * MODAL tree (`PinNoteDialog`, the `ActivityDrawer`, the methodology dialog) and kept green, UNEDITED, after the
 * in-page conversion: each container is found by its kept name (`opened`), only named controls are driven, and
 * Escape only where it works today (the note form, the activity detail with focus on its X).
 *
 *  - N3 the run-gap callout's `Pin a note for these days` (QA engineer) → the form prefilled with the gap's days
 *    → `POST /projects/{p}/timeline-notes` `{ starts_on, ends_on, text (trimmed), suite_name: null }`,
 *    `Note pinned`, the notes re-read and the hero's chip; Cancel, the X and Escape send nothing;
 *  - N4 the hero's Notes chip › Edit → nothing changed: no request and `Note updated`; the end day and the text
 *    changed → `PATCH …/timeline-notes/{id}` with ONLY those two fields; a refused edit (403) keeps the form with
 *    the server's sentence once, as text;
 *  - N6 `/activity?category=…` › a row → its detail (`?event=<id>`, the category kept), one
 *    `GET /projects/{p}/activity/{id}`, focus on its X; another row switches it (its own read, the first not
 *    re-read); the X closes it (focus back on the row); Escape with focus inside closes it; a reload with
 *    `?event=` reopens it; a purged event shows the 404 line;
 *  - N7 `/value-metrics` › How is this calculated? → the methodology (one read), `Close`, and reopened from the
 *    hero's figure without a second read.
 *
 * Not here (they CHANGE in W27b, so a characterization cannot hold before and after): Back closing an event
 * (today `?event=` is written with `replace`), and `?event=../../x` (today the raw value reaches the request
 * path). The phase-2 in-page spec pins both.
 *
 * Harness: `tests/lib/rollout.ts` / `production-pages.ts` (fail closed, pinned clock). Fixtures: `notesWorld`
 * (`fixtures-trends-coverage.ts`), `refusingNotesWorld`, `activityInPageWorld`, `METHODOLOGY_HANDLERS`
 * (`fixtures-in-page-b.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark, openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { daysAgo, NOW, PROJECT_ID, TRENDS_ON, USER, VALUE_METRICS } from '../visual/production/fixtures'
import {
  ACTIVITY_EVENTS,
  activityInPageWorld,
  METHODOLOGY,
  METHODOLOGY_HANDLERS,
  NOTE_REFUSAL,
  refusingNotesWorld,
} from '../visual/production/fixtures-in-page-b'
import { note, notesWorld, QA_ENGINEER } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1440, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const NOTES = `/api/v1/projects/${P}/timeline-notes`
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const hero = (page: Page) => page.locator('[data-catalogue-section="trends-pass-rate"]')
const pathAndSearch = (page: Page) => {
  const url = new URL(page.url())
  return `${url.pathname}${url.search}`
}

test('N3 the run gap\'s Pin a note for these days: Cancel, the X and Escape send nothing; the gap\'s days and a trimmed text → the POST, the chip', async ({ page }) => {
  const world = notesWorld([], { author: QA_ENGINEER.full_name })
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON], ready: trendsReady, user: QA_ENGINEER })
  const pin = page.locator('[data-pin-note-open="gap"]')
  await expect(pin).toHaveText('Pin a note for these days')
  await networkQuiet(page, api)
  const reads = requestsTo(api, NOTES).length

  await pin.click()
  let form = opened(page, 'Pin a note')
  await expect(form).toBeVisible()
  await expect(form.locator('[data-pin-note="from"]')).toHaveValue(daysAgo(11))
  await expect(form.locator('[data-pin-note="to"]')).toHaveValue(daysAgo(5))
  await expect(form.locator('[data-pin-note="save"]'), 'no text: Save disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await pin.click()
  form = opened(page, 'Pin a note')
  await form.getByRole('button', { name: 'Close note dialog' }).click()
  await expect(form).toHaveCount(0)
  await pin.click()
  form = opened(page, 'Pin a note')
  await form.locator('[data-pin-note="text"]').fill('typed, then Escape')
  await form.locator('[data-pin-note="text"]').press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await pin.click()
  form = opened(page, 'Pin a note')
  await form.locator('[data-pin-note="text"]').fill('  CI runners paused for the move  ')
  await expect(form.locator('[data-pin-note="count"]')).toHaveText('34 / 280')
  await form.locator('[data-pin-note="save"]').click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Note pinned', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${NOTES}`, body: { starts_on: daysAgo(11), ends_on: daysAgo(5), text: 'CI runners paused for the move', suite_name: null } },
  ])
  expect(requestsTo(api, NOTES).length, 'the notes re-read').toBeGreaterThan(reads)
  await expect(hero(page).locator('[data-timeline-notes-chip]')).toHaveText(/Notes \(1\)/)
  assertHermetic(api, errors)
})

const EDITABLE = note('99999999-9999-4999-8999-0000000000e1', daysAgo(11), daysAgo(5), 'CI runners paused for the datacentre move')
const LOCKED = note('99999999-9999-4999-8999-0000000000e2', daysAgo(3), daysAgo(3), 'release freeze', { created_by_name: 'Dana Whitfield' })

test('N4 the hero\'s Notes › Edit: nothing changed sends nothing; two changes → a PATCH of only those fields; a refusal stays inline', async ({ page }) => {
  const world = notesWorld([EDITABLE, LOCKED])
  const { api, errors } = await openRollout(page, '/trends', {
    handlers: [...refusingNotesWorld(world, [LOCKED.id]), ...TRENDS_ON],
    ready: trendsReady,
    user: QA_ENGINEER,
  })
  const chip = hero(page).locator('[data-timeline-notes-chip]')
  await expect(chip).toHaveText(/Notes \(2\)/)
  await networkQuiet(page, api)
  const edit = async (id: string) => {
    await chip.click()
    await page.locator(`[data-timeline-note-edit="${id}"]`).click()
  }

  // Nothing changed: nothing sent, and still "Note updated".
  await edit(EDITABLE.id)
  let form = opened(page, 'Edit note')
  await expect(form).toBeVisible()
  await expect(form.locator('[data-pin-note="from"]')).toHaveValue(EDITABLE.starts_on)
  await expect(form.locator('[data-pin-note="to"]')).toHaveValue(EDITABLE.ends_on)
  await expect(form.locator('[data-pin-note="text"]')).toHaveValue(EDITABLE.text)
  await form.locator('[data-pin-note="save"]').click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Note updated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // Cancel sends nothing.
  await edit(EDITABLE.id)
  form = opened(page, 'Edit note')
  await form.locator('[data-pin-note="text"]').fill('changed, then cancelled')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // The end day and the text: only those two.
  await edit(EDITABLE.id)
  form = opened(page, 'Edit note')
  await form.locator('[data-pin-note="to"]').fill(daysAgo(6))
  await form.locator('[data-pin-note="text"]').fill('  CI paused: datacentre move  ')
  await form.locator('[data-pin-note="save"]').click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Note updated', { exact: true }).first()).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PATCH ${NOTES}/${EDITABLE.id}`, body: { ends_on: daysAgo(6), text: 'CI paused: datacentre move' } },
  ])

  // A refused edit: the server's sentence, once, in the form; the form stays.
  await edit(LOCKED.id)
  form = opened(page, 'Edit note')
  await form.locator('[data-pin-note="text"]').fill('release freeze (extended)')
  await form.locator('[data-pin-note="save"]').click()
  await expect(form.locator('[data-pin-note-refusal]')).toHaveText(NOTE_REFUSAL)
  await expect(page.getByText(NOTE_REFUSAL), 'the refusal is shown once').toHaveCount(1)
  await expect(form, 'a refused edit keeps the form').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: `PATCH ${NOTES}/${LOCKED.id}`, body: { text: 'release freeze (extended)' } }])
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  assertHermetic(api, errors)
})

const activityReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Activity' })
const eventPath = (id: string) => `/api/v1/projects/${P}/activity/${id}`
const rowButton = (page: Page, summary: string) => page.getByRole('feed').getByRole('button', { name: summary, exact: true })
const [FLAG, RETENTION, GONE] = ACTIVITY_EVENTS

test('N6 /activity: a row opens its detail (?event=, the category kept), another row switches it, the X and Escape close it, a reload reopens it', async ({ page }) => {
  const { handlers } = activityInPageWorld()
  const { api, errors } = await openRollout(page, '/activity?category=configuration', { handlers, ready: activityReady })
  await expect(rowButton(page, FLAG.summary)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, /^\/api\/v1\/projects\/[^/]+\/activity\/[^/]+$/), 'no detail before a row opens').toEqual([])

  await rowButton(page, FLAG.summary).click()
  let detail = opened(page, FLAG.summary)
  await expect(detail).toBeVisible()
  await expect.poll(() => pathAndSearch(page)).toBe(`/activity?category=configuration&event=${FLAG.id}`)
  await expect(detail).toContainText('feature_flag.updated')
  await expect(detail).toContainText('Priya Nair')
  await expect(detail).toContainText('Compliance record: audit_logs · al-1')
  await expect(detail.getByRole('button', { name: 'Show raw payload' })).toBeVisible()
  await expect(detail.getByRole('button', { name: 'Copy link to this event' })).toBeVisible()
  await expect(detail.getByRole('button', { name: 'Close activity details' }), 'focus moves to the X').toBeFocused()
  await networkQuiet(page, api)
  expect(requestsTo(api, eventPath(FLAG.id))).toEqual([`GET ${eventPath(FLAG.id)}`])

  // Another row switches the detail: its own read, the first not re-read. (Keyboard: today the drawer's backdrop
  // covers the feed for a pointer; nothing traps focus, so the row is reachable and Enter opens it.)
  await rowButton(page, RETENTION.summary).focus()
  await page.keyboard.press('Enter')
  detail = opened(page, RETENTION.summary)
  await expect(detail).toBeVisible()
  await expect(opened(page, FLAG.summary)).toHaveCount(0)
  await expect.poll(() => pathAndSearch(page)).toBe(`/activity?category=configuration&event=${RETENTION.id}`)
  await networkQuiet(page, api)
  expect(requestsTo(api, eventPath(RETENTION.id))).toEqual([`GET ${eventPath(RETENTION.id)}`])
  expect(requestsTo(api, eventPath(FLAG.id))).toHaveLength(1)

  // The X closes it, the category stays.
  await detail.getByRole('button', { name: 'Close activity details' }).click()
  await expect(detail).toHaveCount(0)
  await expect.poll(() => pathAndSearch(page)).toBe('/activity?category=configuration')

  // Escape with focus inside closes it, and focus goes back to the row that opened it.
  await rowButton(page, FLAG.summary).click()
  detail = opened(page, FLAG.summary)
  await expect(detail.getByRole('button', { name: 'Close activity details' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(detail).toHaveCount(0)
  await expect.poll(() => pathAndSearch(page)).toBe('/activity?category=configuration')
  await expect(rowButton(page, FLAG.summary), 'focus returns to the row').toBeFocused()

  // A reload with ?event= reopens it.
  await page.goto(`/activity?category=configuration&event=${RETENTION.id}`)
  await expect(opened(page, RETENTION.summary)).toBeVisible()
  await expect(opened(page, RETENTION.summary)).toContainText('retention.updated')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('N6 /activity: a purged event (404) shows its line, and nothing crashes', async ({ page }) => {
  const { handlers } = activityInPageWorld()
  const { api, errors } = await openRollout(page, '/activity', { handlers, ready: activityReady })
  await rowButton(page, GONE.summary).click()
  const detail = opened(page, /^(Activity event|API key ci-uploader revoked)$/)
  await expect(detail.getByText('This event no longer exists. It may have been removed by the retention purge.')).toBeVisible()
  await expect.poll(() => pathAndSearch(page)).toBe(`/activity?event=${GONE.id}`)
  await networkQuiet(page, api)
  expect(requestsTo(api, eventPath(GONE.id)).length, 'the purged event was asked').toBeGreaterThan(0)
  await detail.getByRole('button', { name: 'Close activity details' }).click()
  await expect(detail).toHaveCount(0)
  await expect(rowButton(page, FLAG.summary), 'the feed is still there').toBeVisible()
  assertHermetic(api, errors)
})

test('N7 /value-metrics: How is this calculated? reads the methodology once; Close; the hero\'s figure reopens it without a second read', async ({ page }) => {
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
  const METHOD = '/api/v1/value-metrics/methodology'
  expect(requestsTo(api, METHOD), 'not read before it opens').toEqual([])

  await page.getByRole('button', { name: 'How is this calculated?' }).click()
  let methodology = opened(page, 'How hours saved is calculated')
  await expect(methodology).toBeVisible()
  await expect(methodology.getByRole('heading', { name: 'How is this calculated?' })).toBeVisible()
  await expect(methodology.getByText(`Methodology version ${METHODOLOGY.version}`)).toBeVisible()
  await expect(methodology.getByRole('heading', { name: 'Triage time saved' })).toBeVisible()
  await expect(methodology).toContainText('Triage 6 min/failure · blocked-run wait 30 min · defect filing 10 min')
  await networkQuiet(page, api)
  expect(requestsTo(api, METHOD)).toEqual([`GET ${METHOD}`])
  await methodology.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(methodology).toHaveCount(0)

  await page.locator('[data-testid="hours-saved-window"]').locator('xpath=ancestor::section[1]').getByTitle('How is this calculated?').click()
  methodology = opened(page, 'How hours saved is calculated')
  await expect(methodology.getByText(`Methodology version ${METHODOLOGY.version}`)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, METHOD), 'cached: not read again').toEqual([`GET ${METHOD}`])
  await methodology.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(methodology).toHaveCount(0)
  assertHermetic(api, errors)
})
