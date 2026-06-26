import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import RunStepFlipCard from './RunStepFlipCard'
import type { RunStepFlips } from '@/types/runs'

vi.mock('@/hooks/useRuns', () => ({
  useRunStepFlips: vi.fn(),
}))

async function mockHook(value: { data?: RunStepFlips; isLoading: boolean }) {
  const { useRunStepFlips } = await import('@/hooks/useRuns')
  ;(useRunStepFlips as ReturnType<typeof vi.fn>).mockReturnValue(value)
}

function renderCard() {
  return render(
    <MemoryRouter>
      <RunStepFlipCard runId="r1" />
    </MemoryRouter>,
  )
}

const flakyTest = {
  test_id: 't-flaky',
  test_name: 'test_login',
  test_fingerprint: 'fp-flaky',
  status: 'FAILED',
  report: {
    has_step_flip: true,
    runs_analyzed: 3,
    total_flips: 2,
    flips: [],
    flipping_steps: [
      {
        ordinal: 0,
        step_name: 'login',
        flip_count: 2,
        runs_observed: 3,
        last_status: 'PASSED',
        is_flaky: true,
      },
    ],
    summary: "Step-level flakiness: 'login' flipped PASSED<->FAILED 2x across 3 runs.",
  },
}

describe('RunStepFlipCard', () => {
  it('shows stable empty state when no test has a flickering step', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        project_id: 'p1',
        tests_analyzed: 5,
        tests_with_flips: 0,
        total_flips: 0,
        truncated: false,
        tests: [],
      },
      isLoading: false,
    })
    renderCard()
    expect(screen.getByText(/No flickering steps across 5 analysed tests/i)).toBeInTheDocument()
  })

  it('lists each flickering test with its top step, flip count, and a detail link', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        project_id: 'p1',
        tests_analyzed: 4,
        tests_with_flips: 1,
        total_flips: 2,
        truncated: false,
        tests: [flakyTest],
      },
      isLoading: false,
    })
    renderCard()
    expect(screen.getByText('test_login')).toBeInTheDocument()
    expect(screen.getByText(/2 flips/)).toBeInTheDocument()
    expect(screen.getByText(/step .*login.* now PASSED/i)).toBeInTheDocument()
    expect(screen.getByText(/1 of 4 tests/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /test_login/i })
    expect(link).toHaveAttribute('href', '/runs/r1/tests/t-flaky')
  })

  it('notes truncation when the run exceeds the analysis cap', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        project_id: 'p1',
        tests_analyzed: 200,
        tests_with_flips: 1,
        total_flips: 2,
        truncated: true,
        tests: [flakyTest],
      },
      isLoading: false,
    })
    renderCard()
    expect(screen.getByText(/more tests than the step-flip analysis cap/i)).toBeInTheDocument()
  })
})
