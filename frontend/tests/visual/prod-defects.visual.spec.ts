/**
 * BEFORE/AFTER baselines of /defects (Wave 2.5, VIZ-104): the queue
 * verdict with its health meter (G2) and the five KPI slots, four of which
 * draw hard-coded glyphs today; "Oldest open P0" is a real 4-day bar.
 * UX redesign P3 (page template), same region names, new contents:
 * `defects-verdict` is the one-line StatusBanner (the "Defect queue verdict"
 * landmark); `defects-kpis` is the KpiStrip of five compact tiles (the oldest
 * P0's bar beside its value). NEW region `defects-health`: the queue meter
 * (G2) and its three dimensions left the verdict for the "How queue health is
 * computed" Disclosure below the table, captured open.
 *
 * W06 "defects" (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §4.6):
 * `defects-kpis` changes (Open defects, P0 / P1 open and Time to resolve draw
 * the trend's sparklines; "Escape rate" from its endpoint replaces "Unlinked
 * to Jira"), `defects-health` changes (four dimensions: Ownership 20 %). NEW
 * regions: `defects-table` (the Owner column in every form, the Invalid row,
 * the "not filed (offline)" Jira chip, the row menus closed, and the
 * `?defect=` row highlighted) and `defects-scope-chip` (the verdict banner
 * with "Only this release" on: "Affecting 2026.09" in place of the "All
 * releases" badge).
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, landmark, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { DEFECTS, NOW, PROJECT_ID, USER } from './production/fixtures'
import { DEFECT_ID, defectsWorld, SCOPE_RELEASE } from './production/fixtures-defects'

test.use(PINNED)

const ready = (p: Page) => landmark(p, 'Defect KPIs')

for (const theme of THEMES) {
  test(`defects regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/defects', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: DEFECTS,
      ready,
    })

    const kpis = landmark(page, 'Defect KPIs')
    await expect(kpis).toContainText('Oldest open P0')
    await expect(kpis).toContainText('Escape rate')
    // The three trend tiles have drawn their sparklines before the strip is shot.
    await expect(kpis.locator('[data-metric-card] svg')).toHaveCount(3)

    await visualRegion(page, 'defects-verdict', theme, landmark(page, 'Defect queue verdict'))
    await visualRegion(page, 'defects-kpis', theme, kpis)

    const health = page.locator('[data-disclosure]').filter({ has: page.getByRole('button', { name: /^How queue health is computed/ }) })
    await health.getByRole('button', { name: /^How queue health is computed/ }).click()
    await expect(health.getByRole('meter', { name: 'Queue health' })).toBeVisible()
    await expect(health).toContainText('Ownership')
    await visualRegion(page, 'defects-health', theme, health)
    assertHermetic(api, errors)
  })

  test(`defects table, a row highlighted by ?defect= — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/defects?defect=${DEFECT_ID.searchPaginates}`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: DEFECTS,
      ready,
    })
    const table = page.locator('[data-primary]')
    await expect(table.locator('tbody tr[data-highlighted="true"]')).toHaveCount(1)
    await expect(table.locator('tbody tr[data-highlighted="true"]')).toContainText('search paginates')
    // A QA lead: every row has its menu, all closed.
    await expect(table.getByRole('button', { name: 'Defect actions' })).toHaveCount(9)
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(table).toContainText('Unassigned')
    await expect(table).toContainText('from ownership rules')
    await visualRegion(page, 'defects-table', theme, table)
    assertHermetic(api, errors)
  })

  test(`defects scope chip, Only this release on — ${theme}`, async ({ page }) => {
    const { handlers } = defectsWorld({ releases: true })
    const { api, errors } = await openProductionPage(page, `/defects?release=${SCOPE_RELEASE.id}&scope=release`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers,
      ready,
    })
    const verdict = landmark(page, 'Defect queue verdict')
    await expect(verdict.locator('[data-defects-scope-chip]')).toHaveText(`Affecting ${SCOPE_RELEASE.name}`)
    await expect(page.getByTestId('defects-release-scope')).toHaveAttribute('aria-pressed', 'true')
    await visualRegion(page, 'defects-scope-chip', theme, verdict)
    assertHermetic(api, errors)
  })
}
