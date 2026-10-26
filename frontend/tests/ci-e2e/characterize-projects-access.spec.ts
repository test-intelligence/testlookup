/**
 * W27b PAN-05c + PAN-06a CHARACTERIZATION, projects and access (`docs/deferred-scope-2026-10-13/work/W27b-contract.md`
 * §2.3, rows P1-P3, O1-O2, U1-U3, M1-M3, K1-K4, F2, D1, and the AMENDMENT's billing 404 flow), hermetic. Written
 * against the MODAL tree (the forms in modal dialogs, the deletes behind native `confirm()`s) and kept green,
 * UNEDITED, after the in-page conversion: each container is found by its kept name (`opened`, or its heading for
 * Import CODEOWNERS, `openedByHeading`), each question answered whichever shape asks it (`answerConfirm`), only
 * named controls driven, Escape only where it works today (New / Edit Project, the streaming key).
 *
 *  - P1 New Project → `POST /api/v1/projects` (only non-empty strings, not trimmed; the slug follows the name),
 *    `Project created`, the list re-read, the new project active; P2 Edit → nothing changed: `No changes to save`
 *    and no request; two changes → `PUT /api/v1/projects/{id}` with only those; P3 Archive → dismissed nothing,
 *    accepted `DELETE /api/v1/projects/{id}`; the card's click (select) never runs;
 *  - O1 a rule's Delete → `DELETE …/ownership/rules/{id}`, the rules and coverage re-read; O2 Import CODEOWNERS →
 *    `{ source: 'text', text: <raw> }` / `{ source: 'github' }`, the toast, the rules re-read;
 *  - U1 Edit user → its projects and memberships read on open, `PATCH /api/v1/users/{id}` with only the changed
 *    fields, a project removed and added at once; U2 Add User → `POST /api/v1/users` (blank full name omitted), the
 *    temporary password shown once, copied, gone after Done; U3 Invite → `POST /api/v1/users/invite`, the absolute
 *    link on this origin, Close drops it;
 *  - M1 Add Member → `POST …/members` `{ user_id, role }`; M2 Remove from project → dismissed nothing, accepted the
 *    DELETE; M3 Reset QA Lead password → dismissed nothing, accepted `POST …/default-qa-lead/reset-password` `{}`,
 *    the password in the clipboard, the toast names the email (never the password);
 *  - K1 Generate streaming key → `POST /api/v1/keys` (trimmed name, `stream:write`, the project, the expiry or
 *    null), the key shown once with its snippets, closed by Done, the X and Escape; K2 Revoke; K3 Generate my key
 *    (name untrimmed, no project, blank expiry omitted), shown once, Done; K4 Revoke key;
 *  - F2 a flag's Delete → dismissed nothing, accepted `DELETE /api/v1/feature-flags/{key}`, `<key> deleted`;
 *  - D1 Delete test runs → a wrong name keeps the confirm disabled (`aria-disabled`), the exact name →
 *    `POST /api/v1/projects/{id}/reset` `{ mode: 'runs', confirmation_name }`, the result card and the toast;
 *  - B1 (amendment) a project with NO quota (`GET …/llm-quota` answers 404): the editor shows the defaults and Save
 *    sends them with the edit. (Today's non-404 behaviour, defaults over a failed read, is the bug G-C fixes: not
 *    pinned.)
 *  - every one-time secret: no toast's text and no request URL contains it;
 *  - role gating: a QA lead has no Add User / Invite / Edit user, no Generate streaming key, and Project data is
 *    admin only; a viewer cannot own a personal key.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-in-page-b.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { answerConfirm, opened, openedByHeading } from '../lib/inPage'
import type { MockedApi } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { BILLING_PROJECT, billingHandlers } from '../visual/production/fixtures-agents-billing-flags'
import { PROJECT_ID, USER } from '../visual/production/fixtures'
import {
  DEFAULT_QA_LEAD_EMAIL,
  flagsDeleteWorld,
  IMPORT_RESULT,
  keysInPageWorld,
  LEGACY_PROJECT,
  NEW_PROJECT_ID,
  ONE_TIME,
  OWNERSHIP_RULE_ROWS,
  ownershipInPageWorld,
  PERSONAL_KEY,
  projectDataWorld,
  projectsInPageWorld,
  RESET_DELETED,
  STREAMING_KEY,
  UM_USERS,
  usersInPageWorld,
} from '../visual/production/fixtures-in-page-b'
import { ADMIN, VIEWER } from '../visual/production/fixtures-releases'

test.use({
  viewport: { width: 1440, height: 1000 },
  timezoneId: 'UTC',
  locale: 'en-US',
  reducedMotion: 'reduce',
  permissions: ['clipboard-read', 'clipboard-write'],
})

const P = PROJECT_ID
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const clipboard = (page: Page) => page.evaluate(() => navigator.clipboard.readText())
/** A one-time secret never reaches a toast or a request URL. */
async function expectSecretKept(page: Page, api: MockedApi, secret: string) {
  await expect(page.getByRole('status').filter({ hasText: secret }), 'no toast carries the secret').toHaveCount(0)
  expect([...api.seen, ...api.unhandled].filter((line) => line.includes(secret)), 'no request URL carries the secret').toEqual([])
}
const activeProjectId = (page: Page) =>
  page.evaluate(() => (JSON.parse(localStorage.getItem('testlookup-active-project') ?? '{}') as { state?: { activeProjectId?: string } }).state?.activeProjectId)

// ── /projects ──────────────────────────────────────────────────────────────

const projectCard = (page: Page, name: string): Locator =>
  page.locator('.card').filter({ has: page.getByText(name, { exact: true }) })

test('P1 New Project: Cancel, the X and Escape send nothing; the POST of the non-empty fields, the toast, the new project active', async ({ page }) => {
  const world = projectsInPageWorld()
  const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1('Projects') })
  await expect(projectCard(page, LEGACY_PROJECT.name)).toBeVisible()
  await networkQuiet(page, api)
  const create = () => page.getByRole('button', { name: 'New Project' }).click()

  await create()
  let form = opened(page, 'New Project')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Project Name *'), 'the name is focused').toBeFocused()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await create()
  form = opened(page, 'New Project')
  await form.getByRole('button', { name: 'Close new project dialog' }).click()
  await expect(form).toHaveCount(0)
  await create()
  form = opened(page, 'New Project')
  await form.getByLabel('Project Name *').fill('typed, then Escape')
  await form.getByLabel('Project Name *').press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/projects').length
  await create()
  form = opened(page, 'New Project')
  await expect(form.getByLabel('Project Name *'), 'a reopened form is empty').toHaveValue('')
  await form.getByLabel('Project Name *').fill('Payment Gateway API')
  await expect(form.getByLabel('Slug *'), 'the slug follows the name').toHaveValue('payment-gateway-api')
  await form.getByLabel('Description').fill('Cards and wallets ')
  await form.getByLabel('Jira Project Key').fill('pay')
  await expect(form.getByLabel('Jira Project Key')).toHaveValue('PAY')
  await form.getByRole('button', { name: 'Create Project' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Project created', { exact: true })).toBeVisible()
  await expect(projectCard(page, 'Payment Gateway API')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/projects', body: { name: 'Payment Gateway API', slug: 'payment-gateway-api', description: 'Cards and wallets ', jira_project_key: 'PAY' } },
  ])
  expect(requestsTo(api, '/api/v1/projects').length, 'the list re-reads').toBeGreaterThan(reads)
  await expect.poll(() => activeProjectId(page), { message: 'the new project is the active one' }).toBe(NEW_PROJECT_ID)
  assertHermetic(api, errors)
})

test('P2 Edit Project: Cancel, the X and Escape send nothing; nothing changed is not sent; two changes → a PUT of only those', async ({ page }) => {
  const world = projectsInPageWorld()
  const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1('Projects') })
  await expect(projectCard(page, LEGACY_PROJECT.name)).toBeVisible()
  await networkQuiet(page, api)
  const edit = async () => {
    await projectCard(page, LEGACY_PROJECT.name).hover()
    await projectCard(page, LEGACY_PROJECT.name).getByRole('button', { name: 'Edit' }).click()
  }

  await edit()
  let form = opened(page, 'Edit Project')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Project Name *')).toHaveValue(LEGACY_PROJECT.name)
  await expect(form.getByLabel('Slug')).toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await edit()
  form = opened(page, 'Edit Project')
  await form.getByRole('button', { name: 'Close edit project dialog' }).click()
  await expect(form).toHaveCount(0)
  await edit()
  form = opened(page, 'Edit Project')
  await form.getByLabel('Description').fill('typed, then Escape')
  await form.getByLabel('Description').press('Escape')
  await expect(form).toHaveCount(0)

  // Nothing changed: nothing sent.
  await edit()
  form = opened(page, 'Edit Project')
  await form.getByRole('button', { name: 'Save Changes' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('No changes to save', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const reads = requestsTo(api, '/api/v1/projects').length
  await edit()
  form = opened(page, 'Edit Project')
  await form.getByLabel('Description').fill('Retired storefront ')
  await form.getByLabel('Tags (comma-separated)').fill(' web, legacy ,, ')
  await form.getByRole('button', { name: 'Save Changes' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Project updated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PUT /api/v1/projects/${LEGACY_PROJECT.id}`, body: { description: 'Retired storefront ', tags: ['web', 'legacy'] } },
  ])
  expect(requestsTo(api, '/api/v1/projects').length, 'the list re-reads').toBeGreaterThan(reads)
  expect(await activeProjectId(page), 'editing a card never selects it').toBe(P)
  assertHermetic(api, errors)
})

test('P3 Archive: dismissed sends nothing; accepted → the DELETE, the toast, the card gone; the card is never selected', async ({ page }) => {
  const world = projectsInPageWorld()
  const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1('Projects') })
  await expect(projectCard(page, LEGACY_PROJECT.name)).toBeVisible()
  await networkQuiet(page, api)
  const archive = () => projectCard(page, LEGACY_PROJECT.name).getByRole('button', { name: 'Archive' })

  await projectCard(page, LEGACY_PROJECT.name).hover()
  const asked = await answerConfirm(page, archive(), 'dismiss')
  expect(asked.message).toBe(`Archive project "${LEGACY_PROJECT.name}"? It will be hidden but not deleted.`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await expect(projectCard(page, LEGACY_PROJECT.name)).toBeVisible()

  await projectCard(page, LEGACY_PROJECT.name).hover()
  await answerConfirm(page, archive(), 'accept', { confirmLabel: 'Archive' })
  await expect(page.getByText('Project archived', { exact: true })).toBeVisible()
  await expect(projectCard(page, LEGACY_PROJECT.name)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/projects/${LEGACY_PROJECT.id}`, body: null }])
  expect(await activeProjectId(page), 'archiving a card never selects it').toBe(P)
  assertHermetic(api, errors)
})

// ── /ownership ─────────────────────────────────────────────────────────────

const RULES = `/api/v1/projects/${P}/ownership/rules`
const COVERAGE = `/api/v1/projects/${P}/ownership/codeowners/coverage`
const ruleRow = (page: Page, pattern: string) =>
  page.locator('div.grid-cols-7:not([data-in-page-row])').filter({ has: page.getByText(pattern, { exact: true }) })

test('O1 a rule\'s Delete: dismissed sends nothing; accepted → the DELETE, the toast, the rules and coverage re-read', async ({ page }) => {
  const world = ownershipInPageWorld()
  const { api, errors } = await openRollout(page, '/ownership', { handlers: world.handlers, ready: h1('Ownership') })
  const [, search] = OWNERSHIP_RULE_ROWS
  await expect(ruleRow(page, search.match_pattern)).toBeVisible()
  await networkQuiet(page, api)
  const del = () => ruleRow(page, search.match_pattern).getByRole('button', { name: 'Delete', exact: true })

  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe('Delete this ownership rule?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const rules = requestsTo(api, RULES).length
  const coverage = requestsTo(api, COVERAGE).length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText('Rule deleted', { exact: true })).toBeVisible()
  await expect(ruleRow(page, search.match_pattern)).toHaveCount(0)
  await expect(ruleRow(page, OWNERSHIP_RULE_ROWS[0].match_pattern)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE ${RULES}/${search.id}`, body: null }])
  expect(requestsTo(api, RULES).length, 'the rules re-read').toBeGreaterThan(rules)
  expect(requestsTo(api, COVERAGE).length, 'the coverage re-reads').toBeGreaterThan(coverage)
  assertHermetic(api, errors)
})

test('O2 Import CODEOWNERS: Cancel sends nothing; pasted text → the raw text POST, the count, the toast; Fetch from GitHub → { source: github }', async ({ page }) => {
  const world = ownershipInPageWorld()
  const { api, errors } = await openRollout(page, '/ownership', { handlers: world.handlers, ready: h1('Ownership') })
  await expect(ruleRow(page, OWNERSHIP_RULE_ROWS[0].match_pattern)).toBeVisible()
  await networkQuiet(page, api)
  const importer = () => page.getByRole('button', { name: 'Import CODEOWNERS' }).click()

  await importer()
  let form = openedByHeading(page, 'Import CODEOWNERS')
  await expect(form).toBeVisible()
  const source = form.getByRole('radiogroup', { name: 'Import source' })
  await expect(source.getByRole('radio', { name: 'Paste text' })).toHaveAttribute('aria-checked', 'true')
  await expect(form.getByRole('button', { name: 'Import', exact: true }), 'no text: disabled').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const raw = '# payments team\nsrc/payments/**  @acme/payments\n\ndocs/  @alice\n'
  const rules = requestsTo(api, RULES).length
  await importer()
  form = openedByHeading(page, 'Import CODEOWNERS')
  await form.getByRole('textbox').fill(raw)
  await expect(form.getByText('2 owner lines detected')).toBeVisible()
  await form.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Imported ${IMPORT_RESULT.rules_created} rules (replaced ${IMPORT_RESULT.rules_replaced})`, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const IMPORT = `/api/v1/projects/${P}/ownership/codeowners/import`
  expect(world.writes).toEqual([{ line: `POST ${IMPORT}`, body: { source: 'text', text: raw } }])
  expect(requestsTo(api, RULES).length, 'the rules re-read').toBeGreaterThan(rules)

  await importer()
  form = openedByHeading(page, 'Import CODEOWNERS')
  await form.getByRole('radiogroup', { name: 'Import source' }).getByRole('radio', { name: 'Fetch from GitHub' }).click()
  await expect(form).toContainText('.github/CODEOWNERS')
  await form.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([{ line: `POST ${IMPORT}`, body: { source: 'github' } }])
  assertHermetic(api, errors)
})

// ── /users ─────────────────────────────────────────────────────────────────

const userRow = (page: Page, name: string) =>
  page.locator('tbody tr:not([data-in-page-row])').filter({ has: page.getByText(name, { exact: true }) })
const PRIYA = UM_USERS.priya

test('U1 Edit user: its projects and memberships read on open; Cancel sends nothing; only the changed fields are PATCHed; a project removed and added at once', async ({ page }) => {
  const world = usersInPageWorld()
  const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
  await expect(userRow(page, PRIYA.full_name ?? '')).toBeVisible()
  await networkQuiet(page, api)
  const MEMBERSHIPS = `/api/v1/users/${PRIYA.id}/memberships`
  expect(requestsTo(api, MEMBERSHIPS), 'no memberships before the form opens').toEqual([])
  const projects = requestsTo(api, '/api/v1/projects').length
  const edit = () => userRow(page, PRIYA.full_name ?? '').getByRole('button', { name: 'Edit user' }).click()

  await edit()
  let form = opened(page, 'Edit User')
  await expect(form).toBeVisible()
  await expect(form.getByText('Checkout', { exact: true }), 'the memberships are shown').toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, MEMBERSHIPS).length, 'the memberships are read on open').toBeGreaterThan(0)
  expect(requestsTo(api, '/api/v1/projects').length, 'the projects are read on open').toBeGreaterThan(projects)
  await form.getByLabel('Full Name').fill('typed, then cancelled')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const users = requestsTo(api, '/api/v1/users').length
  await edit()
  form = opened(page, 'Edit User')
  await form.getByLabel('Full Name').fill('Priya N. Nair')
  await form.getByLabel('Role', { exact: true }).selectOption('QA_LEAD')
  await form.getByRole('button', { name: 'Save Changes' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('User updated', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `PATCH /api/v1/users/${PRIYA.id}`, body: { full_name: 'Priya N. Nair', role: 'QA_LEAD' } }])
  expect(requestsTo(api, '/api/v1/users').length, 'the users re-read').toBeGreaterThan(users)

  // The project access acts at once: remove Checkout, then add it back as a tester.
  await userRow(page, 'Priya N. Nair').getByRole('button', { name: 'Edit user' }).click()
  form = opened(page, 'Edit User')
  await form.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(page.getByText('Removed from project', { exact: true })).toBeVisible()
  await expect(form.getByText('Not assigned to any projects.')).toBeVisible()
  await form.getByLabel('Project to add').selectOption(P)
  await form.getByLabel('Role in project').selectOption('TESTER')
  await form.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(page.getByText('Added to project', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `DELETE /api/v1/projects/${P}/members/${PRIYA.id}`, body: null },
    { line: `POST /api/v1/projects/${P}/members`, body: { user_id: PRIYA.id, role: 'TESTER' } },
  ])
  await form.getByRole('button', { name: 'Save Changes' }).click()
  await expect(page.getByText('No changes to save', { exact: true })).toBeVisible()
  await expect(form).toHaveCount(0)
  expect(world.writes).toHaveLength(3)
  assertHermetic(api, errors)
})

/*
 * U2 / U3 pin the request, the toast, the users re-read and that the one-time value never reaches a toast or a
 * URL, and NOT the result phase. FINDING (pre-existing, measured): today the result phase is never shown.
 * `refreshUsers()` (`hooks/useUserManagement.ts`) calls `mutate(filter, undefined, { revalidate: true })`, which
 * CLEARS every users key, so `UsersTab`'s `isLoading` early return unmounts the open form with the temporary
 * password (or the invitation link) and remounts it empty. With the data kept (`mutate(filter)`), the same flow
 * shows the password, copies it and drops it on Done (checked on a scratch copy). A characterization must not
 * make that bug the expected output, and must hold if it is fixed: so neither shape is asserted here.
 */
test('U2 Add User: Cancel sends nothing; the POST (blank full name omitted), the toast, the users re-read; the password in no toast or URL', async ({ page }) => {
  const world = usersInPageWorld()
  const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
  await expect(userRow(page, PRIYA.full_name ?? '')).toBeVisible()
  await networkQuiet(page, api)
  const projects = requestsTo(api, '/api/v1/projects').length

  await page.getByRole('button', { name: 'Add User' }).click()
  let form = opened(page, 'Add User')
  await expect(form).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/projects').length, 'the projects are read when the form opens').toBeGreaterThan(projects)
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const users = requestsTo(api, '/api/v1/users').length
  await page.getByRole('button', { name: 'Add User' }).click()
  form = opened(page, 'Add User')
  await form.getByLabel('Email address').fill('jane.doe@example.test')
  await form.getByLabel('Username').fill('jdoe')
  await form.getByLabel('Role').selectOption('TESTER')
  await form.getByRole('button', { name: 'Create User' }).click()
  await expect(page.getByText('User created', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: 'POST /api/v1/users', body: { email: 'jane.doe@example.test', username: 'jdoe', role: 'TESTER' } }])
  expect(requestsTo(api, '/api/v1/users').length, 'the users re-read').toBeGreaterThan(users)
  await expect(userRow(page, 'jdoe'), 'the new user is listed').toBeVisible()
  await expectSecretKept(page, api, ONE_TIME.tempPassword)
  assertHermetic(api, errors)
})

test('U3 Invite User: Cancel sends nothing; the POST, the toast, the users re-read; the invitation token in no toast or URL', async ({ page }) => {
  const world = usersInPageWorld()
  const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
  await expect(userRow(page, PRIYA.full_name ?? '')).toBeVisible()
  await networkQuiet(page, api)

  await page.getByRole('button', { name: 'Invite User' }).click()
  let form = opened(page, 'Invite User')
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  const users = requestsTo(api, '/api/v1/users').length
  await page.getByRole('button', { name: 'Invite User' }).click()
  form = opened(page, 'Invite User')
  await form.getByLabel('Email address').fill('new.hire@example.test')
  await form.getByLabel('Role').selectOption('VIEWER')
  await form.getByRole('button', { name: 'Send Invitation' }).click()
  await expect(page.getByText('Invitation created', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: 'POST /api/v1/users/invite', body: { email: 'new.hire@example.test', role: 'VIEWER', project_memberships: [] } }])
  expect(requestsTo(api, '/api/v1/users').length, 'the users re-read').toBeGreaterThan(users)
  await expectSecretKept(page, api, 'w27b-fixture-invite-token')
  assertHermetic(api, errors)
})

test('role gating: a QA lead reads /users with no Add User, Invite User or Edit user', async ({ page }) => {
  const world = usersInPageWorld()
  const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: USER })
  await expect(userRow(page, PRIYA.full_name ?? '')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add User' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Invite User' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Edit user' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

// ── /users › Project access ────────────────────────────────────────────────

async function openMembers(page: Page, user = ADMIN) {
  const world = usersInPageWorld()
  const opened_ = await openRollout(page, '/users?tab=project-members', { handlers: world.handlers, ready: h1('User Management'), user })
  await page.getByLabel('Select Project:').selectOption(P)
  await expect(userRow(page, PRIYA.full_name ?? '')).toBeVisible()
  await networkQuiet(page, opened_.api)
  return { world, ...opened_ }
}

test('M1 Add Member: Cancel sends nothing; a user and a role → the POST, the toast, the new row', async ({ page }) => {
  const { world, api, errors } = await openMembers(page)
  await page.getByRole('button', { name: 'Add Member' }).click()
  let form = opened(page, 'Add Member to Checkout')
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  expect(world.writes).toEqual([])

  await page.getByRole('button', { name: 'Add Member' }).click()
  form = opened(page, 'Add Member to Checkout')
  await expect(form.getByRole('button', { name: 'Add Member' }), 'no user: disabled').toBeDisabled()
  await form.getByRole('combobox').first().selectOption(UM_USERS.omar.id)
  await form.getByLabel('Project Role').selectOption('QA_ENGINEER')
  await form.getByRole('button', { name: 'Add Member' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Member added successfully', { exact: true })).toBeVisible()
  await expect(userRow(page, UM_USERS.omar.full_name ?? '')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/members`, body: { user_id: UM_USERS.omar.id, role: 'QA_ENGINEER' } }])
  assertHermetic(api, errors)
})

test('M2 Remove from project: dismissed sends nothing; accepted → the DELETE, the toast, the row gone', async ({ page }) => {
  const { world, api, errors } = await openMembers(page)
  const remove = () => userRow(page, PRIYA.full_name ?? '').getByRole('button', { name: 'Remove from project' })
  const asked = await answerConfirm(page, remove(), 'dismiss')
  expect(asked.message).toBe('Remove this user from the project?')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await answerConfirm(page, remove(), 'accept', { confirmLabel: 'Remove' })
  await expect(page.getByText('Member removed', { exact: true })).toBeVisible()
  await expect(userRow(page, PRIYA.full_name ?? '')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE /api/v1/projects/${P}/members/${PRIYA.id}`, body: null }])
  assertHermetic(api, errors)
})

test('M3 Reset QA Lead password: dismissed sends nothing; accepted → the POST {}, the password in the clipboard, the toast names the email only', async ({ page }) => {
  const { world, api, errors } = await openMembers(page)
  const reset = () => page.getByRole('button', { name: 'Reset QA Lead password' })
  const asked = await answerConfirm(page, reset(), 'dismiss')
  expect(asked.message).toBe(
    'Reset the password of this project’s auto-provisioned QA Lead user? The new password will be displayed once — copy it before closing.',
  )
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await answerConfirm(page, reset(), 'accept', { confirmLabel: 'Reset password' })
  await expect(page.getByText(`Password reset for ${DEFAULT_QA_LEAD_EMAIL}. Copied to clipboard.`, { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/default-qa-lead/reset-password`, body: {} }])
  expect(await clipboard(page), 'the clipboard holds the new password').toBe(ONE_TIME.qaLeadPassword)
  await expectSecretKept(page, api, ONE_TIME.qaLeadPassword)
  assertHermetic(api, errors)
})

// ── The two API-key pages ──────────────────────────────────────────────────

const KEYS = '/api/v1/keys'

test('K1 Generate streaming key: the POST (trimmed name, the project, the expiry or null), the key shown once; Done, the X and Escape drop it', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/api-keys', { handlers: world.handlers, ready: h1('Streaming API keys'), user: ADMIN })
  await expect(page.getByRole('cell', { name: STREAMING_KEY.name, exact: true })).toBeVisible()
  await networkQuiet(page, api)

  const generate = async (name: string, expires: string) => {
    await page.getByRole('button', { name: 'Generate streaming key' }).click()
    await page.getByPlaceholder('ci-runner-prod').fill(name)
    await page.getByRole('spinbutton').fill(expires)
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    const created = opened(page, 'API key generated')
    await expect(created).toBeVisible()
    return created
  }

  const reads = requestsTo(api, KEYS).length
  let created = await generate('  ci-runner-staging ', '30')
  await expect(created.locator('code').first(), 'the raw key, shown once').toHaveText(ONE_TIME.streamingKey)
  await expect(created.locator('pre').filter({ hasText: ONE_TIME.streamingKey }), 'and in its three snippets').toHaveCount(3)
  await created.getByRole('button', { name: 'Copy key' }).click()
  await expect.poll(() => clipboard(page)).toBe(ONE_TIME.streamingKey)
  await created.getByRole('button', { name: 'Done' }).click()
  await expect(created).toHaveCount(0)
  await expect(page.getByText(ONE_TIME.streamingKey), 'Done drops the key').toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, KEYS).length, 'the keys re-read').toBeGreaterThan(reads)

  created = await generate('ci-runner-nightly', '')
  await created.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(created).toHaveCount(0)
  created = await generate('ci-runner-weekly', '7')
  await page.keyboard.press('Escape')
  await expect(created).toHaveCount(0)
  await expect(page.getByText(ONE_TIME.streamingKey)).toHaveCount(0)
  await networkQuiet(page, api)
  const body = (name: string, expires: number | null) => ({ line: `POST ${KEYS}`, body: { name, scopes: ['stream:write'], project_id: P, expires_days: expires } })
  expect(world.writes).toEqual([body('ci-runner-staging', 30), body('ci-runner-nightly', null), body('ci-runner-weekly', 7)])
  await expect(page.getByRole('status').filter({ hasText: 'API key' }), 'no toast for a generated key').toHaveCount(0)
  await expectSecretKept(page, api, ONE_TIME.streamingKey)
  assertHermetic(api, errors)
})

test('K2 a streaming key\'s Revoke: dismissed sends nothing; accepted → the DELETE, the re-read, the toast', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/api-keys', { handlers: world.handlers, ready: h1('Streaming API keys'), user: ADMIN })
  const row = page.locator('tbody tr:not([data-in-page-row])').filter({ has: page.getByRole('cell', { name: STREAMING_KEY.name, exact: true }) })
  await expect(row).toBeVisible()
  await networkQuiet(page, api)
  const asked = await answerConfirm(page, row.getByRole('button', { name: 'Revoke' }), 'dismiss')
  expect(asked.message).toBe(`Revoke "${STREAMING_KEY.name}"? Active clients using this key will start failing immediately.`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  const reads = requestsTo(api, KEYS).length
  await answerConfirm(page, row.getByRole('button', { name: 'Revoke' }), 'accept', { confirmLabel: 'Revoke' })
  await expect(page.getByText('API key revoked', { exact: true })).toBeVisible()
  await expect(row).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE ${KEYS}/${STREAMING_KEY.id}`, body: null }])
  expect(requestsTo(api, KEYS).length, 'the keys re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('K3 Generate my API key: Cancel sends nothing; the POST (name untrimmed, no project, blank expiry omitted); shown once; Done drops it', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/my-api-keys', { handlers: world.handlers, ready: h1('My API keys') })
  await expect(page.getByRole('cell', { name: PERSONAL_KEY.name, exact: true })).toBeVisible()
  await networkQuiet(page, api)

  await page.getByRole('button', { name: 'Generate key' }).click()
  let form = opened(page, 'Generate my API key')
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  expect(world.writes).toEqual([])

  await page.getByRole('button', { name: 'Generate key' }).click()
  form = opened(page, 'Generate my API key')
  await form.getByLabel('Key name').fill(' GitHub Actions ')
  await form.getByRole('group', { name: 'Scopes (leave empty for full access)' }).getByRole('button', { name: 'project:write' }).click()
  await form.getByRole('button', { name: 'Generate', exact: true }).click()
  await expect(form.getByText('Copy this key now — it will not be shown again.')).toBeVisible()
  await expect(form.locator('code'), 'the key, shown once').toHaveText(ONE_TIME.personalKey)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${KEYS}`, body: { name: ' GitHub Actions ', scopes: ['project:write'] } }])
  await form.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByText('Copied!', { exact: true })).toBeVisible()
  expect(await clipboard(page)).toBe(ONE_TIME.personalKey)
  await form.getByRole('button', { name: 'Done' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(ONE_TIME.personalKey), 'Done drops the key').toHaveCount(0)
  await expectSecretKept(page, api, ONE_TIME.personalKey)
  assertHermetic(api, errors)
})

test('K4 Revoke key: dismissed sends nothing; accepted → the DELETE, the toast', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/my-api-keys', { handlers: world.handlers, ready: h1('My API keys') })
  const revoke = () => page.getByRole('button', { name: `Revoke key ${PERSONAL_KEY.name}` })
  await expect(revoke()).toBeVisible()
  await networkQuiet(page, api)
  const asked = await answerConfirm(page, revoke(), 'dismiss')
  expect(asked.message).toBe(`Revoke your key "${PERSONAL_KEY.name}"? This cannot be undone.`)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  await answerConfirm(page, revoke(), 'accept', { confirmLabel: 'Revoke' })
  await expect(page.getByText('API key revoked', { exact: true })).toBeVisible()
  await expect(revoke()).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `DELETE ${KEYS}/${PERSONAL_KEY.id}`, body: null }])
  assertHermetic(api, errors)
})

test('role gating: a QA lead has no Generate streaming key; a viewer cannot own a personal key', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/api-keys', { handlers: world.handlers, ready: h1('Streaming API keys'), user: USER })
  await expect(page.getByText('Project-scoped streaming keys can only be generated by an Administrator.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate streaming key' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('role gating: a viewer\'s My API keys has no Generate key', async ({ page }) => {
  const world = keysInPageWorld()
  const { api, errors } = await openRollout(page, '/settings/my-api-keys', { handlers: world.handlers, ready: h1('My API keys'), user: VIEWER })
  await expect(page.getByText('Your role cannot own API keys')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate key' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

// ── Feature flags, project data, billing ───────────────────────────────────

test('F2 a flag\'s Delete: dismissed sends nothing; accepted → the DELETE, the refresh, the toast, the row gone', async ({ page }) => {
  const { handlers, state } = flagsDeleteWorld()
  const { api, errors } = await openRollout(page, '/settings/feature-flags', { handlers, ready: h1('Feature Flags'), user: ADMIN })
  const key = 'defect_commander'
  const del = () => page.getByRole('button', { name: `Delete flag ${key}` })
  await expect(del()).toBeVisible()
  await networkQuiet(page, api)
  const asked = await answerConfirm(page, del(), 'dismiss')
  expect(asked.message).toBe(`Delete feature flag "${key}"? This cannot be undone.`)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([])
  const reads = requestsTo(api, '/api/v1/feature-flags').length
  await answerConfirm(page, del(), 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText(`${key} deleted`, { exact: true })).toBeVisible()
  await expect(page.locator(`tr[data-flag-key="${key}"]`)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([{ line: `DELETE /api/v1/feature-flags/${key}`, body: null }])
  expect(requestsTo(api, '/api/v1/feature-flags').length, 'the flags re-read').toBeGreaterThan(reads)
  assertHermetic(api, errors)
})

test('D1 Delete test runs: Cancel and the X send nothing; a wrong name keeps the confirm aria-disabled; the exact name → the reset POST, the result card, the toast', async ({ page }) => {
  const world = projectDataWorld()
  const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: h1('Project Data'), user: ADMIN })
  await networkQuiet(page, api)
  const trigger = () => page.getByRole('button', { name: 'Delete test runs' }).click()

  await trigger()
  let form = opened(page, 'Delete test runs')
  await expect(form).toBeVisible()
  await expect(form).toContainText('This will permanently delete:')
  await expect(form).toContainText('Will be kept:')
  await expect(form).toContainText('Type Checkout to confirm.')
  const field = form.getByLabel('Type Checkout to confirm.')
  await expect(field).toBeFocused()
  const confirm = form.getByRole('button', { name: 'Delete test runs' })
  await expect(confirm).toHaveAttribute('aria-disabled', 'true')
  await field.fill('checkout')
  await expect(confirm, 'the name is case-sensitive').toHaveAttribute('aria-disabled', 'true')
  await field.fill(' Checkout')
  await expect(confirm, 'and not trimmed').toHaveAttribute('aria-disabled', 'true')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await trigger()
  form = opened(page, 'Delete test runs')
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  await trigger()
  form = opened(page, 'Delete test runs')
  await form.getByLabel('Type Checkout to confirm.').fill('Checkout')
  await expect(form.getByRole('button', { name: 'Delete test runs' })).toHaveAttribute('aria-disabled', 'false')
  await form.getByRole('button', { name: 'Delete test runs' }).click()
  await expect(form).toHaveCount(0)
  const total = Object.values(RESET_DELETED).reduce((a, b) => a + b, 0)
  await expect(page.getByText(`Reset complete: ${total} rows deleted`, { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Reset complete (runs)' })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/reset`, body: { mode: 'runs', confirmation_name: 'Checkout' } }])
  assertHermetic(api, errors)
})

test('role gating: Project data is admin only (a QA lead reads the refusal, no control)', async ({ page }) => {
  const world = projectDataWorld()
  const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: (p) => p.getByText('Admin access required'), user: USER })
  await expect(page.getByRole('button', { name: 'Delete test runs' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('B1 (amendment) a project with no quota: the 404 read shows the defaults, Close sends nothing, Save sends the defaults with the edit', async ({ page }) => {
  const { handlers, state } = billingHandlers()
  const { api, errors } = await openRollout(page, '/settings/billing', { handlers, ready: (p) => p.locator('[data-billing-table]'), user: ADMIN })
  const checkout = BILLING_PROJECT.checkout
  const row = page.locator(`tr[data-billing-project="${checkout}"]`)
  await expect(row).toContainText('UNLIMITED')
  await networkQuiet(page, api)
  const QUOTA = `/api/v1/projects/${checkout}/llm-quota`
  expect(requestsTo(api, QUOTA), 'no quota read before the editor opens').toEqual([])

  await row.getByRole('button', { name: 'Edit' }).click()
  let editor = opened(page, 'Edit LLM cost budget')
  await expect(editor.getByLabel('Included per month ($)')).toHaveValue('0')
  await expect(editor.getByLabel('Hard cap ($)')).toHaveValue('100')
  await expect(editor.getByLabel('Overage rate ($/$)')).toHaveValue('1')
  await networkQuiet(page, api)
  expect(requestsTo(api, QUOTA), 'the quota is read once the editor opens (404: none configured)').toEqual([`GET ${QUOTA}`])
  await editor.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(editor).toHaveCount(0)
  expect(state.writes).toEqual([])

  const overviews = requestsTo(api, '/api/v1/billing/overview').length
  await row.getByRole('button', { name: 'Edit' }).click()
  editor = opened(page, 'Edit LLM cost budget')
  await expect(editor.getByLabel('Hard cap ($)')).toHaveValue('100')
  await editor.getByLabel('Included per month ($)').fill('10')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor).toHaveCount(0)
  await expect(page.getByText('Quota saved', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(state.writes).toEqual([
    {
      line: `PUT ${QUOTA}`,
      body: {
        enabled: true,
        period_type: 'MONTHLY',
        included_usd: 10,
        overage_rate_usd: 1,
        hard_cap_usd: 100,
        soft_warn_threshold_pct: 80,
        at_cap_action: 'AUTO_DOWNGRADE_TO_ML',
      },
    },
  ])
  expect(requestsTo(api, '/api/v1/billing/overview').length, 'the overview re-reads').toBeGreaterThan(overviews)
  await expect(row).not.toContainText('UNLIMITED')
  expect(observed(api).filter((line) => line.startsWith('PUT ')), 'exactly one write').toHaveLength(1)
  assertHermetic(api, errors)
})
