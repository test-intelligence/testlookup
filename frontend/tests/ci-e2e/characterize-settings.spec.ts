/**
 * W27b PAN-05d + PAN-06b CHARACTERIZATION, policies, search, chat and the settings pages
 * (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.4, rows Y1 (its success path and Cancel; the 409 race is
 * `policy-publish-conflict.spec.ts`'), Y2-Y4, R1-R2, G1-G2, S1-S3, I1-I2, Z1, W1; C1 is
 * `settings-notifications.spec.ts`'), hermetic. Written against the tree where every one of them asks a native
 * `confirm()` (or a modal typed confirmation: the purge, the criteria deletion) and kept green, UNEDITED, after
 * each asks in an inline strip where its trigger is (`answerConfirm` answers either shape, and pins the sentence)
 * or opens its typed form in the page (`opened`). Only named controls are driven; Escape is never pressed (none of
 * these had it).
 *
 *  - every question: dismissed → nothing sent and nothing changed; accepted → exactly the request below, the toast
 *    (or none), the re-read;
 *  - Y1 Publish → `POST /release-gate-policies/{id}/publish` (no body), `Policy published and activated`, the editor
 *    read-only; Y2 Deactivate → `POST …/{id}/deactivate`, `Policy deactivated`, the list re-read; Y3 Clear history →
 *    `DELETE /search/recent` (W12 SRCH-03: recents live on the server), `Recent searches cleared`; Y4 the open conversation's
 *    delete → `DELETE /chat/sessions/{id}`, the sessions re-read, the welcome view back, no toast;
 *  - R1 Purge now → a wrong name keeps `Queue purge` aria-disabled, the exact name → `POST …/retention-policy/purge`
 *    `{ confirmation_name }`, the toast; R2 Delete by criteria › Preview (`{ older_than_days }`) → Delete n runs → the
 *    exact name → `POST …/deletion/execute` `{ job_id, confirmation_name }`, the toast, the preview cleared;
 *  - G1 Unsubscribe → `DELETE /digests/subscriptions/{id}`, `Unsubscribed`; G2 a view's Delete →
 *    `DELETE /saved-views/{id}`, no toast;
 *  - S1 Activate an enforced config asks; an optional one and Deactivate send `PATCH …/sso/configs/{id}`
 *    `{ is_active }` at once; S2 Delete → `DELETE /sso/configs/{id}`; S3 Revoke → `DELETE /scim-tokens/{id}`;
 *  - I1 Remove → `DELETE …/github-integration`, `Integration removed`, the form back to its defaults; I2 Remove token →
 *    nothing sent, the removal armed; Save → `PUT …/integrations/gitlab` with `token: ''`;
 *  - Z1 the Slack card's Remove → `DELETE /notifications/preferences/{id}`, `Slack preference removed`;
 *  - W1 a webhook's delete → `DELETE /webhooks/{id}`, `Deleted`;
 *  - role gating: a QA lead has no Publish / Deactivate and reads "Admin access required" on retention and SSO.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-in-page-b.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { answerConfirm, opened } from '../lib/inPage'
import type { MockedApi } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, USER } from '../visual/production/fixtures'
import {
  ACTIVE_POLICY,
  CHAT_SESSIONS,
  chatDeleteWorld,
  CRITERIA_PREVIEW,
  digestsDeleteWorld,
  DRAFT_POLICY,
  GITLAB_CONFIG,
  gitlabInPageWorld,
  githubInPageWorld,
  notificationsDeleteWorld,
  policiesInPageWorld,
  RECENT_SEARCHES,
  retentionInPageWorld,
  SCIM_TOKEN,
  searchPageWorld,
  SLACK_PREF_ID,
  SSO_ENFORCED,
  SSO_OPTIONAL,
  ssoInPageWorld,
  WEBHOOK,
  webhooksInPageWorld,
} from '../visual/production/fixtures-in-page-b'
import { SUB_IDS, VIEW_IDS } from '../visual/production/fixtures-notify-flaky'
import { ADMIN } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const h1 = (name: string | RegExp) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: typeof name === 'string' ? true : undefined })
/** Nothing but reads since `from` (index into the request log). */
const writesSince = (api: MockedApi, from: number) => observed(api).slice(from).filter((line) => !line.startsWith('GET '))

// ── /policies ──────────────────────────────────────────────────────────────

const POLICIES = '/api/v1/release-gate-policies'

test('Y1 a draft\'s Publish: dismissed sends nothing; accepted → the publish POST (no body), the toast, the editor read-only', async ({ page }) => {
  const world = policiesInPageWorld()
  const { api, errors } = await openRollout(page, `/policies/${DRAFT_POLICY.id}`, { handlers: world.handlers, ready: h1(`Edit: ${DRAFT_POLICY.name}`), user: ADMIN })
  await networkQuiet(page, api)
  const publish = () => page.getByRole('button', { name: 'Publish', exact: true })

  const asked = await answerConfirm(page, publish(), 'dismiss')
  expect(asked.message).toBe('Publish this policy? It will become active and the previous version will be deactivated.')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await expect(h1(`Edit: ${DRAFT_POLICY.name}`)(page), 'still the editable draft').toBeVisible()

  await answerConfirm(page, publish(), 'accept', { confirmLabel: 'Publish' })
  await expect(page.getByText('Policy published and activated', { exact: true })).toBeVisible()
  await expect(h1(`View: ${DRAFT_POLICY.name}`)(page), 'the published policy is read-only').toBeVisible()
  await expect(publish()).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${POLICIES}/${DRAFT_POLICY.id}/publish`, body: null }])
  assertHermetic(api, errors)
})

test('Y2 an active policy\'s Deactivate: dismissed sends nothing; accepted → the deactivate POST, the toast, the list re-read', async ({ page }) => {
  const world = policiesInPageWorld()
  const { api, errors } = await openRollout(page, '/policies', { handlers: world.handlers, ready: h1('Release Gate Policies'), user: ADMIN })
  const card = page.locator('[data-primary] .card').filter({ hasText: ACTIVE_POLICY.name }).filter({ hasText: 'Active' })
  await expect(card).toHaveCount(1)
  await networkQuiet(page, api)
  const deactivate = () => card.getByRole('button', { name: 'Deactivate', exact: true })

  const asked = await answerConfirm(page, deactivate(), 'dismiss')
  expect(asked.message).toBe('Deactivate this policy?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, POLICIES).length
  await answerConfirm(page, deactivate(), 'accept', { confirmLabel: 'Deactivate' })
  await expect(page.getByText('Policy deactivated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${POLICIES}/${ACTIVE_POLICY.id}/deactivate`, body: null }])
  expect(requestsTo(api, POLICIES).length, 'the list re-reads').toBeGreaterThan(reads)
  await expect(page.locator('[data-primary]').getByText('Active', { exact: true }), 'no active policy left').toHaveCount(0)
  assertHermetic(api, errors)
})

test('role gating: a QA lead sees the policies with no Deactivate and the draft with no Publish', async ({ page }) => {
  const world = policiesInPageWorld()
  const { api, errors } = await openRollout(page, '/policies', { handlers: world.handlers, ready: h1('Release Gate Policies'), user: USER })
  await expect(page.locator('[data-primary] .card')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Deactivate' })).toHaveCount(0)
  await page.goto(`/policies/${DRAFT_POLICY.id}`)
  await expect(h1(`View: ${DRAFT_POLICY.name}`)(page)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

// ── /search, /chat ─────────────────────────────────────────────────────────

test('Y3 Clear history: dismissed keeps both recents and sends nothing; accepted → DELETE /search/recent, the toast, the empty card', async ({ page }) => {
  // W12 SRCH-03 (deliberate edit, contract §6.5): the recents are the server's (`GET /api/v1/search/recent`), so
  // nothing is seeded in the browser and clearing is ONE `DELETE /api/v1/search/recent` (it was local before).
  const world = searchPageWorld()
  const { api, errors } = await openRollout(page, '/search', { handlers: world.handlers, ready: (p) => p.getByTestId('searchable-data-summary') })
  await expect(page.getByText(RECENT_SEARCHES[0].q, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const from = observed(api).length
  const clear = () => page.getByRole('button', { name: 'Clear history' })

  const asked = await answerConfirm(page, clear(), 'dismiss')
  expect(asked.message).toBe("Clear all 2 recent searches? This can't be undone.")
  await expect(page.getByText(RECENT_SEARCHES[1].q, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(writesSince(api, from), 'dismissed: nothing is sent').toEqual([])

  await answerConfirm(page, clear(), 'accept', { confirmLabel: 'Clear' })
  await expect(page.getByText('Recent searches cleared', { exact: true })).toBeVisible()
  await expect(page.getByText('Your queries will appear here.')).toBeVisible()
  await expect(page.getByText(RECENT_SEARCHES[0].q, { exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes, 'accepted: exactly one DELETE of the reader\'s recents').toEqual([{ line: 'DELETE /api/v1/search/recent', body: null }])
  expect(writesSince(api, from)).toEqual(['DELETE /api/v1/search/recent'])
  expect(await page.evaluate(() => localStorage.getItem('tl.search.recent')), 'nothing is kept in the browser').toBeNull()
  assertHermetic(api, errors)
})

test('Y4 the open conversation\'s delete: dismissed sends nothing; accepted → the DELETE, the sessions re-read, the welcome view, no toast', async ({ page }) => {
  const world = chatDeleteWorld()
  const { api, errors } = await openRollout(page, '/chat', {
    handlers: world.handlers,
    flags: { ask_ai_chat: true },
    ready: (p) => p.getByRole('heading', { name: 'Ask about Checkout' }),
  })
  const [first, second] = CHAT_SESSIONS
  const item = (title: string) => page.getByTestId('chat-session').filter({ hasText: title })
  await item(first.title).click()
  await expect(page.getByText('Two tests failed in the Auth suite.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Ask about Checkout' })).toHaveCount(0)
  await networkQuiet(page, api)
  const del = () => item(first.title).getByRole('button', { name: 'Delete conversation' })

  await item(first.title).hover()
  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe('Delete this conversation?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await expect(page.getByText('Two tests failed in the Auth suite.'), 'the conversation stays open').toBeVisible()

  const reads = requestsTo(api, '/api/v1/chat/sessions').length
  await item(first.title).hover()
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(item(first.title)).toHaveCount(0)
  await expect(item(second.title)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Ask about Checkout' }), 'the open conversation resets to the welcome view').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/chat/sessions/${first.id}`, body: null }])
  expect(requestsTo(api, '/api/v1/chat/sessions').length, 'the sessions re-read').toBeGreaterThan(reads)
  await expect(page.getByRole('status').filter({ hasText: /conversation/i }), 'no toast').toHaveCount(0)
  assertHermetic(api, errors)
})

// ── /settings/retention ────────────────────────────────────────────────────

test('R1 Purge now: Cancel and the X send nothing; a wrong name keeps Queue purge aria-disabled; the exact name → the purge POST, the toast', async ({ page }) => {
  const world = retentionInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: ADMIN })
  await networkQuiet(page, api)
  const open = () => page.getByRole('button', { name: 'Purge now' }).click()

  await open()
  let form = opened(page, 'Purge old data now')
  await expect(form).toBeVisible()
  const field = form.getByLabel('Type Checkout to confirm.')
  await expect(field).toBeFocused()
  const confirm = form.getByRole('button', { name: 'Queue purge' })
  await expect(confirm).toHaveAttribute('aria-disabled', 'true')
  await field.fill('checkout')
  await expect(confirm, 'case-sensitive').toHaveAttribute('aria-disabled', 'true')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await open()
  form = opened(page, 'Purge old data now')
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await open()
  form = opened(page, 'Purge old data now')
  await form.getByLabel('Type Checkout to confirm.').fill('Checkout')
  await expect(form.getByRole('button', { name: 'Queue purge' })).toHaveAttribute('aria-disabled', 'false')
  await form.getByRole('button', { name: 'Queue purge' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Purge queued — check back for the audit record', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/retention-policy/purge`, body: { confirmation_name: 'Checkout' } }])
  assertHermetic(api, errors)
})

test('R2 Delete by criteria: Preview freezes a set; Delete n runs → Cancel and the X send nothing; the exact name → the execute POST with the job id, the preview cleared', async ({ page }) => {
  const world = retentionInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: ADMIN })
  await networkQuiet(page, api)
  await page.getByLabel('Older than days').fill('30')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  const preview = page.getByTestId('criteria-preview')
  await expect(preview.getByTestId('preview-run-count')).toHaveText(String(CRITERIA_PREVIEW.run_count))
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/deletion/preview`, body: { older_than_days: 30 } }])
  const title = `Delete ${CRITERIA_PREVIEW.run_count} runs`
  const open = () => preview.getByRole('button', { name: title }).click()

  await open()
  let form = opened(page, title)
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Type Checkout to confirm.')).toBeFocused()
  await expect(form.getByRole('button', { name: 'Delete permanently' })).toHaveAttribute('aria-disabled', 'true')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await open()
  form = opened(page, title)
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toHaveLength(1)

  await open()
  form = opened(page, title)
  await form.getByLabel('Type Checkout to confirm.').fill('Checkout')
  await form.getByRole('button', { name: 'Delete permanently' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Deleting ${CRITERIA_PREVIEW.run_count} runs — track it in the job list`, { exact: true })).toBeVisible()
  await expect(preview, 'the frozen set is consumed').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `POST /api/v1/projects/${P}/deletion/execute`, body: { job_id: CRITERIA_PREVIEW.job_id, confirmation_name: 'Checkout' } },
  ])
  assertHermetic(api, errors)
})

test('role gating: retention and SSO are admin only (a QA lead reads the refusal, no control)', async ({ page }) => {
  const world = retentionInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/retention', { handlers: [...world.handlers, ...ssoInPageWorld().handlers], ready: (p) => p.getByText('Admin access required'), user: USER })
  await expect(page.getByRole('button', { name: 'Purge now' })).toHaveCount(0)
  await page.goto('/settings/sso')
  await expect(page.getByText('Admin access required')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Activate' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writesSince(api, 0)).toEqual([])
  assertHermetic(api, errors)
})

// ── /settings/digests ──────────────────────────────────────────────────────

test('G1 Unsubscribe: dismissed sends nothing; accepted → the DELETE, the toast, the subscriptions re-read', async ({ page }) => {
  const { handlers, state } = digestsDeleteWorld()
  const { api, errors } = await openRollout(page, '/settings/digests', { handlers, ready: h1('Digests & Saved Views') })
  const row = page.locator(`[data-subscription-row="${SUB_IDS.weekly}"]`)
  await expect(row).toBeVisible()
  await networkQuiet(page, api)
  const unsubscribe = () => row.getByRole('button', { name: 'Unsubscribe' })

  const asked = await answerConfirm(page, unsubscribe(), 'dismiss')
  expect(asked.message).toBe('Unsubscribe from this digest?')
  await networkQuiet(page, api)
  expect(state.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/digests/subscriptions').length
  await answerConfirm(page, unsubscribe(), 'accept', { confirmLabel: 'Unsubscribe' })
  await expect(page.getByText('Unsubscribed', { exact: true })).toBeVisible()
  await expect(row).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([{ line: `DELETE /api/v1/digests/subscriptions/${SUB_IDS.weekly}`, body: null }])
  expect(requestsTo(api, '/api/v1/digests/subscriptions').length, 'the subscriptions re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('G2 Saved Views › a view\'s Delete: dismissed sends nothing; accepted → the DELETE, the views re-read, no toast', async ({ page }) => {
  const { handlers, state } = digestsDeleteWorld()
  const { api, errors } = await openRollout(page, '/settings/digests?tab=saved-views', { handlers, ready: h1('Digests & Saved Views') })
  const row = page.locator(`[data-digest-view-row="${VIEW_IDS.regressions}"]`)
  await expect(row).toBeVisible()
  await networkQuiet(page, api)
  const del = () => row.getByRole('button', { name: 'Delete', exact: true })

  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe('Delete this saved view?')
  await networkQuiet(page, api)
  expect(state.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/saved-views').length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(row).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([{ line: `DELETE /api/v1/saved-views/${VIEW_IDS.regressions}`, body: null }])
  expect(requestsTo(api, '/api/v1/saved-views').length, 'the views re-read').toBeGreaterThan(reads)
  await expect(page.getByRole('status').filter({ hasText: /deleted|removed/i }), 'no toast').toHaveCount(0)
  assertHermetic(api, errors)
})

// ── /settings/sso ──────────────────────────────────────────────────────────

const ssoCard = (page: Page, name: string) =>
  page.locator('div.space-y-2').filter({ has: page.getByRole('heading', { name, exact: true, level: 3 }) }).last()

test('S1 Activate: an enforced config asks (dismissed: nothing; accepted: the PATCH); an optional one and Deactivate send at once', async ({ page }) => {
  const world = ssoInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
  await expect(ssoCard(page, SSO_ENFORCED.display_name)).toBeVisible()
  await networkQuiet(page, api)
  const activate = (name: string) => ssoCard(page, name).getByRole('button', { name: 'Activate', exact: true })

  const asked = await answerConfirm(page, activate(SSO_ENFORCED.display_name), 'dismiss')
  expect(asked.message).toBe(
    'Activate SSO enforcement? Password login will stop working for everyone except admins using the fallback. They must sign in through the identity provider.',
  )
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/sso/configs').length
  await answerConfirm(page, activate(SSO_ENFORCED.display_name), 'accept', { confirmLabel: 'Activate' })
  await expect(ssoCard(page, SSO_ENFORCED.display_name).getByRole('button', { name: 'Deactivate', exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `PATCH /api/v1/sso/configs/${SSO_ENFORCED.id}`, body: { is_active: true } }])
  expect(requestsTo(api, '/api/v1/sso/configs').length, 'the configs re-read').toBeGreaterThan(reads)

  // An optional config and a deactivation ask nothing.
  await activate(SSO_OPTIONAL.display_name).click()
  await expect(ssoCard(page, SSO_OPTIONAL.display_name).getByRole('button', { name: 'Deactivate', exact: true })).toBeVisible()
  await expect(page.locator('[data-inline-confirm]')).toHaveCount(0)
  await ssoCard(page, SSO_ENFORCED.display_name).getByRole('button', { name: 'Deactivate', exact: true }).click()
  await expect(activate(SSO_ENFORCED.display_name)).toBeVisible()
  await expect(page.locator('[data-inline-confirm]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `PATCH /api/v1/sso/configs/${SSO_OPTIONAL.id}`, body: { is_active: true } },
    { line: `PATCH /api/v1/sso/configs/${SSO_ENFORCED.id}`, body: { is_active: false } },
  ])
  assertHermetic(api, errors)
})

test('S2 a config\'s Delete: dismissed sends nothing; accepted → the DELETE, the configs re-read', async ({ page }) => {
  const world = ssoInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
  await expect(ssoCard(page, SSO_OPTIONAL.display_name)).toBeVisible()
  await networkQuiet(page, api)
  const del = () => ssoCard(page, SSO_OPTIONAL.display_name).getByRole('button', { name: 'Delete', exact: true })
  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe('Are you sure you want to delete this SSO configuration?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  const reads = requestsTo(api, '/api/v1/sso/configs').length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByRole('heading', { name: SSO_OPTIONAL.display_name })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/sso/configs/${SSO_OPTIONAL.id}`, body: null }])
  expect(requestsTo(api, '/api/v1/sso/configs').length).toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('S3 SCIM › an active token\'s Revoke: dismissed sends nothing; accepted → the DELETE, the tokens re-read', async ({ page }) => {
  const world = ssoInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
  await page.getByRole('button', { name: 'SCIM Tokens' }).click()
  const row = page.locator('div.flex').filter({ has: page.getByText(SCIM_TOKEN.name, { exact: true }) }).filter({ has: page.getByRole('button', { name: 'Revoke' }) }).last()
  await expect(row).toBeVisible()
  await networkQuiet(page, api)
  const revoke = () => row.getByRole('button', { name: 'Revoke', exact: true })
  const asked = await answerConfirm(page, revoke(), 'dismiss')
  expect(asked.message).toBe('Revoke this SCIM token?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  const reads = requestsTo(api, '/api/v1/scim-tokens').length
  await answerConfirm(page, revoke(), 'accept', { confirmLabel: 'Revoke' })
  await expect(page.getByText('Revoked', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/scim-tokens/${SCIM_TOKEN.id}`, body: null }])
  expect(requestsTo(api, '/api/v1/scim-tokens').length).toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

// ── /settings/github, /settings/gitlab ─────────────────────────────────────

test('I1 GitHub › Remove: dismissed sends nothing; accepted → the DELETE, the toast, the form back to its defaults', async ({ page }) => {
  const world = githubInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/github', { handlers: world.handlers, ready: h1('GitHub Integration') })
  await expect(page.getByRole('button', { name: 'Remove' })).toBeVisible()
  await networkQuiet(page, api)
  const remove = () => page.getByRole('button', { name: 'Remove', exact: true })
  const asked = await answerConfirm(page, remove(), 'dismiss')
  expect(asked.message).toBe('Delete GitHub integration for this project?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await answerConfirm(page, remove(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText('Integration removed', { exact: true })).toBeVisible()
  await expect(remove(), 'nothing stored: no Remove').toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/projects/${P}/github-integration`, body: null }])
  assertHermetic(api, errors)
})

test('I2 GitLab › Remove token: dismissed changes nothing; accepted arms the removal and sends nothing; Save → the PUT with token ""', async ({ page }) => {
  const world = gitlabInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/gitlab', { handlers: world.handlers, ready: h1('GitLab Integration') })
  await expect(page.getByText('Token set')).toBeVisible()
  await networkQuiet(page, api)
  const from = observed(api).length
  const remove = () => page.getByRole('button', { name: 'Remove token' })

  const asked = await answerConfirm(page, remove(), 'dismiss')
  expect(asked.message).toBe('Remove the stored GitLab token? The removal is applied when you save.')
  await expect(page.getByText('Token set'), 'dismissed: the token stays').toBeVisible()

  await answerConfirm(page, remove(), 'accept', { confirmLabel: 'Remove token' })
  await expect(page.getByText('Token will be removed when you save')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel — keep the stored token' })).toBeVisible()
  await networkQuiet(page, api)
  expect(writesSince(api, from), 'arming the removal sends nothing').toEqual([])
  expect(world.writes).toEqual([])

  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('GitLab integration saved', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const { has_token: _hasToken, last_error: _lastError, last_error_at: _lastErrorAt, ...form } = GITLAB_CONFIG
  expect(world.writes).toEqual([{ line: `PUT /api/v1/projects/${P}/integrations/gitlab`, body: { ...form, token: '' } }])
  assertHermetic(api, errors)
})

// ── /settings/my-notifications, /settings/webhooks ─────────────────────────

test('Z1 the Slack card\'s Remove: dismissed sends nothing; accepted → the DELETE, the toast, the preferences re-read', async ({ page }) => {
  const { handlers, state } = notificationsDeleteWorld()
  const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers, ready: h1('My notifications') })
  const card = page.locator('[data-channel-card="slack"]')
  const remove = () => card.getByRole('button', { name: 'Remove', exact: true })
  await expect(remove()).toBeVisible()
  await networkQuiet(page, api)

  const asked = await answerConfirm(page, remove(), 'dismiss')
  expect(asked.message).toBe('Remove Slack notification preference?')
  await networkQuiet(page, api)
  expect(state.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/notifications/preferences').length
  await answerConfirm(page, remove(), 'accept', { confirmLabel: 'Remove' })
  await expect(page.getByText('Slack preference removed', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(state.writes).toEqual([{ line: `DELETE /api/v1/notifications/preferences/${SLACK_PREF_ID}`, body: null }])
  expect(requestsTo(api, '/api/v1/notifications/preferences').length, 'the preferences re-read').toBeGreaterThan(reads)
  await expect(remove(), 'no stored preference: no Remove').toHaveCount(0)
  assertHermetic(api, errors)
})

test('W1 a webhook\'s delete: dismissed sends nothing; accepted → the DELETE, the re-read, the toast', async ({ page }) => {
  const world = webhooksInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/webhooks', { handlers: world.handlers, ready: h1('Outbound Webhooks') })
  const del = () => page.getByRole('button', { name: `Delete ${WEBHOOK.name}` })
  await expect(del()).toBeVisible()
  await networkQuiet(page, api)

  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe(`Delete webhook "${WEBHOOK.name}"?`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/webhooks').length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText('Deleted', { exact: true })).toBeVisible()
  await expect(del()).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/webhooks/${WEBHOOK.id}`, body: null }])
  expect(requestsTo(api, '/api/v1/webhooks').length, 'the list re-reads').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})
