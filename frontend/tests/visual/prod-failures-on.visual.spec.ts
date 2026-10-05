/**
 * AFTER baselines of /failures with its Wave 3 sections (plan 5 "Visual"):
 * the failure groups (bubbles, then the related-groups view), the systemic
 * clusters tab, the drill ladder (level 0, the leaf, the leaf's rows panel
 * open) and the project-wide test scatter. Flag-off Failures PNGs are not
 * touched except `failures-categories` (FK3's intended BEFORE fix).
 * Phase D, S5: the page asks no flag; the scatter's frame toolbar now always
 * offers "View in 3D" (the harness used to answer `viz_three_d` off), so
 * `failures-on-scatter` gains that button.
 *
 * The page with its three sections is taller than 4000 px: a 1280 x 6000
 * viewport (the shell scrolls `#main-content`, so the height only sets how
 * much is on screen; nothing scrolls, every section is near at load).
 * Lands WITH its PNGs (`visual-baselines.yml`, Linux only).
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, THEMES, visualRegion } from '../lib/production-pages'
import {
  bringNear,
  expectDrawn,
  expectNoErrorFrame,
  expectOneRowsPanel,
  mountEverySection,
  openRollout,
  section,
  sectionFrame,
} from '../lib/rollout'
import { FAILURES_ON } from './production/fixtures'

test.use({ ...PINNED_TALL, viewport: { width: 1280, height: 6000 } })

for (const theme of THEMES) {
  test(`failures regions, both flags — ${theme}`, async ({ page }) => {
    const ready = (p: typeof page) => landmark(p, 'Failure verdict')
    const { api, errors } = await openRollout(page, '/failures', { theme, handlers: FAILURES_ON, ready })
    await mountEverySection(page, api)
    const groups = section(page, 'failures-groups')
    const groupsFrame = sectionFrame(page, 'failures-groups', 'Failures grouped by error message')
    const ladder = sectionFrame(page, 'failures-drill', 'Results by suite')
    const scatter = sectionFrame(page, 'scatter-project', 'Test duration vs failure rate')
    await expectDrawn(groupsFrame, 'groups')
    await expectDrawn(ladder, 'ladder')
    await expectDrawn(scatter, 'scatter')
    await expect(scatter.locator('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready')
    await expectNoErrorFrame(page)

    await visualRegion(page, 'failures-on-groups', theme, groups)
    await groupsFrame.getByRole('button', { name: 'Related groups' }).click()
    await expect(groups.locator('[data-group-plot]')).toHaveAttribute('data-group-plot', 'relations')
    await visualRegion(page, 'failures-on-groups-related', theme, groups)
    await groups.getByRole('tab', { name: 'Systemic flake clusters' }).click()
    await expect(groups.locator('[data-clusters-list]')).toBeVisible()
    await visualRegion(page, 'failures-on-clusters', theme, groups)

    await visualRegion(page, 'failures-on-drill', theme, section(page, 'failures-drill'))
    await visualRegion(page, 'failures-on-scatter', theme, section(page, 'scatter-project'))

    // The leaf (Payments, failed) and its rows panel, from a shared link and the keyboard.
    await page.goto('/failures?drill=suite~payments&drill=status~failed')
    await bringNear(page, 'failures-drill')
    await expect(section(page, 'failures-drill')).toHaveAttribute('data-drill-level', 'tests')
    await expectDrawn(section(page, 'failures-drill').locator('[data-chart-frame]'), 'leaf')
    await visualRegion(page, 'failures-on-drill-leaf', theme, section(page, 'failures-drill'))
    // The leaf's smallest bar (its last), so the open panel's first page is short.
    await section(page, 'failures-drill').locator('[data-chart-cursor]').focus()
    await page.keyboard.press('End')
    await page.keyboard.press('Enter')
    const panel = await expectOneRowsPanel(page, /^Executions in /)
    await visualRegion(page, 'failures-on-drill-rows', theme, panel.locator('[data-rows-state]'))
    assertHermetic(api, errors)
  })
}
