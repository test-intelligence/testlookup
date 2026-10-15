/**
 * W03 AIH-03/04 on `/runs/compare?mode=manual&left=<#58>&right=<#60>`, hermetic: the "AI analysis" section shows
 * both runs' AI analyses side by side (verdict, engine, categories, top suggested root causes) and what changed
 * between them, the narrative labelled "deterministic summary"; the AI Comparison Report lists its v2 "AI analysis
 * changes". A hostile root-cause label arrives as text. The section asks the TARGET run for a compare against the
 * LEFT run as the baseline.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-ai-history.ts`
 * (`RUN_COMPARE_PAGE`).
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { HOSTILE_NAME } from '../visual/production/fixtures'
import { TEST_CASE_NAME } from '../visual/production/fixtures-pages'
import { AI_COMPARE_PATH, COMPARE_PAGE_PATH, PREVIOUS_RUN_ID, RUN_COMPARE_PAGE } from '../visual/production/fixtures-ai-history'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('Run Compare shows both runs\' AI analyses side by side and what changed between them', async ({ page }) => {
  const { api, errors } = await openRollout(page, COMPARE_PAGE_PATH, {
    handlers: RUN_COMPARE_PAGE,
    ready: (p) => p.locator('[data-ai-compare-section] [data-ai-compare-side="current"] [data-ai-compare-verdict]'),
  })
  const section = page.locator('[data-ai-compare-section]')
  const baseline = section.locator('[data-ai-compare-side="baseline"]')
  const current = section.locator('[data-ai-compare-side="current"]')
  await expect(baseline).toContainText('Build #238')
  await expect(baseline.locator('[data-ai-compare-verdict]')).toHaveText('GO')
  await expect(baseline).toContainText('rules')
  await expect(baseline).toContainText('deterministic')
  await expect(baseline).toContainText(HOSTILE_NAME)
  await expect(current).toContainText('Build #240')
  await expect(current.locator('[data-ai-compare-verdict]')).toHaveText('NO_GO')
  await expect(current).toContainText('LLM llama3.1')
  await expect(current).toContainText('Session token expiry before MFA')

  const diff = section.locator('[data-ai-diff]')
  await expect(diff).toHaveAttribute('data-ai-diff-mode', 'cross_run')
  await expect(diff.locator('[data-ai-diff-section]').first()).toHaveAttribute('data-ai-diff-section', 'provenance')
  await expect(diff.locator('[data-ai-diff-section="verdict"]')).toContainText('Verdict GO -> NO_GO')
  await expect(diff.locator('[data-ai-diff-group="added"]').first()).toContainText('Session token expiry before MFA')
  await expect(diff.locator('[data-ai-diff-section="tests"]')).toContainText(`${TEST_CASE_NAME}`)
  await expect(section.locator('[data-ai-compare-narrative-label]')).toHaveText('deterministic summary')

  // The results report lists the v2 AI analysis changes.
  await expect(page.locator('[data-ai-report-changes]')).toContainText('Verdict GO -> NO_GO (risk 35 -> 72)')

  // Text, never markup.
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined()
  await expect(section.locator('img')).toHaveCount(0)

  await networkQuiet(page, api)
  const asked = requestsTo(api, AI_COMPARE_PATH)
  expect(asked.length).toBeGreaterThan(0)
  expect(asked.every((line) => line.includes(`baseline_run_id=${PREVIOUS_RUN_ID}`))).toBe(true)
  assertHermetic(api, errors)
})
