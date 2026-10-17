/**
 * W08 TRND-01 / TRND-02 (contract §2.1, §2.2), hermetic, on `/trends`: the
 * header's **⋯** menu (`More actions`) offers the page as a report.
 *
 *   - a QA lead: **Email report…** opens a dialog that names the scope and
 *     lists the project's PEOPLE (no service account, no synthetic QA lead, no
 *     deactivated user); two ticked members → **Send** → ONE
 *     `POST /reports/email-trends` whose body names exactly their two user ids,
 *     and the toast `Sent to 2 recipients`. The availability read and the
 *     members list are asked only once the dialog is open (no page-load
 *     request: `rollout-trends.spec.ts`'s inventory is the other half);
 *   - the relay is not configured: the dialog says why BEFORE any click and
 *     **Send** stays disabled; nothing is posted;
 *   - **Export PDF** downloads `trends-<project>-14d.pdf` with the page's scope;
 *   - a viewer: Export PDF and Compare runs, no Email report;
 *   - All Projects (an admin): `Export PDF (pick a project)` and
 *     `Compare runs (pick a project)` are disabled;
 *   - TRND-02: suite `Auth` selected → **Compare runs** → `/runs/compare?mode=latest&suite=Auth`,
 *     whose latest-suite section renders.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures:
 * `trendsReportWorld()` (`fixtures-trends-coverage.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { landmark, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, TRENDS_ON, USER } from '../visual/production/fixtures'
import { RUN_COMPARE_PAGE } from '../visual/production/fixtures-ai-history'
import {
  EMAIL_AVAILABILITY_PATH,
  EMAIL_TRENDS_PATH,
  LISTED_MEMBERS,
  MEMBER,
  NOT_CONFIGURED,
  TRENDS_PDF_PATH,
  trendsReportWorld,
  VIEWER,
} from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ADMIN = { ...USER, id: '00000000-0000-4000-8000-0000000008ad', username: 'admin', full_name: 'Admin', role: 'ADMIN' }
const ready = (p: Page) => landmark(p, 'Trend metrics')
const MEMBERS_PATH = `/api/v1/projects/${P}/members`

const menuTrigger = (page: Page) => page.locator('[data-page-header]').getByRole('button', { name: 'More actions' })
const menuItem = (page: Page, name: string | RegExp) => page.getByRole('menuitem', { name })
const dialog = (page: Page) => opened(page, /Email report/)

async function openTrends(page: Page, handlers: ApiHandlers, user = USER) {
  return openRollout(page, '/trends', { handlers: [...handlers, ...TRENDS_ON], ready, user })
}

async function openMenu(page: Page) {
  await menuTrigger(page).click()
  await expect(menuItem(page, /^Export PDF/)).toBeVisible()
}

test('a QA lead emails the report to two members: one POST naming their ids, then "Sent to 2 recipients"', async ({ page }) => {
  const world = trendsReportWorld()
  const { api, errors } = await openTrends(page, world.handlers)
  await networkQuiet(page, api)
  // Nothing about email is asked before the dialog opens.
  expect(requestsTo(api, EMAIL_AVAILABILITY_PATH), 'availability on load').toEqual([])
  expect(requestsTo(api, MEMBERS_PATH), 'the members on load').toEqual([])

  await openMenu(page)
  await expect(menuItem(page, 'Compare runs')).toBeVisible()
  await menuItem(page, 'Email report…').click()
  await expect(dialog(page)).toBeVisible()
  // What will be sent: the page's window (14 days, no suite, no release).
  await expect(dialog(page)).toContainText('Trends · last 14 days')
  // People only: the service account, the synthetic QA lead and the deactivated user are not listed.
  for (const m of LISTED_MEMBERS) await expect(dialog(page).getByText(m.email)).toBeVisible()
  for (const m of [MEMBER.service, MEMBER.synthetic, MEMBER.inactive]) await expect(dialog(page).getByText(m.email)).toHaveCount(0)
  await expect.poll(() => requestsTo(api, EMAIL_AVAILABILITY_PATH).length, { message: 'availability once the dialog opened' }).toBeGreaterThan(0)

  const send = dialog(page).getByRole('button', { name: 'Send', exact: true })
  await expect(send, 'no recipient yet').toBeDisabled()
  await dialog(page).locator(`[data-email-member="${MEMBER.dana.user_id}"]`).check()
  await dialog(page).locator(`[data-email-member="${MEMBER.omar.user_id}"]`).check()
  await expect(send).toBeEnabled()
  await send.click()
  await expect(page.getByText('Sent to 2 recipients')).toBeVisible()
  await expect(dialog(page)).toHaveCount(0)

  expect(world.writes).toHaveLength(1)
  const { line, body } = world.writes[0] as { line: string; body: Record<string, unknown> }
  expect(line).toBe(`POST ${EMAIL_TRENDS_PATH}`)
  expect([...(body.recipient_user_ids as string[])].sort()).toEqual([MEMBER.dana.user_id, MEMBER.omar.user_id].sort())
  expect(body.recipient_emails).toEqual([])
  expect(body).toMatchObject({ project_id: P, days: 14 })
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the relay is not configured: the dialog says why before any click, and Send stays disabled', async ({ page }) => {
  const world = trendsReportWorld({ available: false })
  const { api, errors } = await openTrends(page, world.handlers)
  await openMenu(page)
  await menuItem(page, 'Email report…').click()
  await expect(dialog(page)).toBeVisible()
  await expect(dialog(page).getByText(NOT_CONFIGURED)).toBeVisible()
  await dialog(page).locator(`[data-email-member="${MEMBER.dana.user_id}"]`).check()
  await expect(dialog(page).getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await networkQuiet(page, api)
  expect(world.writes, 'nothing was posted').toEqual([])
  assertHermetic(api, errors)
})

test('Export PDF downloads trends-<project>-14d.pdf for the page\'s scope', async ({ page }) => {
  const world = trendsReportWorld()
  const { api, errors } = await openTrends(page, world.handlers)
  await openMenu(page)
  const download = page.waitForEvent('download')
  await menuItem(page, /^Export PDF$/).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/^trends-[a-z0-9_-]+-14d\.pdf$/)
  await expect.poll(() => world.pdfs.length).toBe(1)
  const query = new URLSearchParams(world.pdfs[0].split('?')[1] ?? '')
  expect(world.pdfs[0].startsWith(`GET ${TRENDS_PDF_PATH}?`)).toBe(true)
  expect(query.get('project_id')).toBe(P)
  expect(query.get('days')).toBe('14')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a viewer: Export PDF and Compare runs, no Email report', async ({ page }) => {
  const world = trendsReportWorld()
  const { api, errors } = await openTrends(page, world.handlers, VIEWER)
  await openMenu(page)
  await expect(menuItem(page, /^Export PDF$/)).toBeEnabled()
  await expect(menuItem(page, 'Compare runs')).toBeVisible()
  await expect(menuItem(page, /Email report/)).toHaveCount(0)
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  expect(requestsTo(api, EMAIL_AVAILABILITY_PATH), 'a viewer never asks the relay').toEqual([])
  assertHermetic(api, errors)
})

test('All Projects (an admin): the report and Compare runs ask for a project', async ({ page }) => {
  const world = trendsReportWorld()
  // All Projects is an ADMIN's view (every other role is moved onto its first project).
  const { api, errors } = await openProductionPage(page, '/trends', {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: [...world.handlers, ...TRENDS_ON],
    ready,
    mock: { flags: {} },
  })
  await openMenu(page)
  await expect(menuItem(page, 'Export PDF (pick a project)')).toBeDisabled()
  await expect(menuItem(page, 'Compare runs (pick a project)')).toBeDisabled()
  await page.keyboard.press('Escape')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('TRND-02: suite Auth → Compare runs opens the latest two Auth runs', async ({ page }) => {
  const world = trendsReportWorld()
  const { api, errors } = await openRollout(page, '/trends', {
    handlers: [...world.handlers, ...TRENDS_ON, ...RUN_COMPARE_PAGE],
    ready,
  })
  await page.getByRole('combobox', { name: 'Test suite' }).selectOption('Auth')
  await openMenu(page)
  await expect(menuItem(page, 'Compare runs')).toHaveAttribute('href', '/runs/compare?mode=latest&suite=Auth')
  await menuItem(page, 'Compare runs').click()
  await expect(page).toHaveURL(/\/runs\/compare\?mode=latest&suite=Auth$/)
  // The latest-suite form drew its comparison: the server's latest two Auth runs.
  await expect(page.locator('[data-runs-in-suite]')).toBeVisible()
  await expect.poll(() => requestsTo(api, '/api/v1/runs/compare/latest').length).toBeGreaterThan(0)
  expect(requestsTo(api, '/api/v1/runs/compare/latest').every((l) => l.includes('suite_name=Auth'))).toBe(true)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
