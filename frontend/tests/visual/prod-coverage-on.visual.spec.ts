/**
 * AFTER baselines of /coverage's Wave 3 sections (plan 5 "Visual"): the test
 * coverage map at level 1 (pass rate colour, the gap patterns and their
 * legend, the EPIC caption) and at a suite's classes, and the suite x
 * environment / suite x release heatmaps. No flag is asked since Phase D, S4
 * (the sections mount unconditionally), so none is set.
 *
 * UX redesign P3 (the page template): the map and the heatmap are the page's
 * two tabs (the map the default), each captured in its own tab; the regions
 * keep their names.
 *
 * Lands WITH its PNGs (`visual-baselines.yml`, Linux only): a flag-on spec
 * never sits on the branch before them. Harness: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, THEMES, visualRegion } from '../lib/production-pages'
import { bringNear, expectDrawn, expectNoErrorFrame, openRollout, section, sectionFrame } from '../lib/rollout'
import { COVERAGE_ON } from './production/fixtures'

test.use(PINNED_TALL)

/** Region names (one PNG per name and theme under `production/`). */
const MAP = 'coverage-on-map'
const MAP_SUITE = 'coverage-on-map-suite'
const ENV = 'coverage-on-heatmap-env'
const RELEASE = 'coverage-on-heatmap-release'

for (const theme of THEMES) {
  test(`coverage Wave 3 regions — ${theme}`, async ({ page }) => {
    const ready = (p: typeof page) => landmark(p, 'Run cadence')
    const { api, errors } = await openRollout(page, '/coverage', { theme, handlers: COVERAGE_ON, ready })
    // The map: the default tab.
    const map = sectionFrame(page, 'coverage-map', 'Test coverage map')
    await expectDrawn(map, 'map')
    await expect(map.locator('[data-chart-type="treemap"]')).toHaveAttribute('data-chart-status', 'ready')
    await expectNoErrorFrame(page)
    // The treemap's own wait (spike S1: let the canvas settle before a screenshot).
    await page.waitForTimeout(300)
    await visualRegion(page, MAP, theme, map)

    // The heatmap: its own tab.
    await page.getByRole('tablist', { name: 'Coverage views' }).getByRole('tab', { name: 'Env × release heatmap', exact: true }).click()
    const heatmap = sectionFrame(page, 'heatmap-suite_environment', 'Suite pass rate by environment')
    await expectDrawn(heatmap, 'environment heatmap')
    await expect(heatmap.locator('canvas').first()).toBeVisible()
    await expectNoErrorFrame(page)
    await visualRegion(page, ENV, theme, heatmap)

    await section(page, 'heatmap-suite_environment').getByRole('radio', { name: 'Release', exact: true }).check()
    const release = sectionFrame(page, 'heatmap-suite_environment', 'Suite pass rate by release')
    await expectDrawn(release, 'release heatmap')
    await visualRegion(page, RELEASE, theme, release)

    // A suite's classes (a file-path class, the ungrouped one), from a shared link (the map's tab, the default).
    await page.goto('/coverage?drill=suite~checkout')
    await bringNear(page, 'coverage-map')
    await expectDrawn(map, 'map at Checkout')
    await expect(section(page, 'coverage-map').locator('[data-coverage-breadcrumb] li')).toHaveText(['All suites', 'Checkout'])
    await page.waitForTimeout(300)
    await visualRegion(page, MAP_SUITE, theme, map)
    assertHermetic(api, errors)
  })
}
