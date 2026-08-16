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

  // ── Default scope (changed 2026-08-15) ──────────────────────────────────
  //
  // Team is now the default for anyone allowed to see it. "Mine" opened
  // almost empty for a real lead or admin, because auto-assignment routes
  // failures to the synthetic default-QA-Lead — the page looked broken.
  // These assertions were UPDATED rather than deleted: they previously
  // pinned scope=mine as the default.

  it('default scope is "team" for a lead — the SWR list call includes scope=team', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'team' }),
      )
    })
    expect(mockList).not.toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'mine' }),
    )
  })

  it('a non-lead is still forced to scope=mine', async () => {
    // The toggle is hidden for them, and the server downgrades ?scope=team
    // anyway — but the client must not ASK for team either.
    mockIsQaLead.mockReturnValue(false)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    expect(mockList).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'mine' }),
    )
    expect(mockList).not.toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'team' }),
    )
  })

  it('the default follows a permission that arrives AFTER the first render', async () => {
    // `isQaLead` reads from the auth store, which reports VIEWER until `user`
    // hydrates. A useState initialiser would latch that early `false` and
    // strand a lead on "Mine" depending on load timing — the exact bug this
    // shape avoids by deriving the scope on every render.
    mockIsQaLead.mockReturnValue(false)
    mockList.mockResolvedValue(makeResponse([]))
    const { rerender } = renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())

    mockIsQaLead.mockReturnValue(true)
    rerender(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={['/my-failures']}>
          <Routes>
            <Route path="/my-failures" element={<MyFailuresPage />} />
          </Routes>
        </MemoryRouter>
      </SWRConfig>,
    )

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'team' }),
      )
    })
  })

  it('clicking Mine re-fetches with scope=mine, and Team returns to team', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    const initialCalls = mockList.mock.calls.length

    fireEvent.click(screen.getByText('mine'))

    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'mine' }),
      )
      expect(mockList.mock.calls.length).toBeGreaterThan(initialCalls)
    })

    // An explicit choice must stick, and switching back must work.
    fireEvent.click(screen.getByText('team'))
    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({ scope: 'team' }),
      )
    })
  })

  it('empty state copy reflects the active scope', async () => {
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    // team is the default now — "No open team failures"
    expect(await screen.findByText(/No open team failures/i)).toBeInTheDocument()

    fireEvent.click(screen.getByText('mine'))

    // mine — "You're caught up"
    await waitFor(() => {
      expect(screen.getByText(/You.?re caught up/i)).toBeInTheDocument()
    })
  })

  it('the subtitle says whose failures are shown', async () => {
    // The page is titled "My Failures" while opening on the team inbox, so
    // the subtitle must not claim the rows are the viewer's own.
    mockIsQaLead.mockReturnValue(true)
    mockList.mockResolvedValue(makeResponse([]))
    renderPage()

    expect(await screen.findByText(/Every unresolved failure across/i)).toBeInTheDocument()

    fireEvent.click(screen.getByText('mine'))
    await waitFor(() => {
      expect(screen.getByText(/Failures assigned to you across/i)).toBeInTheDocument()
    })
  })
})
