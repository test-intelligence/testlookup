/**
 * MyFailuresPage tests — pin the contract for the "auto-assigned failures
 * inbox" page (migration 0080 + 2026-05-14 action-queue follow-up):
 *
 *   - Empty state when total is 0.
 *   - Row render when items exist.
 *   - Days-window chip filter re-fetches.
 *   - Row click navigates via the backend-provided ``navigation_url``.
 *
 * The page uses ``useProjectScopedSWR`` which reads from ``projectStore``,
 * so we mock the store to a stable "All projects" value. Network calls
 * go through ``myFailuresService`` which we mock directly.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MyFailuresPage from './MyFailuresPage'
import type { MyFailureListResponse } from '@/types/myFailures'

const mockList = vi.fn()
const mockCount = vi.fn()

vi.mock('@/services/myFailuresService', () => ({
  myFailuresService: {
    list:  (...args: unknown[]) => mockList(...args),
    count: (...args: unknown[]) => mockCount(...args),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: {
    activeProjectId: string; activeProject: null | { name: string }
  }) => unknown) => selector({ activeProjectId: '__ALL__', activeProject: null })),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { user: { username: string } | null }) => unknown) =>
    selector({ user: { username: 'qalead' } })),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

function makeResponse(items: MyFailureListResponse['items']): MyFailureListResponse {
  return {
    items, total: items.length, page: 1, size: 25,
    pages: items.length > 0 ? 1 : 0,
    unresolved_total: items.length,
  }
}

function renderPage() {
  // Fresh SWRConfig per render so the global cache doesn't carry data
  // from a previous test, which would suppress fetcher calls under the
  // same key and make "re-fetch on filter change" assertions impossible.
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/my-failures']}>
        <Routes>
          <Route path="/my-failures" element={<MyFailuresPage />} />
          <Route path="/runs/:rid/tests/:cid" element={<div>RUN PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('MyFailuresPage', () => {
  beforeEach(() => {
    mockList.mockReset()
    mockCount.mockReset()
  })

  it('renders the empty state when the backend returns total=0', async () => {
    mockList.mockResolvedValue(makeResponse([]))

    renderPage()

    expect(await screen.findByText(/You.?re caught up/i)).toBeInTheDocument()
    // Window chips are still rendered so the user can change the filter.
    expect(screen.getByText('24h')).toBeInTheDocument()
    expect(screen.getByText('30d')).toBeInTheDocument()
  })

  it('renders rows when the backend returns items', async () => {
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc1', test_name: 'test_login_failed',
        suite_name: 'AuthSuite',
        status: 'FAILED', severity: 'major',
        error_message: 'Connection refused',
        created_at: new Date(Date.now() - 60_000).toISOString(),
        test_run_id: 'run-1', build_number: '42',
        project_id: 'p1', project_name: 'GoogleProject',
        navigation_url: '/runs/run-1/tests/cc1',
        class_name: null, failure_category: null, duration_ms: null,
      },
    ]))

    renderPage()

    expect(await screen.findByText('test_login_failed')).toBeInTheDocument()
    // Project + build context renders alongside the test name.
    expect(screen.getByText('GoogleProject')).toBeInTheDocument()
    expect(screen.getByText(/Build 42/)).toBeInTheDocument()
  })

  it('re-fetches when the user picks a different days window', async () => {
    mockList.mockResolvedValue(makeResponse([]))

    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    const initialCalls = mockList.mock.calls.length

    // Click the 7-day chip.
    fireEvent.click(screen.getByText('7d'))

    await waitFor(() => {
      // The hook should re-run with days=7.
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ days: 7, page: 1, size: 25 }),
      )
      expect(mockList.mock.calls.length).toBeGreaterThan(initialCalls)
    })
  })

  it('navigates via the backend-provided navigation_url on row click', async () => {
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc1', test_name: 'test_a',
        suite_name: 'S',
        status: 'FAILED', severity: null,
        error_message: null,
        created_at: new Date().toISOString(),
        test_run_id: 'run-1', build_number: null,
        project_id: 'p1', project_name: 'P1',
        navigation_url: '/runs/run-1/tests/cc1',
        class_name: null, failure_category: null, duration_ms: null,
      },
    ]))

    renderPage()

    const row = await screen.findByText('test_a')
    fireEvent.click(row.closest('tr')!)

    // The router renders RUN PAGE for /runs/:rid/tests/:cid.
    expect(await screen.findByText('RUN PAGE')).toBeInTheDocument()
  })
})
