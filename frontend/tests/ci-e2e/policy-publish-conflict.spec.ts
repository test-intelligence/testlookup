/**
 * W05 GATE-03, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.1, §4.5), on
 * `/policies/:id` as an ADMIN: two admins published two drafts of one scope at the same moment, and the
 * database (one active policy per scope, migration 0210) refused the second with a 409.
 *
 *  - the loser sees exactly ONE toast, the server's sentence (it saw two: the interceptor's, and the page's
 *    own Axios "Request failed with status code 409");
 *  - the policy list reloads AFTER the refusal and shows exactly one Active badge, the winner's, with the
 *    loser's draft still a Draft;
 *  - the same draft published twice reads "Policy is already published", once.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { isoAgo, LAYOUT, PROJECT_ID, USER } from '../visual/production/fixtures'

const DRAFT_ID = 'bbbbbbbb-0000-4000-8000-000000000011'
const WINNER_ID = 'bbbbbbbb-0000-4000-8000-000000000012'
const RETIRED_ID = 'bbbbbbbb-0000-4000-8000-000000000013'

const RACE =
  'Another policy was published for this scope at the same moment. Reload the policies and publish again if you still want this version.'

const DOCUMENT = {
  schema_version: 1,
  thresholds: { go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
  dimension_weights: {
    user_impact: 0.25, env_sensitivity: 0.1, reproducibility: 0.15, regression_likely: 0.2,
    hist_recurrence: 0.1, blast_radius: 0.15, diagnosis_conf: 0.05,
  },
  rules: [],
  pass_rate_bands: { orange_min: 90, yellow_min: 95, green_min: 99 },
  hard_caps: { max_p0_defects: 0, max_flaky_count: 10, max_new_failures_24h: 20 },
  kind_rules: { enabled: false },
}

const policy = (id: string, name: string, version: number, state: { is_active: boolean; is_draft: boolean }) => ({
  id,
  project_id: PROJECT_ID,
  version,
  name,
  description: null,
  rules: DOCUMENT,
  ...state,
  created_by: 'admin',
  activated_by: state.is_active ? 'other_admin' : null,
  activated_at: state.is_active ? isoAgo(0) : null,
  created_at: isoAgo(3),
  updated_at: isoAgo(0),
})

/** The loser's draft (v5), the other admin's winner (v4, published a moment ago), the policy it replaced. */
const DRAFT = policy(DRAFT_ID, 'Checkout gate (strict)', 5, { is_active: false, is_draft: true })
const WINNER = policy(WINNER_ID, 'Checkout gate (lenient)', 4, { is_active: true, is_draft: false })
const RETIRED = policy(RETIRED_ID, 'Checkout gate', 3, { is_active: false, is_draft: false })

const ADMIN = { ...USER, id: '00000000-0000-4000-8000-0000000000ad', username: 'admin', email: 'admin@example.test', full_name: 'Admin', role: 'ADMIN' }

function handlers(refusal: { status: number; detail: string }): ApiHandlers {
  return [
    ['/api/v1/release-gate-policies', () => [DRAFT, WINNER, RETIRED]],
    [`/api/v1/release-gate-policies/${DRAFT_ID}/publish`, () => respond(refusal.status, { detail: refusal.detail }), 'POST'],
    [`/api/v1/release-gate-policies/${DRAFT_ID}`, () => DRAFT],
    ...LAYOUT,
  ]
}

const editorReady = (p: Page) => p.getByRole('heading', { name: `Edit: ${DRAFT.name}`, level: 1 })
const LIST = '/api/v1/release-gate-policies'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('a publish that lost the race: one toast with the server sentence, then the list with the winner Active', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/policies/${DRAFT_ID}`, {
    handlers: handlers({ status: 409, detail: RACE }),
    ready: editorReady,
    user: ADMIN,
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, LIST), 'the editor does not read the list').toEqual([])
  const dialogs: string[] = []
  page.on('dialog', (dialog) => {
    dialogs.push(dialog.message())
    void dialog.accept()
  })

  await page.getByRole('button', { name: 'Publish', exact: true }).click()

  await expect(page.getByText(RACE)).toHaveCount(1)
  await expect(page.getByText(/status code 409/)).toHaveCount(0)
  expect(dialogs).toEqual(['Publish this policy? It will become active and the previous version will be deactivated.'])

  // The list, read again after the refusal: one Active badge, the winner's; the loser is still a Draft.
  await expect(page).toHaveURL(/\/policies$/)
  await expect(page.getByRole('heading', { name: 'Release Gate Policies', level: 1 })).toBeVisible()
  const primary = page.locator('[data-primary]')
  await expect(primary.getByText('Active', { exact: true })).toHaveCount(1)
  const winnerRow = primary.locator('.card').filter({ hasText: WINNER.name })
  await expect(winnerRow.getByText('Active', { exact: true })).toBeVisible()
  await expect(primary.locator('.card').filter({ hasText: DRAFT.name }).getByText('Draft', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const publishAt = api.seen.indexOf(`POST ${LIST}/${DRAFT_ID}/publish`)
  const listAt = api.seen.indexOf(`GET ${LIST}`)
  expect(publishAt, 'the publish was sent').toBeGreaterThanOrEqual(0)
  expect(listAt, 'the list is read after the refusal').toBeGreaterThan(publishAt)
  // The single toast stays single once the list has rendered.
  await expect(page.getByText(RACE)).toHaveCount(1)
  assertHermetic(api, errors)
})

test('the same draft published twice: "Policy is already published", once', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/policies/${DRAFT_ID}`, {
    handlers: handlers({ status: 409, detail: 'Policy is already published' }),
    ready: editorReady,
    user: ADMIN,
  })
  page.on('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: 'Publish', exact: true }).click()
  await expect(page.getByText('Policy is already published')).toHaveCount(1)
  await expect(page.getByRole('heading', { name: 'Release Gate Policies', level: 1 })).toBeVisible()
  await networkQuiet(page, api)
  await expect(page.getByText('Policy is already published')).toHaveCount(1)
  assertHermetic(api, errors)
})
