import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePermissions } from '@/hooks/usePermissions'
import { refreshSuites } from '@/hooks/useSuites'
import { suitesService } from '@/services/suitesService'
import CanonicalDetailPage from './CanonicalDetailPage'

// We mock the hooks rather than the SWR layer so tests don't need to
// stand up an SWR cache or a fake fetcher. Same pattern as
// ``TestCasePage.test.tsx`` in this directory.
vi.mock('@/hooks/useSuites', () => ({
  refreshSuites: vi.fn().mockResolvedValue(undefined),
  useCanonicalCase: vi.fn(),
  useCanonicalRuns: vi.fn(),
  useSuite: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn() }))
vi.mock('@/services/suitesService', () => ({
  suitesService: {
    unlinkManagedCase: vi.fn(),
    promoteCanonical: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const PATH = '/canonical-test-cases/:canonicalId'

function renderAt(canonicalId: string) {
  return render(
    <MemoryRouter initialEntries={[`/canonical-test-cases/${canonicalId}`]}>
      <Routes>
        <Route path="/suites" element={<div>Suites List</div>} />
        <Route path="/suites/:suiteId" element={<div>Suite Detail</div>} />
        <Route path={PATH} element={<CanonicalDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CanonicalDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: true })
    ;(refreshSuites as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
  })

  it('renders the identity header + run-history rows', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-1',
        project_id: 'proj-1',
        test_suite_id: 'suite-1',
        test_suite_name: 'Regression',
        test_fingerprint: 'abcdef0123456789',
        test_name: 'should sign in',
        class_name: 'auth.LoginTest',
        status: 'active',
        source: 'execution',
        first_seen_run_id: null,
        last_seen_run_id: 'run-aaaa1111',
        last_seen_test_case_id: 'tc-9',
        deleted_at_run_id: null,
        managed_test_case_id: null,
        review_tag: null,
        tags: null,
        run_count: 2,
        created_at: '2026-05-15T10:00:00Z',
        updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { id: 'suite-1', project_id: 'proj-1', name: 'Regression' },
    })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            test_case_id: 'tc-9',
            test_run_id: 'run-aaaa1111',
            status: 'PASSED',
            duration_ms: 1500,
            suite_name: 'Regression',
            created_at: '2026-05-16T10:00:00Z',
          },
          {
            test_case_id: 'tc-8',
            test_run_id: 'run-bbbb2222',
            status: 'FAILED',
            duration_ms: 250,
            suite_name: 'Regression',
            created_at: '2026-05-15T10:00:00Z',
          },
        ],
        total: 2,
      },
      isLoading: false,
      mutate: vi.fn(),
    })

    renderAt('canon-1')

    // Header carries the test name + class.
    expect(
      await screen.findByRole('heading', { name: /should sign in/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/auth\.LoginTest/)).toBeInTheDocument()

    // Status pill rendered from the canonical's lifecycle state.
    expect(screen.getByText('active')).toBeInTheDocument()

    // Both run-history rows are visible with their run-id prefixes.
    // The first 8 chars of each run id, mirroring the table contract.
    expect(screen.getByText('run-aaaa')).toBeInTheDocument()
    expect(screen.getByText('run-bbbb')).toBeInTheDocument()
    // Status pill per row uses upper-case status text.
    expect(screen.getByText('PASSED')).toBeInTheDocument()
    expect(screen.getByText('FAILED')).toBeInTheDocument()

    // Duration formatter: 1500ms → "1.5s", 250ms → "250ms" (per
    // ``utils/formatters.ts:formatDuration``).
    expect(screen.getByText('1.5s')).toBeInTheDocument()
    expect(screen.getByText('250ms')).toBeInTheDocument()

    // "Open latest run" CTA points at the per-run test page when we
    // have both ids.
    const cta = screen.getByRole('link', { name: /Open latest run/i })
    expect(cta).toHaveAttribute('href', '/runs/run-aaaa1111/tests/tc-9')
  })

  it('shows an empty state when the canonical has no run history yet', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-2',
        project_id: 'proj-1',
        test_suite_id: 'suite-1',
        test_suite_name: 'Regression',
        test_fingerprint: 'fp-no-runs-yet',
        test_name: 'recently authored, never run',
        class_name: null,
        status: 'active',
        source: 'managed',
        first_seen_run_id: null,
        last_seen_run_id: null,
        last_seen_test_case_id: null,
        deleted_at_run_id: null,
        managed_test_case_id: 'mtc-1',
        review_tag: null,
        tags: null,
        run_count: null,
        created_at: '2026-05-17T10:00:00Z',
        updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })

    renderAt('canon-2')

    expect(
      await screen.findByRole('heading', { name: /recently authored, never run/i }),
    ).toBeInTheDocument()
    // Empty-state copy from EmptyState component.
    expect(screen.getByText(/No runs yet/i)).toBeInTheDocument()
    // No "Open latest run" CTA when last_seen_run_id is null — saves an
    // ambiguous nav target.
    expect(screen.queryByRole('link', { name: /Open latest run/i })).toBeNull()
  })

  it('renders not-found state when the canonical lookup fails', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')

    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('not found'),
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
    })

    renderAt('canon-missing')

    expect(
      await screen.findByText(/Test case not found/i),
    ).toBeInTheDocument()
  })

  it('requires a reason before unlinking the managed case', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    const mutateCanonical = vi.fn().mockResolvedValue(undefined)
    const canonical = {
      id: 'canon-linked', project_id: 'proj-1', test_suite_id: 'suite-1',
      test_suite_name: 'Regression', test_fingerprint: 'fingerprint-linked',
      test_name: 'linked test', class_name: null, status: 'active', source: 'linked',
      first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
      deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
      tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
    }
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: canonical,
      isLoading: false,
      mutate: mutateCanonical,
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })
    ;(suitesService.unlinkManagedCase as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...canonical,
      managed_test_case_id: null,
    })

    renderAt('canon-linked')
    fireEvent.click(screen.getByRole('button', { name: 'Unlink managed case' }))
    const dialog = screen.getByRole('dialog', { name: 'Unlink managed case' })
    const confirm = within(dialog).getByRole('button', { name: 'Unlink' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Reason/ }), {
      target: { value: '  Incorrect automation mapping  ' },
    })
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(suitesService.unlinkManagedCase).toHaveBeenCalledWith(
        'canon-linked',
        'Incorrect automation mapping',
      )
    })
  })

  it('does not expose promote or unlink controls below QA engineer', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: false })
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'canon-viewer', project_id: 'proj-1', test_suite_id: 'suite-1',
        test_suite_name: 'Regression', test_fingerprint: 'fingerprint-viewer',
        test_name: 'viewer test', class_name: null, status: 'active', source: 'linked',
        first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
        deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
        tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })

    renderAt('canon-viewer')

    expect(screen.queryByRole('button', { name: 'Unlink managed case' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Promote to managed case' })).not.toBeInTheDocument()
  })

  it('keeps unlink disabled locally when cache refresh fails after success', async () => {
    const { useCanonicalCase, useCanonicalRuns, useSuite } = await import('@/hooks/useSuites')
    const canonical = {
      id: 'canon-refresh', project_id: 'proj-1', test_suite_id: 'suite-1',
      test_suite_name: 'Regression', test_fingerprint: 'fingerprint-refresh',
      test_name: 'refresh test', class_name: null, status: 'active', source: 'linked',
      first_seen_run_id: null, last_seen_run_id: null, last_seen_test_case_id: null,
      deleted_at_run_id: null, managed_test_case_id: 'managed-1', review_tag: null,
      tags: null, run_count: 0, created_at: '2026-05-17T10:00:00Z', updated_at: null,
    }
    const mutateCanonical = vi.fn().mockRejectedValue(new Error('cache unavailable'))
    ;(useCanonicalCase as ReturnType<typeof vi.fn>).mockReturnValue({
      data: canonical,
      isLoading: false,
      mutate: mutateCanonical,
    })
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useCanonicalRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    })
    ;(suitesService.unlinkManagedCase as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...canonical,
      managed_test_case_id: null,
    })

    renderAt('canon-refresh')
    fireEvent.click(screen.getByRole('button', { name: 'Unlink managed case' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Reason/ }), {
      target: { value: 'Incorrect mapping' },
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Unlink' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Unlink managed case' })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('managed link was removed')
    expect(suitesService.unlinkManagedCase).toHaveBeenCalledTimes(1)
  })
})
