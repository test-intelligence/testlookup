/**
 * W04 AGT-04 baseline: the `/agents` pipeline list with its row actions — Retry beside a failed, a
 * degraded and a review-rejected row, Stop beside a running one, nothing beside a passed one (the actions
 * sit next to the row's button, never inside it). Hermetic fixtures: `fixtures-agents-billing-flags.ts`.
 * PNGs are rendered by CI only (`visual-baselines.yml`, Linux).
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { agentsActionsHandlers, PIPELINE } from './production/fixtures-agents-billing-flags'

test.use(PINNED)

for (const theme of THEMES) {
  test(`agents pipeline row actions — ${theme}`, async ({ page }) => {
    const { handlers } = agentsActionsHandlers()
    const { api, errors } = await openRollout(page, '/agents', {
      theme,
      handlers,
      ready: (p) => p.getByTestId('pipeline-list'),
    })
    const list = page.getByTestId('pipeline-list')
    await expect(list.getByTestId('pipeline-retry')).toHaveCount(3)
    await expect(list.getByTestId('pipeline-stop')).toHaveCount(1)
    await expect(list.locator(`[data-pipeline-id="${PIPELINE.passed}"] [data-testid="pipeline-row-actions"]`)).toHaveCount(0)
    await networkQuiet(page, api)
    await visualRegion(page, 'agents-pipeline-row-actions', theme, list)
    assertHermetic(api, errors)
  })
}
