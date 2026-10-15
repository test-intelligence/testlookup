/**
 * W03 AIH-04: baseline of Run Compare's "AI analysis" section (`/runs/compare?mode=manual&left=<#58>&right=<#60>`,
 * `fixtures-ai-history.ts` `RUN_COMPARE_PAGE`):
 *
 *   compare-ai-section   both runs' AI analyses side by side (GO at 35 by rules, deterministic; NO_GO at 72 by
 *                        LLM llama3.1), their categories and top suggested root causes (a hostile label, as text),
 *                        then the cross-run diff (engine note first, "Verdict GO -> NO_GO", new / resolved /
 *                        persisting causes, new failing / resolved / changed tests) and the narrative label.
 *
 * New with W03: its baseline comes from CI. The same answers drive `tests/ci-e2e/run-compare-ai.spec.ts`.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, USER } from './production/fixtures'
import { COMPARE_PAGE_PATH, RUN_COMPARE_PAGE } from './production/fixtures-ai-history'

test.use(PINNED)

for (const theme of THEMES) {
  test(`run compare AI section — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, COMPARE_PAGE_PATH, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RUN_COMPARE_PAGE,
      ready: (p) => p.locator('[data-ai-compare-section] [data-ai-compare-side="current"] [data-ai-compare-verdict]'),
    })
    const section = page.locator('[data-ai-compare-section]')
    await expect(section.locator('[data-ai-diff-section="verdict"]')).toContainText('Verdict GO -> NO_GO')
    await expect(section.locator('[data-ai-compare-narrative-label]')).toHaveText('deterministic summary')
    await visualRegion(page, 'compare-ai-section', theme, section)
    assertHermetic(api, errors)
  })
}
