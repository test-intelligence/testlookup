import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase } from '@/types/test-management'
import { ReviewsTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useTestCases: vi.fn(),
}))

vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      aiReview: vi.fn(),
      reviewAction: vi.fn(),
      transitionCase: vi.fn(),
    },
  }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

function reviewCase(overrides: Partial<ManagedTestCase> = {}): ManagedTestCase {
  return {
    id: 'case-1', project_id: 'project-1', title: 'Review sign in',
    test_type: 'functional', priority: 'high', severity: 'major',
    test_suite_id: null,
    status: 'review_requested', version: 1, is_automated: false,
    automation_status: 'manual', ai_generated: false,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

describe('ReviewsTab lifecycle queue', () => {
  const mutateRequested = vi.fn().mockResolvedValue(undefined)
  const mutateClaimed = vi.fn().mockResolvedValue(undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    mutateRequested.mockResolvedValue(undefined)
    mutateClaimed.mockResolvedValue(undefined)
    ;(useTestCases as ReturnType<typeof vi.fn>).mockImplementation((params: { status?: string }) => {
      if (params.status === 'review_requested') {
        return {
          data: { items: [reviewCase()], total: 1, page: 1, size: 50, pages: 1 },
          isLoading: false,
          error: undefined,
          mutate: mutateRequested,
        }
      }
      return {
        data: { items: [], total: 0, page: 1, size: 50, pages: 0 },
        isLoading: false,
        error: undefined,
        mutate: mutateClaimed,
      }
    })
  })

  it('deduplicates cases and surfaces a partial queue error with a combined retry', () => {
    ;(useTestCases as ReturnType<typeof vi.fn>).mockImplementation((params: { status?: string }) => params.status === 'review_requested'
      ? {
          data: { items: [reviewCase()], total: 1 },
          isLoading: false,
          error: undefined,
          mutate: mutateRequested,
        }
      : {
          data: { items: [reviewCase({ status: 'under_review', allowed_actions: ['approve'] })], total: 1 },
          isLoading: false,
          error: new Error('claimed queue unavailable'),
          mutate: mutateClaimed,
        })

    render(<ReviewsTab projectId="project-1" lifecycleV2 />)

    expect(screen.getAllByText('Review sign in')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded completely')
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.getByText('Actions unavailable until this review list refreshes.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry both lists' }))
    expect(mutateRequested).toHaveBeenCalledTimes(1)
    expect(mutateClaimed).toHaveBeenCalledTimes(1)
  })

  it('fails closed on missing v2 allowed_actions', () => {
    render(<ReviewsTab projectId="project-1" lifecycleV2 />)

    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument()
  })

  it('preserves the legacy review shim while requiring audit notes', async () => {
    ;(testManagementService.reviewAction as ReturnType<typeof vi.fn>).mockResolvedValue(reviewCase({ status: 'approved' }))
    render(<ReviewsTab projectId="project-1" lifecycleV2={false} />)

    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }))
    const dialog = screen.getByRole('dialog', { name: 'Request changes' })
    const confirm = within(dialog).getByRole('button', { name: 'Request changes' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Review notes/ }), {
      target: { value: '  Clarify the expected error state  ' },
    })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(testManagementService.reviewAction).toHaveBeenCalledWith(
        'case-1',
        'request_changes',
        'Clarify the expected error state',
      )
    })
    expect(testManagementService.transitionCase).not.toHaveBeenCalled()
  })

  it('sends trimmed v2 request-changes notes, applies draft locally, and warns on refresh failure', async () => {
    ;(useTestCases as ReturnType<typeof vi.fn>).mockImplementation((params: { status?: string }) => params.status === 'review_requested'
      ? {
          data: { items: [], total: 0 },
          isLoading: false,
          error: undefined,
          mutate: mutateRequested,
        }
      : {
          data: { items: [reviewCase({ status: 'under_review', allowed_actions: ['request_changes'] })], total: 1 },
          isLoading: false,
          error: undefined,
          mutate: mutateClaimed,
        })
    ;(testManagementService.transitionCase as ReturnType<typeof vi.fn>).mockResolvedValue(
      reviewCase({ status: 'draft', allowed_actions: ['request_review', 'deprecate'] }),
    )
    mutateClaimed.mockRejectedValue(new Error('refresh unavailable'))
    render(<ReviewsTab projectId="project-1" lifecycleV2 />)

    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }))
    const dialog = screen.getByRole('dialog', { name: 'Request changes' })
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Review notes/ }), {
      target: { value: '  Clarify the expected error state  ' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Request changes' }))

    await waitFor(() => {
      expect(testManagementService.transitionCase).toHaveBeenCalledWith('case-1', {
        action: 'request_changes',
        notes: 'Clarify the expected error state',
      })
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('Review sign in')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('action was saved')
    expect(testManagementService.transitionCase).toHaveBeenCalledTimes(1)
  })
})
