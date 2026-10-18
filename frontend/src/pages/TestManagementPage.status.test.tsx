/**
 * W28 TCM-02 / TCM-05 (contract §2.2 frontend 14-15, §2.5 4) on the page.
 *
 *  - the Details toolbar (`Case actions`) renders first in the Details tab,
 *    only with lifecycle v2 on, TESTER+, one project and an authored case; the
 *    review line says whether this project requires review;
 *  - an edited-after-approval case is marked on its row's meta line and in the
 *    pane's subtitle (review on only);
 *  - Plans › Link Suite links the Active (approved / active) manual cases by
 *    default; `Include cases that are not Active` adds the other non-retired
 *    ones; a deprecated / archived case is never linked; a row without
 *    `lifecycle_status` counts as not Active; the toast names what was left
 *    out; nothing Active says how to link the others;
 *  - Plans' items table: `#`, `Test Case`, `Status`, `Ran against`, `Actions`
 *    (Status stays index 2), and the version's steps open in a row under the
 *    item.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import toast from 'react-hot-toast'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAuditLog,
  usePlanItems,
  useTestCaseHistory,
  useTestCases,
  useTestPlans,
} from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import { useAuthStore, type User } from '@/store/authStore'
import type { ManagedTestCase, PaginatedResponse, TestCaseVersion, TestPlan, TestPlanItem } from '@/types/test-management'
import TestManagementPage, { TestCasesTab } from './TestManagementPage'

vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', activeProject: { id: 'project-1', name: 'Project One' } }
  return {
    ALL_PROJECTS_ID: '__ALL__',
    useProjectStore: (selector: (s: typeof state) => unknown) => selector(state),
  }
})
vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: () => true }))
vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
  useTestPlans: vi.fn(),
  usePlanItems: vi.fn(),
  useTestCaseHistory: vi.fn(),
  useUsers: () => ({ data: [] }),
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [] }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/testManagementService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testManagementService')>()
  return {
    ...actual,
    testManagementService: {
      ...actual.testManagementService,
      listSuites: vi.fn(),
      getSuiteCases: vi.fn(),
      addPlanItem: vi.fn(),
    },
  }
})

const service = vi.mocked(testManagementService)

function signIn(role: string) {
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role,
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'high',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', author_id: 'u-author',
    allowed_actions: ['activate', 'request_review'], reason_required_actions: [], review_required: false,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}

const DRAFT = base({ id: 'c-1', title: 'Verify refund email' })
const EDITED = base({
  id: 'c-2', title: 'Sign in with SSO', status: 'active', version: 7, review_required: true,
  edited_after_approval: true, edited_since_approval_version: 6, allowed_actions: ['request_review', 'flag_stale'],
  reason_required_actions: ['flag_stale'],
})
const AUTOMATION = base({ id: 'auto-1', title: 'checkout_spec.ts › pays', source: 'automation', allowed_actions: [] })

function mockCases(items: ManagedTestCase[]) {
  const page: PaginatedResponse<ManagedTestCase> = { items, total: items.length, page: 1, size: 25, pages: 1 }
  vi.mocked(useTestCases).mockImplementation(() => ({
    data: page, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn(),
  } as unknown as ReturnType<typeof useTestCases>))
}

function renderTab({ lifecycleV2 = true, projectId = 'project-1' as string | null } = {}) {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2={lifecycleV2} />} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLTableRowElement
const pane = (title: string) => screen.getByRole('complementary', { name: title })

describe('/test-management: the Status moves in the Details tab (W28)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    signIn('TESTER')
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    vi.mocked(useTestCaseHistory).mockReturnValue(
      { data: undefined, isLoading: false, error: undefined, mutate: vi.fn() } as unknown as ReturnType<typeof useTestCaseHistory>,
    )
    mockCases([DRAFT, EDITED, AUTOMATION])
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
  })

  it('renders the toolbar first in the Details tab with the review line (v2 on, TESTER, one project, authored)', () => {
    renderTab()
    fireEvent.click(row(DRAFT.id))
    const toolbar = within(pane(DRAFT.title)).getByRole('group', { name: 'Case actions' })
    expect(within(toolbar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Make active', 'Request review', 'Edit'])
    expect(within(pane(DRAFT.title)).getByText('Review is optional in this project.')).toBeInTheDocument()
    // First thing above the details.
    const details = toolbar.closest('[data-case-status-control]')?.parentElement as HTMLElement
    expect(details.firstElementChild?.contains(toolbar)).toBe(true)
  })

  it('has no Status moves with lifecycle v2 off, for an automation row, for a viewer, or in All Projects', () => {
    let view = renderTab({ lifecycleV2: false })
    fireEvent.click(row(DRAFT.id))
    expect(within(pane(DRAFT.title)).queryByRole('button', { name: 'Make active' })).toBeNull()
    expect(within(pane(DRAFT.title)).queryByText(/^Review is/)).toBeNull()
    view.unmount()

    view = renderTab()
    fireEvent.click(row(AUTOMATION.id))
    expect(within(pane(AUTOMATION.title)).queryByRole('group', { name: 'Case actions' })).toBeNull()
    view.unmount()

    signIn('VIEWER')
    view = renderTab()
    fireEvent.click(row(DRAFT.id))
    expect(within(pane(DRAFT.title)).queryByRole('group', { name: 'Case actions' })).toBeNull()
    view.unmount()

    signIn('QA_LEAD')
    renderTab({ projectId: null })
    fireEvent.click(row(DRAFT.id))
    expect(within(pane(DRAFT.title)).queryByRole('group', { name: 'Case actions' })).toBeNull()
  })

  it('marks an edited-after-approval case on its row and in the pane, with Request review first', () => {
    renderTab()
    expect(row(EDITED.id)).toHaveTextContent('edited after approval')
    expect(row(DRAFT.id)).not.toHaveTextContent('edited after approval')
    fireEvent.click(row(EDITED.id))
    const editedPane = pane(EDITED.title)
    expect(editedPane.querySelector('[data-edited-after-approval-mark]')).toHaveTextContent('edited after approval')
    expect(editedPane.querySelector('[data-edited-after-approval]')).toHaveTextContent('Edited after approval (since v6; now v7).')
    expect(within(editedPane).getByText('Review is required before Active in this project.')).toBeInTheDocument()
    const toolbar = within(editedPane).getByRole('group', { name: 'Case actions' })
    expect(within(toolbar).getAllByRole('button')[0]).toHaveTextContent('Request review')
  })

  it('hides the edited-after-approval mark when the project no longer requires review', () => {
    mockCases([{ ...EDITED, review_required: false, edited_after_approval: false }])
    renderTab()
    expect(row(EDITED.id)).not.toHaveTextContent('edited after approval')
  })
})

// ── Plans ────────────────────────────────────────────────────────────────

const PLAN: TestPlan = {
  id: 'plan-1', project_id: 'project-1', name: 'Release 2.4 regression', status: 'active', ai_generated: false,
  total_cases: 2, executed_cases: 1, passed_cases: 1, failed_cases: 0, blocked_cases: 0,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
}
const ITEMS: TestPlanItem[] = [
  {
    id: 'item-1', plan_id: 'plan-1', test_case_id: 'c-1aaaaaaa', order_index: 0, execution_status: 'passed',
    executed_at: '2026-10-05T00:00:00Z', executed_case_version: 1, case_version: 2, created_at: '2026-10-01T00:00:00Z',
  },
  {
    id: 'item-2', plan_id: 'plan-1', test_case_id: 'c-2bbbbbbb', order_index: 1, execution_status: 'not_run',
    executed_case_version: null, case_version: 1, created_at: '2026-10-01T00:00:00Z',
  },
]

function suiteRow(id: string, lifecycle: string | undefined, source = 'manual') {
  return {
    id, test_name: id, suite_name: 'Checkout', status: 'passed', duration_ms: null, class_name: null, package_name: null,
    created_at: null, source, ...(lifecycle === undefined ? {} : { lifecycle_status: lifecycle }),
  }
}

function renderPlans() {
  return render(
    <MemoryRouter initialEntries={['/test-management?tab=plans']}>
      <Routes>
        <Route path="/test-management" element={<TestManagementPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function openPlan() {
  renderPlans()
  fireEvent.click(screen.getByText(PLAN.name))
  return await screen.findByRole('table')
}

async function linkSuite(includeNotActive = false) {
  await openPlan()
  fireEvent.click(screen.getByRole('button', { name: 'Link Suite' }))
  const form = await screen.findByRole('region', { name: 'Link Test Suite to Plan' })
  const select = await within(form).findByRole('combobox')
  fireEvent.change(select, { target: { value: 'Checkout' } })
  if (includeNotActive) fireEvent.click(within(form).getByRole('checkbox', { name: 'Include cases that are not Active' }))
  fireEvent.click(within(form).getByRole('button', { name: 'Link Suite' }))
}

describe('/test-management Plans (W28)', () => {
  const history: TestCaseVersion[] = [
    { id: 'v-2', test_case_id: 'c-1aaaaaaa', version: 2, title: 'Refund v2', status: 'active', change_type: 'updated', created_at: '2026-10-06T00:00:00Z', steps: [{ step_number: 1, action: 'New step text', expected_result: 'New' }] },
    { id: 'v-1', test_case_id: 'c-1aaaaaaa', version: 1, title: 'Refund', status: 'active', change_type: 'created', created_at: '2026-10-01T00:00:00Z', steps: [{ step_number: 1, action: 'Old step text', expected_result: 'Old' }] },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    signIn('QA_ENGINEER')
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    mockCases([])
    vi.mocked(useTestPlans).mockReturnValue({
      data: { items: [PLAN], total: 1, page: 1, size: 10, pages: 1 }, isLoading: false, mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestPlans>)
    vi.mocked(usePlanItems).mockReturnValue({ data: ITEMS, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof usePlanItems>)
    vi.mocked(useTestCaseHistory).mockImplementation((id?: string) => ({
      data: id ? history : undefined, isLoading: false, error: undefined, mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTestCaseHistory>))
    service.listSuites.mockResolvedValue([
      { suite_name: 'Checkout', test_count: 5, passed_count: 5, failed_count: 0, last_run_at: null, last_run_id: null, pass_rate: 100 },
    ])
    service.addPlanItem.mockResolvedValue({} as TestPlanItem)
  })
  afterEach(() => {
    act(() => useAuthStore.setState({ user: null }))
  })

  it('the items table: #, Test Case, Status, Ran against, Actions (Status stays the third cell)', async () => {
    const table = await openPlan()
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['#', 'Test Case', 'Status', 'Ran against', 'Actions'])
    const first = within(table).getAllByRole('row')[1]
    expect(within(first).getAllByRole('cell')[2]).toHaveTextContent('passed')
  })

  it('a version button opens that version\'s steps in a row under the item, one at a time', async () => {
    const table = await openPlan()
    fireEvent.click(within(table).getByRole('button', { name: /^v1/ }))
    const versionRow = table.querySelector('[data-plan-item-version-row="item-1"]') as HTMLElement
    expect(versionRow).not.toBeNull()
    expect(versionRow.querySelector('td')).toHaveAttribute('colspan', '5')
    expect(versionRow).toHaveTextContent('Old step text')
    expect(versionRow).not.toHaveTextContent('New step text')
    fireEvent.click(within(table).getByRole('button', { name: /^v1/ }))
    expect(table.querySelector('[data-plan-item-version-row]')).toBeNull()
  })

  it('Link Suite links only the Active cases by default and says how many were left out', async () => {
    service.getSuiteCases.mockResolvedValue({
      items: [
        suiteRow('m-active', 'active'), suiteRow('m-approved', 'approved'), suiteRow('m-draft', 'draft'),
        suiteRow('m-deprecated', 'deprecated'), suiteRow('m-unknown', undefined), suiteRow('auto-1', undefined, 'automation'),
      ],
      total: 6, page: 1, pages: 1, size: 500,
    } as never)
    await linkSuite()
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(service.addPlanItem.mock.calls.map((c) => c[1])).toEqual([{ test_case_id: 'm-active' }, { test_case_id: 'm-approved' }])
    expect(toast.success).toHaveBeenCalledWith('Linked 2 test cases from "Checkout" · 2 not Active left out')
  })

  it('the box adds the non-retired others; a retired case is never linked', async () => {
    service.getSuiteCases.mockResolvedValue({
      items: [suiteRow('m-active', 'active'), suiteRow('m-draft', 'draft'), suiteRow('m-archived', 'archived'), suiteRow('m-unknown', undefined)],
      total: 4, page: 1, pages: 1, size: 500,
    } as never)
    await linkSuite(true)
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(service.addPlanItem.mock.calls.map((c) => c[1])).toEqual([
      { test_case_id: 'm-active' }, { test_case_id: 'm-draft' }, { test_case_id: 'm-unknown' },
    ])
    expect(toast.success).toHaveBeenCalledWith('Linked 3 test cases from "Checkout"')
  })

  it('links as before when every manual case is Active (no suffix)', async () => {
    service.getSuiteCases.mockResolvedValue({
      items: [suiteRow('m-1', 'active'), suiteRow('m-2', 'active')], total: 2, page: 1, pages: 1, size: 500,
    } as never)
    await linkSuite()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Linked 2 test cases from "Checkout"'))
  })

  it('nothing Active: says how to link the others and links nothing', async () => {
    service.getSuiteCases.mockResolvedValue({
      items: [suiteRow('m-draft', 'draft'), suiteRow('m-deprecated', 'deprecated')], total: 2, page: 1, pages: 1, size: 500,
    } as never)
    await linkSuite()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'No Active test cases in "Checkout". Tick "Include cases that are not Active" to link the others.',
    ))
    expect(service.addPlanItem).not.toHaveBeenCalled()
  })
})
