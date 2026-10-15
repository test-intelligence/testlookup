/**
 * W03 AIH-05: baseline of the test case History tab's "AI analysis across
 * runs" strip (`fixtures-ai-test-history.ts`: three analysed runs of ten, the
 * AI changing its mind on #60, the root cause reworded on #57), with #60's
 * versions open (v1 rules, v2 LLM, v3 human correction (anna)):
 *
 *   test-ai-strip   the chips oldest -> newest coloured by category, the change
 *                   markers, and the open versions popover.
 *
 * New with W03: its baseline comes from CI. The same answers drive the hermetic
 * spec `tests/ci-e2e/test-case-ai-history.spec.ts`.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { TEST_CASE_PATH } from './production/fixtures-pages'
import { WITH_AI_TEST_HISTORY } from './production/fixtures-ai-test-history'

test.use({ ...PINNED })

for (const theme of THEMES) {
  test(`test case AI history strip — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, TEST_CASE_PATH, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: WITH_AI_TEST_HISTORY,
      ready: (p) => p.locator('[data-test-ai-strip] [data-test-ai-chip]').first(),
    })

    const strip = page.locator('[data-test-ai-strip]')
    await expect(strip.locator('[data-test-ai-chip]')).toHaveCount(3)
    await expect(strip.locator('[data-test-ai-change]')).toHaveCount(2)
    await strip.locator(`[data-test-ai-chip][data-run-id="${RUN_ID}"]`).click()
    await expect(strip.locator('[data-test-ai-popover] [data-test-ai-version]')).toHaveCount(3)
    await visualRegion(page, 'test-ai-strip', theme, strip)
    assertHermetic(api, errors)
  })
}
