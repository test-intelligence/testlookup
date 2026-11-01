/**
 * W27a PAN-04 (`W27a-contract.md` §2.4, assertion 3): /defects' forms open IN
 * THE PAGE, with the REAL row menu and forms (no stub of `DefectRowActions`).
 *
 *  - Defect actions › Change status… / Assign… / Link Jira issue… open that
 *    form in a full-width row RIGHT UNDER the defect's row
 *    (`td[colspan="8"]`), never inside the Actions cell;
 *  - one form per table: another row's form while one has a choice made asks
 *    first; a clean one is replaced at once;
 *  - Save still sends exactly `{ resolution_status }` and closes the form;
 *  - New defect opens right under the page header;
 *  - no `aria-modal`, no `role="dialog"`, no portal.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DefectsPage from './DefectsPage'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(async () => ({})),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
const rows = vi.hoisted(() => ({ items: [] as Record<string, unknown>[], mutate: vi.fn(async () => undefined) }))
vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary: d, useTrendData: d, useFlakyTests: d, useFailureCategories: d, useTopFailing: d,
    useCoverage: d, useSuiteDetail: d, useAiSummary: d,
    useDefects: () => ({ data: { items: rows.items, total: rows.items.length, pages: 1 }, isLoading: false, mutate: rows.mutate }),
    refreshDefects: vi.fn(async () => undefined),
  }
})
vi.mock('@/hooks/useDefectInsights', () => ({
  useDefectTrend: () => ({ data: undefined, isLoading: false }),
  useDefectEscapeRate: () => ({ data: undefined, isLoading: false }),
  refreshDefectInsights: vi.fn(async () => undefined),
}))
vi.mock('@/hooks/useIntegrationsConfig', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useIntegrationsConfig')>(),
  useIntegrationsConfig: () => ({ config: undefined, error: undefined, isLoading: true }),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: () => ({ options: [], isLoading: false }) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string; activeProject: { name: string } }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } }),
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] }, isLoading: false }) }))
const updateDefect = vi.hoisted(() => vi.fn())
vi.mock('@/services/defectService', () => ({ updateDefect }))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: () => ({ metadata: undefined, isLoading: true, isError: false }),
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

const defect = (id: string, title: string) => ({
  id, project_id: 'proj-1', title, test_name: `test ${id}`, severity: 'LOW', failure_category: 'FLAKY',
  resolution_status: 'OPEN', created_at: '2026-10-15T00:00:00Z',
})

function renderDefects() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/defects']}>
        <Routes>
          <Route path="/defects" element={<DefectsPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const rowEl = (id: string) => document.querySelector(`tr[data-defect-row="${id}"]`) as HTMLElement
function choose(id: string, item: string) {
  fireEvent.click(within(rowEl(id)).getByRole('button', { name: 'Defect actions' }))
  fireEvent.click(screen.getByRole('menuitem', { name: item }))
}
function expectNoModal() {
  expect(document.querySelector('[aria-modal]')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
}

beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { id: 'eng', role: 'QA_ENGINEER' } as never })
  rows.items = [defect('a', 'Checkout fails'), defect('b', 'Refund twice')]
})

describe('/defects row forms open under their row (W27 PAN-04)', () => {
  it.each([
    ['Change status…', 'Change status', 'defect-status-dialog'],
    ['Assign…', 'Assign defect', 'assign-defect-dialog'],
    ['Link Jira issue…', 'Link a Jira issue', 'link-jira-dialog'],
  ])('%s renders its form in td[colspan="8"] right under the row, not in the Actions cell', async (item, name, testId) => {
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    choose('a', item)
    const form = screen.getByRole('region', { name })
    expect(form).toHaveAttribute('data-testid', testId)
    const formRow = form.closest('tr') as HTMLElement
    expect(formRow).toHaveAttribute('data-in-page-row')
    expect(formRow.previousElementSibling).toBe(rowEl('a'))
    expect(formRow.querySelector('td')).toHaveAttribute('colspan', '8')
    expect(rowEl('a').contains(form)).toBe(false)
    expect(screen.getByRole('table', { name: 'Defects' }).contains(form)).toBe(true)
    expectNoModal()
  })

  it("another row's form while a status is picked asks first; Discard changes opens it; a clean one replaces at once", async () => {
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    choose('a', 'Change status…')
    const status = screen.getByRole('region', { name: 'Change status' })
    fireEvent.click(within(status).getByRole('radio', { name: 'In progress' }))

    choose('b', 'Assign…')
    expect(within(status).getByRole('group', { name: 'Discard your changes to "Change status"?' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Assign defect' })).toBeNull()
    fireEvent.click(within(status).getByRole('button', { name: 'Keep editing' }))
    expect(within(status).getByRole('radio', { name: 'In progress' })).toBeChecked()

    choose('b', 'Assign…')
    fireEvent.click(within(status).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'Change status' })).toBeNull()
    const assign = screen.getByRole('region', { name: 'Assign defect' })
    expect((assign.closest('tr') as HTMLElement).previousElementSibling).toBe(rowEl('b'))

    // Clean: at once.
    choose('a', 'Link Jira issue…')
    expect(screen.queryByRole('region', { name: 'Assign defect' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Link a Jira issue' })).toBeInTheDocument()
    expect(document.querySelectorAll('tr[data-in-page-row]')).toHaveLength(1)
    expect(updateDefect).not.toHaveBeenCalled()
  })

  it('Save sends exactly { resolution_status } and the form closes; Cancel and Escape send nothing', async () => {
    updateDefect.mockResolvedValue({
      defect: { ...defect('a', 'Checkout fails'), resolution_status: 'IN_PROGRESS' },
      changed_fields: ['resolution_status'], jira: null, write_back: null,
    })
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })

    choose('a', 'Change status…')
    fireEvent.click(within(screen.getByRole('region', { name: 'Change status' })).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Change status' })).toBeNull()
    choose('a', 'Change status…')
    fireEvent.keyDown(screen.getByRole('radio', { name: 'Open' }), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Change status' })).toBeNull()
    expect(updateDefect).not.toHaveBeenCalled()

    choose('a', 'Change status…')
    const form = screen.getByRole('region', { name: 'Change status' })
    fireEvent.click(within(form).getByRole('radio', { name: 'In progress' }))
    await act(async () => {
      fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    })
    expect(updateDefect).toHaveBeenCalledTimes(1)
    expect(updateDefect).toHaveBeenCalledWith('proj-1', 'a', { resolution_status: 'IN_PROGRESS' }, { suppressToast: true })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Change status' })).toBeNull())
  })
})

describe('/defects New defect opens under the page header (W27 PAN-04)', () => {
  it('right after [data-page-header], before the queue verdict, outside the table', async () => {
    renderDefects()
    await screen.findByRole('table', { name: 'Defects' })
    fireEvent.click(within(document.querySelector('[data-page-header]') as HTMLElement).getByRole('button', { name: 'New defect' }))
    const form = screen.getByRole('region', { name: 'New defect' })
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const verdict = screen.getByRole('region', { name: 'Defect queue verdict' })
    expect(header.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(form.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(form.closest('[data-primary]')).toBeNull()
    expect(document.activeElement).toBe(within(form).getByLabelText(/^Title/))
    expectNoModal()
    fireEvent.click(within(form).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'New defect' })).toBeNull()
  })
})
