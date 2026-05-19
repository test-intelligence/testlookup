/**
 * Regression: /test-management Test Suites tab — run_count + per-status
 * cumulative columns + Trend link.
 *
 * Bug pinned (2026-05-19): user feature request — every suite-bearing
 * page must track and show the test runs count along with test cases
 * count, passed / failed / skipped counts. The new
 * ``suite_history_service`` powers these aggregates on the backend;
 * the Test Suites tab consumes them via
 * ``testManagementService.listSuites`` and renders new cells.
 *
 * Strategy: source-text invariants on the page so a future refactor
 * that drops the Trend link or the run_count cell fails this test
 * before users see it. Heavy DOM rendering of the full
 * TestManagementPage is not worth the test cost — the rendering
 * contract is "if the fields are in the payload, the cells show".
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, expect, it } from 'vitest'

const PAGE_FILE = path.resolve(__dirname, 'TestManagementPage.tsx')

describe('TestManagementPage — suite aggregate cells (regression)', () => {
  const src = fs.readFileSync(PAGE_FILE, 'utf-8')

  it('SuiteItem interface declares the cumulative aggregate fields', () => {
    expect(src).toMatch(/run_count\?:\s*number/)
    expect(src).toMatch(/total_executions\?:\s*number/)
    expect(src).toMatch(/total_skipped\?:\s*number/)
    expect(src).toMatch(/total_broken\?:\s*number/)
  })

  it('renders a Trend → link to /coverage/suite', () => {
    expect(src).toMatch(/\/coverage\/suite\?name=/)
    expect(src).toMatch(/Trend/)
  })

  it('renders the run_count cell when > 0', () => {
    // Guard against ``run_count === 0`` rendering a zero-row that
    // clutters every suite line.
    expect(src).toMatch(/suite\.run_count \?\? 0\) > 0/)
  })

  it('shows skipped/broken counts only when non-zero', () => {
    expect(src).toMatch(/suite\.total_skipped \?\? 0\) > 0/)
    expect(src).toMatch(/suite\.total_broken \?\? 0\) > 0/)
  })
})
