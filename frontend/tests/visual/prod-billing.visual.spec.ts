/**
 * W04 AIC-06 baseline: the `/settings/billing` table with the overage columns — Payments over its $20
 * allowance (Covered $20.00 · Overage $10.00 · Charge $15.00 · proj. $47.14 by month end), Checkout with
 * no quota (UNLIMITED, — in the new cells, sub-cent spend) and Search under its allowance. Clock pinned at
 * 2026-09-18 12:00 UTC. Hermetic fixtures: `fixtures-agents-billing-flags.ts`. PNGs are rendered by CI only.
 * W32 (W29e F-9): `billing-summary` — the workspace tiles above the table, four compact MetricCards two by two.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { ADMIN, BILLING_PROJECT, billingHandlers } from './production/fixtures-agents-billing-flags'

test.use(PINNED)

for (const theme of THEMES) {
  test(`billing table with overage — ${theme}`, async ({ page }) => {
    const { handlers, state } = billingHandlers()
    // Payments as saved with Included $20 and an overage rate of 1.5.
    const payments = state.quotas[BILLING_PROJECT.payments]
    if (payments) state.quotas[BILLING_PROJECT.payments] = { ...payments, included_usd: 20, overage_rate_usd: 1.5 }
    const { api, errors } = await openRollout(page, '/settings/billing', {
      theme,
      handlers,
      user: ADMIN,
      ready: (p) => p.locator('[data-billing-table]'),
    })
    const table = page.locator('[data-billing-table]')
    await expect(table.locator(`tr[data-billing-project="${BILLING_PROJECT.payments}"] [data-billing-charge]`)).toContainText('$15.00')
    await networkQuiet(page, api)
    await visualRegion(page, 'billing-table', theme, table)
    assertHermetic(api, errors)
  })

  // W32 (W29e F-9): the four workspace tiles as compact MetricCards in a 2 × 2 grid, same world as the table —
  // spend, calls, no project over soft-warn (the count's plain ink), Payments' projected overage charge.
  test(`billing summary tiles — ${theme}`, async ({ page }) => {
    const { handlers, state } = billingHandlers()
    const payments = state.quotas[BILLING_PROJECT.payments]
    if (payments) state.quotas[BILLING_PROJECT.payments] = { ...payments, included_usd: 20, overage_rate_usd: 1.5 }
    const { api, errors } = await openRollout(page, '/settings/billing', {
      theme,
      handlers,
      user: ADMIN,
      ready: (p) => p.locator('[data-billing-summary]'),
    })
    const summary = page.locator('[data-billing-summary]')
    await expect(summary.locator('[data-metric-card="compact"]')).toHaveCount(4)
    await expect(summary.getByTestId('billing-projected-charge')).toContainText(/Projected overage charge\$\d/)
    await networkQuiet(page, api)
    await visualRegion(page, 'billing-summary', theme, summary)
    assertHermetic(api, errors)
  })
}
