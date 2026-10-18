/**
 * W27b PAN-07 CHARACTERIZATION, a report page's saved views (`docs/deferred-scope-2026-10-13/work/W27b-contract.md`
 * §2.2, row N12; the menu is shared by seven report pages, Trends drives it here), hermetic. Written against the
 * tree where a view's Delete asks a native `confirm()` and kept green, UNEDITED, after it asks in a strip inside
 * the Views popover (`answerConfirm` answers either shape).
 *
 *  - N12 Views › my view's Delete: dismissed → nothing sent, the popover and the view stay; accepted →
 *    `DELETE /api/v1/saved-views/{id}`, the page's saved views re-read, `Deleted "<name>"`, the row gone, the
 *    popover still open; a teammate's shared view offers no Delete.
 *
 * The catalogue rows / failure-group panels (N8, N9) are pinned by the rollout specs through `rowsPanels`
 * (`tests/lib/rollout.ts`, role-agnostic since W27b phase 1).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `savedViewsDeleteWorld()`
 * (`fixtures-in-page-b.ts`) over `TRENDS_ON`.
 */
import { expect, test, type Page } from '@playwright/test'
import { answerConfirm } from '../lib/inPage'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { TRENDS_ON } from '../visual/production/fixtures'
import { MY_VIEW, SHARED_VIEW, savedViewsDeleteWorld } from '../visual/production/fixtures-in-page-b'

test.use({ viewport: { width: 1280, height: 1600 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Trend metrics')
const SAVED_VIEWS = '/api/v1/saved-views'
const popover = (page: Page) => page.locator('[data-saved-views-panel]')
/** A view's own row in the popover (never a strip a conversion puts under it). */
const viewRow = (page: Page, id: string) => popover(page).locator(`li[data-saved-view="${id}"]`)

test('N12 Views › my view\'s Delete: dismissed sends nothing; accepted → the DELETE, the re-read, the toast; the popover stays open', async ({ page }) => {
  const world = savedViewsDeleteWorld()
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...world.handlers, ...TRENDS_ON], ready })
  await networkQuiet(page, api)
  await page.locator('[data-saved-views-trigger]').first().click()
  await expect(viewRow(page, MY_VIEW.id)).toBeVisible()
  await expect(viewRow(page, SHARED_VIEW.id)).toContainText('(shared)')
  await expect(viewRow(page, SHARED_VIEW.id).locator('[data-saved-view-delete]'), 'a shared view of a teammate has no Delete').toHaveCount(0)
  const del = () => viewRow(page, MY_VIEW.id).locator('[data-saved-view-delete]')
  await expect(del()).toHaveAttribute('title', 'Delete this view')

  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe(`Delete the view "${MY_VIEW.name}"?`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await expect(popover(page), 'the popover stays open').toBeVisible()
  await expect(viewRow(page, MY_VIEW.id)).toBeVisible()

  const reads = requestsTo(api, SAVED_VIEWS).length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText(`Deleted "${MY_VIEW.name}"`, { exact: true })).toBeVisible()
  await expect(viewRow(page, MY_VIEW.id)).toHaveCount(0)
  await expect(popover(page), 'the popover stays open').toBeVisible()
  await expect(viewRow(page, SHARED_VIEW.id)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE ${SAVED_VIEWS}/${MY_VIEW.id}`, body: null }])
  expect(requestsTo(api, SAVED_VIEWS).length, 'the saved views re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})
