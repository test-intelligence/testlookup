/**
 * Regression: /my-failures Mine/Team scope toggle (2026-05-19).
 *
 * Bug pinned: admin viewing /my-failures saw almost nothing because
 * auto-assignment routes failures to the synthetic default-QA-Lead
 * user. The fix added a Mine/Team scope toggle that QA_LEAD/ADMIN
 * use to see project-wide unresolved failures without reassigning
 * rows. Lower roles never see the toggle (and the server silently
 * downgrades ?scope=team anyway).
 *
 * Companion to ``MyFailuresPage.test.tsx`` — this file pins ONLY the
 * scope-toggle contract so a future test refactor doesn't drop the
 * coverage.
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

// usePermissions is the toggle's gate. Per-test override via
// ``mockReturnValue`` so the same suite can exercise both branches.
const mockIsQaLead = vi.fn()
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: mockIsQaLead() }),
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
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/my-failures']}>
        <Routes>
          <Route path="/my-failures" element={<MyFailuresPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('MyFailuresPage — scope toggle (regression)', () => {
  beforeEach(() => {
    mockList.mockReset()
    mockCount.mockReset()
    mockIsQaLead.mockReset()
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('hides the Mine/Team toggle for non-QA_LEAD users', async () => {
    mockIsQaLead.mockReturnValue(false)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())

    // The toggle label "Scope" never renders for non-QA_LEAD. Window
    // chips ("24h", "7d", …) DO render — they're always available.
    expect(screen.queryByText('Scope')).not.toBeInTheDocument()
  })

  it('shows the Mine/Team toggle for QA_LEAD users', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())

    expect(screen.getByText('Scope')).toBeInTheDocument()
    // Both radio options visible.
    expect(screen.getByText('mine')).toBeInTheDocument()
    expect(screen.getByText('team')).toBeInTheDocument()
  })

  it('default scope is "mine" — the SWR list call includes scope=mine', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'mine' }),
      )
    })
  })

  it('clicking Team re-fetches with scope=team', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    const initialCalls = mockList.mock.calls.length

    fireEvent.click(screen.getByText('team'))

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'team' }),
      )
      expect(mockList.mock.calls.length).toBeGreaterThan(initialCalls)
    })
  })

  it('empty state copy reflects the active scope', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    // mine — "You're caught up"
    expect(await screen.findByText(/You.?re caught up/i)).toBeInTheDocument()

    fireEvent.click(screen.getByText('team'))

    // team — "No open team failures"
    await waitFor(() => {
      expect(screen.getByText(/No open team failures/i)).toBeInTheDocument()
    })
  })
})
