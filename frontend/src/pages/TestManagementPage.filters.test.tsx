/**
 * W13 TCL-01 (contract §2.2 frontend 9): the Cases tab's filter bar.
 *
 *  - Status / Type / Priority are multi-select `MultiSelect`s, Suite and Owner
 *    single-value ones (Owner = Me + the project's members, absent in All
 *    Projects);
 *  - every filter lives in the URL (CSV lists), written with `replace`, and a
 *    remount on that URL restores the list; unknown values are never sent;
 *  - the four presets REALLY filter (their params, not their labels);
 *  - the authored-only note when the answer says `automation_rows_excluded`;
 *  - a filter change clears the bulk selection;
 *  - the Excel export carries the same filters, a list as repeated keys.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { api } from '@/services/api'
import { useAuthStore, type User } from '@/store/authStore'
import type { ManagedTestCase, ManagedTestCaseListResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))
vi.mock('@/hooks/useTestLibrary', async () => (await import('@/test/testLibraryMocks')).testLibraryHookMocks({
  ownerMembers: [{ value: '6f1c2a54-0000-4000-8000-000000000002', label: 'Priya Shah' }],
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: [], isLoading: false }) }))
vi.mock('@/components/testManagement/EvidenceGapLists', () => ({ default: () => null }))
vi.mock('@/hooks/useNow', () => ({ useNow: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('@/hooks/useDataFreshness', () => ({ useDataFreshness: () => Date.parse('2026-10-10T12:00:00Z') }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/services/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/services/api')>()
  return { ...real, api: { ...real.api, get: vi.fn() } }
})

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'medium',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', allowed_actions: ['activate'],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}
const A = base({ id: 'c-1', title: 'Verify refund email' })
const B = base({ id: 'c-2', title: 'Sign in with SSO', status: 'approved' })

const answer = { excluded: false }
const where = { url: '', type: '' }

function Where() {
  const location = useLocation()
  const type = useNavigationType()
  useEffect(() => {
    where.url = location.pathname + location.search
    where.type = type
  }, [location, type])
  return null
}

function renderTab(url = '/test-management', projectId: string | null = 'project-1') {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Where />
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId={projectId} lifecycleV2 />} />
      </Routes>
    </MemoryRouter>,
  )
}

function signIn(role: string) {
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role,
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
}

/** The params of the most recent `useTestCases` call (the table's read). */
const lastParams = () => {
  const calls = vi.mocked(useTestCases).mock.calls
  return calls[calls.length - 1]?.[0] as Record<string, unknown>
}
const trigger = (label: string) => screen.getByRole('button', { name: new RegExp(`^${label}`) })

function pick(label: string, options: string[]) {
  fireEvent.click(trigger(label))
  for (const option of options) fireEvent.click(screen.getByRole('option', { name: option }))
  fireEvent.click(trigger(label))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  answer.excluded = false
  signIn('QA_ENGINEER')
  vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
  vi.mocked(useTestCases).mockImplementation(() => {
    const data: ManagedTestCaseListResponse = {
      items: [A, B], total: 2, page: 1, size: 25, pages: 1, automation_rows_excluded: answer.excluded,
    }
    return { data, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn() } as unknown as ReturnType<typeof useTestCases>
  })
})
afterEach(() => { act(() => useAuthStore.setState({ user: null })) })

describe('/test-management Cases: the filter bar (W13 TCL-01)', () => {
  it('writes Status = Draft + Review requested and Owner = Me to the URL with replace, and reads with them', async () => {
    renderTab('/test-management?tab=cases')
    pick('Status', ['Draft', 'Review requested'])
    pick('Owner', ['Me'])
    await waitFor(() => expect(where.url).toBe('/test-management?tab=cases&status=draft%2Creview_requested&owner=me'))
    expect(where.type).toBe('REPLACE')
    expect(lastParams()).toEqual({ page: 1, size: 25, status: ['draft', 'review_requested'], owner: 'me', include_automation: true })
    expect(trigger('Status')).toHaveAccessibleName('Status, 2 selected')
    expect(trigger('Owner')).toHaveAccessibleName('Owner, 1 selected')
  })

  it('a remount on that URL restores the exact list; unknown values are never sent', () => {
    renderTab('/test-management?tab=cases&status=draft,review_requested,bogus&owner=me&priority=urgent&type=e2e&automation=yes')
    expect(lastParams()).toEqual({
      page: 1, size: 25, status: ['draft', 'review_requested'], test_type: 'e2e', owner: 'me', include_automation: true,
    })
    expect(trigger('Status')).toHaveAccessibleName('Status, 2 selected')
    expect(trigger('Type')).toHaveAccessibleName('Type, 1 selected')
    expect(trigger('Priority')).toHaveAccessibleName('Priority')
  })

  it.each([
    ['My drafts', { owner: 'me', status: 'draft' }, 'status=draft&owner=me'],
    ['My P0/P1', { owner: 'me', priority: ['critical', 'high'] }, 'priority=critical%2Chigh&owner=me'],
    ['Unautomated', { automation: 'not_automated' }, 'automation=not_automated'],
    ['AI drafts', { status: 'draft', ai_generated: true }, 'status=draft&ai=1'],
  ])('the %s preset sends its filters, replacing every other filter', async (label, sent, query) => {
    renderTab('/test-management?type=e2e&q=refund')
    fireEvent.click(screen.getByRole('button', { name: label }))
    await waitFor(() => expect(where.url).toBe(`/test-management?${query}`))
    expect(lastParams()).toEqual({ page: 1, size: 25, include_automation: true, ...sent })
    expect(lastParams()).not.toHaveProperty('assignee_id')
  })

  it("the old \"My team's P0/P1\" view is gone", () => {
    renderTab()
    expect(screen.queryByRole('button', { name: /My team/ })).toBeNull()
  })

  it('says so when the answer left the automation-ingested tests out', () => {
    answer.excluded = true
    renderTab('/test-management?owner=me')
    expect(screen.getByRole('note')).toHaveTextContent(
      'Authored cases only: automation-ingested tests are left out under these filters.',
    )
  })

  it('shows no note when the answer does not say so', () => {
    renderTab()
    expect(screen.queryByRole('note')).toBeNull()
    expect(document.querySelector('[data-cases-filter-note]')).toBeNull()
  })

  it('a requirement filter shows one removable chip', async () => {
    renderTab('/test-management?requirement=REQ-PAY-07')
    expect(lastParams()).toMatchObject({ requirement: 'REQ-PAY-07' })
    fireEvent.click(screen.getByRole('button', { name: 'Remove the requirement filter REQ-PAY-07' }))
    await waitFor(() => expect(where.url).toBe('/test-management'))
    expect(lastParams()).not.toHaveProperty('requirement')
  })

  it('a filter change clears the bulk selection', () => {
    renderTab()
    const box = () => within(document.querySelector('tr[data-case-row="c-1"]') as HTMLElement)
      .getByRole('checkbox', { name: `Select ${A.title}` }) as HTMLInputElement
    fireEvent.click(box())
    expect(box()).toBeChecked()
    pick('Owner', ['Me'])
    expect(box()).not.toBeChecked()
    fireEvent.click(box())
    fireEvent.click(screen.getByRole('button', { name: 'Unautomated' }))
    expect(box()).not.toBeChecked()
  })

  it('All Projects has no Owner filter', () => {
    renderTab('/test-management', null)
    expect(screen.queryByRole('button', { name: /^Owner/ })).toBeNull()
    expect(trigger('Status')).toBeInTheDocument()
  })

  it('the Excel export carries the same filters, a list as repeated keys', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(['x']) } as never)
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    renderTab('/test-management?status=draft,review_requested&owner=me')
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1))
    const url = new URL(vi.mocked(api.get).mock.calls[0][0] as string, 'http://x')
    expect(url.pathname).toBe('/api/v1/test-management/cases/export/excel')
    expect(url.searchParams.getAll('status')).toEqual(['draft', 'review_requested'])
    expect(url.searchParams.get('owner')).toBe('me')
    expect(url.searchParams.get('project_id')).toBe('project-1')
  })
})
