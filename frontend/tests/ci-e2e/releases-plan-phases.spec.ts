/**
 * W11 REL-05 / REL-07 on `/releases`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.1, §2.2,
 * §4.1), at 1280 and 1440:
 *
 *  - as QA LEAD the planning release 2026.12 (no phases yet) offers **Copy phases from…** and **Draft from a PRD…**
 *    in its "No phases scoped yet" callout, on one line with the callout's text (§4.1: text + two `btn-sm` fit the
 *    card without a pane); a click opens the release in the pane BESIDE the list (never a dialog) with the form open
 *    at the top of its Release Phases, and does not open the card's own click;
 *  - Copy: the `Copy phases` form lists the project's other releases that have phases (`2026.11 · 3 phases`,
 *    `2026.10 · 4 phases`), previews the chosen one's phases, takes **First phase starts**, and **Copy phases** sends
 *    exactly `{ from_release_id, shift_to }` (the day at 00:00 UTC): the detail then lists the four phases, all
 *    Pending, every planned date moved 63 days, and one toast `Copied 4 phases from 2026.10.`;
 *  - Draft: the `Draft phases from a PRD` form posts the pasted text as multipart `text` only (nothing in the URL,
 *    nothing in localStorage), shows the hermetic rules draft (`Drafted by rules — the deployment is offline
 *    (AI_OFFLINE_MODE)`) as three typed, editable rows; a renamed row and **Create 3 phases** send ONE bulk request
 *    whose phases are the edited rows and whose `source` is the draft's; the phases are listed, toast
 *    `Created 3 phases from the draft.`;
 *  - a QA ENGINEER never reaches the controls: `/releases` is a management route and sends them to `/overview`, and
 *    nothing of it is asked or written;
 *  - `#main-content` never scrolls sideways; the callout row's numbers are printed (`INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { assertHermetic, freezeClock, mockApi, seedSession, stillMotion, watchPageErrors } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import {
  CLONE_SHIFT_DAYS,
  CLONE_FIRST_START,
  DRAFT_FALLBACK,
  DRAFTED_PHASES,
  PRD_TEXT,
  QA_ENGINEER,
  QA_LEAD,
  RELEASE_PLANNING_ID,
  RELEASE_SHIPPED_ID,
  releasePlanningWorld,
  SOURCE_PHASES,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const DAY_MS = 86_400_000
const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace('.', '\\.')} `) })
const pane = (page: Page, name: string) => page.getByRole('complementary', { name, exact: true })
const toast = (page: Page, text: string) => page.getByRole('status').filter({ hasText: text })
// `has` is resolved INSIDE each candidate, so its locator starts at the page (as `sectionFrame` does).
const phasesCard = (scope: Locator) => scope.locator('.card').filter({ has: scope.page().getByRole('heading', { name: 'Release Phases', exact: true }) })
const PLAN_BUTTONS = ['Copy phases from…', 'Draft from a PRD…'] as const

/** The callout's two buttons sit on one line with its text: their boxes and the room left on the row. */
async function calloutLine(page: Page, width: number) {
  const planned = card(page, '2026.12')
  const text = planned.getByText('No phases scoped yet')
  const copy = planned.getByRole('button', { name: PLAN_BUTTONS[0], exact: true })
  const draft = planned.getByRole('button', { name: PLAN_BUTTONS[1], exact: true })
  const sub = planned.getByText("Add 2026.12's phases in its detail.")
  const [t, s, c, d] = [await boxOf(text), await boxOf(sub), await boxOf(copy), await boxOf(draft)]
  // The room left on the line: the gap between the text's widest line and the first button, less the row's 12 px gap
  // (what CI's wider DejaVu eats before the buttons wrap under the text).
  const numbers = { text: t, sub: s, copy: c, draft: d, slack: c.left - Math.max(t.right, s.right) - 12 }
  inPageLine('/releases planning callout', width, numbers)
  expect(Math.abs(c.top - d.top), `@${width}: the two buttons share one line`).toBeLessThanOrEqual(1)
  expect(c.top, `@${width}: the buttons sit beside the text, not under it`).toBeLessThan(t.top + 24)
  expect(c.left, `@${width}: right of the text`).toBeGreaterThan(t.right)
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`Copy phases from…: the pane opens with the form, the POST body, four Pending phases 63 days later — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releasePlanningWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      const planned = card(page, '2026.12')
      await expect(planned.getByText('No phases scoped yet')).toBeVisible()
      for (const name of PLAN_BUTTONS) await expect(planned.getByRole('button', { name, exact: true })).toBeVisible()
      // The released and in-progress releases have phases: their cards offer neither.
      await expect(page.getByRole('button', { name: PLAN_BUTTONS[0], exact: true }), 'only the release with no phases offers it').toHaveCount(1)
      await calloutLine(page, width)
      await expectMainFits(page, `callout @${width}`)

      await planned.getByRole('button', { name: PLAN_BUTTONS[0], exact: true }).click()
      const detail = pane(page, '2026.12')
      await expect(detail, 'the release opens beside the list').toBeVisible()
      await expect(page).toHaveURL(new RegExp(`[?&]open=${RELEASE_PLANNING_ID}(&|$)`))
      const form = detail.getByRole('region', { name: 'Copy phases', exact: true })
      await expect(form, 'the form is in the pane, at the top of Release Phases').toBeVisible()
      await expectNoOverlay(page)

      // The project's other releases that have phases, the active / in-progress one first.
      const source = form.getByLabel('From release')
      const options = await source.locator('option').evaluateAll((els) =>
        els.filter((o) => (o as HTMLOptionElement).value !== '').map((o) => (o.textContent ?? '').trim()),
      )
      expect(options).toEqual(['2026.11 · 3 phases', '2026.10 · 4 phases'])
      await source.selectOption({ label: '2026.10 · 4 phases' })
      // The preview names each source phase (its full name in `title`, beside its type and days).
      for (const p of SOURCE_PHASES) await expect(form.getByTitle(p.name, { exact: true })).toBeVisible()
      const firstStart = form.getByLabel('First phase starts')
      await expect(firstStart, 'offered: the source phases have dates').toBeVisible()
      await firstStart.fill(CLONE_FIRST_START)
      await expectMainFits(page, `clone form @${width}`)

      await form.getByRole('button', { name: 'Copy phases', exact: true }).click()
      await expect(toast(page, 'Copied 4 phases from 2026.10.')).toBeVisible()
      await expect(form, 'the form closes once copied').toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes.filter((w) => w.line.endsWith('/phases/clone'))).toEqual([
        {
          line: `POST /api/v1/releases/${RELEASE_PLANNING_ID}/phases/clone`,
          body: { from_release_id: RELEASE_SHIPPED_ID, shift_to: expect.stringMatching(new RegExp(`^${CLONE_FIRST_START}T00:00:00(\\.000)?Z$`)) },
        },
      ])
      // The detail re-read lists the copies: in order, all Pending, every date moved by the same 63 days.
      const phases = phasesCard(detail)
      // Each phase's own status control names it; every copy is Pending.
      for (const p of SOURCE_PHASES) await expect(phases.getByRole('combobox', { name: `Status of phase ${p.name}`, exact: true })).toHaveValue('pending')
      await expect(phases.getByRole('combobox', { name: /^Status of phase / })).toHaveCount(SOURCE_PHASES.length)
      const copied = world.world.rows.find((r) => r.id === RELEASE_PLANNING_ID)?.phases ?? []
      expect(copied.map((p) => [p.name, p.status, p.planned_start])).toEqual(
        SOURCE_PHASES.map((p) => [p.name, 'pending', new Date(Date.parse(`${p.start}T00:00:00Z`) + CLONE_SHIFT_DAYS * DAY_MS).toISOString()]),
      )
      expect(requestsTo(api, `/api/v1/releases/${RELEASE_PLANNING_ID}`).length, 'the detail is read again').toBeGreaterThan(1)
      await expectNoOverlay(page)
      await expectMainFits(page, `after the copy @${width}`)
      assertHermetic(api, errors)
    })

    test(`Draft from a PRD…: multipart text only, the rules draft, a renamed row, one bulk create — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releasePlanningWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      await card(page, '2026.12').getByRole('button', { name: PLAN_BUTTONS[1], exact: true }).click()
      const detail = pane(page, '2026.12')
      const form = detail.getByRole('region', { name: 'Draft phases from a PRD', exact: true })
      await expect(form).toBeVisible()
      await expectNoOverlay(page)

      await form.getByLabel('PRD text').fill(PRD_TEXT)
      await form.getByRole('button', { name: 'Draft phases', exact: true }).click()
      await expect(form.getByText(`Drafted by rules — ${DRAFT_FALLBACK.text}`, { exact: true })).toBeVisible()
      // One multipart request: the text as `text`, no file part, nothing in the URL.
      expect(world.drafts).toHaveLength(1)
      expect(world.drafts[0].target).toBe(`/api/v1/releases/${RELEASE_PLANNING_ID}/phases/generate`)
      expect(world.drafts[0].contentType).toMatch(/^multipart\/form-data; boundary=/)
      expect(world.drafts[0].parts.map((p) => [p.name, p.filename ?? null])).toEqual([['text', null]])
      // multipart/form-data sends a text field's line breaks as CRLF (the server folds them back to LF).
      expect(world.drafts[0].parts[0].value.replace(/\r\n/g, '\n')).toBe(PRD_TEXT)

      // Three typed, editable rows, in the draft's order.
      const names = form.getByRole('textbox', { name: /^Name/ })
      await expect(names).toHaveCount(DRAFTED_PHASES.length)
      for (const [i, phase] of DRAFTED_PHASES.entries()) await expect(names.nth(i)).toHaveValue(phase.name)
      const types = form.getByRole('combobox', { name: /^Type/ })
      await expect(types).toHaveCount(DRAFTED_PHASES.length)
      for (const [i, phase] of DRAFTED_PHASES.entries()) await expect(types.nth(i)).toHaveValue(phase.phase_type)
      await names.first().fill('Smoke and sanity')
      await expectMainFits(page, `draft preview @${width}`)

      await form.getByRole('button', { name: 'Create 3 phases', exact: true }).click()
      await expect(toast(page, 'Created 3 phases from the draft.')).toBeVisible()
      await networkQuiet(page, api)
      const bulk = world.writes.filter((w) => w.line.endsWith('/phases/bulk'))
      expect(bulk.map((w) => w.line)).toEqual([`POST /api/v1/releases/${RELEASE_PLANNING_ID}/phases/bulk`])
      const body = bulk[0].body as { phases: Array<Record<string, unknown>>; source: string; model_name?: string | null }
      expect(body.source).toBe('rules')
      expect(body.model_name ?? null).toBeNull()
      expect(body.phases.map((p) => [p.name, p.phase_type])).toEqual([
        ['Smoke and sanity', 'qa_testing'],
        ['Regression', 'qa_testing'],
        ['UAT', 'uat'],
      ])
      const phases = phasesCard(detail)
      for (const name of ['Smoke and sanity', 'Regression', 'UAT']) {
        await expect(phases.getByRole('combobox', { name: `Status of phase ${name}`, exact: true })).toHaveValue('pending')
      }
      // The PRD never left the form: not in a URL, not in the browser's storage.
      expect(api.seen.filter((line) => line.includes('Checkout%20v2') || line.includes('Smoke'))).toEqual([])
      const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))
      expect(stored.includes('Checkout v2 test plan'), 'the PRD is not in storage').toBe(false)
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })
  })
}

test('a QA ENGINEER never reaches the planning controls: /releases sends them to /overview, nothing of it is asked', async ({ page }) => {
  const world = releasePlanningWorld()
  const errors = watchPageErrors(page)
  await seedSession(page, { theme: 'signal', user: QA_ENGINEER, projectId: PROJECT_ID })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, QA_ENGINEER, [...world.handlers.filter(([, , verb]) => verb && verb !== 'GET'), ...OVERVIEW_ON])
  await page.setViewportSize({ width: 1280, height: 1000 })
  await page.goto('/releases')
  await expect(page).toHaveURL(/\/overview(\?|$)/)
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible({ timeout: 20_000 })
  for (const name of PLAN_BUTTONS) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(world.drafts).toEqual([])
  assertHermetic(api, errors)
})
