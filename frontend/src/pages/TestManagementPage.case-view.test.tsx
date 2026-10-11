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
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))

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

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId="project-1" lifecycleV2 />} />
        <Route path="/runs/:runId/tests/:testId" element={<p>RUN RESULT PAGE</p>} />
        <Route path="/canonical-test-cases/:id" element={<p>RUN HISTORY PAGE</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

function openRow(title: string) {
  fireEvent.click(screen.getByText(title))
  return screen.getByRole('dialog')
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
})
