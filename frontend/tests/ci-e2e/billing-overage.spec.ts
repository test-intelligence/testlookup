/**
 * W04 AIC-06, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.5, §4.6), on
 * `/settings/billing` (clock pinned at 2026-09-18 12:00 UTC: 17.5 of the period's 30 days elapsed):
 *
 *  - an ADMIN edits Payments (spent $30, a cap-only quota): Included 20, Overage rate 1.5 -> Save; the PUT
 *    body carries both, and the row then reads Spend $30.00 · Covered $20.00 · Overage $10.00 · Charge
 *    $15.00 with "proj. $47.14 by month end" (30 / (17.5/30) = 51.43 projected, 31.43 over, × 1.5);
 *  - a project with no quota stays UNLIMITED with — in the three new cells, its sub-cent spend readable;
 *  - the "Projected overage charge" tile sums the projections; the editor says what the two fields do;
 *  - a QA lead (the lowest role the route admits) reads the table and the editor read-only; no ADMIN write.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agents-billing-flags.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { ADMIN, BILLING_PROJECT, billingHandlers } from '../visual/production/fixtures-agents-billing-flags'
import { USER } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const table = (p: Page) => p.locator('[data-billing-table]')
const row = (p: Page, id: string) => p.locator(`tr[data-billing-project="${id}"]`)
const cell = (p: Page, id: string, name: string) => row(p, id).locator(`[data-billing-${name}]`)

test('Included 20 and Overage rate 1.5 -> Covered $20.00, Overage $10.00, Charge $15.00 and the month-end projection', async ({ page }) => {
  const { handlers, state } = billingHandlers()
  const { api, errors } = await openRollout(page, '/settings/billing', { handlers, user: ADMIN, ready: table })
  const payments = BILLING_PROJECT.payments
  // A cap with no included allowance (the form default): no charge is printed.
  for (const name of ['covered', 'overage', 'charge']) await expect(cell(page, payments, name)).toHaveText('—')

  await row(page, payments).getByRole('button', { name: 'Edit' }).click()
  const dialog = opened(page, 'Edit LLM cost budget')
  await expect(dialog.locator('[data-billing-hint="included"]')).toHaveText('Spend up to this amount each month is covered; spend above it is overage.')
  await expect(dialog.locator('[data-billing-hint="rate"]')).toHaveText('Charge = overage × rate. Shown in the table; it does not limit spend (the hard cap does).')
  await dialog.getByLabel('Included per month ($)').fill('20')
  await dialog.getByLabel('Overage rate ($/$)').fill('1.5')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toHaveCount(0)

  await expect(cell(page, payments, 'spend')).toHaveText('$30.00')
  await expect(cell(page, payments, 'covered')).toHaveText('$20.00')
  await expect(cell(page, payments, 'overage')).toHaveText('$10.00')
  await expect(cell(page, payments, 'charge')).toContainText('$15.00')
  const projection = cell(page, payments, 'projection')
  await expect(projection).toHaveText('proj. $47.14 by month end')
  await expect(projection).toHaveAttribute('title', 'linear projection from 17.5 of 30 days of the period')
  // Payments $47.14 + Search $2.00 (spent $3.50 of a $5 allowance, projected $6.00).
  await expect(page.getByTestId('billing-projected-charge')).toContainText('$49.14')
  await networkQuiet(page, api)
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0].line).toBe(`PUT /api/v1/projects/${payments}/llm-quota`)
  expect(state.writes[0].body).toMatchObject({ included_usd: 20, overage_rate_usd: 1.5, hard_cap_usd: 100, enabled: true })
  assertHermetic(api, errors)
})

test('a project without a quota stays UNLIMITED, — in the new cells, its sub-cent spend readable', async ({ page }) => {
  const { handlers } = billingHandlers()
  const { api, errors } = await openRollout(page, '/settings/billing', { handlers, user: ADMIN, ready: table })
  const checkout = BILLING_PROJECT.checkout
  await expect(row(page, checkout)).toContainText('UNLIMITED')
  await expect(cell(page, checkout, 'spend')).toHaveText('$0.0012')
  for (const name of ['covered', 'overage', 'charge']) await expect(cell(page, checkout, name)).toHaveText('—')
  await expect(cell(page, checkout, 'projection')).toHaveCount(0)
  // Search is under its $5 allowance: covered is its spend, no overage yet, but a projection.
  const search = BILLING_PROJECT.search
  await expect(cell(page, search, 'covered')).toHaveText('$3.50')
  await expect(cell(page, search, 'overage')).toHaveText('$0.00')
  await expect(cell(page, search, 'projection')).toHaveText('proj. $2.00 by month end')
  await expect(table(page).getByRole('columnheader')).toHaveText(['Project', 'Spend', 'Covered', 'Overage', 'Charge', 'Cap', 'Utilization', 'Status', 'Cap hits', ''])
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

// The page is a management route (QA lead and above; a viewer is sent home by the route guard). The
// lowest role that reaches it, a QA lead, reads the overage figures and the quota (read-only): the
// ADMIN-only write is never made.
test('a QA lead reads the table and the editor (read-only) with no request a QA lead is refused', async ({ page }) => {
  const refused: string[] = []
  const forbidden: ApiHandlers = [
    [/^\/api\/v1\/projects\/[^/]+\/llm-quota$/, ({ path }) => {
      refused.push(`PUT ${path}`)
      return respond(403, { detail: 'Requires at least ADMIN role' })
    }, 'PUT'],
  ]
  const { handlers } = billingHandlers()
  const { api, errors } = await openRollout(page, '/settings/billing', { handlers: [...forbidden, ...handlers], user: USER, ready: table })
  await expect(cell(page, BILLING_PROJECT.search, 'projection')).toHaveText('proj. $2.00 by month end')
  await row(page, BILLING_PROJECT.search).getByRole('button', { name: 'View' }).click()
  const dialog = opened(page, 'Edit LLM cost budget')
  await expect(dialog.getByLabel('Included per month ($)')).toBeDisabled()
  await expect(dialog.getByRole('button', { name: 'Save' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(refused).toEqual([])
  await expect(page.getByText(/Requires at least/)).toHaveCount(0)
  assertHermetic(api, errors)
})
