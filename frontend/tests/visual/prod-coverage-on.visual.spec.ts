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
 * W08 (PNGs from CI): NEW `coverage-history` (the History tab's "Coverage
 * history" frame, TRND-07: the catalogue and the share exercised, from 90 days
 * of snapshots with three missing nights) and NEW `coverage-map-needs-review`
 * (TRND-12: the level-1 map from a variant whose nodes carry
 * `needs_review_count`; the default fixture carries none, so `coverage-on-map`
 * does not move).
 *
 * Lands WITH its PNGs (`visual-baselines.yml`, Linux only): a flag-on spec
 * never sits on the branch before them. Harness: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, landmark, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { bringNear, expectDrawn, expectNoErrorFrame, openRollout, section, sectionFrame } from '../lib/rollout'
import { COVERAGE_ON } from './production/fixtures'
import { NEEDS_REVIEW_MAP } from './production/fixtures-trends-coverage'

test.use(PINNED_TALL)

/** Region names (one PNG per name and theme under `production/`). */
const MAP = 'coverage-on-map'
const MAP_SUITE = 'coverage-on-map-suite'
const ENV = 'coverage-on-heatmap-env'
const RELEASE = 'coverage-on-heatmap-release'
const HISTORY = 'coverage-history'
const NEEDS_REVIEW = 'coverage-map-needs-review'

for (const theme of THEMES_WITH_PAPER) {
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

  test(`coverage W08 regions — ${theme}`, async ({ page }) => {
    const ready = (p: typeof page) => landmark(p, 'Run cadence')
    // TRND-07: the History tab.
    const { api, errors } = await openRollout(page, '/coverage?tab=history', { theme, handlers: COVERAGE_ON, ready })
    const history = page.getByRole('region', { name: 'Coverage history' })
    await expect(history).toHaveAttribute('data-coverage-history', 'drawn', { timeout: 20_000 })
    const frame = history.locator('[data-chart-frame]')
    await expect(frame).toHaveAttribute('data-chart-state', /^(ready|truncated)$/)
    await expect(frame.locator('.recharts-wrapper svg.recharts-surface path').first()).toBeVisible()
    await expectNoErrorFrame(page)
    await visualRegion(page, HISTORY, theme, frame)
    assertHermetic(api, errors)
  })

  test(`coverage map, tests that need review — ${theme}`, async ({ page }) => {
    const ready = (p: typeof page) => landmark(p, 'Run cadence')
    const { api, errors } = await openRollout(page, '/coverage', { theme, handlers: [...NEEDS_REVIEW_MAP, ...COVERAGE_ON], ready })
    const map = sectionFrame(page, 'coverage-map', 'Test coverage map')
    await expectDrawn(map, 'map')
    await expect(map.locator('[data-chart-type="treemap"]')).toHaveAttribute('data-chart-status', 'ready')
    await expectNoErrorFrame(page)
    await page.waitForTimeout(300)
    await visualRegion(page, NEEDS_REVIEW, theme, map)
    assertHermetic(api, errors)
  })
}
