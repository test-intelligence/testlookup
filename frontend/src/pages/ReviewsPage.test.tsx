/**
 * E8.5: the Review Queue page.
 *
 * QA leads accept or reject pending AI reports (a reject needs a reason code);
 * everyone else sees the queue read-only; All Projects mode shows the project
 * picker instead of fetching a queue that does not exist.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Review } from '@/types/review'

const hookState = vi.hoisted(() => ({
  reviews: [] as unknown[],
  refresh: vi.fn(() => Promise.resolve()),
  lastState: '' as string,
  error: undefined as unknown,
  projectId: 'proj-1',
  canAccessManagement: true,
}))

vi.mock('@/hooks/useReviews', () => ({
  useProjectReviews: (state: string) => {
    hookState.lastState = state
    return {
      reviews: hookState.reviews,
      isLoading: false,
      error: hookState.error,
      isError: !!hookState.error,
      refresh: hookState.refresh,
    }
  },
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: hookState.canAccessManagement }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({ activeProjectId: hookState.projectId, projects: [], setActiveProject: vi.fn() }),
  ),
}))

vi.mock('@/services/reviewService', () => ({
  reviewService: {
    accept: vi.fn(() => Promise.resolve({})),
    reject: vi.fn(() => Promise.resolve({})),
  },
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import ReviewsPage from './ReviewsPage'
import { reviewService } from '@/services/reviewService'

function review(i: number, overrides: Partial<Review> = {}): Review {
  return {
    id: `rev-${i}`,
    project_id: 'proj-1',
    kind: 'report',
    subject_type: 'pipeline_run',
    subject_id: `pipe-${i}`,
    pipeline_run_id: `pipe-${i}`,
    test_run_id: `run-${i}`,
    workflow_type: 'deep',
    state: 'pending_review',
    reviewed: false,
    reviewed_at: null,
    reason_code: null,
    notes: null,
    evidence_bundle_sha256: null,
    superseded_by: null,
    created_at: '2026-09-12T10:00:00Z',
    requires_human_review: true,
    ai_disclaimer: 'AI-generated content. Verify before acting.',
    ai_disclaimer_version: '2026-09-12.v1',
    ...overrides,
  }
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <ReviewsPage />
    </MemoryRouter>,
  )

beforeEach(() => {
  hookState.reviews = [review(1)]
  hookState.projectId = 'proj-1'
  hookState.canAccessManagement = true
  hookState.error = undefined
  hookState.refresh.mockClear()
  vi.mocked(reviewService.accept).mockClear()
  vi.mocked(reviewService.reject).mockClear()
})

describe('ReviewsPage', () => {
  it('shows the project picker instead of a queue in All Projects mode', () => {
    hookState.projectId = '__ALL__'
    renderPage()
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(screen.queryByTestId('review-row')).not.toBeInTheDocument()
  })

  it('lists the pending queue by default with its disclaimer and a link to the report', () => {
    renderPage()
    expect(hookState.lastState).toBe('pending_review')
    expect(screen.getAllByTestId('review-row')).toHaveLength(1)
    expect(screen.getByText('Awaiting review')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open report' })).toHaveAttribute('href', '/runs/run-1/intelligence')
    expect(screen.getByText('AI-generated content. Verify before acting.')).toBeInTheDocument()
  })

  it('switches the state filter from the tabs', () => {
    renderPage()
    fireEvent.click(screen.getByRole('tab', { name: 'Accepted' }))
    expect(hookState.lastState).toBe('accepted')
  })

  it('accepts a report with notes and refreshes the queue', async () => {
    renderPage()
    fireEvent.click(screen.getByTestId('review-accept'))
    fireEvent.change(screen.getByTestId('review-notes'), { target: { value: '  checked the logs ' } })
    fireEvent.click(screen.getByTestId('review-confirm'))
    await waitFor(() => expect(reviewService.accept).toHaveBeenCalledWith('rev-1', 'checked the logs'))
    await waitFor(() => expect(hookState.refresh).toHaveBeenCalled())
  })

  it('cannot reject without a reason code', async () => {
    renderPage()
    fireEvent.click(screen.getByTestId('review-reject'))
    const confirm = screen.getByTestId('review-confirm')
    expect(confirm).toBeDisabled()
    fireEvent.click(confirm)
    expect(reviewService.reject).not.toHaveBeenCalled()

    fireEvent.change(screen.getByTestId('review-reason'), { target: { value: 'unsupported_claim' } })
    expect(confirm).not.toBeDisabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(reviewService.reject).toHaveBeenCalledWith('rev-1', 'unsupported_claim', undefined))
  })

  it('shows the queue read-only to someone who is not a QA lead', () => {
    hookState.canAccessManagement = false
    renderPage()
    expect(screen.getAllByTestId('review-row')).toHaveLength(1)
    expect(screen.queryByTestId('review-accept')).not.toBeInTheDocument()
    expect(screen.getByTestId('review-readonly-note')).toBeInTheDocument()
  })

  it('offers no actions on a settled review', () => {
    hookState.reviews = [review(1, { state: 'accepted', reviewed: true, reviewed_at: '2026-09-12T11:00:00Z' })]
    renderPage()
    expect(screen.queryByTestId('review-accept')).not.toBeInTheDocument()
  })

  it('shows a failed fetch as unavailable, not as an empty queue', () => {
    hookState.reviews = []
    hookState.error = new Error('Network Error')
    renderPage()
    expect(screen.getByTestId('data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Nothing here')).not.toBeInTheDocument()
  })

  it('pages a long queue 25 rows at a time', () => {
    hookState.reviews = Array.from({ length: 30 }, (_, i) => review(i))
    renderPage()
    expect(screen.getAllByTestId('review-row')).toHaveLength(25)
    expect(screen.getByText('30 total results')).toBeInTheDocument()
  })
})
