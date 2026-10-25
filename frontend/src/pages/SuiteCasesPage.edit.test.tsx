/**
 * W13 TCL-07 on `/suites/:suiteId`: the page header's **Edit suite** (QA
 * engineer and up) opens the suite's form right under the header, in the
 * page; Save sends only the changed fields and closes it. A tester is
 * offered no Edit suite.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuiteCasesPage from './SuiteCasesPage'

const SUITE = {
  id: 'suite-a', project_id: 'proj-1', name: 'Login Suite', description: 'Smoke', tags: ['auth'],
  is_default: false, test_case_count: 0, created_at: '2026-05-15T10:00:00Z', updated_at: null,
}

vi.mock('@/hooks/useSuites', () => ({
  useSuite: () => ({ data: SUITE, isLoading: false, error: null }),
  useSuites: () => ({ data: { items: [SUITE], total: 1 } }),
  useSuiteTestCases: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  refreshSuites: vi.fn(),
}))
vi.mock('@/hooks/useMetrics', () => ({
  useSuiteDetail: () => ({ data: undefined, isLoading: false, error: null }),
}))
vi.mock('./SuiteDetailPage', () => ({ SuiteChartsPanel: () => <div data-testid="suite-charts" /> }))
vi.mock('@/hooks/useExecutionMetrics', () => ({
  useSuiteExecutionHistory: () => ({ data: undefined, isLoading: false, error: undefined, mutate: vi.fn() }),
}))
const hasRole = vi.fn()
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ hasRole: (role: string) => hasRole(role), isAdmin: false, canManageUsers: false, canAccessManagement: false }),
}))
vi.mock('@/services/suitesService', () => ({
  suitesService: { linkCanonicalToSuite: vi.fn(), bulkLinkCanonicals: vi.fn() },
}))
const patchData = vi.fn()
vi.mock('@/services/http', () => ({ patchData: (...args: unknown[]) => patchData(...args) }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/suite-a']}>
      <Routes>
        <Route path="/suites/:suiteId" element={<SuiteCasesPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SuiteCasesPage Edit suite', () => {
  beforeEach(() => {
    hasRole.mockReset()
    patchData.mockReset()
  })

  it('a QA engineer edits the suite under the header and Save closes the form', async () => {
    hasRole.mockImplementation((role: string) => ['VIEWER', 'TESTER', 'QA_ENGINEER'].includes(role))
    patchData.mockResolvedValue({ ...SUITE, tags: ['auth', 'smoke'] })
    renderPage()
    expect(screen.queryByRole('region', { name: 'Edit suite' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Edit suite' }))
    const form = screen.getByRole('region', { name: 'Edit suite' })
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Login Suite')
    expect((screen.getByLabelText('Tags') as HTMLInputElement).value).toBe('auth')
    // Right under the header: the form comes before the page's toolbar row.
    const toolbar = document.querySelector('[data-toolbar]') as HTMLElement
    expect(form.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Tags'), { target: { value: 'auth, smoke' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Edit suite' })).toBeNull())
    expect(patchData).toHaveBeenCalledWith('/api/v1/suites/suite-a', { tags: ['auth', 'smoke'] }, { suppressToast: true })
  })

  it('a tester is offered no Edit suite', () => {
    hasRole.mockImplementation((role: string) => ['VIEWER', 'TESTER'].includes(role))
    renderPage()
    expect(screen.getByText('Smoke')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit suite' })).toBeNull()
  })
})
