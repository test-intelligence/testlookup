/**
 * AuditDashboardPage — audit trail and tenant observability.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"). **No defect
 * found.** This page handles all three documented causes of a silently-empty
 * page correctly, and these tests exist to keep it that way, because each one
 * fails without an error toast — the page just renders nothing and looks
 * broken for reasons nobody can see.
 *
 * 1. The "all projects" sentinel is converted to `undefined` rather than sent
 *    to the API. Sending the literal is its own quality gate in this repo
 *    (`frontend.all-projects-literal`) because the backend rejects it and the
 *    422 surfaces as a blank page.
 * 2. The time window comes from a store SHARED across pages, and this page
 *    offers a narrower set of options. `snapToAllowed` maps an out-of-range
 *    stored value onto a supported one; without it a window set elsewhere
 *    produces an empty range here.
 * 3. Tenant observability is project-scoped, so with no project selected the
 *    page states that instead of rendering an empty grid.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditDashboardPage from './AuditDashboardPage'
import { ALL_PROJECTS_ID } from '../../store/projectStore'

const mockUseAuditEvents = vi.fn()
const mockUseProjectObservability = vi.fn()

let activeProjectId: string | null = ALL_PROJECTS_ID
let storedDays = 7

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

vi.mock('../../store/projectStore', async () => {
  const actual =
    await vi.importActual<typeof import('../../store/projectStore')>('../../store/projectStore')
  return {
    ...actual,
    useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) =>
      sel({ activeProjectId }),
  }
})

vi.mock('../../store/timeWindowStore', async () => {
  const actual =
    await vi.importActual<typeof import('../../store/timeWindowStore')>(
      '../../store/timeWindowStore',
    )
  return {
    ...actual,
    useTimeWindowStore: (sel: (s: { days: number; setDays: () => void }) => unknown) =>
      sel({ days: storedDays, setDays: vi.fn() }),
  }
})

vi.mock('@/hooks/useAuditDashboard', () => ({
  // The module exports THREE hooks; a partial mock makes vitest throw on the
  // missing one rather than silently rendering a broken page.
  useAuditCategories: () => ({ categories: [], isLoading: false }),
  useAuditEvents: (...a: unknown[]) => mockUseAuditEvents(...a),
  useProjectObservability: (...a: unknown[]) => mockUseProjectObservability(...a),
}))

vi.mock('../../services/auditDashboardService', () => ({ exportAuditCSV: vi.fn() }))

function renderPage() {
  return render(
    <MemoryRouter>
      <AuditDashboardPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  activeProjectId = ALL_PROJECTS_ID
  storedDays = 7
  mockUseAuditEvents.mockReturnValue({ events: [], total: 0, isLoading: false, isError: false })
  mockUseProjectObservability.mockReturnValue({ observability: null })
})

describe('the "all projects" sentinel never reaches the API', () => {
  it('passes undefined, not the sentinel string', () => {
    activeProjectId = ALL_PROJECTS_ID
    renderPage()

    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { projectId?: string }
    expect(args.projectId).toBeUndefined()
    // Belt and braces: the literal must not appear in any argument, however
    // the call signature is later reshaped.
    expect(JSON.stringify(mockUseAuditEvents.mock.calls)).not.toContain(ALL_PROJECTS_ID)
  })

  it('passes a real project id through unchanged', () => {
    activeProjectId = 'proj-123'
    renderPage()

    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { projectId?: string }
    expect(args.projectId).toBe('proj-123')
  })

  it('treats no selection as undefined rather than null', () => {
    activeProjectId = null
    renderPage()

    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { projectId?: string }
    expect(args.projectId).toBeUndefined()
  })
})

describe('a time window set on another page cannot empty this one', () => {
  it('snaps an unsupported stored window onto a supported one', () => {
    // The store is shared app-wide; this page offers its own option set. An
    // unsnapped value produces a range the query returns nothing for, with no
    // error to explain it.
    storedDays = 3650
    renderPage()

    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { days?: number }
    expect(args.days).toBeDefined()
    expect(args.days).not.toBe(3650)
  })

  it('keeps a window this page already supports', () => {
    storedDays = 7
    renderPage()

    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { days?: number }
    expect(args.days).toBe(7)
  })
})

describe('project-scoped observability says why it is empty', () => {
  it('explains that a project must be selected rather than rendering nothing', () => {
    activeProjectId = ALL_PROJECTS_ID
    renderPage()

    fireObservabilityTab()

    expect(screen.getByText(/select a project/i)).toBeInTheDocument()
  })
})

/** Switch to the observability tab by its control.
 *
 * `fireEvent`, not `dispatchEvent`/`element.click()` — React does not observe
 * a raw DOM event here, so the tab never changes and the assertion reads as a
 * missing message rather than an unclicked tab. */
function fireObservabilityTab() {
  // Must be the TAB, named "Project Observability". A looser /observability/i
  // also matches the WorkflowTimeline stage button "Observability Export",
  // which renders above it — clicking that changes nothing and the assertion
  // then reads as a missing empty-state message.
  fireEvent.click(screen.getByRole('button', { name: /project observability/i }))
}
