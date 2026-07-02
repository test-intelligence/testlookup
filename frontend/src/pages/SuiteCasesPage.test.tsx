import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import SuiteCasesPage from './SuiteCasesPage'

// Mock the hooks so the page renders against scripted state without
// needing an SWR cache or HTTP fetcher. Same pattern as
// ``TestCasePage.test.tsx`` / ``CanonicalDetailPage.test.tsx``.
vi.mock('@/hooks/useSuites', () => ({
  useSuite: vi.fn(),
  useSuites: vi.fn(),
  useSuiteTestCases: vi.fn(),
  refreshSuites: vi.fn(),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({
    hasRole: () => true,
    isAdmin: false,
    canManageUsers: false,
    canAccessManagement: false,
  })),
}))

vi.mock('@/services/suitesService', () => ({
  suitesService: {
    linkCanonicalToSuite: vi.fn(),
    bulkLinkCanonicals: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const SUITE_A = {
  id: 'suite-a',
  project_id: 'proj-1',
  name: 'Login Suite',
  description: 'Smoke',
  tags: null,
  is_default: false,
  test_case_count: 2,
  created_at: '2026-05-15T10:00:00Z',
  updated_at: null,
}

const SUITE_B = {
  id: 'suite-b',
  project_id: 'proj-1',
  name: 'Regression Suite',
  description: null,
  tags: null,
  is_default: true,
  test_case_count: 0,
  created_at: '2026-05-15T10:00:00Z',
  updated_at: null,
}

function _case(id: string, name: string) {
  return {
    id,
    project_id: 'proj-1',
    test_suite_id: 'suite-a',
    test_suite_name: 'Login Suite',
    test_fingerprint: `fp-${id}`,
    test_name: name,
    class_name: 'auth.LoginTest',
    status: 'active' as const,
    source: 'execution' as const,
    first_seen_run_id: null,
    last_seen_run_id: null,
    last_seen_test_case_id: null,
    deleted_at_run_id: null,
    managed_test_case_id: null,
    review_tag: null,
    tags: null,
    run_count: null,
    created_at: '2026-05-15T10:00:00Z',
    updated_at: null,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/suite-a']}>
      <Routes>
        <Route path="/suites" element={<div>Suites List</div>} />
        <Route path="/suites/:suiteId" element={<SuiteCasesPage />} />
        <Route
          path="/canonical-test-cases/:canonicalId"
          element={<div>Canonical Detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SuiteCasesPage bulk move', () => {
  beforeEach(async () => {
    const { useSuite, useSuites, useSuiteTestCases } = await import('@/hooks/useSuites')
    ;(useSuite as ReturnType<typeof vi.fn>).mockReturnValue({
      data: SUITE_A,
      isLoading: false,
      error: null,
    })
    ;(useSuites as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [SUITE_A, SUITE_B], total: 2 },
    })
    ;(useSuiteTestCases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [_case('c1', 'test_login'), _case('c2', 'test_logout')],
        total: 2,
      },
      isLoading: false,
    })
  })

  it('bulk action bar is hidden until a row is selected', () => {
    renderPage()
    expect(screen.queryByRole('region', { name: /Bulk actions/i })).toBeNull()
  })

  it('renders the catalog → analytics link in the header', () => {
    renderPage()
    const analyticsLink = screen.getByRole('link', { name: /Open analytics/i })
    // Spaces in the suite name must be URL-encoded so the destination
    // query param is parseable; pin the exact href so a future rename
    // of the encoding helper doesn't silently break the deep link.
    expect(analyticsLink).toHaveAttribute(
      'href',
      '/coverage/suite?name=Login%20Suite',
    )
  })

  it('per-row checkbox reveals the action bar with the right count', () => {
    renderPage()
    const c1Checkbox = screen.getByLabelText('Select test_login')
    fireEvent.click(c1Checkbox)
    expect(screen.getByRole('region', { name: /Bulk actions/i })).toBeInTheDocument()
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })

  it('select-all checkbox toggles every row', () => {
    renderPage()
    const selectAll = screen.getByLabelText('Select all')
    fireEvent.click(selectAll)
    // Now both per-row boxes should be checked + counter at 2.
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    // And the header checkbox should report "Deselect all" after flipping.
    expect(screen.getByLabelText('Deselect all')).toBeInTheDocument()
  })

  it('Clear button collapses the action bar', () => {
    renderPage()
    fireEvent.click(screen.getByLabelText('Select test_login'))
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Clear/i }))
    expect(screen.queryByRole('region', { name: /Bulk actions/i })).toBeNull()
  })

  it('bulk Move opens the modal and posts the selected ids to the service', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.bulkLinkCanonicals as ReturnType<typeof vi.fn>).mockResolvedValue({
      moved: 2,
      skipped_already_in_target: 0,
      missing_ids: [],
    })

    renderPage()
    fireEvent.click(screen.getByLabelText('Select all'))
    // Open the bulk modal via the action-bar's "Move" button. There's
    // also a per-row Move button — disambiguate by the count suffix on
    // the bar's button label is the same text "Move", so use the bar's
    // region as the scope.
    const bar = screen.getByRole('region', { name: /Bulk actions/i })
    const barButton = bar.querySelector('button')
    if (barButton === null) throw new Error('Bulk actions bar has no button')
    fireEvent.click(barButton)

    // The bulk modal heading reflects the selection count.
    expect(
      await screen.findByRole('heading', { name: /Move 2 test cases/i }),
    ).toBeInTheDocument()

    // Submit moves everything to the default candidate (suite-b is the
    // first non-self target the modal pre-selects).
    fireEvent.click(screen.getByRole('button', { name: /^Move 2$/ }))

    await waitFor(() => {
      expect(suitesService.bulkLinkCanonicals).toHaveBeenCalledWith(
        'suite-b',
        expect.arrayContaining(['c1', 'c2']),
      )
    })
  })
})
