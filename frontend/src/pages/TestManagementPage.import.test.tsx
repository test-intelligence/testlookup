/**
 * W13 TCL-04 (contract §2.5, G-D): the Cases toolbar's **Import** and its slot.
 *
 *  - TESTER+ with one project (the `canWrite` rule): none for a viewer, none in
 *    All Projects; icon-only beside an open case, like Export;
 *  - it opens G-E's `ImportCasesSection` in the page's top slot (`'import'`),
 *    one form at a time with New test case / AI Generate: a typed New test case
 *    asks before Import replaces it;
 *  - after an apply that created cases, both reads refresh (the cases roll and
 *    the library health).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLibraryHealth } from '@/hooks/useTestLibrary'
import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { testLibraryService } from '@/services/testLibraryService'
import { useAuthStore, type User } from '@/store/authStore'
import type { TestCaseImportResponse } from '@/types/test-library'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks())
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/testLibraryService', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/services/testLibraryService')>()
  return { ...real, testLibraryService: { ...real.testLibraryService, importCases: vi.fn() } }
})

const CASE: ManagedTestCase = {
  id: 'case-1', project_id: 'project-1', title: 'Verify refund email', test_type: 'functional', priority: 'medium',
  severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
  automation_status: 'not_automated', ai_generated: false, source: 'managed', allowed_actions: ['activate'],
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
}

function answer(dryRun: boolean): TestCaseImportResponse {
  return {
    project_id: 'project-1', dry_run: dryRun, file_name: 'cases.csv', format: 'csv',
    headers: { recognised: ['Title'], ignored: [], unknown: [] },
    summary: { rows: 1, create: 1, duplicate: 0, invalid: 0, created: dryRun ? 0 : 1 },
    rows: [{ row: 2, outcome: dryRun ? 'create' : 'created', title: 'Imported case', reasons: [], warnings: [], case_id: dryRun ? null : 'case-9' }],
  }
}

function signIn(role: string) {
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role,
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

function renderTab(projectId: string | null = 'project-1') {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2 />} />
      </Routes>
    </MemoryRouter>,
  )
}

const importButton = () => screen.queryByRole('button', { name: 'Import' })
const section = () => screen.queryByRole('region', { name: 'Import test cases' })
const mutateCases = vi.fn()
const mutateHealth = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
  const page: PaginatedResponse<ManagedTestCase> = { items: [CASE], total: 1, page: 1, size: 25, pages: 1 }
  vi.mocked(useTestCases).mockImplementation(() => (
    { data: page, error: undefined, isLoading: false, isValidating: false, mutate: mutateCases } as unknown as ReturnType<typeof useTestCases>
  ))
  vi.mocked(useLibraryHealth).mockReturnValue(
    { data: undefined, error: undefined, isLoading: false, mutate: mutateHealth } as unknown as ReturnType<typeof useLibraryHealth>,
  )
})
afterEach(() => { act(() => useAuthStore.setState({ user: null })) })

describe('/test-management Cases: Import (W13 TCL-04)', () => {
  it.each(['TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN'])('%s with a project gets Import beside Export; it opens the section', (role) => {
    signIn(role)
    renderTab()
    const button = importButton() as HTMLElement
    expect(button).toHaveTextContent('Import')
    // Beside Export, before AI Generate.
    const toolbar = button.parentElement as HTMLElement
    const names = within(toolbar).getAllByRole('button').map((b) => b.getAttribute('aria-label'))
    expect(names.indexOf('Import')).toBe(names.indexOf('Export') + 1)
    expect(section()).toBeNull()
    fireEvent.click(button)
    expect(section()).not.toBeNull()
    expect(section()?.querySelector('input[type="file"]')).toHaveAttribute('accept', '.csv,.xlsx')
  })

  it('a viewer gets no Import; neither does All Projects', () => {
    signIn('VIEWER')
    const view = renderTab()
    expect(importButton()).toBeNull()
    view.unmount()
    signIn('QA_LEAD')
    renderTab(null)
    expect(importButton()).toBeNull()
  })

  it('one form at a time: a typed New test case asks before Import replaces it', () => {
    signIn('TESTER')
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'New test case from catalog toolbar' }))
    const create = screen.getByRole('region', { name: 'New Test Case' })
    fireEvent.change(within(create).getByLabelText('Title *'), { target: { value: 'Guest checkout keeps the basket' } })
    fireEvent.click(importButton() as HTMLElement)
    expect(within(create).getByText(/Discard your changes to/)).toBeInTheDocument()
    expect(section()).toBeNull()
    fireEvent.click(within(create).getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: 'New Test Case' })).toBeNull()
    expect(section()).not.toBeNull()
  })

  it('after an apply that created cases, the cases roll and the library health refresh', async () => {
    signIn('TESTER')
    vi.mocked(testLibraryService.importCases).mockImplementation(async (_p, _f, dryRun) => answer(dryRun))
    renderTab()
    fireEvent.click(importButton() as HTMLElement)
    const input = section()?.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['Title\nImported case\n'], 'cases.csv', { type: 'text/csv' })] } })
    await waitFor(() => expect(testLibraryService.importCases).toHaveBeenCalledWith('project-1', expect.any(File), true))
    fireEvent.click(await screen.findByRole('button', { name: 'Import 1 case' }))
    await waitFor(() => expect(testLibraryService.importCases).toHaveBeenCalledWith('project-1', expect.any(File), false))
    await waitFor(() => expect(mutateCases).toHaveBeenCalled())
    expect(mutateHealth).toHaveBeenCalled()
  })
})
