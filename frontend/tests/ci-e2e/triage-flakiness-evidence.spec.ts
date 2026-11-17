/**
 * D-0008 (GPT-6 exploratory, E2E-0008, the reader's half), hermetic: on a failed test's page
 * (`/runs/<run>/tests/<test>`), an AI triage of a test with ONE recorded execution shows its flakiness evidence as
 * "too few executions" with the counts, and never a history pattern ("CONSISTENTLY FAILING", "FLAKY",
 * "HISTORICALLY STABLE"). GPT-6 read "Total executions analysed: 1" beside "CONSISTENTLY FAILING — likely a genuine
 * product bug", and the model repeated it.
 *
 * What a browser cannot prove: the tool's wording. The page renders the stored evidence verbatim, so the
 * authoritative check is the backend regression `backend/tests/regression/test_flakiness_tool_single_observation.py`
 * (the real tool, a pattern label only from 5 executions). The excerpt here is that tool's output for one failed
 * execution. Negative control (by hand): the pre-fix excerpt makes this spec fail.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `TEST_CASE` (`fixtures-pages.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import {
  TEST_CASE,
  TEST_CASE_ID,
  TEST_CASE_NAME,
  TEST_CASE_PATH,
  TEST_CASE_ROOT_CAUSE,
} from '../visual/production/fixtures-pages'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** `check_test_flakiness` for a test with one recorded execution, as the fixed tool writes it. */
const ONE_EXECUTION_EVIDENCE = [
  `=== Flakiness History: ${TEST_CASE_NAME} ===`,
  'Total executions analysed: 1',
  'Passed: 0 | Failed/Broken: 1 | Skipped: 0',
  'Failure rate: 100.0%',
  'ℹ️  TOO FEW EXECUTIONS — 1 recorded; too early to tell a consistent failure from a one-off or flakiness.',
].join('\n')

const analysis = (excerpt: string) => ({
  test_case_id: TEST_CASE_ID,
  analysis_id: 'analysis-d0008',
  root_cause_summary: TEST_CASE_ROOT_CAUSE,
  failure_category: 'PRODUCT_BUG',
  backend_error_found: false,
  pod_issue_found: false,
  is_flaky: false,
  confidence_score: 62,
  recommended_actions: ['Re-run the test to see whether the failure repeats.'],
  role_actions: { qa: '', developer: '', sre: '', release_manager: '' },
  evidence_references: [{ source: 'check_test_flakiness', reference_id: 'flaky-1', excerpt }],
  tools_used: ['check_test_flakiness'],
  confidence_why: { evidence_count: 1, data_sources: ['history'], is_llm_inference: true, investigation_depth: 'standard' },
  llm_provider: 'ollama',
  llm_model: 'llama3',
  requires_human_review: false,
})

const world = (excerpt: string): ApiHandlers => [[`/api/v1/analyze/${TEST_CASE_ID}`, () => analysis(excerpt)], ...TEST_CASE]

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_CASE_NAME })

test('one recorded execution: the flakiness evidence says too few executions and names no pattern', async ({ page }) => {
  const { api, errors } = await openRollout(page, TEST_CASE_PATH, { handlers: world(ONE_EXECUTION_EVIDENCE), ready })
  const evidence = page.getByTestId('ai-evidence').first()
  await expect(evidence).toContainText('check_test_flakiness')
  await expect(evidence).toContainText('Total executions analysed: 1')
  await expect(evidence).toContainText('TOO FEW EXECUTIONS')
  for (const pattern of ['CONSISTENTLY FAILING', 'HISTORICALLY STABLE', 'FLAKY —', 'product bug']) {
    await expect(evidence).not.toContainText(pattern)
  }
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
