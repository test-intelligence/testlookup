/**
 * BEFORE/AFTER baseline of /release-gate/<run> (Wave 2.5, VIZ-104): the
 * recommendation card with its semicircle risk arc, for a NO_GO floored to
 * 60 by the pass rate. Today the arc's track is `#334155` and its score is
 * literal white, which is theme-blind on `lab`.
 *
 * Wave 2.6 C0 adds `gate-clusters`, the "Linked Failure Clusters" card the
 * catalogue's cluster-share chart will sit directly above. The card is drawn
 * only for a decision with cluster insights, so it is captured from its own
 * page load of the clustered decision; `gate-risk-gauge` keeps its inputs.
 *
 * Phase D S2: the catalogue's Context group mounts on every load (below the
 * card, above the cluster list), so both loads answer its reads
 * (`releaseGateOn`, the same stored decisions) with every flag off, and the
 * regions are captured once it has drawn (its charts are
 * `prod-release-gate-on`'s). The cluster card now sits below the group, past
 * 2400 px, so its load uses `TALL_VIEWPORT` (the shell scrolls inside
 * `#main-content`, so the height changes no pixel of a region).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  assertHermetic,
  cardAround,
  cardByHeading,
  openProductionPage,
  PINNED,
  TALL_VIEWPORT,
  THEMES,
  visualRegion,
} from '../lib/production-pages'
import { expectDrawn, sectionFrame } from '../lib/rollout'
import { GATE_CLUSTERS, HOSTILE_NAME, NOW, PROJECT_ID, releaseGateOn, RUN_ID, USER } from './production/fixtures'

test.use(PINNED)

/** The Context group has drawn (the comparison, and the share when there are clusters): nothing moves after this. */
async function contextDrawn(page: Page, clusters: boolean) {
  await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'gate-releases')
  if (clusters) await expectDrawn(sectionFrame(page, 'gate-clusters', 'Failure cluster share'), 'gate-clusters')
}

for (const theme of THEMES) {
  test(`release gate regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/release-gate/${RUN_ID}`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: releaseGateOn(),
      ready: (p) => p.getByText('Risk Score', { exact: true }),
    })

    const card = cardAround(page.getByText('Risk Score', { exact: true }))
    await expect(card).toContainText('Recommendation')
    // The kit ring draws its number as HTML; the meter carries the reading.
    await expect(card.getByRole('meter', { name: 'Risk Score' })).toHaveAttribute('aria-valuenow', '60')
    await contextDrawn(page, false)

    await visualRegion(page, 'gate-risk-gauge', theme, card)
    assertHermetic(api, errors)
  })
}

test.describe('the cluster card, below the Context group', () => {
  // `PINNED`'s launch options stay file-level (a describe cannot change them); only the height grows.
  test.use({ viewport: { ...TALL_VIEWPORT } })

  for (const theme of THEMES) {
    test(`release gate cluster card — ${theme}`, async ({ page }) => {
      const { api, errors } = await openProductionPage(page, `/release-gate/${RUN_ID}`, {
        theme,
        now: NOW,
        me: USER,
        user: USER,
        projectId: PROJECT_ID,
        handlers: releaseGateOn({ clusters: GATE_CLUSTERS }),
        ready: (p) => p.getByRole('heading', { name: 'Linked Failure Clusters', exact: true }),
      })

      const clusters = cardByHeading(page, 'Linked Failure Clusters')
      // One row per cluster, in the decision's order, each linking to the run.
      await expect(clusters.getByRole('link', { name: 'Details →' })).toHaveCount(GATE_CLUSTERS.length)
      await expect(clusters).toContainText('7 tests · CRITICAL')
      await expect(clusters).toContainText('1 tests · unclassified')
      // A hostile cluster label is text, never markup.
      await expect(clusters.getByText(HOSTILE_NAME, { exact: true })).toBeVisible()
      expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
      await contextDrawn(page, true)

      await visualRegion(page, 'gate-clusters', theme, clusters)
      assertHermetic(api, errors)
    })
  }
})
