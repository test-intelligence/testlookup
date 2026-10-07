/**
 * UX redesign P4 item 2: a test opens from the Run page in a side panel — the
 * test case body (compact), "Open full page", and the previous / next failure
 * on the page — instead of leaving the run. Ctrl / ⌘-click still opens the
 * full page.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunTestsTab from './RunTestsTab'
import type { TestRun } from '@/types/runs'

const { useTestCases } = vi.hoisted(() => ({ useTestCases: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({
  useTestCases,
  useRunAttribution: vi.fn(() => ({ data: { items: [] } })),
}))
// The body is TestCaseBody's own concern (TestCaseBody.test.tsx): here, which test, how.
vi.mock('@/pages/testCase/TestCaseBody', () => ({
  default: ({ runId, testId, compact }: { runId: string; testId: string; compact?: boolean }) => (
    <div data-testid="test-case-body">{`${runId}/${testId}${compact ? ' compact' : ''}`}</div>
  ),
}))

const ROWS = [
  { id: 'p1', test_name: 'login works', status: 'PASSED' },
  { id: 'f1', test_name: 'card declined shows reason', status: 'FAILED' },
  { id: 'p2', test_name: 'search paginates', status: 'PASSED' },
  { id: 'f2', test_name: 'refund webhook retried', status: 'BROKEN' },
]
const RUN = { id: 'r1', total_tests: 4, passed_tests: 2, failed_tests: 1, broken_tests: 1, skipped_tests: 0 } as unknown as TestRun

let path = ''
function Where() {
  const location = useLocation()
  useEffect(() => { path = location.pathname }, [location])
  return null
}

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/runs/r1']}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <RunTestsTab runId="r1" run={RUN} statusFilter="" suiteFilter="" page={1} onStatusFilter={vi.fn()} onSuiteFilter={vi.fn()} onPage={vi.fn()} />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`[data-test-row="${id}"]`) as HTMLElement
const panel = () => screen.getByRole('complementary', { name: /./ })

describe('Run page › Tests: a test opens in a side panel', () => {
  beforeEach(() => {
    path = ''
    useTestCases.mockReturnValue({ data: { items: ROWS, total: 4, pages: 1 }, isLoading: false, error: undefined })
  })

  it('a row opens its test beside the list, compact, and stays on the run', () => {
    renderTab()
    fireEvent.click(row('f1'))
    expect(path).toBe('/runs/r1')
    expect(within(panel()).getByRole('heading', { name: 'card declined shows reason' })).toBeInTheDocument()
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
    expect(row('f1')).toHaveAttribute('aria-selected', 'true')
    expect(within(panel()).getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', '/runs/r1/tests/f1')
  })

  it('walks the failures on the page in the table order: failed and broken first', () => {
    renderTab()
    fireEvent.click(row('f1'))
    const prev = () => within(panel()).getByRole('button', { name: /Previous failure/ })
    const next = () => within(panel()).getByRole('button', { name: /Next failure/ })
    expect(panel()).toHaveTextContent('1 of 2 failures on this page')
    expect(prev()).toBeDisabled()
    fireEvent.click(next())
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f2 compact')
    expect(panel()).toHaveTextContent('2 of 2 failures on this page')
    expect(next()).toBeDisabled()
    fireEvent.click(prev())
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
  })

  it('from a passing test, Next goes to the first failure', () => {
    renderTab()
    fireEvent.click(row('p2'))
    expect(within(panel()).queryByText(/failures on this page/)).toBeNull()
    expect(within(panel()).getByRole('button', { name: /Previous failure/ })).toBeDisabled()
    fireEvent.click(within(panel()).getByRole('button', { name: /Next failure/ }))
    expect(within(panel()).getByTestId('test-case-body')).toHaveTextContent('r1/f1 compact')
  })

  it('closes, and Ctrl-click opens the full page instead', () => {
    renderTab()
    fireEvent.click(row('f2'))
    fireEvent.click(within(panel()).getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByTestId('test-case-body')).toBeNull()
    fireEvent.click(row('f2'), { ctrlKey: true })
    expect(path).toBe('/runs/r1/tests/f2')
    expect(screen.queryByTestId('test-case-body')).toBeNull()
  })
})
