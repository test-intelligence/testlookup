/**
 * Wave 2.4's new states under the shared axe gate (`tests/lib/axe-gate.ts`:
 * every WCAG level the product claims plus best practices, every impact, a
 * ratcheted per-node allowlist — empty here), in every theme:
 *
 *   - a ZOOMED frame (VIZ-407): the brush's sliders, Reset zoom, the disabled
 *     "Apply as time filter" and its reason, the footer's zoom note;
 *   - an OPEN export menu (VIZ-606): the menu button, the menu, its items and
 *     the checkbox item;
 *   - a FULL-SCREEN frame (VIZ-608): the dialog, its name, its controls.
 *
 * The page-wide gallery audit (`chart-gallery.spec.ts`) sees the zoomed items
 * at rest; none of the three states above exists until someone acts.
 */
import { expect, test } from '@playwright/test'
import { ALL_THEMES, expectNoBlockingViolations } from '../lib/axe-gate'
import { galleryItem, openGallery } from '../lib/chart-gallery-page'

const ZOOMED = ['timeseries-zoom-trend', 'timeseries-zoom-apply-disabled', 'multi-series-zoom-hidden', 'duration-band-zoomed']
const scopeOf = (id: string) => `[data-gallery-item="${id}"]`

for (const theme of ALL_THEMES) {
  test(`no accessibility violations: a zoomed frame, an open export menu, a full-screen frame (${theme})`, async ({ page }) => {
    await openGallery(page, `?theme=${theme}`)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

    // Zoomed, drawn, with the Apply reason on screen.
    for (const id of ZOOMED) {
      await expect(galleryItem(page, id).getByRole('button', { name: 'Reset zoom' })).toBeVisible()
    }
    await expect(page.locator('[data-chart-zoom-apply-reason]')).toBeVisible()
    await expectNoBlockingViolations(page, theme, [], ZOOMED.map(scopeOf))

    // An open export menu.
    const bar = galleryItem(page, 'bar-ranked')
    await bar.getByRole('button', { name: 'Export', exact: true }).click()
    await expect(bar.getByRole('menu')).toBeVisible()
    await expectNoBlockingViolations(page, theme, [], [scopeOf('bar-ranked')])
    await page.keyboard.press('Escape')
    await expect(bar.getByRole('menu')).toHaveCount(0)

    // A full-screen frame (API or overlay — either is a dialog).
    const item = galleryItem(page, 'timeseries-zoom-trend')
    await item.getByRole('button', { name: 'Full screen', exact: true }).click()
    const frame = item.locator('[data-chart-frame]')
    await expect(frame).toHaveAttribute('data-chart-fullscreen', /^(api|overlay)$/, { timeout: 5000 })
    await expect(frame.getByRole('button', { name: 'Exit full screen' })).toBeVisible()
    await expectNoBlockingViolations(page, theme, [], [`${scopeOf('timeseries-zoom-trend')} [data-chart-frame]`])
    await frame.getByRole('button', { name: 'Exit full screen' }).click()
    await expect(frame).not.toHaveAttribute('data-chart-fullscreen', /.+/)
  })
}
