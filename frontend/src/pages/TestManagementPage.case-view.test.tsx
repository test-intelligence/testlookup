/**
 * One test case view for every row (owner review, 2026-10-10).
 *
 * A click on a Test Management row did one of four things depending on data
 * the reader could not see: an authored case opened the side panel, an
 * automation row left the page for ONE run's result (/runs/<run>/tests/<id>),
 * an automation row with only a canonical id left for a third page, and one
 * with neither raised a toast. Leaving the page also lost the list's search,
 * filters and page. Every row now opens the same panel in place; the rich
 * pages are explicit links in it -- for authored cases linked to automation too.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { testManagementService } from '@/services/testManagementService'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))

// The evidence-gap lists under the table have tests of their own; here they would only
// make requests (src/test/networkGuard.ts).
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({
  useNow: () => Date.parse('2026-10-10T12:00:00Z'),
}))

vi.mock('@/hooks/useDataFreshness', () => ({
  useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z'),
}))

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x',
    project_id: 'project-1',
    title: 'x',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    status: 'active',
    version: 1,
    is_automated: false,
    automation_status: 'manual',
    ai_generated: false,
    allowed_actions: ['deprecate'],
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-09T00:00:00Z',
    ...overrides,
  }
}

const AUTHORED_MANUAL = base({ id: 'm-1', title: 'Verify refund email', source: 'managed' })
const AUTHORED_LINKED = base({
  id: 'm-2', title: 'Verify checkout with card', source: 'managed', is_automated: true,
  automation_status: 'automated', latest_run_id: 'run-2', latest_test_case_id: 'tc-2',
  canonical_test_case_id: 'canon-2', last_execution_status: 'PASSED',
  last_executed_at: '2026-10-10T09:00:00Z',
})
const AUTOMATION = base({
  id: 'tc-9', title: 'testAuthenticationCase04', source: 'automation', test_type: 'automation',
  is_automated: true, automation_status: 'automated', suite_name: 'AuthSuite',
  feature_area: 'com.qa.auth.AuthSuiteTest', owner: 'auth-team',
  latest_run_id: 'run-9', latest_test_case_id: 'tc-9', canonical_test_case_id: 'canon-9',
  last_execution_status: 'FAILED', last_executed_at: '2026-10-10T11:00:00Z',
})
const AUTOMATION_BARE = base({
  id: 'tc-0', title: 'testOrphanCase', source: 'automation', test_type: 'automation',
  is_automated: true, automation_status: 'automated',
})

function page(items: ManagedTestCase[]): PaginatedResponse<ManagedTestCase> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 }
}

/** The router's query string, for the ?case= tests. */
function Location() {
  return <output data-testid="location">{useLocation().search}</output>
}

function renderTab(entry = '/test-management') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/test-management" element={<><TestCasesTab projectId="project-1" lifecycleV2 /><Location /></>} />
        <Route path="/runs/:runId/tests/:testId" element={<p>RUN RESULT PAGE</p>} />
        <Route path="/canonical-test-cases/:id" element={<p>RUN HISTORY PAGE</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Click a row in the list; the case panel docks beside it, named by the case. */
function openRow(title: string) {
  fireEvent.click(within(screen.getByRole('table')).getByText(title))
  return screen.getByRole('complementary', { name: title })
}

describe('Test Management: one case view for every row', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    vi.mocked(useTestCases).mockImplementation(() => ({
      data: page([AUTHORED_MANUAL, AUTHORED_LINKED, AUTOMATION, AUTOMATION_BARE]),
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestCases>))
  })

  it('opens an automation row in the panel instead of leaving for one run', () => {
    renderTab()
    const panel = openRow('testAuthenticationCase04')

    expect(screen.queryByText('RUN RESULT PAGE')).not.toBeInTheDocument()
    expect(within(panel).getByTestId('tm-case-source')).toHaveTextContent('From automation')
    expect(within(panel).getByRole('link', { name: 'Open latest result →' }))
      .toHaveAttribute('href', '/runs/run-9/tests/tc-9')
    expect(within(panel).getByRole('link', { name: 'Run history →' }))
      .toHaveAttribute('href', '/canonical-test-cases/canon-9')
    // What the result carries, not catalog placeholders.
    expect(within(panel).getByText('AuthSuite')).toBeInTheDocument()
    expect(within(panel).getByText('com.qa.auth.AuthSuiteTest')).toBeInTheDocument()
    expect(within(panel).getByText('auth-team')).toBeInTheDocument()
    expect(within(panel).getByTestId('tm-case-promote')).toBeInTheDocument()
    // Tabs that need a catalog record are not offered before promotion.
    expect(within(panel).queryByRole('button', { name: /Comments/ })).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Lifecycle/ })).not.toBeInTheDocument()
  })

  it('opens an automation row with no stored execution instead of a toast', () => {
    renderTab()
    const panel = openRow('testOrphanCase')

    expect(within(panel).getByTestId('tm-case-no-result')).toBeInTheDocument()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('gives an authored case linked to automation the same result links', () => {
    renderTab()
    const panel = openRow('Verify checkout with card')

    expect(within(panel).getByTestId('tm-case-source')).toHaveTextContent('Authored')
    expect(within(panel).getByRole('link', { name: 'Open latest result →' }))
      .toHaveAttribute('href', '/runs/run-2/tests/tc-2')
    expect(within(panel).getByRole('button', { name: /Comments/ })).toBeInTheDocument()
    expect(within(panel).queryByTestId('tm-case-promote')).not.toBeInTheDocument()
  })

  it('shows no result block for a purely manual case', () => {
    renderTab()
    const panel = openRow('Verify refund email')

    expect(within(panel).queryByTestId('tm-case-latest-result')).not.toBeInTheDocument()
    expect(within(panel).queryByTestId('tm-case-no-result')).not.toBeInTheDocument()
  })

  it('leaves for the run result only when the reader asks to', () => {
    renderTab()
    const panel = openRow('testAuthenticationCase04')

    fireEvent.click(within(panel).getByRole('link', { name: 'Open latest result →' }))

    expect(screen.getByText('RUN RESULT PAGE')).toBeInTheDocument()
  })

  // HON-07: the box promised "ID, title, tags, owner, or requirement" over a
  // title-only search. The API now matches title, description, tags and the
  // id prefix; owner and requirement are not searched, so they are not named.
  it('names only the fields the case search matches', () => {
    renderTab()
    const search = screen.getByRole('searchbox', { name: 'Search cases' })
    expect(search).toHaveAttribute('placeholder', 'Search by title, tags or ID…')
    expect(search.getAttribute('placeholder')).not.toMatch(/owner|requirement/i)
  })
})

describe('Test Management: the case panel docks beside the list (master-detail)', () => {
  // It was a modal pinned to the top-right corner over a backdrop: reading the
  // next case meant closing this one first (owner review, 2026-10-12).
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    vi.mocked(useTestCases).mockImplementation(() => ({
      data: page([AUTHORED_MANUAL, AUTHORED_LINKED, AUTOMATION, AUTOMATION_BARE]),
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestCases>))
  })

  const layout = () => document.querySelector('[data-cases-layout]')?.getAttribute('data-cases-layout')
  const rowOf = (title: string) => within(screen.getByRole('table')).getByText(title).closest('tr') as HTMLElement

  it('opens beside the list: no dialog, no backdrop, the list still there and the open row marked', () => {
    renderTab()
    openRow('Verify refund email')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(layout()).toBe('split')
    expect(screen.getByRole('table')).toBeVisible()
    expect(rowOf('Verify refund email')).toHaveAttribute('aria-current', 'true')
    expect(rowOf('Verify checkout with card')).not.toHaveAttribute('aria-current')
    // Beside the panel the list drops Owner (the panel shows it) to keep titles readable.
    expect(within(screen.getByRole('table')).queryByText('Owner')).toBeNull()
  })

  it('a click on another row swaps the case in place; a click on the open row folds it', () => {
    renderTab()
    openRow('Verify refund email')
    fireEvent.click(within(screen.getByRole('table')).getByText('Verify checkout with card'))
    expect(screen.getAllByRole('complementary')).toHaveLength(1)
    expect(screen.getByRole('complementary', { name: 'Verify checkout with card' })).toBeInTheDocument()

    fireEvent.click(within(screen.getByRole('table')).getByText('Verify checkout with card'))
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(layout()).toBe('list')
    expect(within(screen.getByRole('table')).getByText('Owner')).toBeInTheDocument()
  })

  it('previous / next step through the page without going back to the list', () => {
    renderTab()
    const first = openRow('Verify refund email')
    expect(within(first).getByTestId('tm-case-position')).toHaveTextContent('1 of 4')
    expect(within(first).getByRole('button', { name: 'Previous case' })).toBeDisabled()

    fireEvent.click(within(first).getByRole('button', { name: 'Next case' }))
    const second = screen.getByRole('complementary', { name: 'Verify checkout with card' })
    expect(within(second).getByTestId('tm-case-position')).toHaveTextContent('2 of 4')
    expect(rowOf('Verify checkout with card')).toHaveAttribute('aria-current', 'true')

    fireEvent.click(within(second).getByRole('button', { name: 'Previous case' }))
    expect(screen.getByRole('complementary', { name: 'Verify refund email' })).toBeInTheDocument()
  })

  it('Esc closes the case, but not while the reader types in a field', () => {
    renderTab()
    openRow('Verify refund email')
    fireEvent.keyDown(screen.getByPlaceholderText('Search by title, tags or ID…'), { key: 'Escape' })
    expect(screen.getByRole('complementary', { name: 'Verify refund email' })).toBeInTheDocument()

    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('Expand gives the case the whole row; the list comes back beside it', () => {
    renderTab()
    const panel = openRow('Verify checkout with card')
    fireEvent.click(within(panel).getByRole('button', { name: 'Expand the case to full width' }))
    expect(layout()).toBe('expanded')
    expect(document.querySelector('[data-primary]')).not.toBeVisible()

    fireEvent.click(within(panel).getByRole('button', { name: 'Show the list beside the case' }))
    expect(layout()).toBe('split')
    expect(document.querySelector('[data-primary]')).toBeVisible()
  })

  it('the open case rides the URL, and a link with ?case= reopens it', () => {
    const { unmount } = renderTab()
    openRow('Verify checkout with card')
    expect(screen.getByTestId('location')).toHaveTextContent('case=m-2')
    fireEvent.click(within(screen.getByRole('complementary')).getByRole('button', { name: 'Close case' }))
    expect(screen.getByTestId('location')).not.toHaveTextContent('case=')
    unmount()

    renderTab('/test-management?case=tc-9')
    expect(screen.getByRole('complementary', { name: 'testAuthenticationCase04' })).toBeInTheDocument()
    expect(rowOf('testAuthenticationCase04')).toHaveAttribute('aria-current', 'true')
  })

  it('a link to a case the page does not show is fetched; a stale one is dropped', async () => {
    const far = base({ id: 'far-1', title: 'Verify archive export', source: 'managed' })
    const getCase = vi.spyOn(testManagementService, 'getCase').mockResolvedValueOnce(far)
    const { unmount } = renderTab('/test-management?case=far-1')
    expect(await screen.findByRole('complementary', { name: 'Verify archive export' })).toBeInTheDocument()
    expect(getCase).toHaveBeenCalledWith('far-1')
    unmount()

    getCase.mockRejectedValueOnce(new Error('404'))
    renderTab('/test-management?case=gone-1')
    await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('case='))
    expect(screen.queryByRole('complementary')).toBeNull()
    getCase.mockRestore()
  })
})
