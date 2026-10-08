/**
 * UX redesign P5 item 5, on /releases, hermetic: a release's detail is a
 * drill-down in a side panel (`02-design-spec.md` §2: "Drill-downs (a test, a
 * cluster, a release, a defect) open in the SidePanel; they do not expand
 * inline and push the page down"), with "Open full page" to
 * `/releases/:releaseId`, which renders the same detail as the page itself.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { RELEASE_ACTIVE_ID as ACTIVE_ID, RELEASES_PAGE } from '../visual/production/fixtures-pages'

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

async function geometry(page: Page) {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primary = document.querySelector('[data-primary]')
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
    }
  }, MAIN)
}

const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace('.', '\\.')} `) })
const detailRequests = (seen: readonly string[]) => seen.filter((line) => /^GET \/api\/v1\/releases\/[^/?]+(\/compliance-packs)?$/.test(line))

test('a release opens in a side panel over the list, which does not move', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await networkQuiet(page, api)
  const before = await geometry(page)
  // List-page heights: 1111 px before P5 with nothing open, 2196 px with the
  // in-progress release expanded inline under its card.
  console.log(`FOLD /releases ${JSON.stringify(before)}`)
  expect(detailRequests(api.seen), 'no release detail is asked for until one is opened').toEqual([])

  // The header: one action, no "(planned)" stubs.
  const header = page.locator('[data-page-header]')
  await expect(header).toHaveAttribute('data-compact', 'true')
  await expect(header.getByRole('button', { name: /New release/ })).toBeEnabled()
  await expect(page.getByText('(planned)')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Export schedule|Calendar view|Clone from|Generate from PRD/ })).toHaveCount(0)

  await card(page, '2026.11').click()
  const panel = page.getByRole('dialog', { name: '2026.11' })
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(panel.getByText('Checkout revamp')).toBeVisible()
  await expect(panel.getByRole('heading', { name: 'Release Phases' })).toBeVisible()
  await networkQuiet(page, api)
  expect(detailRequests(api.seen)).toEqual([
    `GET /api/v1/releases/${ACTIVE_ID}`,
    `GET /api/v1/releases/${ACTIVE_ID}/compliance-packs`,
  ])
  // Beside the list, not in it: the page behind is exactly as tall as before.
  const opened = await geometry(page)
  expect(opened).toEqual(before)
  await expect(page.locator('[data-primary]').getByRole('heading', { name: /Linked Test Runs/ })).toHaveCount(0)
  const box = await panel.boundingBox()
  expect(box?.width).toBe(640)
  expect(Math.round((box?.x ?? 0) + (box?.width ?? 0))).toBe(1440)

  // Escape closes it; another release opens in its place from the right rail.
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await page.getByRole('button', { name: 'View packs' }).first().click()
  await expect(page.getByRole('dialog', { name: '2026.11' })).toBeVisible()
  await page.getByRole('button', { name: 'Close release' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('"Open full page" goes to the release’s own page, where the detail is the page', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await card(page, '2026.11').click()
  const panel = page.getByRole('dialog', { name: '2026.11' })
  await expect(panel.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await panel.getByRole('link', { name: /Open full page/ }).click()

  await expect(page).toHaveURL(new RegExp(`/releases/${ACTIVE_ID}$`))
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('heading', { name: '2026.11', exact: true })).toBeVisible()
  await expect(primary.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(primary.getByRole('button', { name: 'Edit' })).toBeVisible()
  await expect(page.getByText('1 active across Checkout')).toBeVisible()
  // Its card is not a toggle on its own page.
  await card(page, '2026.11').click()
  await expect(primary.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Edit from the panel opens the release dialog above it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', { handlers: RELEASES_PAGE, ready })
  await card(page, '2026.11').click()
  const panel = page.getByRole('dialog', { name: '2026.11' })
  await panel.getByRole('button', { name: 'Edit' }).click()
  const editor = page.getByRole('dialog', { name: 'Edit Release' })
  await expect(editor).toBeVisible()
  await expect(editor.getByLabel('Release Name *')).toHaveValue('2026.11')
  // On top: its Cancel button is the element at its own centre.
  const cancel = editor.getByRole('button', { name: 'Cancel' })
  const hit = await cancel.evaluate((el) => {
    const r = el.getBoundingClientRect()
    return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2))
  })
  expect(hit, 'the edit dialog is drawn above the panel').toBe(true)
  // Escape closes the dialog on top only.
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
  await expect(planned.getByRole('button')).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
