/**
 * W22a ACT-05 on the release page, hermetic (`docs/deferred-scope-2026-10-13/work/W22a-contract.md` §4.1, §6.3),
 * at 1280 and 1440, on release 2026.10 opened beside the list (`?open=`):
 *
 *  - a QA lead sees the strip's `Place hold` only (the release page makes NO hold-list request — hold state rides
 *    the release detail and the pack list); `Place hold` sends `{entity_type: "release", entity_id, reason}` and the
 *    detail re-read shows `On hold` + `Held: Audit 2026-Q4`, and the pack row its `On hold` chip; the strip's
 *    header keeps ≥ 40 px for CI's wider fonts and nothing scrolls sideways;
 *  - an ADMIN on a held release sees the pack's `Retire` / `Delete` disabled with `title="Held: Audit 2026-Q4"`;
 *  - with the hold scripted off, `Retire` sends `{reason, confirmation_id}`, and then `Delete` asks inline and sends
 *    `DELETE`; a hold that lands after the page loaded makes the server answer 409
 *    `{"detail": {"blockers": ["Held: …"]}}`, listed inline under the row.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-retention.ts` `retentionHoldsWorld`
 * on `RELEASES_PAGE`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { expectMainFits, inPageLine, ownOverflow } from '../lib/inPageGeometry'
import { cardByHeading } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { RELEASES_PAGE } from '../visual/production/fixtures-pages'
import {
  AUDIT_REASON,
  HOLD_PACK_ID,
  HOLD_RELEASE_ID,
  retentionHoldsWorld,
  worldHold,
  type RetentionHoldsWorldOptions,
} from '../visual/production/fixtures-retention'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const HOLDS_PATH = `/api/v1/projects/${PROJECT_ID}/retention-holds`
const RETIRE_PATH = `/api/v1/compliance-packs/${HOLD_PACK_ID}/retire`
const PACK_PATH = `/api/v1/compliance-packs/${HOLD_PACK_ID}`
const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const pane = (page: Page) => page.getByRole('complementary', { name: '2026.10', exact: true })
const strip = (page: Page) => pane(page).locator('[data-release-hold]')
const packPanel = (page: Page): Locator => cardByHeading(pane(page), 'Compliance Pack')

async function openRelease(page: Page, options: RetentionHoldsWorldOptions) {
  const world = retentionHoldsWorld({ base: RELEASES_PAGE, ...options })
  const opened = await openRollout(page, `/releases?open=${HOLD_RELEASE_ID}`, {
    handlers: world.handlers, ready: releasesReady, user: world.user,
  })
  await expect(packPanel(page)).toBeVisible()
  await networkQuiet(page, opened.api)
  return { ...opened, world }
}

/** A row's free width: the row minus its children and gaps (an auto margin is free). */
async function rowSlack(row: Locator): Promise<number> {
  return row.evaluate((el: HTMLElement) => {
    const kids = Array.from(el.children) as HTMLElement[]
    const gap = parseFloat(getComputedStyle(el).columnGap) || 0
    const used = kids.reduce((sum, kid) => sum + kid.getBoundingClientRect().width, 0) + gap * Math.max(0, kids.length - 1)
    return Math.round(el.clientWidth - used)
  })
}

for (const [width, height] of [[1440, 900], [1280, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`a QA lead places a release hold from the strip — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const { api, errors, world } = await openRelease(page, { role: 'QA_LEAD' })
      await expect(strip(page)).toHaveAttribute('data-release-hold', 'none')
      await expect(strip(page).getByRole('button')).toHaveText(['Place hold'])
      expect(requestsTo(api, HOLDS_PATH), 'the release page reads no hold list').toEqual([])

      await strip(page).getByRole('button', { name: 'Place hold' }).click()
      const form = strip(page).locator('[data-place-hold-form="release"]')
      await form.getByLabel('Reason').fill(AUDIT_REASON)
      await form.getByRole('button', { name: 'Place hold' }).click()
      await expect(form).toHaveCount(0)
      expect(world.writes.filter((w) => w.line === `POST ${HOLDS_PATH}`).map((w) => w.body)).toEqual([
        { entity_type: 'release', entity_id: HOLD_RELEASE_ID, reason: AUDIT_REASON },
      ])

      await expect(strip(page)).toHaveAttribute('data-release-hold', 'held')
      await expect(strip(page).locator('[data-release-hold-chip]')).toHaveText('On hold')
      await expect(strip(page)).toContainText(`Held: ${AUDIT_REASON}`)
      await expect(packPanel(page).locator('[data-pack-hold]')).toHaveText('On hold')

      const slack = await rowSlack(strip(page).locator('[data-release-hold-header]'))
      inPageLine('/releases hold strip header', width, { slack })
      expect(slack, 'the strip header keeps 40 px for wider fonts').toBeGreaterThanOrEqual(40)
      expect(await ownOverflow(strip(page))).toBe(0)
      expect(await ownOverflow(packPanel(page))).toBe(0)
      await expectMainFits(page, `/releases hold strip @${width}`)
      await networkQuiet(page, api)
      expect(requestsTo(api, HOLDS_PATH), 'the place, and no hold-list read after it').toEqual([`POST ${HOLDS_PATH}`])
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })

    test(`an ADMIN sees a held pack's Retire / Delete disabled with the sentence — ${width}`, async ({ page }) => {
      const { api, errors } = await openRelease(page, { role: 'ADMIN', holds: [worldHold()] })
      await expect(strip(page)).toContainText(`Held: ${AUDIT_REASON}`)
      for (const name of ['Retire', 'Delete']) {
        const button = packPanel(page).getByRole('button', { name, exact: true })
        await expect(button).toBeDisabled()
        await expect(button).toHaveAttribute('title', `Held: ${AUDIT_REASON}`)
      }
      await expect(packPanel(page).getByRole('button', { name: 'Hold', exact: true })).toBeEnabled()
      expect(await ownOverflow(packPanel(page))).toBe(0)
      await expectMainFits(page, `/releases held pack @${width}`)
      assertHermetic(api, errors)
    })
  })
}

test('with the hold off, Retire sends {reason, confirmation_id}, then Delete asks inline and sends DELETE', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  watchNativeDialogs(page)
  const { api, errors, world } = await openRelease(page, { role: 'ADMIN' })
  const panel = packPanel(page)
  await expect(panel.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled()   // the window is open

  await panel.getByRole('button', { name: 'Retire', exact: true }).click()
  const form = panel.locator('[data-pack-retire-form]')
  await form.getByLabel('Reason').fill('Superseded sentinel')
  await expect(form.getByRole('button', { name: 'Retire pack' })).toBeDisabled()
  await form.getByLabel('Type the pack id to confirm').fill(HOLD_PACK_ID)
  await form.getByRole('button', { name: 'Retire pack' }).click()
  await expect(form).toHaveCount(0)
  expect(world.writes.filter((w) => w.line === `POST ${RETIRE_PATH}`).map((w) => w.body)).toEqual([
    { reason: 'Superseded sentinel', confirmation_id: HOLD_PACK_ID },
  ])

  const remove = panel.getByRole('button', { name: 'Delete', exact: true })
  await expect(remove).toBeEnabled()   // the re-read pack's window ends now
  await remove.click()
  await expect(panel.getByText('Delete this compliance pack and its ZIP? This cannot be undone.')).toBeVisible()
  expect(world.writes.filter((w) => w.line === `DELETE ${PACK_PATH}`)).toEqual([])
  await panel.getByRole('button', { name: 'Delete pack' }).click()
  await expect(panel.getByText('No compliance packs generated yet for this release.')).toBeVisible()
  expect(world.writes.filter((w) => w.line === `DELETE ${PACK_PATH}`)).toHaveLength(1)
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('a hold placed after the page loaded: the 409 blockers are listed inline', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const { api, errors, world } = await openRelease(page, { role: 'ADMIN' })
  const panel = packPanel(page)
  await panel.getByRole('button', { name: 'Retire', exact: true }).click()
  const form = panel.locator('[data-pack-retire-form]')
  await form.getByLabel('Reason').fill('Superseded sentinel')
  await form.getByLabel('Type the pack id to confirm').fill(HOLD_PACK_ID)
  world.state.holds.push(worldHold())   // another admin's hold lands now
  await form.getByRole('button', { name: 'Retire pack' }).click()
  const blockers = panel.locator('[data-pack-blockers]')
  await expect(blockers).toHaveText(`Held: ${AUDIT_REASON}`)
  await expect(form).toHaveCount(0)
  assertHermetic(api, errors)
})
