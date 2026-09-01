import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { refreshSuites } from '@/hooks/useSuites'
import { usePermissions } from '@/hooks/usePermissions'
import { suitesService } from '@/services/suitesService'
import { mutate } from 'swr'
import PromotionAction from './PromotionAction'

vi.mock('@/hooks/useSuites', () => ({ refreshSuites: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn() }))
vi.mock('swr', () => ({ mutate: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/services/suitesService', () => ({
  suitesService: { promoteCanonical: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn() },
}))

const result = {
  canonical: {
    id: 'canonical-1',
    project_id: 'project-1',
    test_suite_id: 'suite-1',
    test_suite_name: 'Regression',
    test_fingerprint: 'fingerprint-1',
    test_name: 'signs in',
    class_name: null,
    status: 'active',
    source: 'linked',
    first_seen_run_id: 'run-1',
    last_seen_run_id: 'run-1',
    deleted_at_run_id: null,
    managed_test_case_id: 'managed-1',
    review_tag: null,
    tags: null,
    run_count: 1,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  },
  managed_case: {
    id: 'managed-1',
    project_id: 'project-1',
    title: 'signs in',
    test_type: 'functional',
    priority: 'medium',
    severity: 'major',
    test_suite_id: null,
    status: 'draft' as const,
    version: 1,
    is_automated: true,
    automation_status: 'automated',
    ai_generated: false,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  },
}

describe('PromotionAction', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: true })
    ;(refreshSuites as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    ;(mutate as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
  })

  it('does not expose promotion below QA engineer', () => {
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: false })
    render(<PromotionAction canonicalId="canonical-1" />)

    expect(screen.queryByRole('button', { name: 'Promote to managed case' })).not.toBeInTheDocument()
    expect(suitesService.promoteCanonical).not.toHaveBeenCalled()
  })

  it('fails closed when the automation row has no canonical identity', () => {
    render(<PromotionAction />)

    const button = screen.getByRole('button', { name: 'Promote to managed case' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('no canonical test identity'))
    expect(suitesService.promoteCanonical).not.toHaveBeenCalled()
  })

  it('promotes exclusively through canonical identity and refreshes both catalogs', async () => {
    ;(suitesService.promoteCanonical as ReturnType<typeof vi.fn>).mockResolvedValue(result)
    const onPromoted = vi.fn()
    render(<PromotionAction canonicalId="canonical-1" onPromoted={onPromoted} />)

    fireEvent.click(screen.getByRole('button', { name: 'Promote to managed case' }))

    await waitFor(() => expect(suitesService.promoteCanonical).toHaveBeenCalledWith('canonical-1'))
    expect(refreshSuites).toHaveBeenCalled()
    expect(mutate).toHaveBeenCalledWith(expect.any(Function))
    expect(onPromoted).toHaveBeenCalledWith(result)
  })

  it('prevents a second promotion when the managed link already exists', () => {
    render(<PromotionAction canonicalId="canonical-1" linkedManagedCaseId="managed-1" />)
    expect(screen.getByRole('button', { name: 'Already promoted' })).toBeDisabled()
  })

  it('keeps a successful promotion disabled when catalog refresh fails', async () => {
    ;(suitesService.promoteCanonical as ReturnType<typeof vi.fn>).mockResolvedValue(result)
    ;(refreshSuites as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('refresh unavailable'))
    render(<PromotionAction canonicalId="canonical-1" />)

    fireEvent.click(screen.getByRole('button', { name: 'Promote to managed case' }))

    const promoted = await screen.findByRole('button', { name: 'Already promoted' })
    expect(promoted).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Promotion succeeded')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(suitesService.promoteCanonical).toHaveBeenCalledTimes(1)
  })
})
