/**
 * W10 INT-01 + INT-02 on `/settings/integrations`, hermetic (`docs/deferred-scope-2026-10-13/work/W10-contract.md`
 * §2.1, §2.2, §4), at 1280 and 1440:
 *
 *  - as ADMIN, changing ONLY the Splunk base URL sends exactly one `PUT /api/v1/settings/integrations` whose body is
 *    `{"splunk_base_url": "https://splunk.corp.example:8089"}` (the page no longer echoes the values the server showed
 *    from the environment); Save with nothing changed says `No changes to save` and sends nothing;
 *  - a refused host (the world answers the egress guard's 422 `target_not_allowed`) shows `Target host is not allowed`
 *    under Base URL, in the page, with no toast, and the form keeps the typed value;
 *  - the environment hints render (`From the deployment environment`, a token `(set in the environment)`);
 *  - the Slack card has Webhook URL and the help line linking to `/settings/team-channels`, no Default Channel;
 *  - as QA LEAD every field is disabled, there is no Save, the load's GET only (one per mount: the dev server's
 *    StrictMode mounts twice) and no write;
 *  - `#main-content` never scrolls sideways (each width's slack is printed as an `INPAGE` line).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-integrations-runtime.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { expectMainFits, inPageLine, mainOverflow, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  ADMIN,
  BLOCKED_SPLUNK_URL,
  ENV_SPLUNK_URL,
  integrationsWorld,
  NEW_SPLUNK_URL,
  QA_LEAD,
  TARGET_NOT_ALLOWED,
} from '../visual/production/fixtures-integrations-runtime'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const READ = '/api/v1/settings/integrations'
const ENV_HINT = 'From the deployment environment'
const SLACK_HELP = 'The channel is fixed by the incoming webhook. Route other channels per team in Team channels.'
const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Integrations', exact: true })
const card = (page: Page, name: string): Locator =>
  page.locator('.card').filter({ has: page.getByRole('heading', { level: 3, name, exact: true }) })
const toast = (page: Page, text: string | RegExp) => page.getByRole('status').filter({ hasText: text })

/**
 * The page's requests to `/settings/integrations`: the load's GETs (the dev server's React StrictMode runs the load
 * effect twice, as `SHELL_BASE` lists `auth/me` and `projects` twice; production asks once), then `puts` PUTs.
 */
function expectSettingsRequests(lines: string[], puts: number) {
  const gets = lines.filter((line) => line === `GET ${READ}`)
  expect(gets.length, 'the load reads the settings (once per mount)').toBeGreaterThanOrEqual(1)
  expect(gets.length, 'and nothing re-reads them').toBeLessThanOrEqual(2)
  expect(lines.slice(0, gets.length), 'the reads come first').toEqual(gets)
  expect(lines.slice(gets.length), 'then the writes').toEqual(Array.from({ length: puts }, () => `PUT ${READ}`))
}

/** `#main-content` fits, and the slack of the given cards is printed (CI's DejaVu is wider than Windows' fonts). */
async function expectFits(page: Page, width: number, what: string, cards: Record<string, Locator>) {
  await expectMainFits(page, `${what} @${width}`)
  const o = await mainOverflow(page)
  const slack: Record<string, number> = { main: o.clientWidth - o.scrollWidth }
  for (const [name, locator] of Object.entries(cards)) slack[name] = await minContentSlack(locator)
  inPageLine(`/settings/integrations ${what}`, width, slack)
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`as ADMIN, a changed Splunk base URL is the whole PUT body; nothing changed sends nothing — ${width}`, async ({ page }) => {
      const world = integrationsWorld()
      const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
      const splunk = card(page, 'Splunk')
      const baseUrl = splunk.getByLabel('Base URL', { exact: true })
      await expect(baseUrl).toHaveValue(ENV_SPLUNK_URL)
      // The environment hints: a text field's line under its input, a token's qualifier in its label.
      await expect(splunk.locator('[data-env-hint="splunk_base_url"]')).toHaveText(ENV_HINT)
      await expect(card(page, 'OpenShift / Kubernetes').locator('[data-env-hint="ocp_default_namespace"]')).toHaveText(ENV_HINT)
      await expect(splunk.getByLabel('API Token (set in the environment)', { exact: true })).toBeVisible()
      await expect(card(page, 'Jira').getByLabel('API Token (set)', { exact: true })).toBeVisible()
      await expect(page.locator('[data-env-hint]')).toHaveCount(2)
      await networkQuiet(page, api)

      // Nothing changed: the inline line, no request.
      await page.getByRole('button', { name: 'Save Integrations' }).click()
      await expect(page.locator('[data-no-changes]')).toHaveText('No changes to save')
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])

      await baseUrl.fill(NEW_SPLUNK_URL)
      await expect(splunk.locator('[data-env-hint="splunk_base_url"]'), 'a typed value is no longer the environment\'s').toHaveCount(0)
      await page.getByRole('button', { name: 'Save Integrations' }).click()
      await expect(toast(page, 'Integrations saved')).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: 'PUT /api/v1/settings/integrations', body: { splunk_base_url: NEW_SPLUNK_URL } }])
      expectSettingsRequests(requestsTo(api, READ), 1)
      await expect(baseUrl).toHaveValue(NEW_SPLUNK_URL)
      await expect(splunk.locator('[data-env-hint="splunk_base_url"]'), 'the saved value is the page\'s now').toHaveCount(0)
      await expectNoOverlay(page)
      await expectFits(page, width, 'admin save', { splunk, slack: card(page, 'Slack') })
      assertHermetic(api, errors)
    })

    test(`a refused host: the sentence under Base URL, no toast, the typed value kept — ${width}`, async ({ page }) => {
      const world = integrationsWorld()
      const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
      const splunk = card(page, 'Splunk')
      const baseUrl = splunk.getByLabel('Base URL', { exact: true })
      await baseUrl.fill(BLOCKED_SPLUNK_URL)
      await page.getByRole('button', { name: 'Save Integrations' }).click()

      const error = splunk.locator('[data-field-error="splunk_base_url"]')
      await expect(error).toHaveText(TARGET_NOT_ALLOWED)
      await expect(error).toHaveAttribute('role', 'alert')
      await expect(baseUrl).toHaveAttribute('aria-invalid', 'true')
      const describedBy = (await baseUrl.getAttribute('aria-describedby')) ?? ''
      expect(describedBy.split(' '), 'the input is described by its error').toContain(await error.getAttribute('id'))
      await expect(baseUrl, 'the form keeps what was typed').toHaveValue(BLOCKED_SPLUNK_URL)
      await expect(splunk.getByLabel('API Token (set in the environment)', { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      await expect(page.getByText(TARGET_NOT_ALLOWED, { exact: true }), 'the sentence is said once, under its field').toHaveCount(1)
      await expect(toast(page, /Could not save|Integrations saved|Target host/), 'no toast').toHaveCount(0)
      expect(world.writes).toEqual([{ line: 'PUT /api/v1/settings/integrations', body: { splunk_base_url: BLOCKED_SPLUNK_URL } }])
      expect(world.config.splunk_base_url, 'nothing was applied').toBe(ENV_SPLUNK_URL)

      // Typing again clears the field's error; the next Save sends the new value only.
      await baseUrl.fill(NEW_SPLUNK_URL)
      await expect(error).toHaveCount(0)
      await page.getByRole('button', { name: 'Save Integrations' }).click()
      await expect(toast(page, 'Integrations saved')).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.body)).toEqual([{ splunk_base_url: BLOCKED_SPLUNK_URL }, { splunk_base_url: NEW_SPLUNK_URL }])
      await expectNoOverlay(page)
      await expectFits(page, width, 'refused host', { splunk })
      assertHermetic(api, errors)
    })

    test(`the Slack card: Webhook URL and the help line to Team channels, no Default Channel — ${width}`, async ({ page }) => {
      const world = integrationsWorld()
      const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: ADMIN })
      const slack = card(page, 'Slack')
      await expect(slack.getByLabel('Webhook URL (set)', { exact: true })).toBeVisible()
      const help = slack.locator('[data-slack-channel-help]')
      await expect(help).toHaveText(SLACK_HELP)
      await expect(help.getByRole('link', { name: 'Team channels', exact: true })).toHaveAttribute('href', '/settings/team-channels')
      await expect(page.getByLabel('Default Channel')).toHaveCount(0)
      await expect(page.getByText('Default Channel')).toHaveCount(0)
      // One field: the webhook URL (a password input) beside the Enabled switch.
      await expect(slack.getByRole('textbox')).toHaveCount(1)
      await expect(slack.getByRole('textbox')).toHaveAccessibleName('Webhook URL (set)')
      await expect(slack.locator('input')).toHaveCount(2)
      await networkQuiet(page, api)
      expectSettingsRequests(requestsTo(api, READ), 0)
      await expectFits(page, width, 'slack card', { slack })
      assertHermetic(api, errors)
    })

    test(`as QA LEAD every field is disabled, no Save, the load's GET only and no write — ${width}`, async ({ page }) => {
      const world = integrationsWorld()
      const { api, errors } = await openRollout(page, '/settings/integrations', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      const form = page.locator('[data-integrations-form]')
      await expect(form.locator('input')).not.toHaveCount(0)
      for (const input of await form.locator('input').all()) await expect(input).toBeDisabled()
      await expect(page.getByRole('button', { name: 'Save Integrations' })).toHaveCount(0)
      // The hints are read-only information: a QA lead sees where each value comes from too.
      await expect(card(page, 'Splunk').locator('[data-env-hint="splunk_base_url"]')).toHaveText(ENV_HINT)
      await networkQuiet(page, api)
      expectSettingsRequests(requestsTo(api, READ), 0)
      expect(world.writes).toEqual([])
      await expectFits(page, width, 'qa lead', { splunk: card(page, 'Splunk') })
      assertHermetic(api, errors)
    })
  })
}
