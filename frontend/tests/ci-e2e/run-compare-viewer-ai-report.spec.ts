/**
 * D-0003 (GPT-6 exploratory, E2E-0003) on `/runs/compare?mode=latest&suite=Auth`, hermetic: a Viewer's
 * "Compare latest" on a pair with no saved AI report shows the diff and a note that no report exists yet,
 * "created when a QA engineer or above opens this comparison"; the server no longer generates a paid report
 * for a Viewer (`ai_report: null`). A Viewer still sees a report once one exists, and a QA engineer sees the
 * report and no note.
 *
 * What a browser cannot prove: that no model call happened. The authoritative check is the backend regression
 * `backend/tests/regression/test_run_compare_viewer_does_not_generate.py` (the real handlers, the real
 * `BudgetedLLM` around a scripted model). This spec guards what the reader sees.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-ai-history.ts`
 * (`RUN_COMPARE_PAGE`, `RUN_COMPARE`), `fixtures-releases.ts` (`VIEWER`, `QA_ENGINEER`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { RUN_COMPARE, RUN_COMPARE_PAGE } from '../visual/production/fixtures-ai-history'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const PATH = '/runs/compare?mode=latest&suite=Auth'
const LATEST = '/api/v1/runs/compare/latest'

/** The latest-suite compare as the server answers a cache miss for a Viewer: the diff, no report. */
const noReportYet: ApiHandlers = [
  [LATEST, () => ({ ...RUN_COMPARE, ai_report: null, selection: { ...RUN_COMPARE.selection, mode: 'latest' } })],
  ...RUN_COMPARE_PAGE,
]

const ready = (page: Page) => page.locator('[data-delta-table]')

test('a Viewer on a pair with no AI report sees the diff and a note, never a report', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: noReportYet, user: VIEWER, ready })
  const note = page.locator('[data-ai-report-not-generated]')
  await expect(note).toBeVisible()
  await expect(note).toContainText('No comparison report exists for these runs yet')
  await expect(note).toContainText('QA engineer or above')
  await expect(page.getByText('Comparison Report', { exact: true })).toHaveCount(0)
  // The comparison itself is served.
  await expect(page.locator('[data-delta-table]')).toBeVisible()

  await networkQuiet(page, api)
  const asked = requestsTo(api, LATEST)
  expect(asked.length).toBeGreaterThan(0)
  // The page asks for the default (attach a report); the server decides by role.
  expect(asked.every((line) => !line.includes('include_ai_report=false'))).toBe(true)
  assertHermetic(api, errors)
})

test('a Viewer still sees a report that already exists, with no note', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: RUN_COMPARE_PAGE, user: VIEWER, ready })
  await expect(page.getByText('Comparison Report', { exact: true })).toBeVisible()
  await expect(page.locator('[data-ai-report-not-generated]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a QA engineer sees the report and no note', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: RUN_COMPARE_PAGE, user: QA_ENGINEER, ready })
  await expect(page.getByText('Comparison Report', { exact: true })).toBeVisible()
  await expect(page.locator('[data-ai-report-not-generated]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
