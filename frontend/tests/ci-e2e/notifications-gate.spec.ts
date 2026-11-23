/**
 * W09 NTF-01 (contract §2.2), hermetic: "Quality gate failed" is a real notification, and "Flaky test
 * detected" became "Test newly flaky".
 *
 *  - My notifications offers no "Flaky test detected" in any channel card, and offers "Quality gate failed";
 *    a preference stored with the old `flaky_test_detected` reads as "Test newly flaky (transition)" ticked;
 *    ticking Quality gate failed and saving PUTs `quality_gate_failed` and `test.newly_flaky` (never the
 *    alias);
 *  - a QA lead evaluates the never-evaluated hotfix on `/releases`; the gate answers NO_GO (a transition into
 *    NO_GO), the server stages the in-app entry in the same transaction, and the bell's next poll lists
 *    `Quality gate failed: 2026.11.1 — test_pay (checkout-e2e) is FAILED` as a link to `/releases/<id>`;
 *    clicking it closes the bell and opens the release, whose gate reads No-Go;
 *  - the bell follows only the two allow-listed shapes, re-checked by role: a viewer sees the release entry as
 *    text and the run decision's `/release-gate/<run>` as a link; a link of any other shape is text for
 *    everyone.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock: the spec moves the bell's 30 s poll with
 * `page.clock.runFor`). Fixtures: `gateNotifyWorld()` / `notificationsWorld()` (`fixtures-notify-flaky.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  DECISION_RUN_ID,
  GATE_NOTIFICATION_ID,
  GATE_REASON,
  gateNotification,
  gateNotifyWorld,
  HOTFIX_NAME,
  LEGACY_EMAIL_PREF,
  LEGACY_EMAIL_PREF_ID,
  notificationLog,
  notificationsWorld,
  RELEASE_HOTFIX_ID,
  runDecisionNotification,
  VIEWER,
} from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-page-header] h1')
const channelCard = (page: Page, channel: string) => page.locator(`[data-channel-card="${channel}"]`)
const bell = (page: Page) => page.getByRole('button', { name: /^Notifications/ })
const releaseCard = (page: Page, name: string) =>
  page.getByRole('article', { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} `) })

const GATE_TITLE = `Quality gate failed: ${HOTFIX_NAME} — ${GATE_REASON}`
const HISTORY_PATH = '/api/v1/notifications/history'
const MY_NOTIFICATIONS = '/settings/my-notifications'

/** Every link in the (existing) bell popover, as its whole `href`, in order: the header's and footer's Settings links bracket the entries. */
async function bellHrefs(page: Page): Promise<(string | null)[]> {
  return page.getByRole('dialog', { name: 'Notifications' }).locator('a').evaluateAll((links) => links.map((link) => link.getAttribute('href')))
}

function eventOption(card: Locator, label: string): Locator {
  return card.locator('label').filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
}

test('My notifications: no "Flaky test detected"; the stored alias reads as Test newly flaky; Quality gate failed saves', async ({ page }) => {
  const world = notificationsWorld({ preferences: [LEGACY_EMAIL_PREF] })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready })
  const email = channelCard(page, 'email')
  await expect(eventOption(email, 'Run failed').getByRole('checkbox')).toBeChecked()

  // Open the two cards with no stored preference: none offers the dead event either.
  for (const channel of ['slack', 'teams']) await channelCard(page, channel).getByRole('button').first().click()
  for (const channel of ['email', 'slack', 'teams']) {
    const card = channelCard(page, channel)
    await expect(eventOption(card, 'Quality gate failed'), `${channel}: Quality gate failed is offered`).toHaveCount(1)
    await expect(card.getByText('Flaky test detected'), `${channel}: no Flaky test detected`).toHaveCount(0)
  }

  // The stored `flaky_test_detected` is the event that fires: Test newly flaky, ticked.
  await expect(eventOption(email, 'Test newly flaky (transition)').getByRole('checkbox')).toBeChecked()
  const gate = eventOption(email, 'Quality gate failed').getByRole('checkbox')
  await expect(gate).not.toBeChecked()
  await gate.check()

  const put = page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname === `/api/v1/notifications/preferences/${LEGACY_EMAIL_PREF_ID}`)
  await email.getByRole('button', { name: 'Save', exact: true }).click()
  const body = (await put).postDataJSON() as { events: string[] }
  expect([...body.events].sort()).toEqual(['quality_gate_failed', 'run_failed', 'test.newly_flaky'])
  await expect(page.getByText('Email notifications saved')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.state.writes.map((w) => w.line)).toEqual([`PUT /api/v1/notifications/preferences/${LEGACY_EMAIL_PREF_ID}`])
  assertHermetic(api, errors)
})

test('a QA lead evaluates the hotfix into NO_GO: the bell lists "Quality gate failed" and its link opens the release', async ({ page }) => {
  const world = gateNotifyWorld()
  const { api, errors } = await openRollout(page, '/releases', {
    handlers: world.handlers,
    ready: (p) => p.getByRole('heading', { name: 'Releases', level: 1 }),
  })
  const hotfix = releaseCard(page, HOTFIX_NAME)
  await expect(hotfix.locator('[data-gate-decision]').first()).toHaveText('Not evaluated yet')

  // Nothing in the bell yet.
  await bell(page).click()
  await expect(page.getByText('No notifications yet')).toBeVisible()
  await bell(page).click()
  await expect(page.getByText('No notifications yet')).toHaveCount(0)

  await hotfix.getByTestId('release-evaluate').click()
  await expect(hotfix.locator('[data-gate-decision]').first()).toHaveText('No-Go')
  expect(world.releases.writes).toEqual([{ line: `POST /api/v1/releases/${RELEASE_HOTFIX_ID}/gate/evaluate`, body: null }])

  // The bell polls its history every 30 s (the page clock is pinned, so the spec moves the timers).
  const reads = requestsTo(api, HISTORY_PATH).length
  await page.clock.runFor(31_000)
  await expect.poll(() => requestsTo(api, HISTORY_PATH).length, { message: 'the bell asked for its history again' }).toBeGreaterThan(reads)
  await expect(bell(page)).toHaveAttribute('aria-label', 'Notifications (1 unread)')
  await bell(page).click()
  const entry = page.getByRole('link', { name: GATE_TITLE })
  await expect(entry).toBeVisible()
  // The whole value: an internal path, never a URL built from text.
  await expect(entry).toHaveAttribute('href', `/releases/${RELEASE_HOTFIX_ID}`)

  await entry.click()
  await expect(page).toHaveURL((url) => url.pathname === `/releases/${RELEASE_HOTFIX_ID}`)
  await expect(page.getByRole('link', { name: GATE_TITLE }), 'the bell closed').toHaveCount(0)
  const gatePanel = page.locator('#gate')
  await expect(gatePanel.locator('[data-gate-decision]')).toHaveText('No-Go')
  await expect(gatePanel.getByRole('list', { name: 'Blocking' }).getByRole('listitem').first()).toHaveText(GATE_REASON)
  await networkQuiet(page, api)
  expect(world.notifications.history.map((log) => log.id)).toEqual([GATE_NOTIFICATION_ID])
  assertHermetic(api, errors)
})

/** Three entries: a release's gate (a management route), a run's decision (every role), and a foreign shape. */
const MIXED_HISTORY = [
  gateNotification(GATE_NOTIFICATION_ID, RELEASE_HOTFIX_ID, HOTFIX_NAME),
  runDecisionNotification('00000000-0000-4000-8000-000000000c02'),
  notificationLog('00000000-0000-4000-8000-000000000c03', 'Quality gate failed: Payments build 7 — elsewhere', {
    event_type: 'quality_gate_failed',
    // Not one of the two shapes: the bell must never follow it, whatever the server sent.
    link: `//evil.example.com/releases/${RELEASE_HOTFIX_ID}`,
  }),
]
const RUN_TITLE = runDecisionNotification('x').title
const FOREIGN_TITLE = 'Quality gate failed: Payments build 7 — elsewhere'

test('a viewer: the release entry is text, the run decision a link to its release gate, a foreign link text', async ({ page }) => {
  const world = notificationsWorld({ history: MIXED_HISTORY })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready, user: VIEWER })
  await bell(page).click()
  await expect(page.getByText(GATE_TITLE)).toBeVisible()
  await expect(page.getByRole('link', { name: GATE_TITLE }), '/releases is a QA lead\'s route').toHaveCount(0)
  await expect(page.getByRole('link', { name: RUN_TITLE })).toHaveAttribute('href', `/release-gate/${DECISION_RUN_ID}`)
  await expect(page.getByText(FOREIGN_TITLE)).toBeVisible()
  await expect(page.getByRole('link', { name: FOREIGN_TITLE })).toHaveCount(0)
  // The whole list of hrefs: the run decision is the only entry that links anywhere.
  expect(await bellHrefs(page)).toEqual([MY_NOTIFICATIONS, `/release-gate/${DECISION_RUN_ID}`, MY_NOTIFICATIONS])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA lead: both allow-listed entries are links; the foreign one stays text', async ({ page }) => {
  const world = notificationsWorld({ history: MIXED_HISTORY })
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: world.handlers, ready })
  await bell(page).click()
  await expect(page.getByRole('link', { name: GATE_TITLE })).toHaveAttribute('href', `/releases/${RELEASE_HOTFIX_ID}`)
  await expect(page.getByRole('link', { name: RUN_TITLE })).toHaveAttribute('href', `/release-gate/${DECISION_RUN_ID}`)
  await expect(page.getByRole('link', { name: FOREIGN_TITLE })).toHaveCount(0)
  expect(await bellHrefs(page)).toEqual([MY_NOTIFICATIONS, `/releases/${RELEASE_HOTFIX_ID}`, `/release-gate/${DECISION_RUN_ID}`, MY_NOTIFICATIONS])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
