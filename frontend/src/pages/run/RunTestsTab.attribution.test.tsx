/**
 * D-72: the Category cell's attribution badge is fetched once while the test list polls every 5 s. When the
 * pipeline writes a category onto a test already on screen, the badge is fetched again; on a finished run
 * (categories never change between polls) it is not.
 */
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunTestsTab from './RunTestsTab'
import type { TestRun } from '@/types/runs'

const { useTestCases, useRunAttribution, refreshAttribution } = vi.hoisted(() => ({
  useTestCases: vi.fn(),
  useRunAttribution: vi.fn(),
  refreshAttribution: vi.fn(),
}))
vi.mock('@/hooks/useRuns', () => ({ useTestCases, useRunAttribution }))
vi.mock('@/pages/testCase/TestCaseBody', () => ({ default: () => <div data-testid="test-case-body" /> }))

const RUN = { id: 'r1', total_tests: 2, passed_tests: 0, failed_tests: 2, broken_tests: 0, skipped_tests: 0 } as unknown as TestRun

function listOf(categories: Array<string | undefined>) {
  return {
    data: {
      items: categories.map((failure_category, i) => ({
        id: `t${i + 1}`, test_name: `test ${i + 1}`, suite_name: 'suite', status: 'FAILED', failure_category,
      })),
      total: categories.length,
      pages: 1,
    },
    isLoading: false,
    error: undefined,
  }
}

function tab() {
  return (
    <MemoryRouter initialEntries={['/runs/r1']}>
      <Routes>
        <Route
          path="*"
          element={<RunTestsTab runId="r1" run={RUN} statusFilter="" suiteFilter="" page={1} onStatusFilter={vi.fn()} onSuiteFilter={vi.fn()} onPage={vi.fn()} />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('Run page › Tests: the attribution badge follows the polled categories (D-72)', () => {
  beforeEach(() => {
    refreshAttribution.mockReset()
    useRunAttribution.mockReturnValue({ data: { items: [] }, mutate: refreshAttribution })
  })

  it('fetches the attribution again when the polled list re-resolves with a category', () => {
    useTestCases.mockReturnValue(listOf([undefined, undefined]))
    const { rerender } = render(tab())
    expect(refreshAttribution).not.toHaveBeenCalled()

    // The next poll: the pipeline has classified the first failure.
    useTestCases.mockReturnValue(listOf(['PRODUCT_BUG', undefined]))
    rerender(tab())
    expect(refreshAttribution).toHaveBeenCalledTimes(1)
  })

  it('does not fetch it again on a finished run, however many times the list polls', () => {
    useTestCases.mockReturnValue(listOf(['PRODUCT_BUG', 'INFRASTRUCTURE']))
    const { rerender } = render(tab())
    for (let poll = 0; poll < 5; poll++) {
      // Each poll hands back a fresh object with the same categories.
      useTestCases.mockReturnValue(listOf(['PRODUCT_BUG', 'INFRASTRUCTURE']))
      rerender(tab())
    }
    expect(refreshAttribution).not.toHaveBeenCalled()
  })
})
