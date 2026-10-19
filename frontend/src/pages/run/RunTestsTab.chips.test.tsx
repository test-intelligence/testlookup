/**
 * W29a UIX-03 (contract §2.3 test 5): the Run page's status chips mark each
 * RESULT with its own hue — skipped is the skipped hue (it was the muted grey),
 * so a chip, a badge and a chart legend never disagree. UIX-11: a failed read
 * says "Could not load …".
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunTestsTab from './RunTestsTab'
import type { TestRun } from '@/types/runs'

const { useTestCases } = vi.hoisted(() => ({ useTestCases: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({
  useTestCases,
  useRunAttribution: vi.fn(() => ({ data: { items: [] } })),
}))
vi.mock('@/pages/testCase/TestCaseBody', () => ({ default: () => <div data-testid="test-case-body" /> }))

const RUN = { id: 'r1', total_tests: 7, passed_tests: 2, failed_tests: 1, broken_tests: 1, skipped_tests: 3 } as unknown as TestRun

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/runs/r1']}>
      <Routes>
        <Route
          path="*"
          element={<RunTestsTab runId="r1" run={RUN} statusFilter="" suiteFilter="" page={1} onStatusFilter={vi.fn()} onSuiteFilter={vi.fn()} onPage={vi.fn()} />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

/** The hue of a status chip's dot. */
function dotHue(value: string): string {
  const chip = document.querySelector<HTMLElement>(`[data-status-chip="${value}"]`)
  const dot = chip?.querySelector<HTMLElement>('span[aria-hidden]')
  if (!dot) throw new Error(`no ${value} chip`)
  return dot.style.background
}

describe('Run page › Tests: the status chips (W29a UIX-03)', () => {
  beforeEach(() => {
    useTestCases.mockReturnValue({ data: { items: [], total: 0, pages: 1 }, isLoading: false, error: undefined })
  })

  it('marks every result with its own status hue: skipped is the skipped hue', () => {
    renderTab()
    expect(dotHue('SKIPPED')).toBe('var(--status-skipped)')
    expect(dotHue('FAILED')).toBe('var(--status-failed)')
    expect(dotHue('BROKEN')).toBe('var(--status-broken)')
    expect(dotHue('PASSED')).toBe('var(--status-passed)')
    expect(screen.getByRole('button', { name: /3 skipped/ })).toBeInTheDocument()
  })

  it('says "Could not load test cases" when the read fails', () => {
    useTestCases.mockReturnValue({ data: undefined, isLoading: false, error: new Error('boom') })
    renderTab()
    expect(screen.getByText(/^Could not load test cases — boom$/)).toBeInTheDocument()
    expect(screen.queryByText(/Failed to load/)).toBeNull()
  })
})
