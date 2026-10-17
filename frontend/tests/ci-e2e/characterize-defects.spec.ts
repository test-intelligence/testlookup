/**
 * W27a PAN-04 CHARACTERIZATION, `/defects` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.4, row D1),
 * hermetic. Written against the MODAL tree (New defect in a modal dialog) and kept green, UNEDITED, after the
 * in-page conversion: the form is found by the name it keeps (`opened`).
 *
 *  - D1 New defect → Create defect: `POST /api/v1/analytics/defects` with every empty optional field OMITTED
 *    (`{ project_id, title, severity, failure_category }` for a title alone, the trimmed optional fields and
 *    `affects_releases: [id]` when given); the toast names the new id; the list and the insights re-read and the
 *    new row is in the table; the title needs three characters and the Jira link an http(s) URL; Cancel, the X
 *    and Escape send nothing;
 *  - a refused write (a viewer: the server's 403) keeps the form open with the server's sentence;
 *  - All projects: New defect asks for one project and opens nothing.
 *
 * D2-D4 (the row's status / assign / Jira forms) are `defects-triage.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `defectsIntakeWorld()`
 * (`fixtures-in-page.ts`, over `defectsWorld({ releases: true })`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark, openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, RELEASE_ID } from '../visual/production/fixtures'
import { defectsIntakeWorld, INTAKE_REFUSAL, NEW_DEFECT_ID } from '../visual/production/fixtures-in-page'
import { ADMIN, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Defect KPIs')
const newDefect = (page: Page) => page.locator('[data-page-header]').getByRole('button', { name: 'New defect' })
const table = (page: Page) => page.getByRole('table', { name: 'Defects' })
/** A defect's own row (never a form row a conversion might put under it). */
const row = (page: Page, text: string) => table(page).locator('tbody tr:not([data-in-page-row])').filter({ hasText: text })
const listReads = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, '/api/v1/analytics/defects').filter((line) => line.startsWith('GET ')).length
const trendReads = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, '/api/v1/analytics/defects/trend').length

test('D1 a title alone: validation, Cancel / X / Escape send nothing, then the POST omits every empty field', async ({ page }) => {
  const world = defectsIntakeWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers: world.handlers, ready })

  await newDefect(page).click()
  let form = opened(page, 'New defect')
  await expect(form).toBeVisible()
  const create = form.getByRole('button', { name: 'Create defect' })
  await expect(create, 'a title is required').toBeDisabled()
  await form.getByLabel(/^Title/).fill('ab')
  await expect(create, 'three characters at least').toBeDisabled()
  await form.getByLabel(/^Title/).fill('Card retry charges twice')
  await expect(create).toBeEnabled()
  await form.getByLabel(/^Jira ticket URL/).fill('jira.example.test/browse/SHOP-1')
  await expect(form.getByText('Must be an http(s) URL.')).toBeVisible()
  await expect(create, 'not an http(s) link').toBeDisabled()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)

  await newDefect(page).click()
  form = opened(page, 'New defect')
  await expect(form.getByLabel(/^Title/), 'a reopened form starts empty').toHaveValue('')
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)

  await newDefect(page).click()
  form = opened(page, 'New defect')
  await form.getByLabel(/^Title/).focus()
  await page.keyboard.press('Escape')
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // A title (trimmed) and the defaults: P1, Product bug.
  const lists = listReads(api)
  await newDefect(page).click()
  form = opened(page, 'New defect')
  await form.getByLabel(/^Title/).fill('  Card retry charges twice  ')
  await form.getByRole('button', { name: 'Create defect' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Defect ${NEW_DEFECT_ID.slice(0, 8)} created`)).toBeVisible()
  await expect(row(page, 'Card retry charges twice')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/analytics/defects', body: { project_id: P, title: 'Card retry charges twice', severity: 'P1', failure_category: 'PRODUCT_BUG' } },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)
  assertHermetic(api, errors)
})

test('D1 every field: the trimmed optional fields and affects_releases in the POST; the list and the insights re-read', async ({ page }) => {
  const world = defectsIntakeWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  const lists = listReads(api)
  const trends = trendReads(api)

  await newDefect(page).click()
  const form = opened(page, 'New defect')
  await form.getByLabel(/^Title/).fill('Refund webhook fires twice')
  await form.getByRole('group', { name: 'Severity' }).getByRole('button', { name: 'P0 — Blocker' }).click()
  await form.getByLabel('Failure category').selectOption('INFRASTRUCTURE')
  await form.getByLabel('Component').fill('  payments-webhooks  ')
  await form.getByLabel(/^Test name/).fill('refund webhook retried')
  await form.getByLabel(/^Suite name/).fill('  Payments ')
  await form.getByLabel('Affects release').selectOption(RELEASE_ID.current)
  await form.getByLabel(/^Jira ticket URL/).fill(' https://jira.example.test/browse/SHOP-950 ')
  await form.getByLabel(/^Description/).fill('  Seen twice in the last build.  ')
  await form.getByRole('button', { name: 'Create defect' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText(`Defect ${NEW_DEFECT_ID.slice(0, 8)} created`)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/analytics/defects',
      body: {
        project_id: P,
        title: 'Refund webhook fires twice',
        severity: 'P0',
        failure_category: 'INFRASTRUCTURE',
        description: 'Seen twice in the last build.',
        component: 'payments-webhooks',
        test_name: 'refund webhook retried',
        suite_name: 'Payments',
        jira_ticket_url: 'https://jira.example.test/browse/SHOP-950',
        affects_releases: [RELEASE_ID.current],
      },
    },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)
  expect(trendReads(api), 'the insights re-read').toBeGreaterThan(trends)
  assertHermetic(api, errors)
})

test('D1 a refused write: a viewer\'s Create keeps the form open with the server\'s sentence', async ({ page }) => {
  const world = defectsIntakeWorld({ refuse: true })
  const { api, errors } = await openRollout(page, '/defects', { handlers: world.handlers, ready, user: VIEWER })
  await newDefect(page).click()
  const form = opened(page, 'New defect')
  await form.getByLabel(/^Title/).fill('Card retry charges twice')
  await form.getByRole('button', { name: 'Create defect' }).click()
  await expect(form.getByText(INTAKE_REFUSAL).first()).toBeVisible()
  await expect(form, 'the form stays open with what was typed').toBeVisible()
  await expect(form.getByLabel(/^Title/)).toHaveValue('Card retry charges twice')
  await expect(row(page, 'Card retry charges twice')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: 'POST /api/v1/analytics/defects', body: { project_id: P, title: 'Card retry charges twice', severity: 'P1', failure_category: 'PRODUCT_BUG' } },
  ])
  assertHermetic(api, errors)
})

test('D1 in All projects: New defect asks for one project and opens nothing', async ({ page }) => {
  const world = defectsIntakeWorld()
  const { api, errors } = await openProductionPage(page, '/defects', {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: world.handlers,
    ready,
    mock: { flags: {} },
  })
  await newDefect(page).click()
  await expect(page.getByText('Pick a single project to intake a defect')).toBeVisible()
  await expect(opened(page, 'New defect')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
