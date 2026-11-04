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
 *
 * W30 ADM-05 phase 2 (`work/W30-contract.md` §6.3, §6.5): three NEW regions, each a state the product produces in
 * one visit:
 *  - `project-data-export-history` — "Include run history" ticked, "Runs from the last" with the 90-day select, the
 *    history intro and the estimate line "About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments";
 *  - `project-data-export-history-running` — the same form after Export (box and select disabled), the export in its
 *    documents phase: the spinner, "Stored documents… 5,000 of 9,120", Export disabled;
 *  - `project-data-import-history-report` — the Import card after a history import finished in this visit (a small
 *    12 KB schema-2 archive goes through the single POST): W24's report + the History block — "2 runs from Sep 14,
 *    2026 to Sep 16, 2026", 6 + 2 stored documents, one 2 KB attachment, ONE export-side skip, the retention pause
 *    (no retention note: 0 runs past the 365-day window), "2 imported runs are from the last 7 days…" and the
 *    reindex line — numbers a 12 KB archive can carry (the big numbers are the estimate region's).
 * The existing regions change deliberately: the heading "Export project data", the new intro and the unticked
 * history row (`project-data-export-ready`, `project-data-export-running`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import {
  HISTORY_IMPORT_JOB_ID,
  historyDataJob,
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

  // ── W30: run history ───────────────────────────────────────────────────────

  test(`the Export card with run history ticked and its estimate — ${theme}`, async ({ page }) => {
    const world = projectDataWorld()
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: exportCard, user: ADMIN })
    await exportCard(page).getByRole('checkbox', { name: 'Include run history' }).check()
    await expect(exportCard(page).getByRole('combobox')).toHaveValue('90')
    await expect(exportCard(page).getByText('About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments', { exact: true })).toBeVisible()
    await expect(exportCard(page).getByRole('button', { name: 'Export', exact: true })).toBeEnabled()
    await blur(page)
    await networkQuiet(page, api)
    expect(world.estimates).toEqual([90])
    await visualRegion(page, 'project-data-export-history', theme, exportCard(page))
    assertHermetic(api, errors)
  })

  test(`the Export card while a history export writes its documents — ${theme}`, async ({ page }) => {
    const documents = historyDataJob('export', 'documents')
    const world = projectDataWorld({ historyJob: { exportAnswers: [documents] } })
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: exportCard, user: ADMIN })
    await exportCard(page).getByRole('checkbox', { name: 'Include run history' }).check()
    await expect(exportCard(page).getByText('About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments', { exact: true })).toBeVisible()
    await exportCard(page).getByRole('button', { name: 'Export', exact: true }).click()
    await expect(exportCard(page).getByText('Stored documents… 5,000 of 9,120', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(exportCard(page).getByRole('button', { name: 'Export', exact: true })).toBeDisabled()
    await expect(exportCard(page).getByRole('combobox')).toBeDisabled()
    await blur(page)
    expect(world.writes.map((w) => w.body)).toEqual([{ include_history: true, history_days: 90, max_runs: 2000 }])
    await visualRegion(page, 'project-data-export-history-running', theme, exportCard(page))
    assertHermetic(api, errors)
  })

  test(`the Import card after a finished history import — ${theme}`, async ({ page }) => {
    const world = projectDataWorld({
      importPost: historyDataJob('import', 'queued'),
      jobAnswers: { [HISTORY_IMPORT_JOB_ID]: [historyDataJob('import', 'succeeded')] },
    })
    const { api, errors } = await openRollout(page, '/settings/project-data', { theme, handlers: world.handlers, ready: importCard, user: ADMIN })
    // A 12 KB archive and what it can carry (the W30 baseline reading: a 3 KB file beside "412 runs … 380.0 MB" was
    // a state the product cannot produce): 2 runs, 6 results, 6 + 2 documents, one 2 KB attachment.
    await page.getByLabel('Export file (.zip)').setInputFiles({ name: 'payment-history.zip', mimeType: 'application/zip', buffer: Buffer.alloc(12 * 1024) })
    await expect(importCard(page).getByText('payment-history.zip · 12 KB', { exact: true })).toBeVisible()
    await importCard(page).getByRole('button', { name: 'Import', exact: true }).click()
    await expect(importCard(page).getByText(`Created ${IMPORTED_PROJECT_NAME}.`, { exact: true })).toBeVisible({ timeout: 15_000 })
    const history = importCard(page).locator('[data-import-history]')
    await expect(history.getByText('2 runs from Sep 14, 2026 to Sep 16, 2026', { exact: true })).toBeVisible()
    await expect(history.locator('[data-report-list="history-documents"] li')).toHaveCount(2)
    await expect(history.getByText('1 attachment · 2 KB', { exact: true })).toBeVisible()
    await expect(history.locator('[data-report-list="history-skipped"] li')).toHaveCount(1)
    await expect(history.getByText('1 attachment was skipped: it is larger than 256 MB', { exact: true })).toBeVisible()
    await expect(history.getByText(/^The retention purge of the new project is paused until .+\.$/)).toBeVisible()
    await expect(history.getByText(/retention window|Retention is off/), '0 runs past a 365-day window: no retention note').toHaveCount(0)
    await expect(history.getByText('2 imported runs are from the last 7 days; an all-projects digest can list them.', { exact: true })).toBeVisible()
    await expect(history.getByText('Search is being rebuilt for the new project.', { exact: true })).toBeVisible()
    await expect(importCard(page).locator('[data-report-list="inserted"] li', { hasText: 'test_runs' })).toHaveText('test_runs: 2 rows')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'project-data-import-history-report', theme, importCard(page))
    assertHermetic(api, errors)
  })
}
