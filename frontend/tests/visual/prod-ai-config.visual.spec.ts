/**
 * W04 G-D: baselines of Settings › AI (`/settings/ai`, an ADMIN, `fixtures-ai-config.ts`):
 *
 *   ai-config-pipeline-settings  the Pipeline Settings card: Confidence Threshold and Timeout (min 5 s) with
 *                                their source hints ("Server default · Applies to new pipelines." / "... Applies
 *                                to every LLM call ..."), and the Deep Investigation ("Set here") and Fine-Tuning
 *                                ("Server default") toggles with theirs (AIC-01, AIC-02);
 *   ai-config-ml-card            the ML classifier card under the mode radios: "Samples 642 / 200 · human labels
 *                                57 · model <version>", the model line ("accuracy 0.84 · trained just now"),
 *                                Train now, and the nightly schedule copy (AIC-05).
 *
 * New with W04: their baselines come from CI (never generated locally). The same answers drive the hermetic
 * specs `tests/ci-e2e/settings-ai-toggles.spec.ts` and `settings-ml-train.spec.ts`. Harness and fail-closed
 * rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID } from './production/fixtures'
import { ADMIN, aiConfigHandlers, MODEL_VERSION } from './production/fixtures-ai-config'

test.use(PINNED)

for (const theme of THEMES) {
  test(`AI configuration regions — ${theme}`, async ({ page }) => {
    const fixture = aiConfigHandlers({
      samples: 642,
      trained: true,
      config: { deep_investigation_enabled_source: 'ai_config' },
    })
    const { api, errors } = await openProductionPage(page, '/settings/ai', {
      theme,
      now: NOW,
      me: ADMIN,
      user: ADMIN,
      projectId: PROJECT_ID,
      handlers: fixture.handlers,
      ready: (p) => p.locator('[data-ml-training-card] [data-ml-model-line]'),
    })

    const pipeline = page
      .locator('[data-settings-form-grid] > .card')
      .filter({ has: page.getByRole('heading', { name: 'Pipeline Settings', exact: true }) })
    await expect(pipeline.locator('[data-config-source-key="deep_investigation_enabled"]')).toHaveText(
      'Set here · Applies within a minute.',
    )
    await expect(pipeline.getByTestId('timeout-hint')).toContainText('Applies to every LLM call')
    await visualRegion(page, 'ai-config-pipeline-settings', theme, pipeline)

    const card = page.locator('[data-ml-training-card]')
    await expect(card.locator('[data-ml-model-line]')).toHaveText(`Model ${MODEL_VERSION} · accuracy 0.84 · trained just now`)
    await expect(card.locator('[data-ml-train-now]')).toHaveText('Train now')
    await visualRegion(page, 'ai-config-ml-card', theme, card)
    assertHermetic(api, errors)
  })
}
