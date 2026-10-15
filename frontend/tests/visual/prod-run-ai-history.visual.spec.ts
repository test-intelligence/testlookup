/**
 * W03 AIH-04: baselines of the top of the run's Analysis tab (`/runs/<run>?tab=analysis`) for a run analysed twice
 * (`fixtures-ai-history.ts`: v1 rules, v2 LLM llama3.1) with a previous run on its branch (#58):
 *
 *   run-ai-previous-card   "vs previous run", expanded: the baseline chip, the engine note, "Verdict GO -> NO_GO",
 *                          new / resolved (a hostile label, as text) / persisting root causes, the changed tests
 *                          and the narrative labelled "deterministic summary";
 *   run-ai-version-picker  the AI history control: "v2 · LLM llama3.1 · ..." (current), the version and compare
 *                          selects;
 *   run-ai-diff            the diff drawer of v1 -> v2: the engine change first, the verdict, categories, the
 *                          resized clusters, the tests whose conclusion changed, the actions.
 *
 * New with W03: their baselines come from CI. The same answers drive the hermetic spec
 * `tests/ci-e2e/run-ai-history.spec.ts`. Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { RUN_PAGE } from './production/fixtures-run'
import { AI_HISTORY } from './production/fixtures-ai-history'

test.use(PINNED)

for (const theme of THEMES) {
  test(`run AI history regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}?tab=analysis`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: [...AI_HISTORY, ...RUN_PAGE],
      ready: (p) => p.locator('[data-previous-run-card][data-previous-run-state="expanded"]'),
    })

    const card = page.locator('[data-previous-run-card]')
    await expect(card.locator('[data-previous-verdict]')).toContainText('Verdict GO -> NO_GO')
    await expect(card.locator('[data-previous-narrative-label]')).toHaveText('deterministic summary')
    await visualRegion(page, 'run-ai-previous-card', theme, card)

    const picker = page.locator('[data-ai-version-picker]')
    await expect(picker.locator('[data-ai-version-meta]')).toContainText('v2 · LLM llama3.1')
    await visualRegion(page, 'run-ai-version-picker', theme, picker)

    // v1 (its stored snapshot) compared with v2: the drawer sits right under the control.
    await page.getByRole('combobox', { name: 'AI analysis version' }).selectOption('1')
    await expect(page.locator('[data-ai-snapshot-version="1"]')).toBeVisible()
    await page.getByRole('combobox', { name: 'Compare with' }).selectOption('2')
    const drawer = page.locator('[data-ai-diff-drawer]')
    await expect(drawer.locator('[data-ai-diff-section="verdict"]')).toContainText('Verdict CONDITIONAL_GO -> NO_GO')
    await visualRegion(page, 'run-ai-diff', theme, drawer)
    assertHermetic(api, errors)
  })
}
