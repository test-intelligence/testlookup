/**
 * W09 NTF-02 (contract §2.1, §1.3), hermetic: the per-project notification policy, as a card and as hints.
 *
 *  - as a QA lead, Email (SMTP) & channels (`/settings/notifications`) holds a third card, **Project
 *    notification policy**, after Shared channels and in the page (no dialog): a project with no policy row
 *    reads `Using defaults: state changes ON, every run OFF.`; **Notify on every run** → **Save** PUTs the
 *    whole policy (`{ transitions_enabled: true, per_run_events_enabled: true, enabled_events: <the 7>,
 *    consecutive_failure_threshold: 2 }`), toasts `Notification policy saved`, and the card re-reads (the
 *    defaults line is gone);
 *  - My notifications (`/settings/my-notifications`) with the default policy carries, under Run failed in every
 *    open channel card, `Not sent in Checkout: per-run notifications are off`, with the QA lead's link to the
 *    card; with the saved policy (the GET answers the PUT's body), no per-run hint;
 *  - state changes off → every state-change event carries its hint; a viewer gets the hints without the link;
 *  - an unreadable policy: the card says so with Retry and shows no form, My notifications shows no hint;
 *  - All Projects (an admin): one line on each page, and no policy request.
 *
 * Each page's requests of one load are held to an inventory (the policy once). Harness: `tests/lib/rollout.ts`
 * (fail closed, pinned clock). Fixtures: `notificationsWorld()` (`fixtures-notify-flaky.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { openProductionPage } from '../lib/production-pages'
import { assertHermetic, expectInventory, networkQuiet, openRollout, requestsTo, SHELL_BASE } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  DEFAULT_TRANSITION_POLICY,
  EVERY_RUN_POLICY_BODY,
  notificationsWorld,
  preference,
  TRANSITION_POLICY_PATH,
  VIEWER,
} from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const policyCard = (page: Page) => page.locator('section[data-notification-policy]')
const channelCard = (page: Page, channel: string) => page.locator(`[data-channel-card="${channel}"]`)

const POLICY_LINE = `GET ${TRANSITION_POLICY_PATH}`
const SMTP_LINE = 'GET /api/v1/settings/smtp'
const INTEGRATIONS_LINE = 'GET /api/v1/settings/integrations'
const PREFERENCES_LINE = 'GET /api/v1/notifications/preferences'
const HISTORY_LINE = 'GET /api/v1/notifications/history?unread_only=false&limit=50'

const PER_RUN_HINT = 'Not sent in Checkout: per-run notifications are off'
const POLICY_LINK = 'Change it in Email (SMTP) & channels'
const POLICY_HREF = '/settings/notifications#notification-policy'

/** Email and Slack stored (both cards open); Teams not configured (collapsed). */
const PREFERENCES = [
  preference('00000000-0000-4000-8000-000000000a11', 'email', ['run_failed', 'test.newly_failing']),
  preference('00000000-0000-4000-8000-000000000a12', 'slack', ['run_failed', 'high_failure_rate'], {
    slack_webhook_url: 'https://hooks.slack.com/services/T000/B000/xyz',
  }),
]

/** The option's label (its checkbox and its text, and the hint inside it, §4.2) in one channel card. */
function eventOption(card: Locator, label: string): Locator {
  return card.locator('label').filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
}

/** The page's requests apart from the notification history, which the bell and the page's closed panel share (once or twice). */
function withoutHistory<T extends { seen: string[] }>(api: T): T {
  return { ...api, seen: api.seen.filter((line) => line !== HISTORY_LINE) }
}

test('Email (SMTP) & channels: the policy card reads the defaults; Notify on every run → Save PUTs the whole policy', async ({ page }) => {
  const world = notificationsWorld({ preferences: PREFERENCES })
  const { api, errors } = await openRollout(page, '/settings/notifications', { handlers: world.handlers, ready })
  await expect(ready(page)).toHaveText('Email (SMTP) & channels')
  const card = policyCard(page)
  await expect(card).toHaveAttribute('aria-label', 'Project notification policy')
  await expect(card.getByRole('heading', { name: 'Project notification policy' })).toBeVisible()
  await expect(card.locator('[data-policy-defaults]')).toHaveText('Using defaults: state changes ON, every run OFF.')

  // The third card: after Shared channels, inside the page's content, not a dialog.
  const order = await page.evaluate(() => {
    const shared = document.querySelector('[data-shared-channels]')
    const policy = document.querySelector('section[data-notification-policy]')
    return shared && policy ? shared.compareDocumentPosition(policy) & Node.DOCUMENT_POSITION_FOLLOWING : -1
  })
  expect(order, 'the policy card follows Shared channels').toBeGreaterThan(0)
  await expect(page.locator('#main-content section[data-notification-policy]')).toHaveCount(1)
  // Still nothing personal on the admin page.
  await expect(page.locator('[data-channel-card]')).toHaveCount(0)
  await expect(page.getByText('Notify me when')).toHaveCount(0)

  // The switches as stored: state changes on, every run off; the seven events ticked and enabled.
  const everyRun = card.getByRole('switch', { name: 'Notify on every run' })
  const stateChanges = card.getByRole('switch', { name: 'Notify on state changes' })
  await expect(everyRun).toHaveAttribute('aria-checked', 'false')
  await expect(stateChanges).toHaveAttribute('aria-checked', 'true')
  await expect(card.getByText('Run failed, Run passed and High failure rate, once per run.')).toBeVisible()
  const events = card.locator('[data-policy-events] input[type="checkbox"]')
  await expect(events).toHaveCount(7)
  for (const box of await events.all()) {
    await expect(box).toBeChecked()
    await expect(box).toBeEnabled()
  }
  await expect(card.getByLabel('Failed runs in a row before "Test newly failing"')).toHaveValue('2')

  await networkQuiet(page, api)
  expectInventory(api, errors, [...SHELL_BASE, SMTP_LINE, SMTP_LINE, INTEGRATIONS_LINE, POLICY_LINE], '/settings/notifications')

  // State changes off disables the seven boxes (they keep their ticks); back on, they are editable again.
  await stateChanges.click()
  for (const box of await events.all()) await expect(box).toBeDisabled()
  await stateChanges.click()
  for (const box of await events.all()) await expect(box).toBeEnabled()

  await everyRun.click()
  await expect(everyRun).toHaveAttribute('aria-checked', 'true')
  const put = page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname === TRANSITION_POLICY_PATH)
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  expect((await put).postDataJSON()).toEqual(EVERY_RUN_POLICY_BODY)
  await expect(page.getByText('Notification policy saved')).toBeVisible()
  // The card re-read the stored row: no defaults line, every run on.
  await expect(card.locator('[data-policy-defaults]')).toHaveCount(0)
  await expect(card.getByRole('switch', { name: 'Notify on every run' })).toHaveAttribute('aria-checked', 'true')
  await expect
    .poll(() => requestsTo(api, TRANSITION_POLICY_PATH).filter((line) => line === POLICY_LINE).length, { message: 'the card re-read the policy' })
    .toBe(2)
  expect(world.state.writes).toEqual([{ line: `PUT ${TRANSITION_POLICY_PATH}`, body: EVERY_RUN_POLICY_BODY }])
  await expect(page.getByRole('dialog'), 'the form and its outcome are in the page').toHaveCount(0)

  // My notifications, with the saved policy: Run failed is sent, so no per-run hint anywhere.
  await page.goto('/settings/my-notifications')
  await expect(ready(page)).toHaveText('My notifications')
  await expect(eventOption(channelCard(page, 'email'), 'Run failed')).toBeVisible()
  await networkQuiet(page, api)
  await expect(page.locator('[data-event-hint]').filter({ hasText: 'per-run notifications are off' })).toHaveCount(0)
  await expect(page.locator('[data-event-hint]')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('My notifications with the default policy: the per-run hint under Run failed in every open card, and the QA lead\'s link', async ({ page }) => {
  const world = notificationsWorld({ preferences: PREFERENCES })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready })
  await expect(ready(page)).toHaveText('My notifications')
  for (const channel of ['email', 'slack']) {
    const card = channelCard(page, channel)
    const runFailed = eventOption(card, 'Run failed')
    await expect(runFailed.locator('[data-event-hint]'), `${channel}: Run failed`).toHaveText(new RegExp(`^${PER_RUN_HINT}`))
    for (const label of ['Run passed', 'High failure rate']) {
      await expect(eventOption(card, label).locator('[data-event-hint]'), `${channel}: ${label}`).toContainText(PER_RUN_HINT)
    }
    // Not governed by the policy, and state changes are on by default: no hint.
    for (const label of ['AI analysis complete', 'Quality gate failed', 'Test newly failing (transition)', 'A test or suite became slower']) {
      await expect(eventOption(card, label).locator('[data-event-hint]'), `${channel}: ${label}`).toHaveCount(0)
    }
    const link = runFailed.getByRole('link', { name: POLICY_LINK })
    await expect(link).toHaveAttribute('href', POLICY_HREF)
  }
  // The collapsed Teams card renders no options at all.
  await expect(channelCard(page, 'teams').locator('[data-event-hint]')).toHaveCount(0)

  await networkQuiet(page, api)
  const history = requestsTo(api, '/api/v1/notifications/history').length
  expect(history, 'the history, by the bell and (past the dedupe) the page').toBeGreaterThanOrEqual(1)
  expect(history).toBeLessThanOrEqual(2)
  expectInventory(
    withoutHistory(api),
    errors,
    [...SHELL_BASE.filter((line) => line !== HISTORY_LINE), PREFERENCES_LINE, POLICY_LINE],
    '/settings/my-notifications',
  )

  // The link opens the card on Email (SMTP) & channels.
  await eventOption(channelCard(page, 'email'), 'Run failed').getByRole('link', { name: POLICY_LINK }).click()
  await expect(page).toHaveURL((url) => url.pathname === '/settings/notifications' && url.hash === '#notification-policy')
  await expect(policyCard(page)).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('state changes off: every state-change event says so; a viewer gets the hints without the link', async ({ page }) => {
  const world = notificationsWorld({
    preferences: PREFERENCES,
    policy: { ...DEFAULT_TRANSITION_POLICY, transitions_enabled: false, per_run_events_enabled: true, is_default: false },
  })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready, user: VIEWER })
  const card = channelCard(page, 'email')
  for (const label of ['Test newly failing (transition)', 'Test recovered (transition)', 'Test newly flaky (transition)', 'Quarantine past its SLA (lifecycle)']) {
    await expect(eventOption(card, label).locator('[data-event-hint]'), label).toContainText(
      'Not sent in Checkout: state-change notifications are off',
    )
  }
  await expect(eventOption(card, 'Run failed').locator('[data-event-hint]')).toHaveCount(0)
  // A viewer cannot change the policy: no link to the admin page.
  await expect(page.getByRole('link', { name: POLICY_LINK })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, TRANSITION_POLICY_PATH)).toEqual([POLICY_LINE])
  assertHermetic(api, errors)
})

test('one state change turned off: only its option says so', async ({ page }) => {
  const world = notificationsWorld({
    preferences: PREFERENCES,
    policy: {
      ...DEFAULT_TRANSITION_POLICY,
      per_run_events_enabled: true,
      enabled_events: DEFAULT_TRANSITION_POLICY.enabled_events.filter((event) => event !== 'test.recovered'),
      is_default: false,
    },
  })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready })
  const card = channelCard(page, 'slack')
  await expect(eventOption(card, 'Test recovered (transition)').locator('[data-event-hint]')).toContainText(
    'Not sent in Checkout: this state change is turned off',
  )
  await expect(card.locator('[data-event-hint]')).toHaveCount(1)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('an unreadable policy: the card says so with Retry and no form; My notifications shows no hint', async ({ page }) => {
  const world = notificationsWorld({ preferences: PREFERENCES, policy: null })
  const { api, errors } = await openRollout(page, '/settings/notifications', { handlers: world.handlers, ready })
  const card = policyCard(page)
  await expect(card.locator('[data-policy-unavailable]')).toContainText("The project's notification policy could not be read.")
  await expect(card.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0)
  await expect(card.getByRole('switch')).toHaveCount(0)

  await page.goto('/settings/my-notifications')
  await expect(eventOption(channelCard(page, 'email'), 'Run failed')).toBeVisible()
  await networkQuiet(page, api)
  // The dispatcher fails open: a "not sent" hint over a policy nobody could read would be the lie.
  await expect(page.locator('[data-event-hint]')).toHaveCount(0)
  expect(world.state.writes, 'nothing was saved').toEqual([])
  assertHermetic(api, errors)
})

test('All Projects (an admin): one line on each page, and no policy request', async ({ page }) => {
  const world = notificationsWorld({ preferences: PREFERENCES })
  const { api, errors } = await openProductionPage(page, '/settings/notifications', {
    theme: 'signal',
    now: NOW,
    // All projects is an ADMIN's view (every other role is moved onto its first project).
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: world.handlers,
    ready,
    mock: { flags: {} },
  })
  await expect(policyCard(page)).toContainText(
    'Each project has its own notification policy. Pick a project in the top bar to see and change it.',
  )
  await expect(policyCard(page).getByRole('switch')).toHaveCount(0)

  await page.goto('/settings/my-notifications')
  await expect(eventOption(channelCard(page, 'email'), 'Run failed')).toBeVisible()
  // One muted line under "Notify me when" in each open card (email and slack), instead of the hints.
  for (const channel of ['email', 'slack']) {
    await expect(channelCard(page, channel).locator('[data-policy-note]')).toHaveText(
      "Run and state-change events depend on each project's notification policy.",
    )
  }
  await expect(channelCard(page, 'teams').locator('[data-policy-note]')).toHaveCount(0)
  await expect(page.locator('[data-event-hint]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, new RegExp(`^/api/v1/notifications/projects/`)), 'no policy is read in All Projects').toEqual([])
  expect(requestsTo(api, TRANSITION_POLICY_PATH.replace(P, 'all'))).toEqual([])
  assertHermetic(api, errors)
})
