/**
 * W24 ADM-05 phase 1 (`docs/deferred-scope-2026-10-13/work/W24-contract.md` §4.1, §6.3, §6.5): three NEW regions on
 * /settings/project-data as an ADMIN, every theme (`THEMES_WITH_PAPER`), each a state the product produces together:
 *
 *  - `project-data-export-ready` — the Export card with a finished export: "4,210 rows in 4 tables", "Signed by this
 *    deployment", "Available until …", Export again and Download (2.1 MB);
 *  - `project-data-export-running` — the Export card while the export runs: the spinner, "Exporting… 12 of 30
 *    tables" and Export disabled (the job keeps answering running);
 *  - `project-data-import-report` — the Import card after an import finished in this visit: the chosen file, "Created
 *    Payment Service (imported)." + Open Payment Service (imported), the signature, five inserted rows (the project, its
 *    catalog and the switched-off webhooks), one switched-off line and one skipped line in the singular.
 *
 * Each region is the card itself (`[data-project-export]` / `[data-project-import]`) in the 732 px settings column.
 * World: `projectDataWorld({...})` in `production/fixtures-in-page-b.ts`. PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux) and READ before merge; nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import {
  IMPORTED_PROJECT_NAME,
  PROJECT_DATA_EXPORT_JOB_ID,
  projectDataJob,
  projectDataWorld,
} from './production/fixtures-in-page-b'
import { ADMIN } from './production/fixtures-releases'

test.use(PINNED)

const exportCard = (p: Page) => p.locator('[data-project-export]')
const importCard = (p: Page) => p.locator('[data-project-import]')
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

for (const theme of THEMES_WITH_PAPER) {
  test(`the Export card with a finished export — ${theme}`, async ({ page }) => {
    const world = projectDataWorld({ exports: [projectDataJob('export', 'succeeded')] })
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: exportCard, user: ADMIN })
    await expect(exportCard(page).getByRole('button', { name: 'Download (2.1 MB)' })).toBeVisible()
    await expect(exportCard(page).getByText('Signed by this deployment', { exact: true })).toBeVisible()
    await expect(exportCard(page).getByText(/^Available until .+\.$/)).toBeVisible()
    await networkQuiet(page, api)
    await visualRegion(page, 'project-data-export-ready', theme, exportCard(page))
    assertHermetic(api, errors)
  })

  test(`the Export card while the export runs — ${theme}`, async ({ page }) => {
    const running = projectDataJob('export', 'running')
    const world = projectDataWorld({ exports: [running], jobAnswers: { [PROJECT_DATA_EXPORT_JOB_ID]: [running] } })
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: exportCard, user: ADMIN })
    await expect(exportCard(page).getByText('Exporting… 12 of 30 tables')).toBeVisible()
    await expect(exportCard(page).getByRole('button', { name: 'Export', exact: true })).toBeDisabled()
    await visualRegion(page, 'project-data-export-running', theme, exportCard(page))
    assertHermetic(api, errors)
  })

  test(`the Import card after a finished import — ${theme}`, async ({ page }) => {
    const world = projectDataWorld()
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: importCard, user: ADMIN })
    await page.getByLabel('Export file (.zip)').setInputFiles({ name: 'payment-service.zip', mimeType: 'application/zip', buffer: Buffer.alloc(3 * 1024) })
    await importCard(page).getByRole('button', { name: 'Import', exact: true }).click()
    await expect(importCard(page).getByText(`Created ${IMPORTED_PROJECT_NAME}.`, { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(importCard(page).locator('[data-report-list="inserted"] li')).toHaveCount(5)
    await expect(importCard(page).locator('[data-report-list="switched-off"] li')).toHaveCount(1)
    await expect(importCard(page).getByText('1 row was skipped: it pointed at a row that is not in the export', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'project-data-import-report', theme, importCard(page))
    assertHermetic(api, errors)
  })
}
