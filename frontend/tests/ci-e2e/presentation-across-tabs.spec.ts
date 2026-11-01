/**
 * HYG-08: presentation mode follows across the tabs of one browser profile.
 *
 * The mode is a per-browser preference (`store/presentationStore.ts`, key
 * `testlookup-presentation`). A toggle in one tab writes localStorage; the
 * browser fires `storage` in every OTHER document of the origin, and the
 * store's listener reads the preference back and re-applies the
 * `data-presentation` attribute on `<html>`. Both pages here are in ONE
 * browser context, i.e. one profile with one localStorage — two tabs.
 *
 * "Within a second" is the acceptance; it is the assertion's budget, not a
 * sleep. The sync reads localStorage only: no request is made by it.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { networkQuiet, observed, openRollout } from '../lib/rollout'
import { OVERVIEW_ON } from '../visual/production/fixtures'

/** The acceptance: the other tab follows within a second. */
const FOLLOWS_WITHIN_MS = 1_000

const overviewReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

/**
 * The presentation toggle, in the account menu since the UX redesign P1
 * (copied from `rollout-presentation.spec.ts`): opens the menu when it is closed.
 */
async function presentationToggle(page: Page) {
  const menu = page.getByRole('button', { name: 'Account menu' })
  if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click()
  return page.locator('[data-account-menu]').getByRole('button', { name: 'Presentation mode' })
}

/** Turn the mode on or off from `page`'s own account menu, and see it take there first. */
async function setMode(page: Page, on: boolean) {
  const toggle = await presentationToggle(page)
  await expect(toggle).toHaveAttribute('aria-pressed', on ? 'false' : 'true')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', on ? 'true' : 'false')
}

/** `page`'s `<html>` carries the mode within the acceptance's second, and its toggle reads it. */
async function expectFollowed(page: Page, on: boolean) {
  const html = page.locator('html')
  if (on) await expect(html).toHaveAttribute('data-presentation', 'on', { timeout: FOLLOWS_WITHIN_MS })
  else await expect(html).not.toHaveAttribute('data-presentation', /.*/, { timeout: FOLLOWS_WITHIN_MS })
  await expect(await presentationToggle(page)).toHaveAttribute('aria-pressed', on ? 'true' : 'false')
}

test.describe('presentation mode across two tabs of one profile (1280 px)', () => {
  test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('on in one tab: the other follows within a second; off follows; and the other way round', async ({ page }) => {
    const a = page
    const b = await page.context().newPage()
    const openedA = await openRollout(a, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    const openedB = await openRollout(b, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    await networkQuiet(a, openedA.api)
    await networkQuiet(b, openedB.api)
    const askedA = observed(openedA.api).length
    const askedB = observed(openedB.api).length

    // Both tabs start at desk size.
    await expect(a.locator('html')).not.toHaveAttribute('data-presentation', /.*/)
    await expect(b.locator('html')).not.toHaveAttribute('data-presentation', /.*/)

    // A turns it on -> B follows; A turns it off -> B follows.
    await setMode(a, true)
    await expect(a.locator('html')).toHaveAttribute('data-presentation', 'on')
    await expectFollowed(b, true)
    await setMode(a, false)
    await expect(a.locator('html')).not.toHaveAttribute('data-presentation', /.*/)
    await expectFollowed(b, false)

    // The other direction: B turns it on -> A follows; B turns it off -> A follows.
    await setMode(b, true)
    await expectFollowed(a, true)
    await setMode(b, false)
    await expectFollowed(a, false)

    // The sync reads localStorage only: neither tab asked for anything while it ran.
    expect(observed(openedA.api).slice(askedA), 'requests from tab A during the sync').toEqual([])
    expect(observed(openedB.api).slice(askedB), 'requests from tab B during the sync').toEqual([])
    for (const [name, opened] of [
      ['A', openedA],
      ['B', openedB],
    ] as const) {
      expect(opened.api.unhandled, `tab ${name}: API requests with no fixture`).toEqual([])
      expect(opened.api.offHost, `tab ${name}: requests that left the dev host`).toEqual([])
      expect(opened.errors, `tab ${name}: uncaught page errors`).toEqual([])
    }
    await b.close()
  })
})
