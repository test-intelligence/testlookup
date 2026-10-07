/**
 * Baselines of the UX redesign P5 settings layout (hermetic fixtures, a QA
 * lead): the grouped sub-nav beside every settings and admin page, and the
 * `/settings` index — the same groups as compact lists, one line per page
 * (it was 22 cards).
 *
 * The index asks for nothing of its own (the groups are data in
 * `settingsNav.ts`), so the shell's handlers are the whole fixture. Lands WITH
 * its PNGs (`visual-baselines.yml`, Linux).
 */
import { expect, test } from '@playwright/test'
import { PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT } from './production/fixtures'

// Tall enough for the whole sub-nav (seven groups): a region must end inside the viewport.
test.use({ ...PINNED, viewport: { width: 1280, height: 1800 } })

for (const theme of THEMES) {
  test(`the P5 settings layout and index — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings', {
      theme,
      handlers: LAYOUT,
      ready: (p) => p.locator('[data-settings-index]'),
    })
    await networkQuiet(page, api)
    const subnav = page.getByRole('navigation', { name: 'Settings' })
    // On the index, "All settings" is the current page.
    await expect(subnav.getByRole('link', { name: 'All settings' })).toHaveAttribute('aria-current', 'page')
    await visualRegion(page, 'settings-subnav', theme, subnav)
    await visualRegion(page, 'settings-index', theme, page.locator('[data-settings-index]'))
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}
