/**
 * W22b baselines for the Retention page (`docs/deferred-scope-2026-10-13/work/W22b-contract.md` §6.3, §6.5), the
 * seven NEW regions, in signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `retention-export-choice`: the criteria preview's export step, `Download an archive first` + `Evidence` chosen;
 *  - `deletion-jobs-card`: the Deletion jobs card — awaiting collection, a completed retain export (ratio and net),
 *    an expired job, a failed export;
 *  - `deletion-job-collect-inline`: the collect row after a complete download (`I have saved it` enabled);
 *  - `retention-schedule`: the purge window 03:00 and the pause (`Paused until …`, `Resume`);
 *  - `retention-policy-inherited`: the day fields — two inherited from the instance, one the project's value;
 *  - `retention-instance-defaults`: the Instance defaults card opened (off);
 *  - `retention-instance-enable-preview`: its enable preview — three affected projects and the phrase input.
 *
 * Each region is the component's own container; the focus ring is not part of a baseline (blurred). The 4000 px
 * viewport (`PINNED_TALL`) keeps every region on screen with nothing scrolled, as `visualRegion` requires. World:
 * `production/fixtures-retention-b.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody
 * regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { NOW } from './production/fixtures'
import {
  retentionExportWorld,
  worldAwaitingJob,
  worldJobs,
  type RetentionExportWorldOptions,
} from './production/fixtures-retention-b'

test.use({ ...PINNED_TALL, timezoneId: 'UTC', locale: 'en-US' })

type Theme = (typeof THEMES_WITH_PAPER)[number]
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const jobsCard = (page: Page) => page.locator('[data-deletion-jobs]')
const instanceCard = (page: Page) => page.locator('[data-retention-instance-defaults]')

async function openRetention(page: Page, theme: Theme, options: RetentionExportWorldOptions = {}) {
  const world = retentionExportWorld({ role: 'ADMIN', ...options })
  const opened = await openRollout(page, '/settings/retention', { theme, handlers: world.handlers, ready: h1('Retention & Purge'), user: world.user })
  await expect(page.getByRole('button', { name: 'Save policy' })).toBeVisible()
  await expect(jobsCard(page)).toBeVisible()
  await networkQuiet(page, opened.api)
  return { world, ...opened }
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the export step — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    await page.getByLabel('Older than days').fill('30')
    await page.getByRole('button', { name: 'Preview', exact: true }).click()
    const step = page.getByTestId('criteria-preview').locator('[data-criteria-export]')
    await step.getByRole('radio', { name: 'Download an archive first' }).check()
    await step.getByRole('radio', { name: /^Evidence — archive/ }).check()
    await expect(step.getByText('Reclaims ≈ 180 MB', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'retention-export-choice', theme, step)
    assertHermetic(api, errors)
  })

  test(`the deletion jobs card — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme, { jobs: worldJobs() })
    await expect(jobsCard(page).locator('tbody tr[data-deletion-job]')).toHaveCount(4)
    await expect(jobsCard(page).getByText('net 166 MB · ratio 1:13', { exact: true })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'deletion-jobs-card', theme, jobsCard(page))
    assertHermetic(api, errors)
  })

  test(`the collect row after a download — ${theme}`, async ({ page }) => {
    const job = worldAwaitingJob()
    const { api, errors } = await openRetention(page, theme, { jobs: [job] })
    await jobsCard(page).getByRole('button', { name: 'Collect', exact: true }).click()
    const row = jobsCard(page).locator(`[data-deletion-job-collect="${job.id}"]`)
    const saved = page.waitForEvent('download')
    await row.getByRole('button', { name: /^Download archive/ }).click()
    await saved
    await expect(row.getByRole('button', { name: 'I have saved it' })).toBeEnabled()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'deletion-job-collect-inline', theme, row)
    assertHermetic(api, errors)
  })

  test(`the purge window and the pause — ${theme}`, async ({ page }) => {
    const pausedUntil = new Date(NOW.getTime() + 7 * 86_400_000).toISOString()
    const { api, errors } = await openRetention(page, theme, { policy: { purge_window_start_utc: 3, purge_paused_until: pausedUntil } })
    const schedule = page.locator('[data-retention-schedule]')
    await expect(schedule.getByText('Paused until Sep 25, 2026, 12:00', { exact: true })).toBeVisible()
    await expect(schedule.locator('#retention-purge_window_start_utc')).toHaveValue('3')
    await blur(page)
    await visualRegion(page, 'retention-schedule', theme, schedule)
    assertHermetic(api, errors)
  })

  test(`the inherited day fields — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme, {
      policy: { raw_events_days: 30, runs_days: null, artifacts_days: null },
      instance: { runs_days: 180, artifacts_days: 120 },
    })
    const fields = page.locator('[data-retention-day-fields]')
    await expect(fields.locator('[data-retention-inherited="runs_days"]')).toContainText('180 days (instance default)')
    await expect(fields.locator('[data-retention-inherited="artifacts_days"]')).toContainText('120 days (instance default)')
    await expect(fields.locator('[data-retention-inherited="raw_events_days"]')).toContainText('Project value')
    await blur(page)
    await visualRegion(page, 'retention-policy-inherited', theme, fields)
    assertHermetic(api, errors)
  })

  test(`the Instance defaults card, opened (off) — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme, { instance: { runs_days: 180 } })
    await instanceCard(page).getByRole('button', { name: 'Open', exact: true }).click()
    await expect(instanceCard(page).locator('[data-retention-instance-form]')).toBeVisible()
    await expect(instanceCard(page).getByRole('button', { name: 'Turn on for every project without its own setting…' })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'retention-instance-defaults', theme, instanceCard(page))
    assertHermetic(api, errors)
  })

  test(`the Instance defaults enable preview — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRetention(page, theme)
    await instanceCard(page).getByRole('button', { name: 'Open', exact: true }).click()
    await instanceCard(page).getByRole('button', { name: 'Turn on for every project without its own setting…' }).click()
    const enable = instanceCard(page).locator('[data-retention-instance-enable]')
    await expect(enable.locator('[data-retention-instance-preview-row]')).toHaveCount(3)
    await expect(enable.getByLabel('Type "enable retention for 3 projects" to confirm')).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'retention-instance-enable-preview', theme, enable)
    assertHermetic(api, errors)
  })
}
