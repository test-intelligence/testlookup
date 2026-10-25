/**
 * W13 TCL-05 (contract §2.6, G-D): an AUTHORED case's panel gains a
 * **Timeline** tab, second after Details. Details stays the default (every
 * spec opens it); History, Reviews and Comments stay. G-E's `CaseTimeline` is
 * mounted only while the tab is open, so nothing is asked before.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTestCaseTimeline } from '@/hooks/useTestLibrary'
import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import { useAuthStore, type User } from '@/store/authStore'
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

function base(overrides: Partial<ManagedTestCase>): ManagedTestCase {
  return {
    id: 'case-x', project_id: 'project-1', title: 'x', test_type: 'functional', priority: 'medium',
    severity: 'major', test_suite_id: null, status: 'draft', version: 1, is_automated: false,
    automation_status: 'not_automated', ai_generated: false, source: 'managed', allowed_actions: ['activate'],
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-09T00:00:00Z', ...overrides,
  }
}
const AUTHORED = base({ id: 'case-1', title: 'Verify refund email' })
const AUTOMATION = base({ id: 'auto-1', title: 'checkout_spec.ts › pays', source: 'automation' })

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/test-management']}>
      <Routes>
        <Route path="/test-management" element={<TestCasesTab projectId="project-1" lifecycleV2 />} />
      </Routes>
    </MemoryRouter>,
  )
}

const row = (id: string) => document.querySelector(`tr[data-case-row="${id}"]`) as HTMLElement
const pane = (title: string) => screen.getByRole('complementary', { name: title })
const tabNames = (title: string) => {
  const strip = within(pane(title)).getByRole('button', { name: 'Details' }).parentElement as HTMLElement
  return within(strip).getAllByRole('button').map((b) => b.textContent)
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  const user: User = {
    id: 'u-1', email: 'qa@example.test', username: 'qa', full_name: 'QA', role: 'QA_ENGINEER',
    is_active: true, must_change_password: false, avatar_color: null,
  }
  act(() => useAuthStore.setState({ user }))
  vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
  const page: PaginatedResponse<ManagedTestCase> = { items: [AUTHORED, AUTOMATION], total: 2, page: 1, size: 25, pages: 1 }
  vi.mocked(useTestCases).mockImplementation(() => (
    { data: page, error: undefined, isLoading: false, isValidating: false, mutate: vi.fn() } as unknown as ReturnType<typeof useTestCases>
  ))
})
afterEach(() => { act(() => useAuthStore.setState({ user: null })) })

describe('/test-management: a case\'s Timeline tab (W13 TCL-05)', () => {
  it('an authored case: Timeline is the second tab; History, Reviews and Comments stay', () => {
    renderTab()
    fireEvent.click(row(AUTHORED.id))
    expect(tabNames(AUTHORED.title)).toEqual(['Details', 'Timeline', 'Lifecycle', 'History', 'Reviews', 'Comments', 'AI Review'])
  })

  it('Details stays the default, and the timeline asks nothing until its tab opens', () => {
    renderTab()
    fireEvent.click(row(AUTHORED.id))
    expect(within(pane(AUTHORED.title)).getByText('Feature Area')).toBeInTheDocument()
    expect(document.querySelector('[data-case-timeline]')).toBeNull()
    expect(useTestCaseTimeline).not.toHaveBeenCalled()

    fireEvent.click(within(pane(AUTHORED.title)).getByRole('button', { name: 'Timeline' }))
    expect(document.querySelector('[data-case-timeline]')).not.toBeNull()
    expect(useTestCaseTimeline).toHaveBeenCalled()
    for (const [caseId, , enabled] of vi.mocked(useTestCaseTimeline).mock.calls) {
      expect(caseId).toBe(AUTHORED.id)
      expect(enabled).toBe(true)
    }
    expect(within(pane(AUTHORED.title)).queryByText('Feature Area')).toBeNull()

    // Back to Details: the timeline is unmounted again.
    fireEvent.click(within(pane(AUTHORED.title)).getByRole('button', { name: 'Details' }))
    expect(document.querySelector('[data-case-timeline]')).toBeNull()
  })

  it('an automation row has no Timeline (Details only)', () => {
    renderTab()
    fireEvent.click(row(AUTOMATION.id))
    expect(tabNames(AUTOMATION.title)).toEqual(['Details'])
    expect(useTestCaseTimeline).not.toHaveBeenCalled()
  })
})
