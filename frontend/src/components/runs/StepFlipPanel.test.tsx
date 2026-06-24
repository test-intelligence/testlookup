import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import StepFlipPanel from './StepFlipPanel'
import type { TestStepFlips } from '@/types/runs'

vi.mock('@/hooks/useRuns', () => ({
  useTestStepFlips: vi.fn(),
}))

async function mockHook(value: { data?: TestStepFlips; isLoading: boolean }) {
  const { useTestStepFlips } = await import('@/hooks/useRuns')
  ;(useTestStepFlips as ReturnType<typeof vi.fn>).mockReturnValue(value)
}

describe('StepFlipPanel', () => {
  it('shows insufficient-history copy when <2 runs analysed', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        test_id: 't1',
        test_fingerprint: 'fp',
        report: {
          has_step_flip: false,
          runs_analyzed: 1,
          total_flips: 0,
          flips: [],
          flipping_steps: [],
          summary: 'Not enough run history',
        },
      },
      isLoading: false,
    })
    render(<StepFlipPanel runId="r1" testId="t1" />)
    expect(screen.getByText(/Not enough cross-run step history/i)).toBeInTheDocument()
  })

  it('shows stable copy when runs analysed but no flip', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        test_id: 't1',
        test_fingerprint: 'fp',
        report: {
          has_step_flip: false,
          runs_analyzed: 4,
          total_flips: 0,
          flips: [],
          flipping_steps: [],
          summary: 'No cross-run step-flip across 4 runs.',
        },
      },
      isLoading: false,
    })
    render(<StepFlipPanel runId="r1" testId="t1" />)
    expect(screen.getByText(/steps are stable/i)).toBeInTheDocument()
  })

  it('renders the flipping step with its flip count and current status', async () => {
    await mockHook({
      data: {
        run_id: 'r1',
        test_id: 't1',
        test_fingerprint: 'fp',
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
      },
      isLoading: false,
    })
    render(<StepFlipPanel runId="r1" testId="t1" />)
    expect(screen.getByText('login')).toBeInTheDocument()
    expect(screen.getByText(/2 flips/)).toBeInTheDocument()
    expect(screen.getByText(/now PASSED/)).toBeInTheDocument()
    expect(screen.getByText(/2 total flips/)).toBeInTheDocument()
  })
})
