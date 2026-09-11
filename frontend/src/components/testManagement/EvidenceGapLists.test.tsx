import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useOrphanedCanonicalCases } from '@/hooks/useSuites'
import { useTestCaseEvidenceGaps } from '@/hooks/useTestManagement'
import { usePermissions } from '@/hooks/usePermissions'
import { onboardingService } from '@/services/onboardingService'
import { suitesService } from '@/services/suitesService'
import EvidenceGapLists from './EvidenceGapLists'

vi.mock('@/hooks/useSuites', () => ({ useOrphanedCanonicalCases: vi.fn(), ORPHANED_PAGE_SIZE: 25 }))
vi.mock('@/hooks/useTestManagement', () => ({ useTestCaseEvidenceGaps: vi.fn() }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn() }))
vi.mock('@/services/onboardingService', () => ({
  onboardingService: { trackEvent: vi.fn().mockResolvedValue(undefined) },
}))
vi.mock('@/services/suitesService', () => ({
  suitesService: { confirmRetirement: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

describe('EvidenceGapLists', () => {
  const mutateOrphans = vi.fn().mockResolvedValue(undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    mutateOrphans.mockResolvedValue(undefined)
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaLead: true })
    ;(useTestCaseEvidenceGaps as ReturnType<typeof vi.fn>).mockImplementation((kind: string) => ({
      data: kind === 'never_executed'
        ? {
            items: [{ id: 'case-1', project_id: 'project-1', title: 'Never run', status: 'active' }],
            total: 1,
          }
        : { items: [], total: 0 },
      isLoading: false,
      error: undefined,
      mutate: vi.fn(),
    }))
    ;(useOrphanedCanonicalCases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [{
          id: 'canonical-1',
          project_id: 'project-1',
          test_suite_id: 'suite-1',
          test_suite_name: 'Regression',
          test_fingerprint: 'fp-1',
          test_name: 'Removed test',
          class_name: null,
          status: 'deleted',
          source: 'execution',
          first_seen_run_id: 'run-1',
          last_seen_run_id: 'run-1',
          deleted_at_run_id: 'run-2',
          deleted_observed_at: '2026-08-31T00:00:00Z',
          managed_test_case_id: null,
          review_tag: null,
          tags: null,
          run_count: 1,
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-31T00:00:00Z',
        }],
        total: 1,
      },
      isLoading: false,
      error: undefined,
      mutate: mutateOrphans,
    })
  })

  it('uses measured totals, exposes proof rows, and records list opens', () => {
    render(<EvidenceGapLists projectId="project-1" />)

    expect(screen.getByLabelText('Execution evidence gaps')).toBeInTheDocument()
    expect(screen.queryByText('Never run')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Authored cases never executed/ }))

    expect(screen.getByText('Never run')).toBeInTheDocument()
    expect(onboardingService.trackEvent).toHaveBeenCalledWith(
      'test_case_evidence_list_opened',
      'project-1',
      { kind: 'never_executed' },
    )
  })

  it('requires a reason before confirming an orphan retirement', async () => {
    ;(suitesService.confirmRetirement as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    render(<EvidenceGapLists projectId="project-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Unconfirmed automation retirements/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm retirement' }))
    const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm retirement' })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'Removed from the source suite' } })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(suitesService.confirmRetirement).toHaveBeenCalledWith(
        'canonical-1',
        'Removed from the source suite',
      )
    })
    expect(mutateOrphans).toHaveBeenCalled()
  })

  it('does not fabricate a zero while a proof list is loading', () => {
    ;(useTestCaseEvidenceGaps as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: undefined,
      mutate: vi.fn(),
    })
    render(<EvidenceGapLists projectId="project-1" />)

    expect(screen.getAllByText('not measured')).toHaveLength(2)
  })

  it('does not expose retirement confirmation below QA lead', () => {
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaLead: false })
    render(<EvidenceGapLists projectId="project-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Unconfirmed automation retirements/ }))
    expect(screen.queryByRole('button', { name: 'Confirm retirement' })).not.toBeInTheDocument()
    expect(suitesService.confirmRetirement).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and displays a retirement mutation error', async () => {
    ;(suitesService.confirmRetirement as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { data: { detail: 'Retirement conflicts with a newer execution' } },
    })
    render(<EvidenceGapLists projectId="project-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Unconfirmed automation retirements/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm retirement' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Reason/ }), {
      target: { value: 'Removed intentionally' },
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm retirement' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Retirement conflicts with a newer execution')
    expect(screen.getByRole('dialog', { name: 'Confirm automation retirement' })).toBeInTheDocument()
  })

  // N16: the backend pages /orphaned at 25; the panel used to show 25 rows
  // under the full total with no way to reach the rest.
  function orphanRow(id: string) {
    return {
      id, project_id: 'project-1', test_suite_id: 'suite-1', test_suite_name: 'Regression',
      test_fingerprint: `fp-${id}`, test_name: `Removed ${id}`, class_name: null, status: 'deleted',
      source: 'execution', first_seen_run_id: 'run-1', last_seen_run_id: 'run-1', deleted_at_run_id: 'run-2',
      deleted_observed_at: '2026-08-31T00:00:00Z', managed_test_case_id: null, review_tag: null, tags: null,
      run_count: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
    }
  }
  function serveOrphanPages(total: number) {
    ;(useOrphanedCanonicalCases as ReturnType<typeof vi.fn>).mockImplementation((page = 1, size = 25) => {
      const start = (page - 1) * size
      const count = Math.max(0, Math.min(size, total - start))
      return {
        data: { items: Array.from({ length: count }, (_, i) => orphanRow(`p${page}-${i}`)), total },
        isLoading: false, error: undefined, mutate: mutateOrphans,
      }
    })
  }

  it('labels the orphaned page and pages through every case', () => {
    serveOrphanPages(60)
    render(<EvidenceGapLists projectId="project-1" />)

    const header = screen.getByRole('button', { name: /Unconfirmed automation retirements/ })
    expect(header).toHaveTextContent('60')
    fireEvent.click(header)
    const pager = screen.getByRole('navigation', { name: 'Unconfirmed retirements pages' })
    expect(pager).toHaveTextContent('Showing 1–25 of 60')
    expect(screen.getByText('Removed p1-0')).toBeInTheDocument()
    expect(within(pager).getByRole('button', { name: 'Previous' })).toBeDisabled()

    fireEvent.click(within(pager).getByRole('button', { name: 'Next' }))
    expect(pager).toHaveTextContent('Showing 26–50 of 60')
    expect(screen.getByText('Removed p2-0')).toBeInTheDocument()
    expect(screen.queryByText('Removed p1-0')).not.toBeInTheDocument()

    fireEvent.click(within(pager).getByRole('button', { name: 'Next' }))
    expect(pager).toHaveTextContent('Showing 51–60 of 60')
    expect(screen.getByText('Removed p3-9')).toBeInTheDocument()
    expect(within(pager).getByRole('button', { name: 'Next' })).toBeDisabled()

    fireEvent.click(within(pager).getByRole('button', { name: 'Previous' }))
    expect(pager).toHaveTextContent('Showing 26–50 of 60')
  })

  it('shows no paging controls when every case fits on one page', () => {
    serveOrphanPages(3)
    render(<EvidenceGapLists projectId="project-1" />)
    fireEvent.click(screen.getByRole('button', { name: /Unconfirmed automation retirements/ }))

    expect(screen.getByText('Removed p1-2')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Unconfirmed retirements pages' })).not.toBeInTheDocument()
  })

  it('closes and hides the confirmed row when mutation succeeds but refresh fails', async () => {
    ;(suitesService.confirmRetirement as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    mutateOrphans.mockRejectedValue(new Error('refresh unavailable'))
    render(<EvidenceGapLists projectId="project-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Unconfirmed automation retirements/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm retirement' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Reason/ }), {
      target: { value: 'Removed intentionally' },
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm retirement' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByText('Removed test')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Retirement was confirmed')
    expect(suitesService.confirmRetirement).toHaveBeenCalledTimes(1)
  })
})
