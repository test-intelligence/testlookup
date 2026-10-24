/**
 * W27b PAN-05c/d + PAN-06 IN-PAGE SHAPE, projects, access and the settings pages
 * (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.3, §2.4, §1.4, §4.1), hermetic, on the CONVERTED pages at
 * 1280 AND 1440 (the settings column: 732 / 892 px with the sidebar expanded). It FAILS on the modal tree by design.
 * For each container: no overlay (`expectNoOverlay`: no `aria-modal`, no `role="dialog"`, no native dialog since the
 * page opened), its DOM position as §1.4 places it, a row form's one cell spanning its table and the table as wide as
 * with nothing open (± 1 px), `#main-content` never scrolling sideways, no clipped table, and one
 * `INPAGE <route> <container> @<w> {…}` line (< 40 px of slack at 1280 is a finding for G-C / G-D; the chat list's
 * strip is the narrow host, §4.1; the project card's archive strip took D-20's fallback, the row after the card).
 *
 * G-C: `/projects` (New under the header; Edit and Archive's question in the full-width grid row after the card),
 * `/ownership` (a rule's strip after its row; Import under the header), `/users` (Add / Invite under the toolbar,
 * replacing each other; Edit user under its row, 4 columns), Project access (Add Member under the top row; Remove's
 * strip under its row, 5; Reset QA Lead's strip in the card, then the password shown ONCE in the card), the two key
 * pages (the generated key where the form was; Revoke under the row, 7 / 6; Generate my key under the intro), billing
 * (the quota under its row, 10), feature flags (Edit and Delete under the row, inside its group, 6), project data (the
 * typed confirmation under its Danger-zone row).
 * G-D: `/policies/:id` (Publish under the header), `/policies` (Deactivate after the card), `/search` (Clear history
 * inside the Recent card), `/chat` (after the conversation), retention (the purge form in the Danger zone; the
 * criteria form inside the preview), digests, SSO, GitHub, GitLab (in the token cell), my notifications, webhooks
 * (inside their card or after their row), team channels (under the row, 4).
 *
 * The flows themselves are the `characterize-projects-access / -settings` specs. Harness: `tests/lib/rollout.ts`
 * (fail closed, pinned clock). Fixtures: `fixtures-in-page-b.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, openedByHeading, watchNativeDialogs } from '../lib/inPage'
import { boxOf, clippedTables, expectMainFits, followedBy, inPageLine, minContentSlack, ownOverflow, precedes, tableBox } from '../lib/inPageGeometry'
import { cardAround } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { BILLING_PROJECT, billingHandlers } from '../visual/production/fixtures-agents-billing-flags'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  ACTIVE_POLICY,
  CHAT_SESSIONS,
  chatDeleteWorld,
  CRITERIA_PREVIEW,
  digestsDeleteWorld,
  DRAFT_POLICY,
  flagsDeleteWorld,
  gitlabInPageWorld,
  githubInPageWorld,
  keysInPageWorld,
  LEGACY_PROJECT,
  notificationsDeleteWorld,
  ONE_TIME,
  OWNERSHIP_RULE_ROWS,
  ownershipInPageWorld,
  PERSONAL_KEY,
  policiesInPageWorld,
  projectDataWorld,
  projectsInPageWorld,
  RECENT_SEARCHES,
  retentionInPageWorld,
  SCIM_TOKEN,
  SEARCH_PAGE,
  SSO_ENFORCED,
  ssoInPageWorld,
  STREAMING_KEY,
  teamChannelsWorld,
  UM_USERS,
  usersInPageWorld,
  WEBHOOK,
  webhooksInPageWorld,
} from '../visual/production/fixtures-in-page-b'
import { SUB_IDS, VIEW_IDS } from '../visual/production/fixtures-notify-flaky'
import { ADMIN } from '../visual/production/fixtures-releases'

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const header = (page: Page) => page.locator('[data-page-header]')
const ownRows = (table: Locator) => table.locator('tbody > tr:not([data-in-page-row])')
const strip = (scope: Page | Locator) => scope.locator('[data-inline-confirm]')

/** `container` is the in-page row right after `row` (one cell spanning `columns`), and the table kept its width. */
async function expectRowForm(page: Page, what: string, width: number, table: Locator, row: Locator, columns: number, container: Locator, closed: { table: number }) {
  await expectNoOverlay(page)
  const next = row.locator('xpath=following-sibling::tr[1]')
  await expect(next, `${what}: the next row is the in-page row`).toHaveAttribute('data-in-page-row', '')
  await expect(next.locator(':scope > td'), `${what}: one cell`).toHaveCount(1)
  await expect(next.locator(':scope > td'), `${what}: spanning every column`).toHaveAttribute('colspan', String(columns))
  await expect(next.locator(':scope > td').locator(container), `${what}: the container is in that row`).toHaveCount(1)
  const open = await tableBox(table)
  expect(Math.abs(open.table - closed.table), `${what} @${width}: the table keeps its width`).toBeLessThanOrEqual(1)
  await expectMainFits(page, `${what} @${width}`)
  expect(await clippedTables(page.locator('#main-content')), `${what} @${width}: a clipped table`).toEqual([])
  const [box, edge] = [await boxOf(container), await boxOf(table)]
  expect(box.right, `${what} @${width}: inside the table`).toBeLessThanOrEqual(edge.right + 1)
  expect(await ownOverflow(container), `${what} @${width}: scrolls sideways`).toBeLessThanOrEqual(1)
  const [tableSlack, formSlack] = [await minContentSlack(table), await minContentSlack(container)]
  inPageLine(what, width, { table: open.table, box: open.box, form: box.width, tableSlack, formSlack, slack: Math.min(tableSlack, formSlack) })
}

/** A container in the page's flow: no overlay, main fits, its slack printed. */
async function expectSection(page: Page, what: string, width: number, container: Locator, extra: Record<string, unknown> = {}) {
  await expectNoOverlay(page)
  await expectMainFits(page, `${what} @${width}`)
  expect(await clippedTables(page.locator('#main-content')), `${what} @${width}: a clipped table`).toEqual([])
  expect(await ownOverflow(container), `${what} @${width}: scrolls sideways`).toBeLessThanOrEqual(1)
  const box = await boxOf(container)
  inPageLine(what, width, { width: box.width, slack: await minContentSlack(container), ...extra })
  return box
}

/** The element right after `host` is an in-page row holding the strip. */
const nextRowHolds = (host: Locator) => async (s: Locator) => {
  const next = host.locator('xpath=following-sibling::*[1]')
  await expect(next, 'the next element is the in-page row').toHaveAttribute('data-in-page-row', '')
  await expect(next.locator(s)).toHaveCount(1)
}

/** A strip's question: inside `host` (or right after it), no overlay, its slack printed; then Cancel. */
async function expectStrip(page: Page, what: string, width: number, trigger: Locator, sentence: string, place: (s: Locator) => Promise<void>, host?: Locator) {
  await trigger.click()
  const s = strip(page)
  await expect(s).toHaveCount(1)
  await expect(s.locator('p').first()).toHaveText(sentence)
  await place(s)
  await expectSection(page, what, width, s, host ? { host: (await boxOf(host)).width } : {})
  await s.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(s).toHaveCount(0)
}

for (const width of [1280, 1440]) {
  test.describe(`${width} x 1000`, () => {
    test.use({ viewport: { width, height: 1000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`/projects @${width}: New under the header; Edit and Archive's question in the full-width row after the card`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = projectsInPageWorld()
      const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1('Projects') })
      const card = page.locator('.card').filter({ has: page.getByText(LEGACY_PROJECT.name, { exact: true }) })
      await expect(card).toBeVisible()
      await networkQuiet(page, api)

      await page.getByRole('button', { name: 'New Project' }).click()
      const create = opened(page, 'New Project')
      await expect(create).toBeVisible()
      expect(await followedBy(header(page), create), 'under the page header').toBe(true)
      await expect(create.getByLabel('Project Name *')).toBeFocused()
      await expectSection(page, '/projects new', width, create)
      await create.getByRole('button', { name: 'Close new project dialog' }).click()

      await card.hover()
      await card.getByRole('button', { name: 'Edit' }).click()
      const edit = opened(page, 'Edit Project')
      await expect(edit).toBeVisible()
      expect(await followedBy(card, edit), 'right after its card').toBe(true)
      const grid = card.locator('xpath=..')
      const [gridBox, editBox] = [await boxOf(grid), await boxOf(edit)]
      expect(editBox.width, 'a full-width row of the grid').toBeGreaterThanOrEqual(gridBox.width - 2)
      await expectSection(page, '/projects edit', width, edit, { grid: gridBox.width })
      await edit.getByRole('button', { name: 'Cancel' }).click()

      // D-20's fallback (§4.1): inside the 316 px card the strip left 21 px at 1280, so it asks in the full-width
      // grid row right after the card, as Edit does.
      await card.hover()
      await expectStrip(page, '/projects archive', width, card.getByRole('button', { name: 'Archive' }),
        `Archive project "${LEGACY_PROJECT.name}"? It will be hidden but not deleted.`,
        async (s) => {
          await nextRowHolds(card)(s)
          const row = card.locator('xpath=following-sibling::*[1]')
          expect((await boxOf(row)).width, 'the full-width row of the grid').toBeGreaterThanOrEqual((await boxOf(grid)).width - 2)
        }, grid)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/ownership @${width}: a rule's Delete strip right after its row; Import CODEOWNERS under the header`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = ownershipInPageWorld()
      const { api, errors } = await openRollout(page, '/ownership', { handlers: world.handlers, ready: h1('Ownership') })
      const pattern = OWNERSHIP_RULE_ROWS[1].match_pattern
      const row = page.locator('div.grid-cols-7:not([data-in-page-row])').filter({ has: page.getByText(pattern, { exact: true }) })
      await expect(row).toBeVisible()
      await networkQuiet(page, api)
      await expectStrip(page, '/ownership rule-delete', width, row.getByRole('button', { name: 'Delete', exact: true }), 'Delete this ownership rule?',
        nextRowHolds(row))
      await page.getByRole('button', { name: 'Import CODEOWNERS' }).click()
      const importer = openedByHeading(page, 'Import CODEOWNERS')
      await expect(importer).toBeVisible()
      expect(await followedBy(header(page), importer), 'under the page header').toBe(true)
      await expectSection(page, '/ownership import', width, importer)
      await importer.getByRole('button', { name: 'Cancel' }).click()
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/users @${width}: Add and Invite under the toolbar, one at a time; Edit user under its row (4 columns)`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = usersInPageWorld()
      const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      const table = page.locator('#main-content table').first()
      const row = ownRows(table).filter({ has: page.getByText(UM_USERS.priya.full_name ?? '', { exact: true }) })
      await expect(row).toBeVisible()
      await networkQuiet(page, api)
      const toolbar = page.getByLabel('Filter by role')

      await page.getByRole('button', { name: 'Add User' }).click()
      const add = opened(page, 'Add User')
      await expect(add).toBeVisible()
      expect(await precedes(toolbar, add), 'under the toolbar').toBe(true)
      expect(await precedes(add, table), 'above the table').toBe(true)
      await expectSection(page, '/users add', width, add)
      await add.getByLabel('Email address').fill('typed@example.test')
      await page.getByRole('button', { name: 'Invite User' }).click()
      const ask = strip(add)
      await expect(ask).toContainText('Discard your changes to "Add User"?')
      await ask.getByRole('button', { name: 'Discard changes' }).click()
      const invite = opened(page, 'Invite User')
      await expect(invite).toBeVisible()
      await expect(add, 'Invite replaced Add').toHaveCount(0)
      expect(await precedes(invite, table)).toBe(true)
      await expectSection(page, '/users invite', width, invite)
      await invite.getByRole('button', { name: 'Cancel' }).click()

      const closed = await tableBox(table)
      await row.getByRole('button', { name: 'Edit user' }).click()
      const edit = opened(page, 'Edit User')
      await expect(edit.getByText('Checkout', { exact: true })).toBeVisible()
      await expectRowForm(page, '/users edit', width, table, row, 4, edit, closed)
      await edit.getByRole('button', { name: 'Cancel' }).click()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/users Project access @${width}: Add Member under the top row; Remove's strip under its row (5); Reset QA Lead in the card, the password shown once`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = usersInPageWorld()
      const { api, errors } = await openRollout(page, '/users?tab=project-members', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      const picker = page.getByLabel('Select Project:')
      await picker.selectOption(PROJECT_ID)
      const table = page.locator('#main-content table').first()
      const row = ownRows(table).filter({ has: page.getByText(UM_USERS.priya.full_name ?? '', { exact: true }) })
      await expect(row).toBeVisible()
      await networkQuiet(page, api)

      await page.getByRole('button', { name: 'Add Member' }).click()
      const add = opened(page, 'Add Member to Checkout')
      await expect(add).toBeVisible()
      expect(await precedes(picker, add), 'under the top row').toBe(true)
      expect(await precedes(add, table), 'above the members').toBe(true)
      await expectSection(page, '/users members add', width, add)
      await add.getByRole('button', { name: 'Cancel' }).click()

      const closed = await tableBox(table)
      await row.getByRole('button', { name: 'Remove from project' }).click()
      const remove = strip(page)
      await expect(remove.locator('p').first()).toHaveText('Remove this user from the project?')
      await expectRowForm(page, '/users members remove', width, table, row, 5, remove, closed)
      await remove.getByRole('button', { name: 'Cancel', exact: true }).click()

      const card = page.locator('div.rounded-xl').filter({ has: page.getByRole('heading', { name: 'Default QA Lead', exact: true }) }).last()
      await page.getByRole('button', { name: 'Reset QA Lead password' }).click()
      const reset = strip(page)
      await expect(card.locator(reset), 'the question inside the card').toHaveCount(1)
      await expectSection(page, '/users members reset-strip', width, reset, { card: (await boxOf(card)).width })
      await reset.locator('[data-inline-confirm-action]').click()
      // D-15: shown once in the card (Copy, Done), never in a live region or a toast.
      await expect(card.getByText(ONE_TIME.qaLeadPassword, { exact: true }), 'the new password, shown in the card').toBeVisible()
      await expect(page.locator('[role="status"]').filter({ hasText: ONE_TIME.qaLeadPassword })).toHaveCount(0)
      await card.getByRole('button', { name: 'Done' }).click()
      await expect(page.getByText(ONE_TIME.qaLeadPassword), 'Done drops it').toHaveCount(0)
      await expectNoOverlay(page)
      expect(world.writes.map((w) => w.line)).toEqual([`POST /api/v1/projects/${PROJECT_ID}/default-qa-lead/reset-password`])
      assertHermetic(api, errors)
    })

    test(`the key pages @${width}: the generated key where the form was; Revoke under the row (7); Generate my key under the intro; Revoke (6)`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = keysInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/api-keys', { handlers: world.handlers, ready: h1('Streaming API keys'), user: ADMIN })
      const table = page.locator('#main-content table').first()
      const row = ownRows(table).filter({ has: page.getByRole('cell', { name: STREAMING_KEY.name, exact: true }) })
      await expect(row).toBeVisible()
      await networkQuiet(page, api)
      const pointer = page.locator('[data-my-api-keys-pointer]')

      await page.getByRole('button', { name: 'Generate streaming key' }).click()
      await page.getByPlaceholder('ci-runner-prod').fill('ci-runner-staging')
      await page.getByRole('button', { name: 'Generate', exact: true }).click()
      const created = opened(page, 'API key generated')
      await expect(created).toBeVisible()
      expect(await precedes(pointer, created), 'after the pointer, where the form was').toBe(true)
      expect(await precedes(created, table), 'above the keys').toBe(true)
      await expectSection(page, '/settings/api-keys created', width, created)
      await created.getByRole('button', { name: 'Done' }).click()
      await expect(page.getByText(ONE_TIME.streamingKey)).toHaveCount(0)

      const closed = await tableBox(table)
      await row.getByRole('button', { name: 'Revoke' }).click()
      const revoke = strip(page)
      await expect(revoke.locator('p').first()).toHaveText(`Revoke "${STREAMING_KEY.name}"? Active clients using this key will start failing immediately.`)
      await expectRowForm(page, '/settings/api-keys revoke', width, table, row, 7, revoke, closed)
      await revoke.getByRole('button', { name: 'Cancel', exact: true }).click()

      await page.goto('/settings/my-api-keys')
      await expect(h1('My API keys')(page)).toBeVisible()
      const mine = page.locator('#main-content table').first()
      const myRow = ownRows(mine).filter({ has: page.getByRole('cell', { name: PERSONAL_KEY.name, exact: true }) })
      await expect(myRow).toBeVisible()
      await page.getByRole('button', { name: 'Generate key' }).click()
      const generate = opened(page, 'Generate my API key')
      await expect(generate).toBeVisible()
      expect(await precedes(page.locator('[data-my-api-keys-intro]'), generate), 'under the intro').toBe(true)
      expect(await precedes(generate, mine), 'above the table').toBe(true)
      await expectSection(page, '/settings/my-api-keys generate', width, generate)
      await generate.getByRole('button', { name: 'Cancel' }).click()
      const closedMine = await tableBox(mine)
      await myRow.getByRole('button', { name: `Revoke key ${PERSONAL_KEY.name}` }).click()
      const revokeMine = strip(page)
      await expectRowForm(page, '/settings/my-api-keys revoke', width, mine, myRow, 6, revokeMine, closedMine)
      await revokeMine.getByRole('button', { name: 'Cancel', exact: true }).click()
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.line)).toEqual(['POST /api/v1/keys'])
      assertHermetic(api, errors)
    })

    test(`/settings/billing @${width}: the quota under its project row (10 columns)`, async ({ page }) => {
      watchNativeDialogs(page)
      const billing = billingHandlers()
      const { api, errors } = await openRollout(page, '/settings/billing', { handlers: billing.handlers, ready: (p) => p.locator('[data-billing-table]'), user: ADMIN })
      const table = page.locator('[data-billing-table] table')
      const projectRow = page.locator(`tr[data-billing-project="${BILLING_PROJECT.payments}"]`)
      await expect(projectRow).toBeVisible()
      await networkQuiet(page, api)
      const closed = await tableBox(table)
      await projectRow.getByRole('button', { name: 'Edit' }).click()
      const quota = opened(page, 'Edit LLM cost budget')
      await expect(quota.getByLabel('Included per month ($)')).toBeVisible()
      await expectRowForm(page, '/settings/billing quota', width, table, projectRow, 10, quota, closed)
      await expect(page.locator('tr[data-billing-project]'), 'the form row is not a project row').toHaveCount(3)
      await quota.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(quota).toHaveCount(0)
      expect(billing.state.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/feature-flags @${width}: a flag's Edit and Delete under its row, inside its group (6 columns)`, async ({ page }) => {
      watchNativeDialogs(page)
      const flags = flagsDeleteWorld()
      const { api, errors } = await openRollout(page, '/settings/feature-flags', { handlers: flags.handlers, ready: h1('Feature Flags'), user: ADMIN })
      const flagRow = page.locator('tr[data-flag-key="defect_commander"]')
      await expect(flagRow).toBeVisible()
      await networkQuiet(page, api)
      const flagTable = flagRow.locator('xpath=ancestor::table[1]')
      const group = flagRow.locator('xpath=ancestor::tbody[1]')
      const closed = await tableBox(flagTable)
      const rowsBefore = await page.locator('tr[data-flag-key]').count()
      await flagRow.getByRole('button', { name: 'Edit' }).click()
      const edit = opened(page, 'Edit defect_commander')
      await expectRowForm(page, '/settings/feature-flags edit', width, flagTable, flagRow, 6, edit, closed)
      await expect(group.locator(edit), 'inside the flag\'s group').toHaveCount(1)
      await expect(page.locator('tr[data-flag-key]'), 'the form row is not a flag row').toHaveCount(rowsBefore)
      await edit.getByRole('button', { name: 'Cancel' }).click()
      await page.getByRole('button', { name: 'Delete flag defect_commander' }).click()
      const del = strip(page)
      await expect(del.locator('p').first()).toHaveText('Delete feature flag "defect_commander"? This cannot be undone.')
      await expectRowForm(page, '/settings/feature-flags delete', width, flagTable, flagRow, 6, del, closed)
      await del.getByRole('button', { name: 'Cancel', exact: true }).click()
      expect(flags.state.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/project-data @${width}: the typed confirmation right under its Danger-zone row`, async ({ page }) => {
      watchNativeDialogs(page)
      const data = projectDataWorld()
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: data.handlers, ready: h1('Project Data'), user: ADMIN })
      await networkQuiet(page, api)
      // `.first()`: once the form is open its confirm button has the same name (§0.11); the trigger comes first.
      const trigger = page.getByRole('button', { name: 'Delete test runs' }).first()
      const dangerRow = trigger.locator('xpath=..')
      await trigger.click()
      const reset = opened(page, 'Delete test runs')
      await expect(reset).toBeVisible()
      expect(await followedBy(dangerRow, reset), 'right under its Danger-zone row').toBe(true)
      await expect(page.getByRole('region', { name: 'Danger zone' }).locator(reset), 'inside the Danger zone').toHaveCount(1)
      await expect(reset.getByRole('button', { name: 'Delete test runs' })).toHaveAttribute('aria-disabled', 'true')
      await expectSection(page, '/settings/project-data typed-confirm', width, reset)
      await reset.getByRole('button', { name: 'Cancel' }).click()
      expect(data.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/policies @${width}: Publish asks right under the header; Deactivate right after the policy card`, async ({ page }) => {
      watchNativeDialogs(page)
      const policies = policiesInPageWorld()
      const { api, errors } = await openRollout(page, `/policies/${DRAFT_POLICY.id}`, { handlers: policies.handlers, ready: h1(`Edit: ${DRAFT_POLICY.name}`), user: ADMIN })
      await networkQuiet(page, api)
      await expectStrip(page, '/policies/:id publish', width, page.getByRole('button', { name: 'Publish', exact: true }),
        'Publish this policy? It will become active and the previous version will be deactivated.',
        async (s) => { expect(await followedBy(header(page), s), 'right under the page header').toBe(true) })

      await page.goto('/policies')
      const card = page.locator('[data-primary] .card').filter({ hasText: ACTIVE_POLICY.name }).filter({ hasText: 'Active' })
      await expect(card).toHaveCount(1)
      await expectStrip(page, '/policies deactivate', width, card.getByRole('button', { name: 'Deactivate', exact: true }), 'Deactivate this policy?', nextRowHolds(card))
      expect(policies.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/search @${width}: Clear history asks inside the Recent searches card`, async ({ page }) => {
      watchNativeDialogs(page)
      // W12 SRCH-03 (deliberate edit, contract §6.5): the two recents are the server's (`SEARCH_PAGE` answers
      // `GET /api/v1/search/recent`), so nothing is seeded in the browser; the card is the one around the first row.
      const { api, errors } = await openRollout(page, '/search', { handlers: SEARCH_PAGE, ready: (p) => p.getByTestId('searchable-data-summary') })
      const recent = cardAround(page.getByText(RECENT_SEARCHES[0].q, { exact: true }))
      await expect(recent).toBeVisible()
      await networkQuiet(page, api)
      await expectStrip(page, '/search clear-history', width, page.getByRole('button', { name: 'Clear history' }), "Clear all 2 recent searches? This can't be undone.",
        async (s) => { await expect(recent.locator(s), 'inside the Recent searches card').toHaveCount(1) }, recent)
      assertHermetic(api, errors)
    })

    test(`/chat @${width}: a conversation's delete asks right after it, in the 240 px list`, async ({ page }) => {
      watchNativeDialogs(page)
      const chat = chatDeleteWorld()
      const { api, errors } = await openRollout(page, '/chat', { handlers: chat.handlers, flags: { ask_ai_chat: true }, ready: (p) => p.getByRole('heading', { name: 'Ask about Checkout' }) })
      const item = page.getByTestId('chat-session').filter({ hasText: CHAT_SESSIONS[0].title })
      await expect(item).toBeVisible()
      await networkQuiet(page, api)
      await item.hover()
      await expectStrip(page, '/chat delete', width, item.getByRole('button', { name: 'Delete conversation' }), 'Delete this conversation?',
        async (s) => { expect(await followedBy(item, s), 'right after the conversation').toBe(true) },
        page.getByRole('complementary', { name: 'Conversations' }))
      expect(chat.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/retention @${width}: the purge form in the Danger zone after the Manual purge row; the criteria form inside the preview`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = retentionInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: ADMIN })
      await networkQuiet(page, api)
      const danger = page.getByRole('region', { name: 'Danger zone' })
      const purgeNow = page.getByRole('button', { name: 'Purge now' })
      await purgeNow.click()
      const purge = opened(page, 'Purge old data now')
      await expect(purge).toBeVisible()
      await expect(danger.locator(purge), 'inside the Danger zone').toHaveCount(1)
      expect(await precedes(purgeNow, purge), 'after the Manual purge row').toBe(true)
      await expectSection(page, '/settings/retention purge', width, purge)
      await purge.getByRole('button', { name: 'Cancel' }).click()

      await page.getByLabel('Older than days').fill('30')
      await page.getByRole('button', { name: 'Preview', exact: true }).click()
      const preview = page.getByTestId('criteria-preview')
      const del = preview.getByRole('button', { name: `Delete ${CRITERIA_PREVIEW.run_count} runs` })
      await del.click()
      const confirm = opened(page, `Delete ${CRITERIA_PREVIEW.run_count} runs`)
      await expect(preview.locator(confirm), 'inside the preview').toHaveCount(1)
      expect(await precedes(del, confirm), 'under its Delete button').toBe(true)
      await expectSection(page, '/settings/retention criteria', width, confirm)
      await confirm.getByRole('button', { name: 'Cancel' }).click()
      expect(world.writes.map((w) => w.line)).toEqual([`POST /api/v1/projects/${PROJECT_ID}/deletion/preview`])
      assertHermetic(api, errors)
    })

    test(`/settings/digests @${width}: Unsubscribe and a view's Delete ask right after their row`, async ({ page }) => {
      watchNativeDialogs(page)
      const digests = digestsDeleteWorld()
      const { api, errors } = await openRollout(page, '/settings/digests', { handlers: digests.handlers, ready: h1('Digests & Saved Views') })
      const sub = page.locator(`[data-subscription-row="${SUB_IDS.weekly}"]`)
      await expect(sub).toBeVisible()
      await networkQuiet(page, api)
      await expectStrip(page, '/settings/digests unsubscribe', width, sub.getByRole('button', { name: 'Unsubscribe' }), 'Unsubscribe from this digest?', nextRowHolds(sub))
      await page.goto('/settings/digests?tab=saved-views')
      const view = page.locator(`[data-digest-view-row="${VIEW_IDS.regressions}"]`)
      await expect(view).toBeVisible()
      await expectStrip(page, '/settings/digests view-delete', width, view.getByRole('button', { name: 'Delete', exact: true }), 'Delete this saved view?', nextRowHolds(view))
      expect(digests.state.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/sso @${width}: Activate and Delete ask inside the config card; a SCIM token's Revoke after its row`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = ssoInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
      const card = page.locator('div.space-y-2').filter({ has: page.getByRole('heading', { name: SSO_ENFORCED.display_name, exact: true, level: 3 }) }).last()
      await expect(card).toBeVisible()
      await networkQuiet(page, api)
      const inside = async (s: Locator) => { await expect(card.locator(s), 'inside the config card').toHaveCount(1) }
      await expectStrip(page, '/settings/sso activate', width, card.getByRole('button', { name: 'Activate', exact: true }),
        'Activate SSO enforcement? Password login will stop working for everyone except admins using the fallback. They must sign in through the identity provider.', inside, card)
      await expectStrip(page, '/settings/sso delete', width, card.getByRole('button', { name: 'Delete', exact: true }), 'Are you sure you want to delete this SSO configuration?', inside, card)
      await page.getByRole('button', { name: 'SCIM Tokens' }).click()
      const token = page.locator('div.flex').filter({ has: page.getByText(SCIM_TOKEN.name, { exact: true }) }).filter({ has: page.getByRole('button', { name: 'Revoke' }) }).last()
      await expectStrip(page, '/settings/sso scim-revoke', width, token.getByRole('button', { name: 'Revoke', exact: true }), 'Revoke this SCIM token?', nextRowHolds(token))
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/github @${width}: Remove asks inside the Repository card, under its action row`, async ({ page }) => {
      watchNativeDialogs(page)
      const github = githubInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/github', { handlers: github.handlers, ready: h1('GitHub Integration') })
      await networkQuiet(page, api)
      const remove = page.getByRole('button', { name: 'Remove', exact: true })
      const actions = remove.locator('xpath=..')
      const repoCard = remove.locator('xpath=ancestor::section[1]')
      await expectStrip(page, '/settings/github remove', width, remove, 'Delete GitHub integration for this project?',
        async (s) => {
          await expect(repoCard.locator(s), 'inside the Repository card').toHaveCount(1)
          await expect(actions.locator(s), 'not inside the action row').toHaveCount(0)
          expect(await precedes(actions, s), 'under its action row').toBe(true)
        }, repoCard)
      expect(github.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/gitlab @${width}: Remove token asks in the token cell`, async ({ page }) => {
      watchNativeDialogs(page)
      const gitlab = gitlabInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/gitlab', { handlers: gitlab.handlers, ready: h1('GitLab Integration') })
      const trigger = page.getByRole('button', { name: 'Remove token' })
      await expect(trigger).toBeVisible()
      await networkQuiet(page, api)
      const cell = trigger.locator('xpath=ancestor::div[2]')
      await expectStrip(page, '/settings/gitlab remove-token', width, trigger, 'Remove the stored GitLab token? The removal is applied when you save.',
        async (s) => { await expect(cell.locator(s), 'in the token cell').toHaveCount(1) }, cell)
      await expect(page.getByText('Token set'), 'Cancel keeps the stored token').toBeVisible()
      expect(gitlab.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/my-notifications @${width}: a channel card's Remove asks inside the card`, async ({ page }) => {
      watchNativeDialogs(page)
      const notes = notificationsDeleteWorld()
      const { api, errors } = await openRollout(page, '/settings/my-notifications', { handlers: notes.handlers, ready: h1('My notifications') })
      const slack = page.locator('[data-channel-card="slack"]')
      const remove = slack.getByRole('button', { name: 'Remove', exact: true })
      await expect(remove).toBeVisible()
      await networkQuiet(page, api)
      await expectStrip(page, '/settings/my-notifications remove', width, remove, 'Remove Slack notification preference?',
        async (s) => { await expect(slack.locator(s), 'inside the card').toHaveCount(1) }, slack)
      expect(notes.state.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/webhooks @${width}: a webhook's delete asks inside its card`, async ({ page }) => {
      watchNativeDialogs(page)
      const hooks = webhooksInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/webhooks', { handlers: hooks.handlers, ready: h1('Outbound Webhooks') })
      const del = page.getByRole('button', { name: `Delete ${WEBHOOK.name}` })
      await expect(del).toBeVisible()
      await networkQuiet(page, api)
      // The webhook's card: the list item of the `space-y-2` list that holds its header row (a bordered `div`).
      const hookCard = page.locator('div.space-y-2 > div').filter({ has: del })
      await expectStrip(page, '/settings/webhooks delete', width, del, `Delete webhook "${WEBHOOK.name}"?`,
        async (s) => { await expect(hookCard.locator(s), 'inside the webhook card').toHaveCount(1) }, hookCard)
      expect(hooks.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/settings/team-channels @${width}: Remove asks in a full-width row under the team (4 columns)`, async ({ page }) => {
      watchNativeDialogs(page)
      const teams = teamChannelsWorld()
      const { api, errors } = await openRollout(page, '/settings/team-channels', { handlers: teams.handlers, ready: h1('Team channels') })
      const table = page.getByRole('table', { name: 'Team channels' })
      const identity = table.locator('[data-team-row="Identity"]')
      await expect(identity).toBeVisible()
      await networkQuiet(page, api)
      const closed = await tableBox(table)
      await identity.getByRole('button', { name: 'Remove' }).click()
      const s = strip(page)
      await expect(s.locator('p').first()).toHaveText('Remove the notification channel for Identity?')
      await expectRowForm(page, '/settings/team-channels remove', width, table, identity, 4, s, closed)
      await expect(table.locator('[data-team-row]'), 'the strip row is not a team row').toHaveCount(2)
      await s.getByRole('button', { name: 'Cancel', exact: true }).click()
      expect(teams.writes).toEqual([])
      assertHermetic(api, errors)
    })
  })
}
