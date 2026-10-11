/**
 * Suites: Delete is QA lead on the API (`DELETE /api/v1/suites/{id}`,
 * `require_role(UserRole.QA_LEAD)`), like Make default. Creating a suite is
 * QA engineer. Delete was drawn for a QA engineer (it shared the create gate)
 * and answered 403; it now follows Make default.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuitesPage from './SuitesPage'
import { useAuthStore } from '@/store/authStore'
import type { TestSuite } from '@/types/suites'

const SUITES: TestSuite[] = [
  {
    id: 'suite-a', project_id: 'p1', name: 'auth-api', description: null, is_default: false,
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
vi.mock('./suite/useSuiteAggregates', () => ({
  useSuiteAggregates: () => ({ byName: new Map(), error: undefined, isLoading: false, loaded: true }),
}))
vi.mock('@/services/suitesService', () => ({
  suitesService: { create: vi.fn(), setDefault: vi.fn(), remove: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

function renderPage() {
  return render(<MemoryRouter><SuitesPage /></MemoryRouter>)
}

describe('SuitesPage row actions follow their endpoints', () => {
  beforeEach(() => vi.clearAllMocks())

  it('a QA engineer creates suites but is offered neither Delete nor Make default', () => {
    signInAs('QA_ENGINEER')
    renderPage()
    expect(screen.getByRole('button', { name: /new suite/i })).toBeInTheDocument()
    expect(screen.queryByTitle('Delete')).toBeNull()
    expect(screen.queryByTitle('Make default')).toBeNull()
  })

  it('a QA lead is offered Delete and Make default on a non-default suite', () => {
    signInAs('QA_LEAD')
    renderPage()
    // One non-default suite: one of each (the default suite has neither).
    expect(screen.getAllByTitle('Delete')).toHaveLength(1)
    expect(screen.getAllByTitle('Make default')).toHaveLength(1)
  })
})
