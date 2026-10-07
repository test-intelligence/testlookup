/**
 * UX redesign P5 items 2 + 4, hermetic, in the real shell at 1440 x 900 (a QA
 * lead, the fixture user):
 *
 *  - `/settings/notifications` is the admin page "Email (SMTP) & channels":
 *    the SMTP form, two columns, and the shared Slack / Teams channels read
 *    from the integrations config; none of the personal preferences, and no
 *    request for them;
 *  - `/settings/my-notifications` holds the personal preferences, its channel
 *    form two columns, and asks for neither the SMTP config nor the shared
 *    channels;
 *  - `/settings/team-channels` is a new page in the settings sub-nav (Project,
 *    after Ownership): the team channel table that was Ownership's last
 *    section, with Save (PUT) and Remove (DELETE) on the wire;
 *  - `/ownership` no longer asks for team channels and links to the new page.
 *
 * Each page's requests of one load are held to an inventory. The page
 * height (`#main-content` scrollHeight) is printed per page (FOLD line).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers } from '../lib/production-pages'
import { respond } from '../lib/production-pages'
import { MAIN, SHELL_BASE, expectInventory, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { LAYOUT, PROJECT_ID } from '../visual/production/fixtures'

const P = PROJECT_ID

const SMTP = {
  enabled: true,
  host: 'smtp.corp.example.com',
  port: 465,
  user: 'mailer@corp.example.com',
  from_address: 'alerts@corp.example.com',
  implicit_tls: true,
  password_set: true,
}

const INTEGRATIONS = {
  jira_enabled: false, jira_domain: null, jira_email: null, jira_token_set: false, jira_default_project_key: 'QA',
  splunk_enabled: false, splunk_base_url: null, splunk_token_set: false,
  ocp_enabled: false, ocp_api_url: null, ocp_token_set: false, ocp_default_namespace: 'default',
  slack_enabled: true, slack_webhook_url: null, slack_webhook_set: true, slack_default_channel: '#qa-alerts',
  teams_enabled: false, teams_webhook_url: null, teams_webhook_set: false,
  github_repo: null, github_token_set: false,
}

const SLACK_PREF = {
  id: '00000000-0000-4000-8000-000000000901',
  user_id: '00000000-0000-4000-8000-000000000001',
  project_id: null,
  channel: 'slack',
  enabled: true,
  events: ['run_failed', 'test.newly_failing'],
  failure_rate_threshold: 80,
  email_override: null,
  slack_webhook_url: 'https://hooks.slack.com/services/T000/B000/xyz',
  teams_webhook_url: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: null,
}

function rule(i: number, team: string) {
  return {
    id: `00000000-0000-4000-8000-0000000009${String(i).padStart(2, '0')}`,
    project_id: P,
    match_type: 'suite_name',
    match_pattern: `${team.toLowerCase()}-*`,
    service_name: `${team.toLowerCase()}-service`,
    team_name: team,
    team_contact: null,
    priority: 10 - i,
    is_active: true,
    created_by: null,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: null,
  }
}

const RULES = [rule(1, 'Payments'), rule(2, 'Identity')]

function teamChannel(team: string, target: string, channelType = 'slack') {
  return {
    id: `ch-${team}`,
    project_id: P,
    team_name: team,
    channel_type: channelType,
    target,
    is_active: true,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: null,
  }
}

const COVERAGE = { path_rules: 0, codeowners_rules: 0, sampled: 0, located: 0, matched: 0, coverage_pct: null, lookback_days: 30 }

/** Fresh state per test: a channel saved in one test never leaks into the next. */
function handlers(): ApiHandlers {
  const channels = new Map([['Identity', teamChannel('Identity', 'https://hooks.slack.com/services/identity')]])
  return [
    ...LAYOUT,
    ['/api/v1/settings/smtp', () => SMTP],
    ['/api/v1/settings/integrations', () => INTEGRATIONS],
    ['/api/v1/notifications/preferences', () => [SLACK_PREF]],
    [`/api/v1/projects/${P}/ownership/rules`, () => RULES],
    [`/api/v1/projects/${P}/ownership/codeowners/coverage`, () => COVERAGE],
    [`/api/v1/projects/${P}/ownership/team-channels`, () => [...channels.values()]],
    [
      new RegExp(`^/api/v1/projects/${P}/ownership/team-channels/[^/]+$`),
      ({ path, route }) => {
        const team = decodeURIComponent(path.split('/').pop() ?? '')
        const body = route.request().postDataJSON() as { channel_type: string; target: string }
        const saved = teamChannel(team, body.target, body.channel_type)
        channels.set(team, saved)
        return saved
      },
      'PUT',
    ],
    [
      new RegExp(`^/api/v1/projects/${P}/ownership/team-channels/[^/]+$`),
      ({ path }) => {
        channels.delete(decodeURIComponent(path.split('/').pop() ?? ''))
        return respond(204)
      },
      'DELETE',
    ],
  ]
}

const ready = (p: Page) => p.locator('[data-page-header] h1')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The page's height in the shell's scroller, printed for the report. */
async function logHeight(page: Page, route: string) {
  const scrollHeight = await page.locator(MAIN).evaluate((main) => main.scrollHeight)
  console.log(`FOLD ${route} ${JSON.stringify({ scrollHeight })}`)
}

/** Two fields share a row: the same top edge, the second to the right of the first. */
async function expectSameRow(page: Page, first: string | RegExp, second: string | RegExp) {
  const a = await page.getByLabel(first).boundingBox()
  const b = await page.getByLabel(second).boundingBox()
  expect(a, `${first} has a box`).not.toBeNull()
  expect(b, `${second} has a box`).not.toBeNull()
  expect(Math.abs((a as { y: number }).y - (b as { y: number }).y), `${first} and ${second} on one row`).toBeLessThan(2)
  expect((b as { x: number }).x).toBeGreaterThan((a as { x: number; width: number }).x + (a as { width: number }).width)
}

const SMTP_LINE = 'GET /api/v1/settings/smtp'
const INTEGRATIONS_LINE = 'GET /api/v1/settings/integrations'
const PREFERENCES_LINE = 'GET /api/v1/notifications/preferences'
const HISTORY_LINE = 'GET /api/v1/notifications/history?unread_only=false&limit=50'
const RULES_LINE = `GET /api/v1/projects/${P}/ownership/rules`
const CHANNELS_LINE = `GET /api/v1/projects/${P}/ownership/team-channels`
const COVERAGE_LINE = `GET /api/v1/projects/${P}/ownership/codeowners/coverage?days=30`

test('/settings/notifications is Email (SMTP) & channels: the server, two columns, and the shared channels', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/notifications', { handlers: handlers(), ready })
  await expect(ready(page)).toHaveText('Email (SMTP) & channels')
  await expect(page.locator('[data-settings-item="email"]')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByLabel(/^SMTP Host/)).toHaveValue('smtp.corp.example.com')
  await networkQuiet(page, api)
  await logHeight(page, '/settings/notifications')

  // Item 4: server beside sender, user beside password.
  await expectSameRow(page, /^SMTP Host/, /^From Address/)
  await expectSameRow(page, /^Username/, /^Password/)

  // The shared channels, as the dispatcher sees them.
  const shared = page.locator('[data-shared-channels]')
  await expect(shared.locator('[data-shared-channel="slack"]')).toContainText('Delivering')
  await expect(shared.locator('[data-shared-channel="teams"]')).toContainText('Off')
  await expect(shared.getByRole('link', { name: /Edit in Integrations/ })).toHaveAttribute('href', '/settings/integrations')

  // Nothing personal on the admin page.
  await expect(page.locator('[data-channel-card]')).toHaveCount(0)
  await expect(page.getByText('Notify me when')).toHaveCount(0)
  // The SMTP card reads in a plain effect, which the dev server's StrictMode
  // runs twice (once in a production build); the shared channels are SWR (deduped).
  expectInventory(api, errors, [...SHELL_BASE, SMTP_LINE, SMTP_LINE, INTEGRATIONS_LINE], '/settings/notifications')
})

test('/settings/my-notifications holds the personal preferences, two columns, and asks for nothing of the server', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: handlers(), ready })
  await expect(ready(page)).toHaveText('My notifications')
  await expect(page.locator('[data-settings-item="my-notifications"]')).toHaveAttribute('aria-current', 'page')
  // The stored Slack preference opens with its webhook; target and threshold share a row.
  const slack = page.locator('[data-channel-card="slack"]')
  await expect(slack.getByLabel('Webhook URL')).toHaveValue('https://hooks.slack.com/services/T000/B000/xyz')
  await networkQuiet(page, api)
  await logHeight(page, '/settings/my-notifications')
  const target = await slack.getByLabel('Webhook URL').boundingBox()
  const threshold = await slack.getByLabel(/High failure rate threshold/).boundingBox()
  expect(Math.abs((target as { y: number }).y - (threshold as { y: number }).y)).toBeLessThan(40)
  expect((threshold as { x: number }).x).toBeGreaterThan((target as { x: number; width: number }).x + (target as { width: number }).width)

  // (The sub-nav beside the page names the admin page; the page itself never mentions the server.)
  await expect(page.locator('[data-settings-content]').getByText(/SMTP/)).toHaveCount(0)
  expect(requestsTo(api, '/api/v1/settings/smtp')).toEqual([])
  expect(requestsTo(api, '/api/v1/settings/integrations')).toEqual([])
  // The history: the top bar's bell reads it, and so does the page's (closed)
  // history panel under the same SWR key, so the page's read goes out only
  // when it mounts more than SWR's 2 s dedupe after the bell's: a matter of
  // timing, held as "once or twice". Everything else exact.
  const history = observed(api).filter((line) => line === HISTORY_LINE).length
  expect(history, 'the notification history, by the bell and (past the dedupe) the page').toBeGreaterThanOrEqual(1)
  expect(history, 'the notification history, by the bell and (past the dedupe) the page').toBeLessThanOrEqual(2)
  expectInventory(
    { ...api, seen: api.seen.filter((line) => line !== HISTORY_LINE) },
    errors,
    [...SHELL_BASE.filter((line) => line !== HISTORY_LINE), PREFERENCES_LINE],
    '/settings/my-notifications',
  )
})

test('/settings/team-channels: in the sub-nav after Ownership; Save and Remove reach the API', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/team-channels', { handlers: handlers(), ready })
  await expect(ready(page)).toHaveText('Team channels')
  // The sub-nav: Project group, right after Ownership, and the current page.
  const projectItems = page.locator('[data-settings-group="project"] [data-settings-item]')
  const ids = await projectItems.evaluateAll((links) => links.map((link) => link.getAttribute('data-settings-item')))
  expect(ids.indexOf('team-channels')).toBe(ids.indexOf('ownership') + 1)
  await expect(page.locator('[data-settings-item="team-channels"]')).toHaveAttribute('aria-current', 'page')

  const table = page.locator('[data-primary]').getByRole('table', { name: 'Team channels' })
  await expect(table.locator('[data-team-row]')).toHaveCount(2)
  await expect(table.locator('[data-team-row="Identity"]')).toContainText('Routed to its channel')
  await expect(table.locator('[data-team-row="Payments"]')).toContainText('Not routed')
  await networkQuiet(page, api)
  await logHeight(page, '/settings/team-channels')
  expectInventory(api, errors, [...SHELL_BASE, RULES_LINE, CHANNELS_LINE], '/settings/team-channels')

  // Every channel type's label fits its select beside the arrow (a 144 px column
  // clipped "Slack webhook"): measured in the select's own font, fonts loaded.
  await page.evaluate(() => document.fonts.ready)
  const fit = await table.getByLabel('Channel type for Payments').evaluate((select: HTMLSelectElement) => {
    const style = getComputedStyle(select)
    const context = document.createElement('canvas').getContext('2d') as CanvasRenderingContext2D
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
    const widest = Math.max(...Array.from(select.options, (option) => context.measureText(option.text).width))
    const ARROW_PX = 20
    const room = select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - ARROW_PX
    return { widest: Math.ceil(widest), room: Math.floor(room) }
  })
  expect(fit.widest, `the widest channel label (${fit.widest} px) fits its select (${fit.room} px)`).toBeLessThanOrEqual(fit.room)

  // Save a channel for Payments: the PUT carries the row's buffer, and the row now routes.
  const payments = table.locator('[data-team-row="Payments"]')
  await payments.getByLabel('Channel type for Payments').selectOption('email')
  await payments.getByLabel('Webhook URL or email for Payments').fill('payments@example.com')
  const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().includes('/team-channels/Payments'))
  await payments.getByRole('button', { name: 'Save' }).click()
  expect((await put).postDataJSON()).toEqual({ channel_type: 'email', target: 'payments@example.com' })
  await expect(payments).toContainText('Routed to its channel')

  // Remove Identity's channel (confirm accepted): the DELETE goes out and the row falls back.
  page.once('dialog', (dialog) => void dialog.accept())
  const identity = table.locator('[data-team-row="Identity"]')
  await identity.getByRole('button', { name: 'Remove' }).click()
  await expect(identity).toContainText('Not routed')
  expect(api.seen.filter((line) => line.startsWith('DELETE '))).toEqual([`DELETE /api/v1/projects/${P}/ownership/team-channels/Identity`])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('/ownership no longer asks for team channels, and its one line opens Team channels', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/ownership', { handlers: handlers(), ready })
  await expect(ready(page)).toHaveText('Ownership')
  await expect(page.locator('[data-primary]')).toContainText('payments-*')
  await networkQuiet(page, api)
  await logHeight(page, '/ownership')
  await expect(page.getByRole('heading', { name: /Team Notification Channels/i })).toHaveCount(0)
  expectInventory(api, errors, [...SHELL_BASE, RULES_LINE, COVERAGE_LINE], '/ownership')

  await page.locator('[data-team-channels-link]').getByRole('link', { name: 'Team channels' }).click()
  await expect(page).toHaveURL(/\/settings\/team-channels$/)
  await expect(ready(page)).toHaveText('Team channels')
  await expect(page.locator('[data-team-row]')).toHaveCount(2)
})
