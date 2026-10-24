/**
 * UX redesign P5 item 5, on /releases, hermetic, IN THE PAGE since W27a PAN-02 (`docs/deferred-scope-2026-10-13/
 * work/W27a-contract.md` §2.2): a release's detail is a drill-down that opens BESIDE the list, in the right rail's
 * place (the rail folds while it is open), not in a modal over it; "Open full page" goes to `/releases/:releaseId`,
 * which renders the same detail as the page itself.
 *
 * The four tests keep their intent from the side-panel version; the shape is the change: the list does not move
 * (its top is where it was), the pane is a labelled region of the page (no dialog), and the release form opens
 * INSIDE the pane (Escape closes the form, not the release).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { RELEASE_ACTIVE_ID as ACTIVE_ID, RELEASES_PAGE } from '../visual/production/fixtures-pages'

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The primary list's top, px below the scroller's top (scrolled to 0). */
async function primaryTop(page: Page) {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const primary = document.querySelector('[data-primary]')
    return primary ? Math.round(primary.getBoundingClientRect().top - main.getBoundingClientRect().top) : null
  }, MAIN)
}

const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace('.', '\\.')} `) })
const release = (page: Page, name: string) => page.getByRole('complementary', { name, exact: true })
// A release's reads: its detail and packs, and (W05 REL-01) its recorded gate, its baseline and its phases' live gates.
const detailRequests = (seen: readonly string[]) =>
  seen.filter((line) => /^GET \/api\/v1\/releases\/[^/?]+(\/compliance-packs|\/gate|\/gate\/baseline|\/phases\/gate)?$/.test(line))

test('a release opens beside the list, in the rail\'s place, and the list does not move', async ({ page }) => {
  watchNativeDialogs(page)
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await networkQuiet(page, api)
  const top = await primaryTop(page)
  console.log(`FOLD /releases ${JSON.stringify({ primaryTop: top })}`)
  expect(detailRequests(api.seen), 'no release detail is asked for until one is opened').toEqual([])

  // The header: one action, no "(planned)" stubs.
  const header = page.locator('[data-page-header]')
  await expect(header).toHaveAttribute('data-compact', 'true')
  await expect(header.getByRole('button', { name: /New release/ })).toBeEnabled()
  await expect(page.getByText('(planned)')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Export schedule|Calendar view|Clone from|Generate from PRD/ })).toHaveCount(0)

  const shipping = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Shipping this week' }) })
  const railLeft = (await shipping.boundingBox())?.x ?? 0
  await card(page, '2026.11').click()
  const panel = release(page, '2026.11')
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(panel.getByText('Checkout revamp')).toBeVisible()
  await expect(panel.getByRole('heading', { name: 'Release Phases' })).toBeVisible()
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  expect([...detailRequests(api.seen)].sort()).toEqual([
    `GET /api/v1/releases/${ACTIVE_ID}`,
    `GET /api/v1/releases/${ACTIVE_ID}/compliance-packs`,
    `GET /api/v1/releases/${ACTIVE_ID}/gate`,
    `GET /api/v1/releases/${ACTIVE_ID}/gate/baseline`,
    `GET /api/v1/releases/${ACTIVE_ID}/phases/gate`,
  ].sort())
  // Beside the list, not in it: the list's top has not moved, the detail is not inside it, and the pane sits where
  // the rail was (the rail is folded).
  expect(await primaryTop(page)).toBe(top)
  await expect(page.locator('[data-primary]').getByRole('heading', { name: /Linked Test Runs/ })).toHaveCount(0)
  await expect(shipping).toHaveCount(0)
  const box = await panel.boundingBox()
  const primaryBox = await page.locator('[data-primary]').boundingBox()
  expect(box && primaryBox && box.x >= primaryBox.x + primaryBox.width, 'the pane starts right of the list').toBe(true)
  expect(Math.round(box?.x ?? 0), 'the pane starts no further right than the rail did').toBeLessThanOrEqual(Math.round(railLeft) + 1)

  // Escape (focus in the pane) closes it; the rail comes back and the release opens again from it
  // ("Shipping this week": due in 5 days). W05: the rail's "View packs" rows are only the releases READY for a
  // pack; this fixture's shipped release has one and its in-progress release is No-Go, so no such row.
  await panel.getByRole('button', { name: 'Close release' }).focus()
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await shipping.getByRole('button', { name: /^2026\.11/ }).click()
  await expect(release(page, '2026.11')).toBeVisible()
  await page.getByRole('button', { name: 'Close release' }).click()
  await expect(page.locator('aside[data-in-page="detail"]')).toHaveCount(0)
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('"Open full page" goes to the release’s own page, where the detail is the page', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await card(page, '2026.11').click()
  const panel = release(page, '2026.11')
  await expect(panel.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await panel.getByRole('link', { name: /Open full page/ }).click()

  const origin = new URL(page.url()).origin
  await expect(page).toHaveURL(`${origin}/releases/${ACTIVE_ID}`)
  await expect(page.locator('aside[data-in-page="detail"]'), 'no pane on the release’s own page').toHaveCount(0)
  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('heading', { name: '2026.11', exact: true })).toBeVisible()
  await expect(primary.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(primary.getByRole('button', { name: 'Edit' })).toBeVisible()
  await expect(page.getByText('1 active across Checkout')).toBeVisible()
  // Its card is not a toggle on its own page.
  await card(page, '2026.11').click()
  await expect(primary.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(page.locator('aside[data-in-page="detail"]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Edit from the pane opens the release form inside it; Escape closes the form only', async ({ page }) => {
  watchNativeDialogs(page)
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await card(page, '2026.11').click()
  const panel = release(page, '2026.11')
  await panel.getByRole('button', { name: 'Edit', exact: true }).click()
  const editor = panel.getByRole('region', { name: 'Edit Release' })
  await expect(editor, 'the form is inside the pane').toBeVisible()
  await expect(editor.getByLabel('Release Name *')).toHaveValue('2026.11')
  await expect(editor.getByLabel('Release Name *'), 'the first field takes focus').toBeFocused()
  await expectNoOverlay(page)
  // Escape closes the innermost container: the form, never the release around it.
  await page.keyboard.press('Escape')
  await expect(editor).toHaveCount(0)
  await expect(panel).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a planned release with no phases offers no stub controls', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  const planned = card(page, '2026.12')
  await expect(planned.getByText('No phases scoped yet')).toBeVisible()
  await expect(planned.getByText("Add 2026.12's phases in its detail.")).toBeVisible()
  // W05 REL-08: the real "Release actions" menu (Set as active release); W11 REL-05 / REL-07 (a QA lead): the two
  // working planning controls, Copy phases from… and Draft from a PRD…. Still no stub (no "(planned)", no Clone /
  // Generate / Export placeholder), and no Evaluate (a planned release has nothing to evaluate).
  await expect(planned.getByRole('button')).toHaveCount(3)
  await expect(planned.getByRole('button', { name: 'Release actions' })).toBeVisible()
  await expect(planned.getByRole('button', { name: 'Copy phases from…', exact: true })).toBeVisible()
  await expect(planned.getByRole('button', { name: 'Draft from a PRD…', exact: true })).toBeVisible()
  await expect(planned.getByRole('button', { name: /Clone from|Generate from PRD|\(planned\)/ })).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
