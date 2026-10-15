/**
 * W03 AIH-04 on `/runs/:runId?tab=analysis`, hermetic (the API is mocked to the W03 contract,
 * `docs/deferred-scope-2026-10-13/work/W03-contract.md` §6):
 *
 *  - the AI history control lists v1 (rules) and v2 (LLM llama3.1), v2 shown as current;
 *  - picking v1 shows its stored snapshot INSTEAD of the live sections, and "Back to current" returns;
 *  - "Compare with v2" opens the diff: the engine change first, then "Verdict CONDITIONAL_GO -> NO_GO";
 *  - "vs previous run": #58 on the same branch, the verdict change, new / resolved / persisting root causes (a
 *    hostile label arrives as text), the narrative labelled "deterministic summary"; a changed test opens it;
 *  - an old `?report_version=1` link lands on the version that published report 1;
 *  - a VIEWER gets all of it with no request a viewer may not make (no 403 toast).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-ai-history.ts` (`AI_HISTORY`,
 * before `RUN_PAGE` of `fixtures-run.ts`: first match wins) and `fixtures-pages.ts` (`TEST_CASE`, the opened test).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { HOSTILE_NAME, RUN_ID, USER } from '../visual/production/fixtures'
import { TEST_CASE, TEST_CASE_ID, TEST_CASE_NAME, TEST_CASE_PATH } from '../visual/production/fixtures-pages'
import { RUN_PAGE } from '../visual/production/fixtures-run'
import {
  AI_COMPARE_PATH,
  AI_DIFF_PATH,
  AI_HISTORY,
  AI_HISTORY_PATH,
  PREVIOUS_PATH,
  V1_ID,
  V2_ID,
} from '../visual/production/fixtures-ai-history'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const HANDLERS: ApiHandlers = [...AI_HISTORY, ...TEST_CASE, ...RUN_PAGE]
const ANALYSIS = `/runs/${RUN_ID}?tab=analysis`

const picker = (page: Page) => page.locator('[data-ai-version-picker]')
const versionSelect = (page: Page) => page.getByRole('combobox', { name: 'AI analysis version' })
const compareSelect = (page: Page) => page.getByRole('combobox', { name: 'Compare with' })
const card = (page: Page) => page.locator('[data-previous-run-card]')
const params = (page: Page) => new URL(page.url()).searchParams

test('the history lists v1 (rules) and v2 (LLM), v2 shown as the current analysis', async ({ page }) => {
  const { api, errors } = await openRollout(page, ANALYSIS, { handlers: HANDLERS, ready: (p) => p.locator('[data-ai-version-meta]') })
  await expect(picker(page).locator('[data-ai-version-meta]')).toHaveText(/^v2 · LLM llama3\.1 · about 1 hour ago \(current\)$/)
  await expect(versionSelect(page).locator('option')).toHaveText([
    'Current',
    /^v2 · LLM llama3\.1 · /,
    /^v1 · rules · /,
  ])
  // The live analysis is under it.
  await expect(page.locator('[data-primary]')).toHaveAttribute('aria-label', 'What failed')
  await networkQuiet(page, api)
  expect(requestsTo(api, AI_HISTORY_PATH).length).toBeGreaterThan(0)
  assertHermetic(api, errors)
})

test('pick v1: its stored snapshot replaces the live sections; "Back to current" returns', async ({ page }) => {
  const { api, errors } = await openRollout(page, ANALYSIS, { handlers: HANDLERS, ready: (p) => p.locator('[data-ai-version-meta]') })
  await versionSelect(page).selectOption('1')
  const snapshot = page.locator('[data-ai-snapshot][data-ai-snapshot-version="1"]')
  await expect(snapshot).toBeVisible()
  await expect(page).toHaveURL(/[?&]ai_version=1/)
  expect(params(page).get('report_version')).toBe('1')
  expect(params(page).get('tab')).toBe('analysis')
  await expect(snapshot.locator('[data-ai-snapshot-banner]')).toContainText('Viewing v1 (rules')
  await expect(snapshot.locator('[data-ai-snapshot-section="verdict"]')).toContainText('CONDITIONAL_GO')
  await expect(snapshot.locator('[data-ai-snapshot-section="summary"]')).toContainText('session store timeouts')
  await expect(snapshot.getByRole('link', { name: 'Open decision report v1' })).toHaveAttribute(
    'href', `/runs/${RUN_ID}?tab=evidence&report_version=1&ai_version=1`,
  )
  // Not the live analysis: no verdict banner, no What failed, no "vs previous run".
  await expect(page.locator('[data-status-banner]')).toHaveCount(0)
  await expect(page.locator('[data-primary]')).toHaveCount(0)
  await expect(card(page)).toHaveCount(0)

  await snapshot.getByRole('button', { name: /Back to current/ }).click()
  await expect(page.locator('[data-ai-snapshot]')).toHaveCount(0)
  await expect(page.locator('[data-primary]')).toHaveAttribute('aria-label', 'What failed')
  expect(params(page).get('ai_version')).toBeNull()
  expect(params(page).get('report_version')).toBeNull()
  await networkQuiet(page, api)
  expect(requestsTo(api, `${AI_HISTORY_PATH}/${V1_ID}`)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('compare v1 with v2: the engine change first, then the verdict change and the test that moved', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${ANALYSIS}&ai_version=1`, {
    handlers: HANDLERS,
    ready: (p) => p.locator('[data-ai-snapshot]'),
  })
  await compareSelect(page).selectOption('2')
  await expect(page).toHaveURL(/[?&]ai_compare=2/)
  const diff = page.locator('[data-ai-diff-drawer] [data-ai-diff]')
  await expect(diff).toHaveAttribute('data-ai-diff-mode', 'versions')
  const sections = diff.locator('[data-ai-diff-section]')
  await expect(sections.first()).toHaveAttribute('data-ai-diff-section', 'provenance')
  await expect(sections.first()).toContainText('The analysis engine changed between these versions (rules -> llm).')
  await expect(diff.locator('[data-ai-diff-section="verdict"]')).toContainText('Verdict CONDITIONAL_GO -> NO_GO')
  await expect(diff.locator('[data-ai-diff-category="PRODUCT_BUG"]')).toHaveText('PRODUCT_BUG +3')
  await expect(diff.locator(`[data-ai-diff-row="test"][data-test-fingerprint]`).first()).toContainText(
    `${TEST_CASE_NAME}`,
  )
  await expect(diff).toContainText('INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)')
  await networkQuiet(page, api)
  // Oldest first, whatever order the two were picked in.
  expect(requestsTo(api, AI_DIFF_PATH).some((line) => line.includes(`from=${V1_ID}`) && line.includes(`to=${V2_ID}`))).toBe(true)

  await page.getByRole('button', { name: 'Close the comparison' }).click()
  await expect(page.locator('[data-ai-diff-drawer]')).toHaveCount(0)
  expect(params(page).get('ai_compare')).toBeNull()
  assertHermetic(api, errors)
})

test('"vs previous run": #58 on main, verdict GO -> NO_GO, new / resolved / persisting causes; a changed test opens', async ({ page }) => {
  const { api, errors } = await openRollout(page, ANALYSIS, {
    handlers: HANDLERS,
    ready: (p) => p.locator('[data-previous-run-card][data-previous-run-state="expanded"]'),
  })
  const previous = card(page)
  await expect(previous.locator('[data-previous-baseline]')).toHaveText(/^Compared with #58 \(Auth, main, \d+ (hours|days?) earlier\)$/)
  await expect(previous.locator('[data-previous-provenance]')).toContainText('Baseline analysed by rules; current by llm.')
  await expect(previous.locator('[data-previous-verdict]')).toContainText('Verdict GO -> NO_GO')
  await expect(previous.locator('[data-previous-new-causes]')).toContainText('Session token expiry before MFA')
  await expect(previous.locator('[data-previous-resolved]')).toContainText(HOSTILE_NAME)
  await expect(previous.locator('[data-previous-persisting]')).toContainText('Session store timeouts')
  await expect(previous.locator('[data-previous-narrative-label]')).toHaveText('deterministic summary')
  // A hostile label is text: no element was made of it, nothing ran.
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined()
  await expect(previous.locator('img')).toHaveCount(0)
  // The ⋯-menu target and the card's link name the same pair.
  await expect(previous.getByRole('link', { name: 'Open Run Compare' })).toHaveAttribute('href', /left=44444444-4444-4444-8444-100000000002&right=33333333/)

  const changed = previous.locator(`[data-previous-tests-changed] [data-test-fingerprint]`).filter({ hasText: TEST_CASE_NAME })
  await expect(changed).toContainText('INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)')
  await changed.click()
  await expect(page).toHaveURL(new RegExp(`${TEST_CASE_PATH}$`))
  await expect(page.getByRole('heading', { level: 1, name: TEST_CASE_NAME })).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, PREVIOUS_PATH).length).toBeGreaterThan(0)
  expect(requestsTo(api, AI_COMPARE_PATH).length).toBeGreaterThan(0)
  expect(page.url()).toContain(TEST_CASE_ID)
  assertHermetic(api, errors)
})

test('an old ?report_version=1 link lands on the AI version that published report 1', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${ANALYSIS}&report_version=1`, {
    handlers: HANDLERS,
    ready: (p) => p.locator('[data-ai-snapshot]'),
  })
  await expect(page.locator('[data-ai-snapshot]')).toHaveAttribute('data-ai-snapshot-version', '1')
  await expect(versionSelect(page)).toHaveValue('1')
  assertHermetic(api, errors)
})

test('a VIEWER gets the history, the snapshot and the previous run with no request a viewer may not make', async ({ page }) => {
  const viewer = { ...USER, username: 'viewer', full_name: 'Viewer', role: 'VIEWER' }
  // What a viewer is refused (QA lead / QA engineer reads and writes): answered 403, and recorded.
  const refused: string[] = []
  const forbidden: ApiHandlers = [
    /^\/api\/v1\/settings\/ai$/,
    /^\/api\/v1\/analyze\//,
    /^\/api\/v1\/agents\/pipelines\/trigger$/,
  ].map((pattern) => [pattern, ({ path }) => {
    refused.push(path)
    return respond(403, { detail: 'Requires at least QA_LEAD role' })
  }] as const)
  const { api, errors } = await openRollout(page, ANALYSIS, {
    handlers: [...forbidden, ...HANDLERS],
    user: viewer,
    ready: (p) => p.locator('[data-previous-run-card][data-previous-run-state="expanded"]'),
  })
  await expect(picker(page).locator('[data-ai-version-meta]')).toContainText('v2 · LLM llama3.1')
  await versionSelect(page).selectOption('1')
  await expect(page.locator('[data-ai-snapshot-version="1"]')).toBeVisible()
  await networkQuiet(page, api)
  expect(refused, 'requests a viewer is refused').toEqual([])
  await expect(page.getByText(/Requires at least/)).toHaveCount(0)
  expect(requestsTo(api, AI_HISTORY_PATH).length).toBeGreaterThan(0)
  expect(requestsTo(api, PREVIOUS_PATH).length).toBeGreaterThan(0)
  assertHermetic(api, errors)
})
