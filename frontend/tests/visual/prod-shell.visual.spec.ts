/**
 * Baselines of the UX redesign P1 shell, on `/trends` (hermetic fixtures):
 * the sidebar expanded and collapsed to its rail, the section tabs, the Help
 * menu, the help drawer open on the page's topic, and the account menu.
 *
 * `/trends` because its help topic (`dashboards`) has no Mermaid diagram, so
 * the drawer's region is drawn the moment its Markdown is. A 900 px tall
 * viewport: the sidebar and the drawer are `h-screen`, so the viewport's
 * height is their height. Lands WITH its PNGs (`visual-baselines.yml`, Linux).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { TRENDS_ON } from './production/fixtures'

test.use({ ...PINNED, viewport: { width: 1280, height: 900 } })

const ready = (p: Page) => landmark(p, 'Trend metrics')

for (const theme of THEMES) {
  test(`the P1 shell — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', { theme, handlers: TRENDS_ON, ready })
    await networkQuiet(page, api)
    const aside = page.locator('aside[data-sidebar]')

    await visualRegion(page, 'shell-sidebar', theme, aside)
    await visualRegion(page, 'shell-section-tabs', theme, page.locator('[data-section-tabs="trends"]'))

    await page.getByRole('button', { name: 'Help' }).click()
    await visualRegion(page, 'shell-help-menu', theme, page.getByRole('menu', { name: 'Help' }))
    await page.getByRole('menuitem', { name: 'Help for this page' }).click()
    const drawer = page.locator('aside').filter({ has: page.locator('[data-help-topic="dashboards"]') })
    await expect(drawer.getByRole('heading', { name: 'The views' })).toBeVisible()
    await visualRegion(page, 'shell-help-drawer', theme, drawer)
    await drawer.getByRole('button', { name: 'Close help' }).click()

    await page.getByRole('button', { name: 'Account menu' }).click()
    await visualRegion(page, 'shell-account-menu', theme, page.getByRole('menu', { name: 'Account' }))
    await page.keyboard.press('Escape')

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await expect(aside).toHaveAttribute('data-sidebar', 'collapsed')
    await expect.poll(async () => (await aside.boundingBox())?.width).toBe(64)
    await visualRegion(page, 'shell-sidebar-collapsed', theme, aside)

    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}
