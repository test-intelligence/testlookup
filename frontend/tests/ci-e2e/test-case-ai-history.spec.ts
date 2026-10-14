/**
 * W03 AIH-05 on `/runs/:runId/tests/:testId` (History tab), hermetic (the API
 * is mocked to the W03 contract §7.3 / R5, `docs/.../W03-contract.md`):
 *
 *  - "AI analysis across runs": one chip per analysed run, oldest -> newest,
 *    between the pass/fail dots and the duration section;
 *  - the marker "AI changed its mind on Run #60: INFRASTRUCTURE → PRODUCT_BUG"
 *    and the rewording on #57;
 *  - the #60 chip opens its versions: "v1 rules · v2 LLM · v3 human correction
 *    (anna)", read once, only when opened; the #57 chip (no version stored)
 *    says so without asking; the #53 chip lists its backfilled version.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock); answers:
 * `tests/visual/production/fixtures-ai-test-history.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { TEST_CASE_API, TEST_CASE_ID, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import {
  AI_VERSIONS_PATH,
  AI_VERSIONS_PATH_53,
  AI_VERSIONS_PATH_57,
  historyRunId,
  historyTestId,
  WITH_AI_TEST_HISTORY,
} from '../visual/production/fixtures-ai-test-history'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const strip = (page: Page) => page.locator('[data-test-ai-strip]')
const chip = (page: Page, runId: string) => strip(page).locator(`[data-test-ai-chip][data-run-id="${runId}"]`)

test('the History tab shows the AI category per run and where the AI changed its mind', async ({ page }) => {
  const { api, errors } = await openRollout(page, TEST_CASE_PATH, {
    handlers: WITH_AI_TEST_HISTORY,
    ready: (p) => p.locator('[data-test-ai-strip] [data-test-ai-chip]').first(),
  })

  const chips = strip(page).locator('[data-test-ai-chip]')
  await expect(chips).toHaveCount(3)
  await expect(chips).toHaveText(['INFRASTRUCTURE 45%', 'INFRASTRUCTURE 40%', 'PRODUCT_BUG 85%'])
  expect(await chips.evaluateAll((els) => els.map((el) => el.getAttribute('data-run-id')))).toEqual([
    historyRunId(7),
    historyRunId(3),
    RUN_ID,
  ])
  await expect(chip(page, RUN_ID)).toHaveAttribute('data-changed', 'category')
  await expect(chip(page, RUN_ID)).toHaveAttribute('data-current-run', '')
  await expect(chip(page, RUN_ID)).toHaveAttribute('title', 'Run #60 - PRODUCT_BUG 85% - LLM llama3')

  const markers = strip(page).locator('[data-test-ai-change]')
  await expect(markers).toHaveText([
    'AI changed its mind on Run #60: INFRASTRUCTURE → PRODUCT_BUG (confidence 40% → 85%)',
    'AI reworded its root cause on Run #57',
  ])

  // Between the pass/fail dots and the duration section.
  const dots = await page.getByText('Recent runs (oldest → newest)').boundingBox()
  const ai = await strip(page).boundingBox()
  const duration = await page.locator('[data-duration-history]').boundingBox()
  if (!dots || !ai || !duration) throw new Error('the dots, the AI strip and the duration section must all be laid out')
  expect(dots.y).toBeLessThan(ai.y)
  expect(ai.y + ai.height).toBeLessThanOrEqual(duration.y)

  // The strip rides the one history read: no version is asked for before a click.
  await networkQuiet(page, api)
  expect(requestsTo(api, `${TEST_CASE_API}/history`)).toHaveLength(1)
  expect(requestsTo(api, /\/ai-history$/)).toEqual([])
  assertHermetic(api, errors)
})

test('a chip opens that run\'s stored versions: v1 rules · v2 LLM · v3 human correction (anna)', async ({ page }) => {
  const { api, errors } = await openRollout(page, TEST_CASE_PATH, {
    handlers: WITH_AI_TEST_HISTORY,
    ready: (p) => p.locator('[data-test-ai-strip] [data-test-ai-chip]').first(),
  })

  await chip(page, RUN_ID).click()
  const popover = page.locator('[data-test-ai-popover]')
  await expect(popover).toBeVisible()
  await expect(popover.locator('[data-test-ai-version-summary]')).toHaveText('v1 rules · v2 LLM · v3 human correction (anna)')
  await expect(popover.locator('[data-test-ai-version]')).toHaveCount(3)
  await expect(popover.locator('[data-test-ai-version][data-version="1"]')).toContainText('INFRASTRUCTURE')
  await expect(popover.locator('[data-test-ai-version][data-version="3"]')).toContainText('latest')
  await expect(popover.locator('[data-test-ai-open-run]')).toHaveAttribute('href', `/runs/${RUN_ID}/tests/${TEST_CASE_ID}`)
  await networkQuiet(page, api)
  expect(requestsTo(api, AI_VERSIONS_PATH)).toHaveLength(1)

  // #57: no version stored yet -- said without asking.
  await chip(page, historyRunId(3)).click()
  await expect(popover).toHaveAttribute('data-run-id', historyRunId(3))
  await expect(popover.locator('[data-test-ai-versions-state="empty"]')).toHaveText('No versions stored yet')

  // #53: its own test result's versions (a backfilled rules version).
  await chip(page, historyRunId(7)).click()
  await expect(popover.locator('[data-test-ai-version-summary]')).toHaveText('v1 rules (backfilled)')
  await expect(popover.locator('[data-test-ai-open-run]')).toHaveAttribute(
    'href',
    `/runs/${historyRunId(7)}/tests/${historyTestId(7)}`,
  )

  // Closing.
  await popover.getByRole('button', { name: 'Close AI versions' }).click()
  await expect(popover).toHaveCount(0)

  await networkQuiet(page, api)
  expect(requestsTo(api, AI_VERSIONS_PATH_57)).toEqual([])
  expect(requestsTo(api, AI_VERSIONS_PATH_53)).toHaveLength(1)
  expect(requestsTo(api, AI_VERSIONS_PATH)).toHaveLength(1)
  assertHermetic(api, errors)
})
