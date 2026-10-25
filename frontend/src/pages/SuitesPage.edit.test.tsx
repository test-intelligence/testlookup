/**
 * W13 TCL-07 on `/suites`: Edit opens the suite's form IN THE PAGE, under its
 * row (the Delete strip's place: opening one closes the other), for a QA
 * engineer and up (`PATCH /api/v1/suites/{id}` is QA_ENGINEER); a tester or a
 * viewer is offered no Edit. A suite the aggregate lists as `empty` (no run
 * reports it yet) reads "no run" in the four run cells and still names its owner.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuitesPage from './SuitesPage'
import { useAuthStore } from '@/store/authStore'
import type { TestSuite } from '@/types/suites'

const SUITES: TestSuite[] = [
  {
    id: 'suite-a', project_id: 'p1', name: 'auth-api', description: 'Login flows', is_default: false,
    tags: null, test_case_count: 3, created_at: '2026-05-13T00:00:00Z', updated_at: '2026-05-13T00:00:00Z',
  },
  {
    id: 'suite-d', project_id: 'p1', name: 'All Tests', description: null, is_default: true,
    tags: null, test_case_count: 9, created_at: '2026-05-13T00:00:00Z', updated_at: '2026-05-13T00:00:00Z',
  },
]

vi.mock('@/hooks/useSuites', () => ({
  useSuites: () => ({ data: { items: SUITES, total: SUITES.length }, isLoading: false, error: undefined }),
  refreshSuites: vi.fn(),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (s: object) => unknown) => selector({
    activeProject: { id: 'p1', name: 'GoogleSearch' },
    activeProjectId: 'p1',
    projects: [{ id: 'p1', name: 'GoogleSearch' }],
  }),
}))
const aggregateRows = new Map<string, Record<string, unknown>>()
vi.mock('./suite/useSuiteAggregates', () => ({
  useSuiteAggregates: () => ({ byName: aggregateRows, error: undefined, isLoading: false, loaded: true }),
}))
vi.mock('@/services/suitesService', () => ({
  suitesService: { create: vi.fn(), setDefault: vi.fn(), remove: vi.fn() },
}))
vi.mock('@/services/http', () => ({ patchData: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

function renderPage() {
  return render(<MemoryRouter><SuitesPage /></MemoryRouter>)
}

function suiteRow(name: string): HTMLElement {
  const cell = screen.getAllByText(name).find((el) => el.hasAttribute('data-suite-name'))
  if (!cell) throw new Error(`no row for ${name}`)
  return cell.closest('tr') as HTMLElement
}

describe('SuitesPage Edit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    aggregateRows.clear()
  })

  it('a QA engineer is offered Edit on every suite; a tester and a viewer are not', () => {
    signInAs('QA_ENGINEER')
    const { unmount } = renderPage()
    expect(screen.getByRole('button', { name: 'Edit auth-api' })).toHaveAttribute('title', 'Edit')
    expect(screen.getByRole('button', { name: 'Edit All Tests' })).toBeInTheDocument()
    unmount()

    for (const role of ['TESTER', 'VIEWER']) {
      signInAs(role)
      const view = renderPage()
      expect(screen.queryByTitle('Edit')).toBeNull()
      view.unmount()
    }
  })

  it('opens the form under the row, prefilled, and opening it closes the Delete strip', () => {
    signInAs('QA_LEAD')
    renderPage()
    fireEvent.click(within(suiteRow('auth-api')).getByTitle('Delete'))
    expect(screen.getByText(/Test cases must already be moved out/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Edit auth-api' }))
    expect(screen.queryByText(/Test cases must already be moved out/)).toBeNull()
    const form = screen.getByRole('region', { name: 'Edit suite' })
    expect(form.closest('tr')).toHaveAttribute('data-suite-edit-row', 'suite-a')
    expect((within(form).getByLabelText('Name') as HTMLInputElement).value).toBe('auth-api')
    expect((within(form).getByLabelText('Description') as HTMLInputElement).value).toBe('Login flows')
    // Opening the row spans every column of the table (9 with the run columns).
    expect(form.closest('td')).toHaveAttribute('colspan', '9')
  })

  it('a half-typed edit asks before Delete takes its place', () => {
    signInAs('QA_LEAD')
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Edit auth-api' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'auth-api v2' } })
    fireEvent.click(within(suiteRow('auth-api')).getByTitle('Delete'))

    expect(screen.getByText(/Discard your changes to/)).toBeInTheDocument()
    expect(screen.queryByText(/Test cases must already be moved out/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Edit suite' })).toBeNull()
    expect(screen.getByText(/Test cases must already be moved out/)).toBeInTheDocument()
  })

  it('an empty aggregate reads "no run" in the run cells and still names its owner', () => {
    signInAs('QA_ENGINEER')
    aggregateRows.set('auth-api', {
      suite_name: 'auth-api', test_count: 0, passed_count: 0, failed_count: 0, last_run_at: null,
      last_run_id: null, pass_rate: null, total_executions: 0, empty: true, suite_id: 'suite-a',
      owner_full_name: 'Lee Lead', owner_email: 'lee@example.invalid', owner_is_fallback: false,
    })
    renderPage()
    const row = suiteRow('auth-api')
    expect(within(row).getAllByTitle('No run has reported this suite')).toHaveLength(4)
    expect(row.querySelector('[data-col="owner"]')).toHaveTextContent('Lee Lead')
    expect(row.querySelector('[data-col="failing"]')).toBeNull()
  })
})
