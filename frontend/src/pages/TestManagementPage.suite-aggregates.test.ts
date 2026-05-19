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
 * Strategy: pull the page source via Vite's ``?raw`` import (no Node
 * built-ins so the production ``tsc`` build doesn't trip on this
 * test) and assert source-text invariants — a future refactor that
 * drops the Trend link or the run_count cell fails this test before
 * users see it.
 */
import { describe, expect, it } from 'vitest'

import pageSource from './TestManagementPage.tsx?raw'

describe('TestManagementPage — suite aggregate cells (regression)', () => {
  it('SuiteItem interface declares the cumulative aggregate fields', () => {
    expect(pageSource).toMatch(/run_count\?:\s*number/)
    expect(pageSource).toMatch(/total_executions\?:\s*number/)
    expect(pageSource).toMatch(/total_skipped\?:\s*number/)
    expect(pageSource).toMatch(/total_broken\?:\s*number/)
  })

  it('renders a Trend → link to /coverage/suite', () => {
    expect(pageSource).toMatch(/\/coverage\/suite\?name=/)
    expect(pageSource).toMatch(/Trend/)
  })

  it('renders the run_count cell when > 0', () => {
    // Guard against ``run_count === 0`` rendering a zero-row that
    // clutters every suite line.
    expect(pageSource).toMatch(/suite\.run_count \?\? 0\) > 0/)
  })

  it('shows skipped/broken counts only when non-zero', () => {
    expect(pageSource).toMatch(/suite\.total_skipped \?\? 0\) > 0/)
    expect(pageSource).toMatch(/suite\.total_broken \?\? 0\) > 0/)
  })
})
