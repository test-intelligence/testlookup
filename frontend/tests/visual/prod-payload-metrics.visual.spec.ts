/**
 * W23d "payload-metrics" baselines (`docs/deferred-scope-2026-10-13/work/W23d-contract.md` §6.3, §6.5 — the four
 * NEW regions; NO existing region changes), three themes each (`THEMES_WITH_PAPER`: signal, lab, paper), each region
 * the in-page container itself, blurred, at `PINNED_TALL` (1280 × 4000, nothing scrolled, as `visualRegion` requires):
 *
 *  - `test-case-http-exchanges`: the test page's HTTP exchanges tab — one exchange opened (Request / Response, the
 *    `[REDACTED]` header badges, a pretty JSON body), the rest collapsed (AGT-14);
 *  - `test-case-http-exchanges-empty`: the same tab with nothing captured — the "none" and "how" sentences;
 *  - `run-specialist-evidence`: the decision report's Specialist evidence row — the API contract card (the drift
 *    violation) and the Log intelligence card (a 40-point spark and its sentence) (AGT-14 / AGT-15);
 *  - `integrations-prometheus-card`: Settings › Integrations › Prometheus, not configured, with the default templates
 *    and the probe answer an unconfigured block gets (`Test connection: skipped — …`) (AGT-15).
 *
 * Hermetic fixtures: `production/fixtures-payload-metrics.ts` (G-A). PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { TEST_CASE_NAME, TEST_CASE_PATH } from './production/fixtures-pages'
import { ADMIN } from './production/fixtures-integrations-runtime'
import { QA_ENGINEER } from './production/fixtures-releases'
import { integrationsPrometheusWorld, runSpecialistWorld, testCaseHttpWorld } from './production/fixtures-payload-metrics'

test.use(PINNED_TALL)

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const testReady = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_CASE_NAME })
const httpPanel = (p: Page) => p.locator('[data-test-case-tab="http"]')

for (const theme of THEMES_WITH_PAPER) {
  test(`the HTTP exchanges tab, one exchange open — ${theme}`, async ({ page }) => {
    const world = testCaseHttpWorld({ role: QA_ENGINEER })
    const { api, errors } = await openRollout(page, `${TEST_CASE_PATH}?tab=http`, { theme, handlers: world.handlers, ready: testReady, user: QA_ENGINEER })
    const panel = httpPanel(page)
    const first = panel.locator('[data-http-exchange]').first()
    await first.getByRole('button', { expanded: false }).click()
    await expect(first.locator('[data-http-side="response"] [data-http-body]')).toBeVisible()
    await expect(first.locator('[data-redacted]').first()).toHaveText('[REDACTED]')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'test-case-http-exchanges', theme, panel)
    assertHermetic(api, errors)
  })

  test(`the HTTP exchanges tab, nothing captured — ${theme}`, async ({ page }) => {
    const world = testCaseHttpWorld({ role: QA_ENGINEER, answer: 'empty' })
    const { api, errors } = await openRollout(page, `${TEST_CASE_PATH}?tab=http`, { theme, handlers: world.handlers, ready: testReady, user: QA_ENGINEER })
    const panel = httpPanel(page)
    await expect(panel.getByText('No HTTP exchanges were captured for this test.', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'test-case-http-exchanges-empty', theme, panel)
    assertHermetic(api, errors)
  })

  test(`the Specialist evidence row — ${theme}`, async ({ page }) => {
    const world = runSpecialistWorld({ findings: 'drift' })
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=evidence`, {
      theme, handlers: world.handlers, ready: (p) => p.getByRole('heading', { name: 'Decision intelligence' }),
    })
    const row = page.locator('[data-specialist-evidence]')
    await expect(row.locator('[data-contract-violation]').first()).toBeVisible()
    await expect(row.getByRole('img', { name: /^p95 latency for checkout: 40 points/ })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'run-specialist-evidence', theme, row)
    assertHermetic(api, errors)
  })

  test(`the Prometheus card, not configured, with the defaults and the skipped probe — ${theme}`, async ({ page }) => {
    const world = integrationsPrometheusWorld({ role: ADMIN, probeAnswer: 'skipped' })
    const { api, errors } = await openRollout(page, '/settings/integrations', {
      theme, handlers: world.handlers, ready: (p) => p.getByRole('heading', { level: 1, name: 'Integrations', exact: true }), user: ADMIN,
    })
    const card = page.locator('[data-prometheus-card]')
    await expect(card.getByText('Defaults in use', { exact: true })).toBeVisible()
    await card.getByRole('button', { name: 'Test connection' }).click()
    await expect(card.getByText('Test connection: skipped — Prometheus is switched off or has no URL', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'integrations-prometheus-card', theme, card)
    assertHermetic(api, errors)
  })
}
