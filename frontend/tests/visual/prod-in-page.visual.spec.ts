/**
 * W27a "in-page panels" baselines (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §6.5, the 16 NEW regions),
 * both themes: every detail and form that used to be an overlay, as it now sits IN the page.
 *
 *  - `/releases`: `releases-detail-open` (a release's pane beside the cards), `releases-edit-inline` (the editor
 *    inside the pane), `releases-confirm-inline` (Delete's strip in the pane footer, admin);
 *  - `/release-gate/:runId`: `release-gate-share-inline` (Share report under the header);
 *  - `/runs`: `runs-upload-inline`, `runs-signatures-inline`; `/runs/:id`: `run-test-pane` (a failing test beside the
 *    Tests list), `run-analysis-promote-inline` (File defect inside its failure block), `run-analysis-trail-inline`
 *    (the decision trail under the verdict banner);
 *  - `/failures`: `failures-row-mute` (Mute under its row), `failures-hints-inline` (Classification hints under the
 *    header); `/defects`: `defects-row-status` (Change status under its row), `defects-intake-inline` (New defect);
 *  - `/flaky`: `flaky-row-details`, `flaky-row-propose`; `/my-failures`: `inbox-row-triage`.
 *
 * Each region is the in-page container itself. The release pane and the test pane are as tall as the viewport lets
 * them be (`max-height: calc(100vh - 96px)`), so those loads use the 4000 px viewport (`TALL_VIEWPORT`): nothing
 * scrolls, as `visualRegion` requires. Hermetic fixtures: `production/fixtures-in-page.ts` and the worlds it
 * composes. PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, landmark, PINNED, TALL_VIEWPORT, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { GATE_CLUSTERS, releaseGateOn, RUN_ID } from './production/fixtures'
import { defectsWorld, QA_ENGINEER } from './production/fixtures-defects'
import {
  defectsIntakeWorld,
  failuresInPageWorld,
  inboxInPageWorld,
  releasesInPageWorld,
  runAnalysisWorld,
  runsUploadWorld,
} from './production/fixtures-in-page'
import { flakyHandlers } from './production/fixtures-pages'
import { ADMIN } from './production/fixtures-releases'

test.use(PINNED)

const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} `) })
const QUARANTINE_ON = { flaky_auto_quarantine: true }

test.describe('the tall panes', () => {
  test.use({ viewport: { ...TALL_VIEWPORT } })

  for (const theme of THEMES) {
    test(`a release open beside the cards, and its editor inside it — ${theme}`, async ({ page }) => {
      const { handlers } = releasesInPageWorld()
      const { api, errors } = await openRollout(page, '/releases', { theme, handlers, ready: releasesReady })
      await card(page, '2026.11').click()
      const pane = page.getByRole('complementary', { name: '2026.11', exact: true })
      await expect(pane.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()
      await networkQuiet(page, api)
      await visualRegion(page, 'releases-detail-open', theme, pane)

      await pane.getByRole('button', { name: 'Edit', exact: true }).click()
      const editor = pane.getByRole('region', { name: 'Edit Release' })
      await expect(editor.getByLabel('Release Name *')).toHaveValue('2026.11')
      // The focus ring is not part of the baseline.
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
      await networkQuiet(page, api)
      await visualRegion(page, 'releases-edit-inline', theme, editor)
      assertHermetic(api, errors)
    })

    test(`Delete's question in the release pane (admin) — ${theme}`, async ({ page }) => {
      const { handlers } = releasesInPageWorld()
      const { api, errors } = await openRollout(page, '/releases', { theme, handlers, ready: releasesReady, user: ADMIN })
      await card(page, '2026.12').click()
      const pane = page.getByRole('complementary', { name: '2026.12', exact: true })
      await pane.getByRole('button', { name: 'Delete', exact: true }).click()
      const strip = pane.locator('[data-inline-confirm]')
      await expect(strip).toContainText('Delete this release and all its phases? This cannot be undone.')
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
      await networkQuiet(page, api)
      await visualRegion(page, 'releases-confirm-inline', theme, strip)
      assertHermetic(api, errors)
    })

    test(`a failing test beside the Tests list — ${theme}`, async ({ page }) => {
      const world = runAnalysisWorld()
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, {
        theme,
        handlers: world.handlers,
        ready: (p) => p.locator('[data-primary] table'),
      })
      const first = world.tests.find((t) => t.status === 'BROKEN')
      await page.locator(`[data-test-row="${first?.id}"]`).click()
      const pane = page.getByRole('complementary', { name: String(first?.test_name), exact: true })
      await expect(pane.locator('[data-test-case-body]')).toBeVisible()
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
      await networkQuiet(page, api)
      await visualRegion(page, 'run-test-pane', theme, pane)
      assertHermetic(api, errors)
    })
  }
})

for (const theme of THEMES) {
  test(`Share report under the release gate's header — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, `/release-gate/${RUN_ID}`, {
      theme,
      handlers: [[`/api/v1/reports/runs/${RUN_ID}/share-links`, () => []], ...releaseGateOn({ clusters: GATE_CLUSTERS })],
      ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    })
    await page.getByRole('button', { name: 'More actions' }).click()
    await page.getByRole('menuitem', { name: /Share report/ }).click()
    const share = page.getByRole('region', { name: 'Share report' })
    await expect(share.getByText('No share links for this run yet.')).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'release-gate-share-inline', theme, share)
    assertHermetic(api, errors)
  })

  test(`the upload form and the failure signatures on Runs — ${theme}`, async ({ page }) => {
    const world = runsUploadWorld()
    const { api, errors } = await openRollout(page, '/runs', {
      theme,
      handlers: world.handlers,
      ready: (p) => p.locator('[data-status-banner]'),
      flags: { manual_upload: true },
    })
    await page.locator('[data-page-header]').getByRole('button', { name: 'Upload report' }).click()
    const upload = page.getByRole('region', { name: 'Upload test report' })
    await expect(upload.getByText('Drop report file(s) or click to browse')).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'runs-upload-inline', theme, upload)
    await upload.getByRole('button', { name: 'Cancel' }).click()
    await expect(upload).toHaveCount(0)

    await page.locator('[data-page-header]').getByRole('button', { name: /^Failure signatures/ }).click()
    const signatures = page.getByRole('complementary', { name: 'Failure signatures' })
    await expect(signatures.getByRole('meter', { name: 'Failed builds by signature' })).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'runs-signatures-inline', theme, signatures)
    assertHermetic(api, errors)
  })

  test(`File defect inside its failure block, and the decision trail under the banner — ${theme}`, async ({ page }) => {
    const world = runAnalysisWorld({ approval: null })
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, {
      theme,
      handlers: world.handlers,
      ready: (p) => p.locator('section[aria-label="What failed"]'),
    })
    await page.locator('section[aria-label="What failed"]').getByRole('button', { name: 'File defect' }).click()
    const promote = page.getByRole('region', { name: 'Promote to Defect' })
    await expect(promote.getByLabel('Title *')).toHaveValue('Gateway timeouts in the Payments suite')
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'run-analysis-promote-inline', theme, promote)
    await promote.getByRole('button', { name: 'Cancel' }).click()

    await page.locator('section[aria-label="Release verdict"]').getByRole('button', { name: 'Decision trail' }).click()
    const trail = page.getByRole('region', { name: 'AI decision trail' })
    await expect(trail.getByRole('searchbox', { name: 'Search decision trail' })).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'run-analysis-trail-inline', theme, trail)
    assertHermetic(api, errors)
  })

  test(`Mute under its row, and Classification hints under the header, on Failures — ${theme}`, async ({ page }) => {
    const world = failuresInPageWorld()
    const { api, errors } = await openRollout(page, '/failures?tab=categories', {
      theme,
      handlers: world.handlers,
      ready: (p) => landmark(p, 'Failure verdict'),
      flags: QUARANTINE_ON,
    })
    const table = page.locator('[data-primary]').getByRole('table', { name: 'Top failing tests' })
    await table.getByRole('button', { name: 'Mute test checkout total includes tax' }).click()
    const mute = opened(page, 'Mute test (propose quarantine)')
    await mute.getByRole('textbox').fill('Flaky on the shared runner')
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'failures-row-mute', theme, mute)
    await mute.getByRole('button', { name: 'Cancel' }).click()

    await page.getByTestId('open-classification-hints').click()
    const hints = opened(page, 'Classification hints')
    await expect(hints.locator('[data-hint-row]')).toHaveCount(1)
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'failures-hints-inline', theme, hints)
    assertHermetic(api, errors)
  })

  test(`Change status under its row on Defects — ${theme}`, async ({ page }) => {
    const { handlers } = defectsWorld()
    const { api, errors } = await openRollout(page, '/defects', { theme, handlers, ready: (p) => landmark(p, 'Defect KPIs'), user: QA_ENGINEER })
    const row = page.getByRole('table', { name: 'Defects' }).locator('tbody > tr:not([data-in-page-row])').filter({ hasText: 'search paginates' })
    await row.getByRole('button', { name: 'Defect actions' }).click()
    await page.getByRole('menuitem', { name: 'Change status…' }).click()
    const status = opened(page, 'Change status')
    await expect(status.getByRole('radio', { name: 'Open', exact: true })).toBeChecked()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'defects-row-status', theme, status)
    assertHermetic(api, errors)
  })

  test(`New defect under the header on Defects — ${theme}`, async ({ page }) => {
    const { handlers } = defectsIntakeWorld()
    const { api, errors } = await openRollout(page, '/defects', { theme, handlers, ready: (p) => landmark(p, 'Defect KPIs') })
    await page.locator('[data-page-header]').getByRole('button', { name: 'New defect' }).click()
    const intake = opened(page, 'New defect')
    await expect(intake.getByLabel(/^Title/)).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'defects-intake-inline', theme, intake)
    assertHermetic(api, errors)
  })

  test(`a detected test's details and Propose quarantine under their rows on Flaky tests — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/flaky', {
      theme,
      handlers: flakyHandlers(),
      ready: (p) => p.locator('[data-page-header] h1'),
      flags: QUARANTINE_ON,
    })
    const table = page.locator('[data-primary]').getByRole('table', { name: 'Flaky tests' })
    await table.getByRole('button', { name: 'test_checkout_flow_4', exact: true }).click()
    const details = page.getByRole('complementary', { name: 'test_checkout_flow_4', exact: true })
    await expect(details).toBeVisible()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'flaky-row-details', theme, details)
    await details.getByRole('button', { name: 'Close panel' }).click()

    await table.locator('tr[data-flaky-row="fp-04"]').getByRole('button', { name: 'Propose quarantine' }).click()
    const propose = opened(page, 'Propose quarantine')
    await propose.getByRole('textbox').fill('Flips on CI only')
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'flaky-row-propose', theme, propose)
    assertHermetic(api, errors)
  })

  test(`Update triage status under its row in the Inbox — ${theme}`, async ({ page }) => {
    const { handlers } = inboxInPageWorld()
    const { api, errors } = await openRollout(page, '/my-failures', { theme, handlers, ready: (p) => p.locator('[data-page-header] h1') })
    const row = page
      .getByRole('table', { name: 'Assigned failures' })
      .locator('tbody > tr:not([data-in-page-row])')
      .filter({ has: page.getByText('test_checkout_step_1', { exact: true }) })
    await row.getByRole('button', { name: 'Update status' }).click()
    const triage = opened(page, 'Update triage status')
    await expect(triage.getByRole('radio', { name: /^Reviewed & approved/ })).toBeChecked()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await networkQuiet(page, api)
    await visualRegion(page, 'inbox-row-triage', theme, triage)
    assertHermetic(api, errors)
  })
}
