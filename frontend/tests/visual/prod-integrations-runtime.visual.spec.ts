/**
 * W10 "integrations-runtime" baselines (`docs/deferred-scope-2026-10-13/work/W10-contract.md` §6.5, §7.3, the nine
 * NEW regions), three themes each (`THEMES_WITH_PAPER`: signal, lab, paper), each region the in-page container
 * itself, blurred, at `PINNED_TALL` (1280 × 4000, nothing scrolled, as `visualRegion` requires):
 *
 *  - `integrations-slack-card`: the Slack card, one field (Webhook URL) and the help line to Team channels (INT-02);
 *  - `integrations-splunk-not-allowed`: the Splunk card after the egress guard's 422 — `Target host is not allowed`
 *    under Base URL, the token's `(set in the environment)` (INT-01);
 *  - `projects-edit-index-invalid`: Edit Project after the server refused `main | delete` as the Splunk index, the
 *    sentence under the field, the two help lines, no Jenkins Job Pattern (INT-03);
 *  - `integration-health-project-rows`: the per-project GitHub / GitLab rows (healthy on GHE, auth_error, Not probed
 *    yet) (INT-04);
 *  - `integration-health-paused-webhooks`: the Paused webhooks section (INT-04 + INT-07);
 *  - `webhooks-row-paused`: a subscription the breaker paused, PAUSED, its line and Re-enable (INT-07);
 *  - `run-changes-commits-offline`, `run-changes-commits-no-integration`, `run-changes-commits-list`: the Changes
 *    tab's card offline, without GitHub, and listing a range counted from a weak anchor (FAIL-09).
 *
 * The one CHANGED region, `projects-edit-inline` (signal, lab: Jenkins gone, the two help lines in), is NOT shot here:
 * a baseline's path is its region name alone (`{testDir}/__screenshots__/{platform}/{arg}{ext}`), so it stays with its
 * one writer, the UNEDITED `prod-in-page-b.visual.spec.ts`; its new content is asserted, without a shot, by the last
 * test below.
 *
 * Hermetic fixtures: `production/fixtures-integrations-runtime.ts`. PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, PINNED_TALL, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import { LEGACY_PROJECT, projectsInPageWorld } from './production/fixtures-in-page-b'
import {
  ADMIN,
  BAD_SPLUNK_INDEX,
  BLOCKED_SPLUNK_URL,
  EDITED_PROJECT,
  integrationHealthWorld,
  integrationsWorld,
  PAUSED_WEBHOOK,
  PAUSED_WEBHOOK_NAME,
  projectsIntegrationWorld,
  QA_LEAD,
  runChangesWorld,
  SPLUNK_INDEX_SENTENCE,
  TARGET_NOT_ALLOWED,
  webhooksBreakerWorld,
  type RunChangesKind,
} from './production/fixtures-integrations-runtime'

test.use(PINNED_TALL)

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const integrationCard = (page: Page, name: string): Locator =>
  page.locator('.card').filter({ has: page.getByRole('heading', { level: 3, name, exact: true }) })
const projectCard = (page: Page, name: string): Locator => page.locator('.card').filter({ has: page.getByText(name, { exact: true }) })
const changesCard = (page: Page) => page.getByRole('region', { name: 'What changed since the last good run' })
const healthRow = (page: Page, title: string): Locator =>
  page.locator('[data-health-row]').filter({ has: page.getByText(title, { exact: true }) })

const RUN_CHANGES: Array<[RunChangesKind, string, (card: Locator) => Promise<void>]> = [
  ['offline', 'run-changes-commits-offline', (c) => expect(c.locator('[data-commit-range-reason]')).toHaveText('Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).')],
  ['no_integration', 'run-changes-commits-no-integration', (c) => expect(c.locator('[data-commit-range-reason]')).toContainText('Commit list unavailable: this project has no GitHub integration.')],
  ['listed', 'run-changes-commits-list', (c) => expect(c.getByText('Commits since build #239 (3)', { exact: true })).toBeVisible()],
]

for (const theme of THEMES_WITH_PAPER) {
  // ── /settings/integrations (INT-01, INT-02) ──
  test(`the Slack card: Webhook URL and the help line — ${theme}`, async ({ page }) => {
    const world = integrationsWorld()
    const { api, errors } = await openRollout(page, '/settings/integrations', { theme, handlers: world.handlers, ready: h1('Integrations'), user: ADMIN })
    const slack = integrationCard(page, 'Slack')
    await expect(slack.locator('[data-slack-channel-help]')).toContainText('Route other channels per team in Team channels.')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'integrations-slack-card', theme, slack)
    assertHermetic(api, errors)
  })

  test(`the Splunk card after a refused host — ${theme}`, async ({ page }) => {
    const world = integrationsWorld()
    const { api, errors } = await openRollout(page, '/settings/integrations', { theme, handlers: world.handlers, ready: h1('Integrations'), user: ADMIN })
    const splunk = integrationCard(page, 'Splunk')
    await splunk.getByLabel('Base URL', { exact: true }).fill(BLOCKED_SPLUNK_URL)
    await page.getByRole('button', { name: 'Save Integrations' }).click()
    await expect(splunk.locator('[data-field-error="splunk_base_url"]')).toHaveText(TARGET_NOT_ALLOWED)
    await expect(splunk.getByLabel('API Token (set in the environment)', { exact: true })).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'integrations-splunk-not-allowed', theme, splunk)
    assertHermetic(api, errors)
  })

  // ── /projects › Edit Project (INT-03) ──
  test(`Edit Project after a refused Splunk index — ${theme}`, async ({ page }) => {
    const world = projectsIntegrationWorld()
    const { api, errors } = await openRollout(page, '/projects', { theme, handlers: world.handlers, ready: h1('Projects'), user: QA_LEAD })
    const card = projectCard(page, EDITED_PROJECT.name)
    await card.hover()
    await card.getByRole('button', { name: 'Edit' }).click()
    const edit = opened(page, 'Edit Project')
    await edit.getByLabel('Splunk Index', { exact: true }).fill(BAD_SPLUNK_INDEX)
    await edit.getByRole('button', { name: 'Save Changes' }).click()
    await expect(edit.locator('[data-field-error="splunk_index"]')).toHaveText(SPLUNK_INDEX_SENTENCE)
    await page.mouse.move(0, 0)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'projects-edit-index-invalid', theme, edit)
    assertHermetic(api, errors)
  })

  // ── /settings/integration-health (INT-04, INT-07) ──
  test(`the per-project integration rows — ${theme}`, async ({ page }) => {
    // No paused webhook in this world: the region is the project rows alone (the paused section has its own).
    const world = integrationHealthWorld({ viewer: 'ADMIN', pausedWebhook: false })
    const { api, errors } = await openRollout(page, '/settings/integration-health', { theme, handlers: world.handlers, ready: h1('Integration Health'), user: ADMIN })
    await expect(healthRow(page, 'GitLab · API Gateway')).toContainText('Not probed yet')
    const section = page.locator('[data-health-project-rows]')
    await expect(section.locator('[data-health-row]')).toHaveCount(3)
    await expect(section.locator('[data-health-paused-webhooks]')).toHaveCount(0)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'integration-health-project-rows', theme, section)
    assertHermetic(api, errors)
  })

  test(`the paused webhooks section — ${theme}`, async ({ page }) => {
    const world = integrationHealthWorld({ viewer: 'ADMIN' })
    const { api, errors } = await openRollout(page, '/settings/integration-health', { theme, handlers: world.handlers, ready: h1('Integration Health'), user: ADMIN })
    const section = page.locator('section[data-health-paused-webhooks]')
    await expect(healthRow(page, `Webhook ${PAUSED_WEBHOOK_NAME} · Checkout`).locator('[data-health-paused-line]')).toHaveText('Paused after 3 consecutive failures (threshold 3)')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'integration-health-paused-webhooks', theme, section)
    assertHermetic(api, errors)
  })

  // ── /settings/webhooks (INT-07) ──
  test(`a subscription the breaker paused — ${theme}`, async ({ page }) => {
    const world = webhooksBreakerWorld()
    const { api, errors } = await openRollout(page, '/settings/webhooks', { theme, handlers: world.handlers, ready: h1('Outbound Webhooks'), user: QA_LEAD })
    const card = page.locator('div.space-y-2 > div').filter({ has: page.getByRole('button', { name: `Delete ${PAUSED_WEBHOOK.name}` }) })
    await expect(card.getByRole('button', { name: 'Re-enable' })).toBeVisible()
    await expect(card.getByText(/^Paused after 3 consecutive failures/)).toBeVisible()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'webhooks-row-paused', theme, card)
    assertHermetic(api, errors)
  })

  // ── /runs/:id?tab=changes (FAIL-09) ──
  for (const [kind, region, ready] of RUN_CHANGES) {
    test(`the Changes tab's card, ${kind} — ${theme}`, async ({ page }) => {
      const world = runChangesWorld(kind)
      const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, { theme, handlers: world.handlers, ready: (p) => changesCard(p).getByText(/Baseline: Build #238/) })
      const card = changesCard(page)
      await ready(card)
      await blur(page)
      await networkQuiet(page, api)
      await visualRegion(page, region, theme, card)
      assertHermetic(api, errors)
    })
  }
}

// The CHANGED region is shot by `prod-in-page-b.visual.spec.ts` (unedited, the same world and container); here only
// its new content is asserted, so the reader of the re-shot PNGs knows what each must show.
test.describe('the CHANGED region (shot by prod-in-page-b) holds the W10 change', () => {
  test('projects-edit-inline: no Jenkins Job Pattern, the Splunk Index and OCP Namespace help lines', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/projects', { handlers: projectsInPageWorld().handlers, ready: h1('Projects') })
    const card = projectCard(page, LEGACY_PROJECT.name)
    await card.hover()
    await card.getByRole('button', { name: 'Edit' }).click()
    const edit = opened(page, 'Edit Project')
    await expect(edit.getByLabel('Jenkins Job Pattern')).toHaveCount(0)
    await expect(edit.locator('#ep-splunk-help')).toHaveText("Used by AI log lookups for this project. Empty: the deployment's index.")
    await expect(edit.locator('#ep-ocp-help')).toHaveText("Used when a run names a pod but no namespace. Empty: the deployment's default namespace.")
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
