/**
 * W22a baselines for the release page's holds (`docs/deferred-scope-2026-10-13/work/W22a-contract.md` §6.3, §6.5),
 * the two NEW regions, in signal, lab and paper (`THEMES_WITH_PAPER`), on release 2026.10 opened beside the list:
 *
 *  - `release-hold-strip`: the strip at the top of the detail of a held release (`On hold`, `Held: Audit 2026-Q4`,
 *    `release 2026.10 · No expiry`), as a QA lead (so `Place hold` shows too);
 *  - `compliance-pack-held`: the held pack's row as an ADMIN (the `On hold` chip; `Hold` enabled, `Retire` and
 *    `Delete` disabled).
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The tall
 * viewport (`PINNED_TALL`) keeps every region on screen with nothing scrolled, as `visualRegion` requires. World:
 * `production/fixtures-retention.ts` on `RELEASES_PAGE`. PNGs are rendered by CI only (`visual-baselines.yml`,
 * Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, cardByHeading, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RELEASES_PAGE } from './production/fixtures-pages'
import { AUDIT_REASON, HOLD_RELEASE_ID, retentionHoldsWorld, worldHold, type WorldRole } from './production/fixtures-retention'

test.use({ ...PINNED_TALL, timezoneId: 'UTC', locale: 'en-US' })

const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const pane = (page: Page) => page.getByRole('complementary', { name: '2026.10', exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

async function openHeld(page: Page, theme: (typeof THEMES_WITH_PAPER)[number], role: WorldRole) {
  const world = retentionHoldsWorld({ base: RELEASES_PAGE, role, holds: [worldHold({ placed_at: '2026-09-16T12:00:00Z' })] })
  const opened = await openRollout(page, `/releases?open=${HOLD_RELEASE_ID}`, {
    theme, handlers: world.handlers, ready: releasesReady, user: world.user,
  })
  const packs = cardByHeading(pane(page), 'Compliance Pack')
  await expect(packs.locator('[data-pack-hold]')).toHaveCount(1)
  await networkQuiet(page, opened.api)
  await blur(page)
  return { ...opened, packs }
}

for (const theme of THEMES_WITH_PAPER) {
  test(`a held release's strip, QA lead — ${theme}`, async ({ page }) => {
    const { api, errors } = await openHeld(page, theme, 'QA_LEAD')
    const strip = pane(page).locator('[data-release-hold="held"]')
    await expect(strip).toContainText(`Held: ${AUDIT_REASON}`)
    await visualRegion(page, 'release-hold-strip', theme, strip)
    assertHermetic(api, errors)
  })

  test(`a held compliance pack's row, ADMIN — ${theme}`, async ({ page }) => {
    const { api, errors, packs } = await openHeld(page, theme, 'ADMIN')
    const row = packs.locator('li').filter({ has: page.locator('[data-pack-hold]') })
    await expect(row.getByRole('button', { name: 'Retire', exact: true })).toBeDisabled()
    await expect(row.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled()
    await visualRegion(page, 'compliance-pack-held', theme, row)
    assertHermetic(api, errors)
  })
}
