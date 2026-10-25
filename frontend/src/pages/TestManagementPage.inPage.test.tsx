/**
 * W27 PAN-01: /test-management's case pane on the shared in-page primitives
 * (MasterDetail + DetailPane + useDetailParam). The page looks and behaves as
 * before (its own tests run unedited); what this adds is the deep-link rule:
 * a `?case=` that could walk a request path (`..`, `/`) is never read as a
 * case — no getCase is made and the key is dropped — while a well-formed id
 * still costs exactly one read, through the same service call as before.
 *
 * (The accept here is isDetailToken, not isUuid: the page's existing tests
 * open cases by fixture ids such as `m-2` and `far-1`, which must keep
 * working unedited. Both shapes keep `.` and `/` out of the request path.)
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

// W13 (deliberate set-up): the page's new reads (library health, the owner list, saved views) stubbed idle.
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

const FAR_ID = '3f2b8c1e-9a4d-4e6f-8b2a-1c0d9e8f7a65'

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'active', version: 1, is_automated: false,
    automation_status: 'manual', ai_generated: false, allowed_actions: ['deprecate'],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

const ON_PAGE = base({ id: 'b1c2d3e4-0000-4000-8000-000000000001', title: 'Verify refund email', source: 'managed' })

function Location() {
  return <output data-testid="location">{useLocation().search}</output>
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/test-management" element={<><TestCasesTab projectId="project-1" lifecycleV2 /><Location /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('/test-management ?case= deep links on useDetailParam', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    const page: PaginatedResponse<ManagedTestCase> = { items: [ON_PAGE], total: 1, page: 1, size: 25, pages: 1 }
    vi.mocked(useTestCases).mockImplementation(() => ({
      data: page, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestCases>))
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    ['../../admin', '?tab=cases&case=../../admin'],
    ['a/b', '?tab=cases&case=a%2Fb'],
    ['..', '?tab=cases&case=..'],
  ])('?case=%s makes no getCase call and drops the key', async (_label, search) => {
    const getCase = vi.spyOn(testManagementService, 'getCase').mockResolvedValue(base({ id: 'x', title: 'Never' }))
    renderAt(`/test-management${search}`)
    // Dropped (replace), the rest of the query kept.
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('?tab=cases'))
    expect(getCase).not.toHaveBeenCalled()
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('?case=<uuid> the page does not show makes exactly one getCase, and opens it', async () => {
    const far = base({ id: FAR_ID, title: 'Verify archive export', source: 'managed' })
    const getCase = vi.spyOn(testManagementService, 'getCase').mockResolvedValue(far)
    renderAt(`/test-management?case=${FAR_ID}`)
    expect(await screen.findByRole('complementary', { name: 'Verify archive export' })).toBeInTheDocument()
    expect(getCase).toHaveBeenCalledTimes(1)
    expect(getCase).toHaveBeenCalledWith(FAR_ID)
    expect(screen.getByTestId('location').textContent).toBe(`?case=${FAR_ID}`)
  })

  it('?case=<id> of a case on the page opens it with no read at all', async () => {
    const getCase = vi.spyOn(testManagementService, 'getCase')
    renderAt(`/test-management?case=${ON_PAGE.id}`)
    expect(screen.getByRole('complementary', { name: 'Verify refund email' })).toBeInTheDocument()
    expect(getCase).not.toHaveBeenCalled()
  })

  it('the case pane is still the in-page aside: no dialog, no aria-modal', () => {
    renderAt(`/test-management?case=${ON_PAGE.id}`)
    const pane = screen.getByRole('complementary', { name: 'Verify refund email' })
    expect(pane).toHaveAttribute('data-in-page', 'detail')
    expect(document.querySelector('[data-cases-layout]')).toHaveAttribute('data-master-detail', 'split')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })
})
