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
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'

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
    // Reset the shared time-window store between tests so state from
    // one test doesn't bleed into the next.
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('defaults to the shared time-window default on first visit', async () => {
    // The shared time-window default is now ``DEFAULT_TIME_WINDOW_DAYS``
    // (7d as of the v2 store migration), not 24h. ``beforeEach`` resets
    // the store to that default, so the first fetch uses it.
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()
    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ days: DEFAULT_TIME_WINDOW_DAYS, page: 1, size: 25 }),
      )
    })
  })

  it('uses the persisted shared window on mount (set by another page)', async () => {
    // Pretend the user picked 30d on a different page first.
    useTimeWindowStore.setState({ days: 30 })
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()
    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ days: 30 }),
      )
    })
  })

  it('writes the new window to the shared store when the user picks one', async () => {
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()
    await waitFor(() => expect(mockList).toHaveBeenCalled())

    fireEvent.click(screen.getByText('7d'))

    await waitFor(() => {
      expect(useTimeWindowStore.getState().days).toBe(7)
    })
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
    // Suite context renders alongside the test name. Project name is
    // intentionally omitted per-row (it's chosen in the global project
    // dropdown — see the row comment in MyFailuresPage.tsx), and the run
    // identifier shows the raw build_number ("42") / "Run #N", not a
    // "Build N" label.
    expect(screen.getByText('AuthSuite')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('shows the CODEOWNERS assignment reason when present (US-8.4)', async () => {
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc2', test_name: 'test_api_call',
        suite_name: 'ApiSuite',
        status: 'FAILED', severity: 'major',
        error_message: 'boom',
        created_at: new Date(Date.now() - 60_000).toISOString(),
        test_run_id: 'run-2', build_number: '7',
        project_id: 'p1', project_name: 'P',
        navigation_url: '/runs/run-2/tests/cc2',
        class_name: null, failure_category: null, duration_ms: null,
        assignment_reason: 'via CODEOWNERS: src/api/**',
      },
    ]))

    renderPage()

    expect(await screen.findByText('via CODEOWNERS: src/api/**')).toBeInTheDocument()
  })

  it('shows the run datetime inline beside the run identifier', async () => {
    // "Run #N" repeats per (project, suite); the run's start datetime is shown
    // inline (not just on hover) so same-numbered runs are distinguishable.
    // Built from local parts so the expected label is timezone-independent.
    const created = new Date(2026, 5, 8, 14, 30) // Jun 8 2026, 14:30 local
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc9', test_name: 'test_checkout', suite_name: 'PaySuite',
        status: 'FAILED', severity: 'major', error_message: 'boom',
        created_at: created.toISOString(),
        test_run_id: 'run-9', build_number: '99', run_seq: 5,
        project_id: 'p1', project_name: 'P', navigation_url: '/runs/run-9/tests/cc9',
        class_name: null, failure_category: null, duration_ms: null,
      },
    ]))

    renderPage()

    expect(await screen.findByText('Run #5')).toBeInTheDocument()
    expect(screen.getByText('Jun 08, 14:30')).toBeInTheDocument()
  })

  it('re-fetches when the user picks a different days window', async () => {
    mockList.mockResolvedValue(makeResponse([]))

    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    const initialCalls = mockList.mock.calls.length

    // Pick a window that is NOT the shared default — clicking the default is
    // a no-op, so it would assert nothing. Derived from the constant rather
    // than hard-coded: this test previously clicked '30d' as "not the
    // default", and silently became vacuous the day 30d BECAME the default.
    const options = [1, 7, 30]
    const nonDefault = options.find(d => d !== DEFAULT_TIME_WINDOW_DAYS)
    expect(nonDefault).toBeDefined()
    // The page labels 1 as "24h", not "1d" — mirror its own rule.
    const label = nonDefault === 1 ? '24h' : `${nonDefault}d`
    fireEvent.click(screen.getByText(label))

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ days: nonDefault, page: 1, size: 25 }),
      )
      expect(mockList.mock.calls.length).toBeGreaterThan(initialCalls)
    })
  })

  it('renders the Test Suite column with the suite_name from the payload', async () => {
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc1', test_name: 'test_login_failed',
        suite_name: 'Realistic TestNG client examples',
        status: 'FAILED', severity: 'major',
        error_message: null,
        created_at: new Date().toISOString(),
        test_run_id: 'run-1', build_number: '42',
        project_id: 'p1', project_name: 'P',
        navigation_url: '/runs/run-1/tests/cc1',
        class_name: null, failure_category: null, duration_ms: null,
      },
    ]))

    renderPage()

    // Column header renders.
    expect(await screen.findByText('Test Suite')).toBeInTheDocument()
    // Suite value renders in its own cell (not bundled under the test name).
    expect(screen.getByText('Realistic TestNG client examples')).toBeInTheDocument()
  })

  it('renders the per-test failure count badge from the backend payload', async () => {
    mockList.mockResolvedValue(makeResponse([
      {
        id: 'cc1', test_name: 'test_login_failed',
        suite_name: 'AuthSuite',
        status: 'FAILED', severity: 'major',
        error_message: null,
        created_at: new Date().toISOString(),
        test_run_id: 'run-1', build_number: null,
        project_id: 'p1', project_name: 'P1',
        navigation_url: '/runs/run-1/tests/cc1',
        class_name: null, failure_category: null, duration_ms: null,
        failure_count: 7,
      },
    ]))

    renderPage()

    // Badge text is "× 7" — tolerate either the ASCII ``x`` or the U+00D7
    // multiplication sign that the component uses.
    expect(await screen.findByText(/[×x]\s*7/i)).toBeInTheDocument()
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
    const tr = row.closest('tr')
    if (!tr) throw new Error('test_a row has no <tr> ancestor')
    fireEvent.click(tr)

    // The router renders RUN PAGE for /runs/:rid/tests/:cid.
    expect(await screen.findByText('RUN PAGE')).toBeInTheDocument()
  })
})
