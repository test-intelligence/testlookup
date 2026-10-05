/**
 * AFTER baselines of /release-gate/<run> with the catalogue ON (Wave 2.6,
 * VIZ-408, plan 5.2): the release comparison (five releases aligned on their
 * starts, a hostile release name, the live caption) and the stored cluster
 * share, as a donut for 3 clusters and as a ranked bar for 7 (two loads).
 * The comparison's region is its whole section, the live caption included
 * (the caption says the verdict is stored and not computed from the chart,
 * plan 2.5). The cluster share's region is its frame, on its own row below
 * the comparison (Wave 2.6 R2-9), its stored caption in the frame's footer
 * (R2-21) and asserted as text too.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES, visualRegion, waitForCharts, type Theme } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, openRollout, section, sectionFrame } from '../lib/rollout'
import { GATE_CLUSTERS, GATE_CLUSTERS_7, releaseGateOn, RUN_ID } from './production/fixtures'

// 1280 x 4000: with the Context group in one column below Risk Dimension
// Breakdown (Wave 2.6 R2-8/R2-9) and the stored caption in the cluster frame's
// footer, the cluster frame ends at y = 2,408, past a 2,400 px viewport.
test.use(PINNED_TALL)

async function openGate(page: Page, theme: Theme, clusters: readonly unknown[]) {
  return openRollout(page, `/release-gate/${RUN_ID}`, {
    theme,
    handlers: releaseGateOn({ clusters }),
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
  })
}

for (const theme of THEMES) {
  test(`release gate context, 3 clusters, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openGate(page, theme, GATE_CLUSTERS)
    const releases = sectionFrame(page, 'gate-releases', 'Pass rate by release')
    const clusters = sectionFrame(page, 'gate-clusters', 'Failure cluster share')
    await expectDrawn(releases, 'releases')
    await expectDrawn(clusters, 'clusters')
    // One line per compared release. Not `waitForCharts`: its first path is
    // 2026.09's line, flat at 100%, whose zero-height box never counts as visible.
    await expect(releases.locator('.recharts-line-curve')).toHaveCount(5)
    await waitForCharts(clusters)
    await expect(section(page, 'gate-clusters').locator('[data-chart-choice="donut"]')).toHaveCount(1)
    await expect(section(page, 'gate-releases').locator('[data-gate-caption="live"]')).toBeVisible()
    await expectNoErrorFrame(page)

    await visualRegion(page, 'gate-on-releases', theme, section(page, 'gate-releases'))
    await expect(section(page, 'gate-clusters').locator('[data-gate-caption="stored"]')).toContainText('build 240')
    await visualRegion(page, 'gate-on-clusters-donut', theme, clusters)
    assertHermetic(api, errors)
  })

  test(`release gate cluster share, 7 clusters, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openGate(page, theme, GATE_CLUSTERS_7)
    const clusters = sectionFrame(page, 'gate-clusters', 'Failure cluster share')
    await expectDrawn(clusters, 'clusters')
    await waitForCharts(clusters)
    await expect(section(page, 'gate-clusters').locator('[data-chart-choice="ranked-bar"]')).toHaveCount(1)
    // The comparison above it must have drawn too (it sets where this frame starts).
    await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'releases')
    await expectNoErrorFrame(page)

    await visualRegion(page, 'gate-on-clusters-bars', theme, clusters)
    assertHermetic(api, errors)
  })
}
