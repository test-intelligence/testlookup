import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePermissions } from '@/hooks/usePermissions'
import { testManagementService } from '@/services/testManagementService'
import type { DuplicateCandidate } from '@/types/test-management'
import { DuplicateCandidateCard } from './TestManagementPage'

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn() }))

vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      dismissDuplicate: vi.fn(),
      mergeDuplicate: vi.fn(),
    },
  }
})

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

const candidate: DuplicateCandidate = {
  id: 'candidate-1',
  project_id: 'project-1',
  band: 'strong',
  score: 0.94,
  reason: 'Equivalent steps and expected result',
  method: 'structural',
  component_scores: { steps: 0.98 },
  status: 'open',
  detected_at: '2026-09-01T00:00:00Z',
  case_a: { id: 'case-a', title: 'Sign in with valid credentials', status: 'active' },
  case_b: { id: 'case-b', title: 'Valid user signs in', status: 'active' },
}

describe('DuplicateCandidateCard lifecycle permissions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps Dismiss but hides Merge from a QA engineer', () => {
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaLead: false })
    render(<DuplicateCandidateCard projectId="project-1" candidate={candidate} onResolved={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Merge' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Keep' })).not.toBeInTheDocument()
  })

  it('allows a QA lead to merge and keeps the action resolved after refresh failure', async () => {
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaLead: true })
    ;(testManagementService.mergeDuplicate as ReturnType<typeof vi.fn>).mockResolvedValue({
      candidate_id: 'candidate-1',
      status: 'merged',
      deprecated_case_id: 'case-b',
    })
    const onResolved = vi.fn().mockRejectedValue(new Error('queue refresh unavailable'))
    render(<DuplicateCandidateCard projectId="project-1" candidate={candidate} onResolved={onResolved} />)

    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Merge' }))

    await waitFor(() => {
      expect(testManagementService.mergeDuplicate).toHaveBeenCalledWith('project-1', 'candidate-1', {
        candidate_id: 'candidate-1',
        keep_case_id: 'case-a',
        deprecate_loser: true,
      })
    })
    expect(screen.queryByRole('button', { name: 'Merge' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument()
    expect(screen.getByText('merged')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('pair was merged')
    expect(onResolved).toHaveBeenCalledWith('candidate-1')
    expect(testManagementService.mergeDuplicate).toHaveBeenCalledTimes(1)
  })
})
