/**
 * W23a TRND-03: Settings › Expected runs (`/settings/expected-runs`), hermetic
 * (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §4.1, §6.3), at 1280 on a QA lead unless named:
 *
 *  - the empty state → Add (Runs of suite `nightly-regression`, Weekdays, by 06:00, Europe/Berlin) → the exact
 *    POST body → the row `Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min`; the form and the table never push
 *    the page sideways, and the zone select keeps ≥ 40 px beside the longest zone name;
 *  - a scripted 409 `duplicate_scope` and the server's 422 for `0 6 1 * *` read inline in the form, once;
 *  - Pause → a reason is required → `{until (with the offset), reason: "release freeze"}` → `Paused` + `until …`;
 *    Resume → `{}` → `Active`; Delete asks inline, then sends the DELETE;
 *  - a QA engineer who opens the URL reads the table without Add or actions, and writes nothing.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock `NOW` = 2026-09-18 12:00 UTC, a Friday). World:
 * `fixtures-insights-cadence.ts` `insightsCadenceWorld` (the settings shared set: RUNS + LAYOUT).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  insightsCadenceWorld,
  pausedCadence,
  worldCadence,
  type InsightsCadenceWorld,
  type InsightsCadenceWorldOptions,
} from '../visual/production/fixtures-insights-cadence'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } })

const BASE = `/api/v1/projects/${PROJECT_ID}/run-cadences`
const h1 = (page: Page) => page.getByRole('heading', { level: 1, name: 'Expected runs', exact: true })
const table = (page: Page) => page.locator('table[data-expected-runs]')
const rows = (page: Page) => page.locator('tr[data-cadence-id]')
const addForm = (page: Page) => page.getByRole('region', { name: 'Add expected cadence' })
const writesTo = (world: InsightsCadenceWorld, line: string) => world.writes.filter((w) => w.line === line).map((w) => w.body)
const allWrites = (world: InsightsCadenceWorld) => world.writes.filter((w) => !w.line.startsWith('GET '))

async function open(page: Page, options: InsightsCadenceWorldOptions = {}) {
  const world = insightsCadenceWorld({ page: 'settings', ...options })
  const opened = await openRollout(page, '/settings/expected-runs', { handlers: world.handlers, ready: h1, user: world.user })
  await networkQuiet(page, opened.api)
  return { world, ...opened }
}

/** The select's free width beside its longest option, px (its own font, padding and arrow allowance). */
async function zoneSelectSlack(select: Locator): Promise<number> {
  return select.evaluate((el: HTMLSelectElement) => {
    const style = getComputedStyle(el)
    const probe = document.createElement('span')
    probe.style.font = style.font
    probe.style.whiteSpace = 'nowrap'
    probe.style.position = 'absolute'
    probe.style.visibility = 'hidden'
    document.body.appendChild(probe)
    let widest = 0
    for (const option of Array.from(el.options)) {
      probe.textContent = option.text
      widest = Math.max(widest, probe.getBoundingClientRect().width)
    }
    probe.remove()
    const ARROW = 24
    const inner = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - ARROW
    return Math.floor(inner - widest)
  })
}

test('the empty state → Add a weekday suite cadence → the exact body → the row', async ({ page }) => {
  const { world, api, errors } = await open(page)
  await expect(page.locator('[data-expected-runs-empty]')).toContainText(
    'No expected cadence configured. Add one so a missing run is noticed.')
  await page.getByRole('button', { name: 'Add expected cadence' }).first().click()
  const form = addForm(page)
  await form.getByLabel('Runs of suite').check()
  await form.getByLabel('Suite name').fill('nightly-regression')
  await form.getByRole('button', { name: 'Weekdays' }).click()
  await form.getByLabel('Expected by').fill('06:00')
  await form.getByLabel('Time zone').selectOption('Europe/Berlin')
  await expectMainFits(page, 'the Add form')
  expect(await zoneSelectSlack(form.getByLabel('Time zone')), 'the zone select beside its longest name').toBeGreaterThanOrEqual(40)
  await form.getByRole('button', { name: 'Save' }).click()
  await expect(addForm(page)).toHaveCount(0)
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText('nightly-regression')
  await expect(rows(page).first()).toContainText('Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min')
  await expect(rows(page).first()).toContainText('Active')
  expect(writesTo(world, `POST ${BASE}`)).toEqual([{
    suite_name: 'nightly-regression', mode: 'weekly', weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
    expected_by: '06:00', timezone: 'Europe/Berlin', grace_minutes: 60,
  }])
  await expectMainFits(page, 'the table')
  const fits = await table(page).evaluate((el: HTMLElement) => el.scrollWidth <= (el.parentElement?.clientWidth ?? 0))
  expect(fits, 'the table fits its panel').toBe(true)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a 409 and the server’s cron refusal read inline in the form, once', async ({ page }) => {
  const { world, api, errors } = await open(page, { refusals: { create: { status: 409, code: 'duplicate_scope' } } })
  await page.getByRole('button', { name: 'Add expected cadence' }).first().click()
  const form = addForm(page)
  await form.getByRole('button', { name: 'Every day' }).click()
  await form.getByLabel('Expected by').fill('02:30')
  await form.getByRole('button', { name: 'Save' }).click()
  const alert = form.getByRole('alert')
  await expect(alert).toHaveText('This project already has an expected cadence for that suite.')
  delete world.state.refusals.create
  await form.getByLabel('Cron (advanced)').check()
  await expect(form.getByRole('button', { name: 'Mon' })).toHaveCount(0)
  await form.getByLabel('Cron expression').fill('0 6 1 * *')
  await form.getByRole('button', { name: 'Save' }).click()
  await expect(alert).toHaveText('Only minute, hour and day-of-week are supported; day-of-month and month must be *.')
  await expect(page.getByText('Only minute, hour and day-of-week', { exact: false })).toHaveCount(1)
  expect(writesTo(world, `POST ${BASE}`)).toHaveLength(2)
  expect(writesTo(world, `POST ${BASE}`)[1]).toMatchObject({ mode: 'cron', cron: '0 6 1 * *' })
  await expect(rows(page)).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Pause needs a reason, then posts until and the reason; Resume; Delete asks first', async ({ page }) => {
  const cadence = worldCadence()
  const { world, api, errors } = await open(page, { cadences: [cadence] })
  const row = rows(page).first()
  await row.getByRole('button', { name: /^Pause / }).click()
  const pause = page.locator(`[data-cadence-pause="${cadence.id}"]`)
  await pause.getByLabel('Pause until').fill('2026-09-25T23:59')
  await pause.getByRole('button', { name: 'Pause expectations' }).click()
  await expect(pause.getByRole('alert')).toHaveText('Say why the expectation is paused.')
  await pause.getByLabel('Reason').fill('release freeze')
  await pause.getByRole('button', { name: 'Pause expectations' }).click()
  await expect(pause).toHaveCount(0)
  await expect(row.locator('[data-cadence-state="paused"]')).toContainText('Paused')
  await expect(row.locator('[data-cadence-state="paused"]')).toContainText('until Fri Sep 25, 23:59')
  await expect(row.locator('[data-cadence-state="paused"]')).toContainText('release freeze')
  expect(writesTo(world, `POST ${BASE}/${cadence.id}/pause`)).toEqual([{ until: '2026-09-25T23:59:00+00:00', reason: 'release freeze' }])

  await row.getByRole('button', { name: /^Resume / }).click()
  await expect(row.locator('[data-cadence-state="active"]')).toHaveText('Active')
  expect(writesTo(world, `POST ${BASE}/${cadence.id}/resume`)).toEqual([{}])

  await row.getByRole('button', { name: /^Delete / }).click()
  expect(writesTo(world, `DELETE ${BASE}/${cadence.id}`)).toEqual([])
  const confirm = page.locator('[data-inline-confirm]')
  await expect(confirm).toContainText('Remove this expectation? Its window history goes with it.')
  await confirm.getByRole('button', { name: 'Delete' }).click()
  await expect(rows(page)).toHaveCount(0)
  expect(writesTo(world, `DELETE ${BASE}/${cadence.id}`)).toEqual([{}])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA engineer who opens the URL reads the table without actions', async ({ page }) => {
  const cadences = [worldCadence(), pausedCadence(worldCadence({ suite_name: 'smoke-suite' }))]
  const { world, api, errors } = await open(page, { role: 'QA_ENGINEER', cadences })
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Add expected cadence' })).toHaveCount(0)
  await expect(table(page).getByRole('columnheader', { name: 'Actions' })).toHaveCount(0)
  await expect(table(page).getByRole('button')).toHaveCount(0)
  await expect(table(page).locator('[data-cadence-state="paused"]')).toContainText('Paused')
  expect(allWrites(world)).toEqual([])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
