/**
 * W22a ACT-05 / ACT-04 on `/settings/retention`, hermetic (`docs/deferred-scope-2026-10-13/work/W22a-contract.md`
 * §4.1, §6.3), at 1280 and 1440:
 *
 *  - ADMIN: the Holds card lists the world's live holds with the answer's `counts` (`Live 2` · `Expired 1` ·
 *    `Released 1`); `Place hold` sends `{entity_type: "project", reason, expires_at}` and the new row appears; a
 *    scripted 422 shows its sentence in the form; `Release` sends `{reason}`; the preview shows `Kept by hold: …`, the
 *    floor line and `3 runs lose their baseline link`; the floor field's PUT carries `keep_last_runs` alone; the
 *    criteria preview lists a held run with `Held: …` first, and the floor note;
 *  - the header row keeps ≥ 40 px of slack (CI's DejaVu is wider than Windows' fonts), nothing scrolls sideways;
 *  - a QA lead gets the refusal page and makes NO holds request.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-retention.ts` `retentionHoldsWorld`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectMainFits, inPageLine, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  AUDIT_REASON,
  HOLD_PACK_ID,
  HOLD_RELEASE_ID,
  HOLD_RUN_ID,
  LITIGATION_REASON,
  retentionHoldsWorld,
  worldHold,
  type RetentionHoldsWorldOptions,
} from '../visual/production/fixtures-retention'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const HOLDS_PATH = `/api/v1/projects/${PROJECT_ID}/retention-holds`
const FLOOR_NOTE = 'Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only.'
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const card = (page: Page) => page.locator('[data-retention-holds-card]')
const rows = (page: Page) => card(page).locator('li[data-retention-hold]')

/** Two live holds (a release hold — Audit 2026-Q4, it pins `HOLD_RUN_ID` — and a pack hold), an expired and a released one. */
function standardWorld(overrides: RetentionHoldsWorldOptions = {}) {
  const audit = worldHold({ entity_label: 'release 2026.10', placed_at: '2026-09-16T12:00:00Z' })
  const pack = worldHold({ entity_type: 'compliance_pack', entity_id: HOLD_PACK_ID, entity_label: 'pack of 2026-09-02', reason: LITIGATION_REASON, placed_at: '2026-09-17T12:00:00Z' })
  const expired = worldHold({ entity_type: 'project', entity_id: null, entity_label: 'the whole project', reason: 'Quarter close', expires_at: new Date(Date.parse('2026-09-18T12:00:00Z') - 86_400_000).toISOString() })
  const released = worldHold({ reason: 'Old matter', released_at: '2026-09-10T09:00:00Z', released_by_name: 'Admin', release_reason: 'Matter closed' })
  const world = retentionHoldsWorld({
    role: 'ADMIN',
    holds: [audit, pack, expired, released],
    keptByHold: { [audit.id]: { runs_kept: 5 } },
    linkedRuns: { [HOLD_RELEASE_ID]: [HOLD_RUN_ID] },
    ...overrides,
  })
  return { world, audit }
}

async function openAsAdmin(page: Page, world: ReturnType<typeof retentionHoldsWorld>) {
  const opened = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: world.user })
  await expect(card(page)).toBeVisible()
  return opened
}

/** The header row's free width: the row minus its children and gaps (the Place hold button's auto margin is free). */
async function headerSlack(header: Locator): Promise<number> {
  return header.evaluate((el: HTMLElement) => {
    const kids = Array.from(el.children) as HTMLElement[]
    const gap = parseFloat(getComputedStyle(el).columnGap) || 0
    const used = kids.reduce((sum, kid) => sum + kid.getBoundingClientRect().width, 0) + gap * (kids.length - 1)
    return Math.round(el.clientWidth - used)
  })
}

for (const [width, height] of [[1440, 900], [1280, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`the card lists the live holds with the answer's counts — ${width}`, async ({ page }) => {
      const { world } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      // placed_at DESC: the pack hold (placed later) first.
      await expect(rows(page)).toHaveCount(2)
      await expect(rows(page).nth(0)).toContainText(`Held: ${LITIGATION_REASON}`)
      await expect(rows(page).nth(0).getByRole('link')).toHaveCount(0)
      await expect(rows(page).nth(1)).toContainText(`Held: ${AUDIT_REASON}`)
      await expect(rows(page).nth(1).getByRole('link', { name: 'release 2026.10' })).toHaveAttribute('href', `/releases/${HOLD_RELEASE_ID}`)
      await expect(card(page).getByRole('radio')).toHaveText(['Live 2', 'Expired 1', 'Released 1'])
      await card(page).getByRole('radio', { name: /Released/ }).click()
      await expect(rows(page).first()).toContainText('Released by Admin on Sep 10, 2026: Matter closed')

      const header = card(page).locator('[data-retention-holds-header]')
      const slack = await headerSlack(header)
      inPageLine('/settings/retention holds header', width, { slack })
      expect(slack, 'the header row keeps 40 px for wider fonts').toBeGreaterThanOrEqual(40)
      expect(await ownOverflow(card(page))).toBe(0)
      await expectMainFits(page, `/settings/retention @${width}`)
      await networkQuiet(page, api)
      const states = new Set(requestsTo(api, HOLDS_PATH).map((line) => new URLSearchParams(line.split('?')[1] ?? '').get('state')))
      expect([...states].sort(), 'one read per selected state').toEqual(['live', 'released'])
      assertHermetic(api, errors)
    })

    test(`Place hold sends a project hold and the new row appears — ${width}`, async ({ page }) => {
      const { world } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      await card(page).getByRole('button', { name: 'Place hold' }).click()
      const form = card(page).locator('[data-retention-hold-form]')
      await expect(form).toContainText('Whole project (')
      await form.getByLabel(/Reason/).fill('Litigation sentinel')
      await form.getByLabel(/Expires/).fill('2026-12-31')
      await form.getByRole('button', { name: 'Place hold' }).click()
      await expect(form).toHaveCount(0)
      const placed = world.writes.filter((w) => w.line === `POST ${HOLDS_PATH}`)
      expect(placed.map((w) => w.body)).toEqual([{ entity_type: 'project', reason: 'Litigation sentinel', expires_at: '2026-12-31T23:59:59.000Z' }])
      await expect(rows(page).filter({ hasText: 'Held: Litigation sentinel' })).toHaveCount(1)
      await expect(card(page).getByRole('radio').first()).toHaveText('Live 3')
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`a scripted 422 is shown inline in the form — ${width}`, async ({ page }) => {
      const { world } = standardWorld({ placeError: { status: 422, code: 'expiry_too_far' } })
      const { api, errors } = await openAsAdmin(page, world)
      await card(page).getByRole('button', { name: 'Place hold' }).click()
      const form = card(page).locator('[data-retention-hold-form]')
      await form.getByLabel(/Reason/).fill('Litigation sentinel')
      await form.getByRole('button', { name: 'Place hold' }).click()
      await expect(form.getByRole('alert')).toHaveText('expires_at may be at most 3650 days ahead.')
      await expect(rows(page)).toHaveCount(2)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`Release sends {reason} and the hold leaves the live list — ${width}`, async ({ page }) => {
      const { world, audit } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      const row = card(page).locator(`li[data-retention-hold="${audit.id}"]`)
      await row.getByRole('button', { name: 'Release' }).click()
      await row.getByLabel('Release reason').fill('Audit closed')
      await row.getByRole('button', { name: 'Release hold' }).click()
      await expect(row).toHaveCount(0)
      expect(world.writes.filter((w) => w.line.endsWith('/release')).map((w) => [w.line, w.body])).toEqual([
        [`POST ${HOLDS_PATH}/${audit.id}/release`, { reason: 'Audit closed' }],
      ])
      await expect(card(page).getByRole('radio')).toHaveText(['Live 1', 'Expired 1', 'Released 2'])
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`the preview says what holds and the floor keep, and the links a purge breaks — ${width}`, async ({ page }) => {
      const { world } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      await page.getByRole('button', { name: 'Preview purge' }).click()
      const kept = page.locator('[data-retention-preview-kept]')
      await expect(kept).toBeVisible()
      await expect(kept.getByText(`Kept by hold: ${AUDIT_REASON} — 5 runs, 0 packs`, { exact: true })).toBeVisible()
      await expect(kept.getByText(`Kept by hold: ${LITIGATION_REASON} — 0 runs, 0 packs`, { exact: true })).toBeVisible()
      await expect(kept.getByText('Kept by the floor: 10 runs (the newest 10, the latest passing run, the latest passing run of 2 suites)', { exact: true })).toBeVisible()
      await expect(kept.getByText('3 runs lose their baseline link', { exact: true })).toBeVisible()
      await expect(kept.locator('[data-kept-line]').last()).toHaveAttribute('data-kept-line', 'not-held-note')
      expect(await ownOverflow(kept)).toBe(0)
      await expectMainFits(page, `/settings/retention preview @${width}`)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`the floor field's PUT carries keep_last_runs alone — ${width}`, async ({ page }) => {
      const { world } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      const floor = page.locator('#retention-keep_last_runs')
      await expect(floor).toHaveValue('10')
      await floor.fill('25')
      await page.getByRole('button', { name: 'Save policy' }).click()
      await expect.poll(() => world.writes.filter((w) => w.line.startsWith('PUT ')).length).toBe(1)
      const put = world.writes.find((w) => w.line.startsWith('PUT '))
      expect(put?.line).toBe(`PUT /api/v1/projects/${PROJECT_ID}/retention-policy`)
      expect(put?.body).toEqual({ keep_last_runs: 25 })
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })

    test(`the criteria preview lists a held run with Held: first, and the floor note — ${width}`, async ({ page }) => {
      const { world } = standardWorld()
      const { api, errors } = await openAsAdmin(page, world)
      await page.getByLabel('Older than days').fill('30')
      await page.getByRole('button', { name: 'Preview', exact: true }).click()
      const preview = page.getByTestId('criteria-preview')
      const held = preview.getByTestId('preview-blocked').getByRole('listitem').filter({ hasText: HOLD_RUN_ID.slice(0, 8) })
      await expect(held).toContainText(`— Held: ${AUDIT_REASON}`)
      await expect(preview.locator('[data-criteria-floor-note]')).toHaveText(FLOOR_NOTE)
      await networkQuiet(page, api)
      assertHermetic(api, errors)
    })
  })
}

test('a QA lead gets the refusal page and makes no holds request', async ({ page }) => {
  const world = retentionHoldsWorld({ role: 'QA_LEAD', holds: [worldHold()] })
  const { api, errors } = await openRollout(page, '/settings/retention', {
    handlers: world.handlers,
    ready: (p) => p.getByText('Admin access required'),
    user: world.user,
  })
  await networkQuiet(page, api)
  await expect(card(page)).toHaveCount(0)
  expect(requestsTo(api, HOLDS_PATH)).toEqual([])
  assertHermetic(api, errors)
})
