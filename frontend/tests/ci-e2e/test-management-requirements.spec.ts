/**
 * W13 TCL-06 "requirement traceability lite" (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §2.7, §4.2),
 * hermetic, 1440 × 900, the flag `test_case_requirement_traceability` ON in its world:
 *
 *  - as a TESTER: an authored case's Details ends with **Requirements** (`GET /cases/<id>/requirements` once the case
 *    opens): three links, keys and titles as text; **Add requirement** → ONE `POST` `{ requirement_key, requirement_title,
 *    link_type }`; a second link of the same normalised key renders the server's 409 in the form; **Remove** asks in
 *    the row and sends ONE `DELETE …/requirements/<link id>`;
 *  - a key click filters the list (`requirement=<normalised>` in the URL and on the wire, the removable chip);
 *  - Insights' sixth card **Requirement coverage**: `Requirements verified 12 of 15`, the gaps with their reasons;
 *    **Show all gaps** pages `GET /traceability/gaps` (asked only then);
 *  - a VIEWER reads the links but has no Add / Remove; the flag OFF: no section, no sixth card, the old placeholder.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `requirementsWorld()` (`fixtures-test-library.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  LIBRARY_TEXT,
  LIBRARY_USERS,
  listQueries,
  REQUIREMENTS_CASE,
  requirementLinkId,
  requirementsApi,
  requirementsWorld,
  tmCaseId,
  tmCaseTitle,
  TRACEABILITY_FLAG,
  TRACEABILITY_GAPS_API,
  type LibraryRole,
  type RequirementsWorldOptions,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const pane = (p: Page) => p.locator('[data-case-panel]')
const section = (p: Page) => pane(p).locator('[data-case-requirements]')
const API = requirementsApi(tmCaseId(REQUIREMENTS_CASE))

async function openCase(page: Page, role: LibraryRole = 'TESTER', options: RequirementsWorldOptions = {}, flagOn = true) {
  const world = requirementsWorld({ role, ...options })
  const opened_ = await openRollout(page, '/test-management', {
    handlers: world.handlers, ready: tmReady, user: LIBRARY_USERS[role], flags: flagOn ? { [TRACEABILITY_FLAG]: true } : {},
  })
  await page.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(REQUIREMENTS_CASE)}"]:not([data-in-page-row])`).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(REQUIREMENTS_CASE) })).toBeVisible()
  return { world, ...opened_ }
}

test('TCL-06 as a tester: the links, Add (one POST of the three fields), the 409 inline, Remove asked in the row (one DELETE)', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await openCase(page)
  await expect(section(page).locator('[data-requirement-link]')).toHaveCount(3)
  await expect(section(page)).toContainText('Refund within 24h')
  await expect(section(page).getByText('<b>Receipts</b> show the refund', { exact: true })).toBeVisible()
  await expect(section(page).locator('b')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, API)).toEqual([`GET ${API}`])

  await section(page).getByRole('button', { name: 'Add requirement', exact: true }).click()
  const form = opened(section(page), 'Add requirement')
  await expect(form.locator('#tm-requirement-add-title')).toHaveText('Add requirement')
  await form.getByLabel('Key').fill('REQ-PAY-14')
  await form.getByLabel('Title (optional)').fill('Refund to the original card')
  await form.getByLabel('Link type').selectOption('partially_verifies')
  await form.getByRole('button', { name: 'Add requirement', exact: true }).click()
  await expect(section(page).locator('[data-requirement-link]')).toHaveCount(4)
  await networkQuiet(page, api)
  expect(world.writes.filter((w) => w.line.startsWith('POST '))).toEqual([{
    line: `POST ${API}`,
    body: { requirement_key: 'REQ-PAY-14', requirement_title: 'Refund to the original card', link_type: 'partially_verifies' },
  }])

  await section(page).getByRole('button', { name: 'Add requirement', exact: true }).click()
  const again = opened(section(page), 'Add requirement')
  await again.getByLabel('Key').fill(' req-pay-07 ')
  await again.getByRole('button', { name: 'Add requirement', exact: true }).click()
  await expect(again.getByRole('alert')).toHaveText(LIBRARY_TEXT.requirementAlreadyLinked('REQ-PAY-07'))
  await again.getByRole('button', { name: 'Cancel', exact: true }).click()

  await section(page).getByRole('button', { name: 'Remove REQ-PAY-11', exact: true }).click()
  expect(world.writes.filter((w) => w.line.startsWith('DELETE ')), 'the first click sends nothing').toEqual([])
  const strip = section(page).getByRole('group', { name: 'Remove REQ-PAY-11 from this case?' })
  await strip.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(section(page).locator('[data-requirement-link]')).toHaveCount(3)
  await networkQuiet(page, api)
  expect(world.writes.filter((w) => w.line.startsWith('DELETE ')).map((w) => w.line)).toEqual([`DELETE ${API}/${requirementLinkId(3)}`])
  assertHermetic(api, errors)
})

test('TCL-06 a key filters the list: requirement=<normalised> in the URL and on the wire, the removable chip', async ({ page }) => {
  const { world, api, errors } = await openCase(page)
  await section(page).getByRole('button', { name: '[req-pay-09]', exact: true }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('requirement')).toBe('REQ-PAY-09')
  const note = page.locator('[data-cases-filter-note]')
  await expect(note).toContainText('Requirement: REQ-PAY-09')
  await networkQuiet(page, api)
  const sent = listQueries(world.reads).filter((q) => q.get('size') === '25')
  expect(sent[sent.length - 1].get('requirement')).toBe('REQ-PAY-09')
  await note.getByRole('button', { name: 'Remove the requirement filter REQ-PAY-09' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.has('requirement')).toBe(false)
  assertHermetic(api, errors)
})

test('TCL-06 Insights\' sixth card: Requirements verified 12 of 15, the gaps; Show all gaps asks only then', async ({ page }) => {
  const { world, api, errors } = await openCase(page, 'QA_LEAD')
  await page.getByRole('button', { name: /^Insights/ }).click()
  const insights = opened(page, 'Insights')
  await expect(insights.locator('[data-insights] > *')).toHaveCount(6)
  const card = insights.getByTestId('tm-requirement-coverage')
  await expect(card.locator('[data-requirements-verified]')).toContainText('Requirements verified')
  await expect(card.locator('[data-requirements-verified]')).toContainText('12 of 15')
  await expect(card.locator('[data-requirement-gap="REQ-PAY-12"]')).toContainText('No case links it.')
  await expect(card.locator('[data-requirement-gap="REQ-PAY-11"]')).toContainText('Only "related" links: no case verifies it.')
  await networkQuiet(page, api)
  expect(requestsTo(api, TRACEABILITY_GAPS_API), 'Insights sends nothing for the card').toEqual([])
  await card.getByRole('button', { name: 'Show all gaps', exact: true }).click()
  await expect(card.locator('[data-requirement-gaps-all] [data-requirement-gap]')).toHaveCount(25)
  await expect(card.locator('[data-requirement-gaps-page]')).toHaveText('1-25 of 40')
  await networkQuiet(page, api)
  const queries = world.reads.filter((r) => r.line.startsWith(`GET ${TRACEABILITY_GAPS_API}?`)).map((r) => new URL(r.line.slice(4), 'http://x').searchParams)
  expect(queries.map((q) => [q.get('project_id'), q.get('page'), q.get('size')])).toEqual([[PROJECT_ID, '1', '25']])
  assertHermetic(api, errors)
})

test('TCL-06 a viewer reads the links without Add / Remove; the flag off shows nothing of it', async ({ page }) => {
  const viewer = await openCase(page, 'VIEWER')
  await expect(section(page).locator('[data-requirement-link]')).toHaveCount(3)
  await expect(section(page).getByRole('button', { name: 'Add requirement' })).toHaveCount(0)
  await expect(section(page).getByRole('button', { name: /^Remove / })).toHaveCount(0)
  assertHermetic(viewer.api, viewer.errors)
})

test('TCL-06 the flag off: no Requirements section, five Insights cards, the old placeholder, no requirement read', async ({ page }) => {
  const { api, errors } = await openCase(page, 'TESTER', { flagOff: true }, false)
  await expect(section(page)).toHaveCount(0)
  await expect(page.getByPlaceholder('Search by title, tags or ID…')).toBeVisible()
  await page.getByRole('button', { name: /^Insights/ }).click()
  await expect(opened(page, 'Insights').locator('[data-insights] > *')).toHaveCount(5)
  await networkQuiet(page, api)
  expect(requestsTo(api, API)).toEqual([])
  assertHermetic(api, errors)
})
