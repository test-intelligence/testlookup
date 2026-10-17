/**
 * W08 TRND-04 (contract §2.6), hermetic: notes pinned on the timeline.
 *
 *   - a QA engineer drags across three days of the Run cadence strip → the
 *     **Pin a note** dialog opens with those dates → `release freeze` → Save →
 *     ONE `POST /projects/{p}/timeline-notes` naming the dates and the text;
 *     the fixture keeps it, the window's list is asked again, and the hero
 *     "Pass rate trend" draws one band (`[data-chart-note]`) labelled
 *     `release freeze`, with `Notes (1)` in its toolbar;
 *   - the same note is drawn on Coverage › History;
 *   - a viewer sees every note drawn and no `Pin a note`; a drag pins nothing;
 *   - a hostile note text is drawn as text (no element made from it, its
 *     handler never runs).
 *
 * `rollout-trends.spec.ts`'s `LOAD` holds the notes read of a load (the
 * window's first and last UTC day). Harness: `tests/lib/rollout.ts` (fail
 * closed, pinned clock). Fixtures: `notesWorld()` (`fixtures-trends-coverage.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark } from '../lib/production-pages'
import { assertHermetic, expectHostileAsText, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { COVERAGE_ON, daysAgo, HOSTILE_NAME, PROJECT_ID, TRENDS_ON } from '../visual/production/fixtures'
import { note, notesWorld, QA_ENGINEER, VIEWER, VISUAL_NOTES } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1440, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const NOTES = `/api/v1/projects/${P}/timeline-notes`
const ready = (p: Page) => landmark(p, 'Trend metrics')
const hero = (page: Page) => page.locator('[data-catalogue-section="trends-pass-rate"]')
const cadence = (page: Page): Locator =>
  page.locator('div, section').filter({ has: page.getByRole('heading', { name: /^Run cadence — last/ }) }).filter({ has: page.locator('[data-day-strip]') }).last()
const pinDialog = (page: Page) => opened(page, 'Pin a note')

/** Press on cell `from` of the cadence strip, move over to cell `to`, release. */
async function dragDays(page: Page, from: number, to: number) {
  const cells = cadence(page).locator('[data-day-strip] [data-day-cell]')
  await expect(cells).toHaveCount(14)
  const centre = async (i: number) => {
    const box = await cells.nth(i).boundingBox()
    if (!box) throw new Error(`cell ${i} has no box`)
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  }
  const start = await centre(from)
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  for (let i = from + 1; i <= to; i++) {
    const at = await centre(i)
    await page.mouse.move(at.x, at.y, { steps: 3 })
  }
  await page.mouse.up()
}

test('a QA engineer drags three cadence days, pins "release freeze", and the hero draws it as a band', async ({ page }) => {
  const world = notesWorld([], { author: QA_ENGINEER.full_name })
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON, ...COVERAGE_ON], ready, user: QA_ENGINEER })
  await networkQuiet(page, api)
  const listReads = requestsTo(api, NOTES).length
  expect(listReads, 'the window\'s notes are asked on load').toBeGreaterThan(0)
  await expect(hero(page).locator('[data-chart-note]')).toHaveCount(0)
  await expect(cadence(page).getByRole('button', { name: 'Pin a note', exact: true })).toBeVisible()

  // Cells 10..12 of the 14-day strip: 3, 2 and 1 days ago.
  await dragDays(page, 10, 12)
  await expect(pinDialog(page)).toBeVisible()
  await expect(pinDialog(page).locator('[data-pin-note="from"]')).toHaveValue(daysAgo(3))
  await expect(pinDialog(page).locator('[data-pin-note="to"]')).toHaveValue(daysAgo(1))
  await pinDialog(page).locator('[data-pin-note="text"]').fill('release freeze')
  await pinDialog(page).locator('[data-pin-note="save"]').click()
  await expect(pinDialog(page)).toHaveCount(0)

  expect(world.writes).toHaveLength(1)
  expect(world.writes[0].line).toBe(`POST ${NOTES}`)
  expect(world.writes[0].body).toMatchObject({ starts_on: daysAgo(3), ends_on: daysAgo(1), text: 'release freeze' })
  expect((world.writes[0].body as { suite_name?: unknown }).suite_name ?? null, 'no suite selected: a project-wide note').toBeNull()
  await expect.poll(() => requestsTo(api, NOTES).length, { message: 'the list asked again after the save' }).toBeGreaterThan(listReads)

  const drawn = hero(page).locator('[data-chart-note]')
  await expect(drawn).toHaveCount(1)
  await expect(drawn).toHaveAttribute('data-chart-note-kind', 'band')
  await expect(drawn).toContainText('release freeze')
  await expect(hero(page).locator('[data-timeline-notes-chip]')).toHaveText(/Notes \(1\)/)

  // The same note on Coverage › History.
  await page.goto('/coverage?tab=history')
  const history = page.getByRole('region', { name: 'Coverage history' })
  await expect(history).toHaveAttribute('data-coverage-history', 'drawn', { timeout: 20_000 })
  await expect(history.locator('[data-chart-note]')).toHaveCount(1)
  await expect(history.locator('[data-chart-note]')).toContainText('release freeze')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a viewer sees every note and no Pin a note; a drag on the strip pins nothing', async ({ page }) => {
  const world = notesWorld(VISUAL_NOTES)
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON], ready, user: VIEWER })
  const drawn = hero(page).locator('[data-chart-note]')
  await expect(drawn).toHaveCount(2)
  await expect(hero(page).locator('[data-chart-note-kind="band"]')).toHaveCount(1)
  await expect(hero(page).locator('[data-chart-note-kind="line"]')).toHaveCount(1)
  await expect(hero(page).locator('[data-timeline-notes-chip]')).toHaveText(/Notes \(2\)/)
  await expect(page.getByRole('button', { name: /Pin a note/ })).toHaveCount(0)
  await dragDays(page, 10, 12)
  await expect(pinDialog(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes, 'a viewer wrote nothing').toEqual([])
  assertHermetic(api, errors)
})

test('a hostile note text is drawn as text', async ({ page }) => {
  const world = notesWorld([note('99999999-9999-4999-8999-0000000000ff', daysAgo(2), daysAgo(2), HOSTILE_NAME)])
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON], ready })
  const drawn = hero(page).locator('[data-chart-note]')
  await expect(drawn).toHaveCount(1)
  // The label is the text cut short, as TEXT; the full text is the note's tooltip title.
  await expect(drawn.locator('title')).toHaveText(new RegExp(HOSTILE_NAME.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  await expectHostileAsText(page, hero(page), 'a note on the hero')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
