/**
 * W23c AGT-10: Settings › AI › Review gate (`/settings/review-gate`), hermetic
 * (`docs/deferred-scope-2026-10-13/work/W23c-contract.md` §2.1, §4.1, §6.3), an ADMIN unless named:
 *
 *  - audit only, at 1280 × 900 and 1440 × 900: the badge, the server's sentence with the 30-day counts, every
 *    counted channel with its count, every gated channel; ONE GET on load; nothing pushes the page sideways and
 *    the date row keeps ≥ 40 px of slack;
 *  - a future local time → Set date → the PUT carries an ISO instant WITH an offset → "Audit only until …";
 *  - a past local time → the inline warning (no request yet) → Enforce now → "Enforced since …";
 *  - Clear date → `{"enforced_from": null}` → audit only again;
 *  - not measured → "Not measured: the audit log could not be read." and no digit in the state card;
 *  - a QA lead who opens the URL reads "Only administrators can change the review gate.", asks nothing, and has no
 *    `Review gate` item in the settings sub-nav (an admin has one).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock `NOW` = 2026-09-18 12:00 UTC; the browser in UTC, so
 * the offset sent is `+00:00`). World: `fixtures-review-gate-inline.ts` `reviewGateWorld` (G-A).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import {
  REVIEW_GATE_PATH,
  WORLD_CHANNELS,
  reviewGateWorld,
  worldGateNotMeasured,
  worldGateScheduled,
  type ReviewGateWorldOptions,
} from '../visual/production/fixtures-review-gate-inline'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } })

const AUDIT_ONLY =
  "Audit only — nothing is withheld. 12 runs' AI reports would have been withheld in the last 30 days (42 distribution attempts)."
const PAST_WARNING =
  'Setting a date in the past enforces the gate at once: CI reads PENDING_REVIEW for every unreviewed AI decision, and PDF and shared links of unreviewed reports are refused.'

const h1 = (page: Page) => page.getByRole('heading', { level: 1, name: 'Review gate', exact: true })
const stateCard = (page: Page) => page.locator('[data-review-gate-state]')
const dateCard = (page: Page) => page.locator('[data-review-gate-date]')
const puts = (world: ReturnType<typeof reviewGateWorld>) => world.writes.filter((w) => w.line === `PUT ${REVIEW_GATE_PATH}`).map((w) => w.body)

async function open(page: Page, options: ReviewGateWorldOptions = {}) {
  const world = reviewGateWorld(options)
  const opened = await openRollout(page, '/settings/review-gate', { handlers: world.handlers, ready: h1, user: world.user })
  await networkQuiet(page, opened.api)
  return { world, ...opened }
}

for (const width of [1280, 1440]) {
  test(`audit only at ${width} × 900: the counts, every channel, ONE read, nothing sideways`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { world, api, errors } = await open(page)
    await expect(stateCard(page)).toContainText('Audit only')
    await expect(stateCard(page)).toContainText(AUDIT_ONLY)
    const byChannel = stateCard(page).locator('[data-review-gate-by-channel] [data-channel]')
    await expect(byChannel).toHaveText([
      'PDF export of a run report20',
      'AI failure-kind labels in GitHub PR comments14',
      'AI summary e-mail and event-driven digests8',
    ])
    await expect(page.locator('[data-review-gate-channels] li')).toHaveText(WORLD_CHANNELS.map((c) => c.label))
    await expect(page.locator('[data-review-gate-channels]')).toContainText('Takes effect on every server within 30 seconds.')
    expect(world.state.reads, 'one GET on load').toBe(1)
    expect(puts(world)).toEqual([])
    await expectMainFits(page, `the review gate at ${width}`)
    // The date row (label · input · Set date) on one line, with room to spare on CI's fonts.
    const row = dateCard(page).locator('div.flex.flex-wrap').first()
    const slack = await row.evaluate((el: HTMLElement) => {
      const used = Array.from(el.children).reduce((sum, child) => sum + (child as HTMLElement).getBoundingClientRect().width, 0)
      const gaps = parseFloat(getComputedStyle(el).columnGap || '0') * Math.max(0, el.children.length - 1)
      return Math.floor(el.getBoundingClientRect().width - used - gaps)
    })
    expect(slack, 'the date row keeps 40 px of slack').toBeGreaterThanOrEqual(40)
    assertHermetic(api, errors)
  })
}

test('a future time saves at once with an offset and reads "Audit only until …"', async ({ page }) => {
  const { world, api, errors } = await open(page)
  await dateCard(page).getByLabel('Enforce from').fill('2026-09-25T09:00')
  await dateCard(page).getByRole('button', { name: 'Set date' }).click()
  await expect(stateCard(page)).toContainText(
    "Audit only until Sep 25, 2026, 09:00 (UTC+00:00) — then unreviewed AI reports are withheld. 12 runs' AI reports would have been withheld in the last 30 days (42 distribution attempts).",
  )
  expect(puts(world)).toEqual([{ enforced_from: '2026-09-25T09:00:00+00:00' }])
  await expect(page.locator('[data-inline-confirm]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a past time asks inline first; Enforce now sends it and reads "Enforced since …"', async ({ page }) => {
  const { world, api, errors } = await open(page)
  await dateCard(page).getByLabel('Enforce from').fill('2026-09-17T08:30')
  await dateCard(page).getByRole('button', { name: 'Set date' }).click()
  const confirm = dateCard(page).locator('[data-inline-confirm]')
  await expect(confirm).toContainText(PAST_WARNING)
  expect(puts(world), 'nothing is sent before the second click').toEqual([])
  await confirm.getByRole('button', { name: 'Enforce now' }).click()
  await expect(stateCard(page)).toContainText(
    'Enforced since Sep 17, 2026, 08:30 (UTC+00:00): unreviewed AI reports are withheld on the channels below.',
  )
  await expect(stateCard(page).locator('[data-review-gate-badge]')).toHaveText('Enforced')
  await expect(confirm).toHaveCount(0)
  expect(puts(world)).toEqual([{ enforced_from: '2026-09-17T08:30:00+00:00' }])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Clear date sends null: audit only again', async ({ page }) => {
  const { world, api, errors } = await open(page, { gate: worldGateScheduled() })
  await expect(stateCard(page)).toContainText('Audit only until Sep 22, 2026, 12:00 (UTC+00:00)')
  await dateCard(page).getByRole('button', { name: 'Clear date' }).click()
  await expect(stateCard(page)).toContainText(AUDIT_ONLY)
  await expect(dateCard(page).getByRole('button', { name: 'Clear date' })).toHaveCount(0)
  expect(puts(world)).toEqual([{ enforced_from: null }])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('not measured: the sentence and no digit in the state card', async ({ page }) => {
  const { api, errors } = await open(page, { gate: worldGateNotMeasured() })
  await expect(stateCard(page)).toContainText('Not measured: the audit log could not be read.')
  await expect(stateCard(page)).toContainText('Audit only — nothing is withheld.')
  expect(await stateCard(page).innerText()).not.toMatch(/\d/)
  assertHermetic(api, errors)
})

test('a QA lead reads who can change it, asks nothing, and has no Review gate item; an admin has one', async ({ page }) => {
  const { world, api, errors } = await open(page, { role: 'QA_LEAD' })
  await expect(page.getByText('Only administrators can change the review gate.')).toBeVisible()
  await expect(dateCard(page)).toHaveCount(0)
  await expect(page.locator('[data-settings-item="review-gate"]')).toHaveCount(0)
  expect(world.state.reads, 'no GET for a QA lead').toBe(0)
  assertHermetic(api, errors)
})

test('an admin has the Review gate item in Settings › AI, current on its page', async ({ page }) => {
  const { api, errors } = await open(page)
  const item = page.locator('[data-settings-group="ai"] [data-settings-item="review-gate"]')
  await expect(item).toHaveText('Review gate')
  await expect(item).toHaveAttribute('href', '/settings/review-gate')
  await expect(item).toHaveAttribute('aria-current', 'page')
  assertHermetic(api, errors)
})
