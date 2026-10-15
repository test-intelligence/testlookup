/**
 * W04 AIC-07 baseline: the "Agent stages (deep pipeline)" group of `/settings/feature-flags` as seeded on
 * a fresh install by migrations 0208/0209 — the four read-only stages and `aiq_gap_refinement` ON,
 * `defect_commander` OFF with its MUTATING pill, `async_decision_report_supersession` OFF. The region is
 * the group's own `<tbody>`. Hermetic fixtures: `fixtures-agents-billing-flags.ts`. PNGs by CI only.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { ADMIN, flagsHandlers } from './production/fixtures-agents-billing-flags'

test.use(PINNED)

for (const theme of THEMES) {
  test(`feature flags agent stages group — ${theme}`, async ({ page }) => {
    const { handlers } = flagsHandlers()
    const { api, errors } = await openRollout(page, '/settings/feature-flags', {
      theme,
      handlers,
      user: ADMIN,
      ready: (p) => p.locator('[data-flag-group-body="agent-stages"]'),
    })
    const group = page.locator('[data-flag-group-body="agent-stages"]')
    await expect(group.locator('tr[data-flag-key]')).toHaveCount(7)
    await expect(group.locator('[data-flag-mutating]')).toHaveText('MUTATING')
    await networkQuiet(page, api)
    await visualRegion(page, 'feature-flags-agent-stages', theme, group)
    assertHermetic(api, errors)
  })
}
