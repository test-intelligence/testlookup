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
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
import { ALL_PROJECTS_ID } from '../../store/projectStore'
import { useAuthStore } from '@/store/authStore'
import { exportAuditCSV } from '../../services/auditDashboardService'

const mockUseAuditEvents = vi.fn()
const mockUseProjectObservability = vi.fn()

let activeProjectId: string | null = ALL_PROJECTS_ID
let storedDays = 7
const mockSetDays = vi.fn()

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
      sel({ days: storedDays, setDays: mockSetDays }),
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

// WIN-01: the dropdown offers 14 days like every windowed page, so a
// fortnight picked elsewhere is shown and queried as a fortnight.
describe('the 14-day window (WIN-01)', () => {
  const windowSelect = () => screen.getByDisplayValue(/^Last \d+ days$/) as HTMLSelectElement

  it('offers Last 7 / 14 / 30 / 90 days, in that order', () => {
    renderPage()
    expect(Array.from(windowSelect().options).map(o => o.textContent)).toEqual([
      'Last 7 days', 'Last 14 days', 'Last 30 days', 'Last 90 days',
    ])
  })

  it('choosing Last 14 days writes 14 to the shared window', () => {
    renderPage()
    fireEvent.change(windowSelect(), { target: { value: '14' } })
    expect(mockSetDays).toHaveBeenCalledWith(14)
  })

  it('a stored 14 shows Last 14 days and asks the events for 14 days (no snap to 7)', () => {
    storedDays = 14
    renderPage()
    expect(windowSelect()).toHaveValue('14')
    const args = mockUseAuditEvents.mock.calls[0]?.[0] as { days?: number }
    expect(args.days).toBe(14)
    expect(mockSetDays).not.toHaveBeenCalled()
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

// P2 item 3: the tenant audit flow is a collapsed "Pipeline" disclosure at the
// bottom of the page; it renders nothing until opened.
describe('the audit workflow timeline is collapsed at the bottom', () => {
  it('starts closed, sits after the tabs, and opens on click', () => {
    renderPage()
    const pipeline = screen.getByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Tenant audit flow')).toBeNull()
    const tab = screen.getByRole('button', { name: /project observability/i })
    expect(tab.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText('Tenant audit flow')).toBeInTheDocument()
  })
})

/** Switch to the observability tab by its control.
 *
 * `fireEvent`, not `dispatchEvent`/`element.click()` — React does not observe
 * a raw DOM event here, so the tab never changes and the assertion reads as a
 * missing message rather than an unclicked tab. */
function fireObservabilityTab() {
  // Must be the TAB, named "Project Observability". A looser /observability/i
  // would also match the WorkflowTimeline stage button "Observability Export"
  // once the "Pipeline" disclosure is open — clicking that changes nothing and
  // the assertion then reads as a missing empty-state message.
  fireEvent.click(screen.getByRole('button', { name: /project observability/i }))
}

describe('the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic; Export CSV stays its one action', () => {
    renderPage()
    expectTemplateHeader('Audit Dashboard', '/settings/audit')
    expect(screen.getByRole('button', { name: 'Export CSV' }).closest('[data-page-header]')).not.toBeNull()
  })
})

// W25a HYG-06 (a): a live session writes ONE append-only `ws_session` access row; the page says what it holds.
describe('a ws_session row reads as one sentence', () => {
  const accessRow = (action: string, detail: Record<string, unknown> | null) => ({
    source: 'access', action, actor_name: 'Avery Admin', actor_id: 'u-1', project_id: 'proj-1', detail,
    created_at: '2026-10-31T09:01:00Z',
  })

  it('shows only what the append-only row knows, beside the action', () => {
    // `routers/live.py` `_ws_session_detail`'s keys; the suite runs in America/Chicago (09:00 UTC = 04:00 CDT).
    const detail = {
      channel: 'live', reason: 'client_disconnect',
      started_at: '2026-10-31T09:00:10Z', ended_at: '2026-10-31T09:00:52Z', session_started_at: '2026-10-31T09:00:00Z',
      connected_s_at_write: 47, reconnect_policy: 'reconnects within 60 s are merged into this session',
    }
    mockUseAuditEvents.mockReturnValue({ events: [accessRow('ws_session', detail)], total: 1, isLoading: false, isError: false })
    renderPage()
    const note = screen.getByText(
      'Live session from Oct 31, 04:00; this connection 04:00:10–04:00:52 (client_disconnect); 47 s connected when ' +
      'recorded; reconnects within 60 s are merged into this session.',
    )
    expect(note).toHaveAttribute('data-audit-note')
    expect(note.parentElement).toContainElement(screen.getByText('ws_session'))
  })

  it('a legacy ws_connect row shows only its action, as before', () => {
    const detail = { channel: 'live', started_at: '2026-10-30T09:00:00Z', ended_at: '2026-10-30T09:00:42Z', session_started_at: '2026-10-30T09:00:00Z' }
    mockUseAuditEvents.mockReturnValue({ events: [accessRow('ws_connect', detail), accessRow('ws_disconnect', null)], total: 2, isLoading: false, isError: false })
    const { container } = renderPage()
    expect(screen.getByText('ws_connect')).toBeInTheDocument()
    expect(screen.getByText('ws_disconnect')).toBeInTheDocument()
    expect(container.querySelector('[data-audit-note]')).toBeNull()
    expect(screen.queryByText(/Live session/)).toBeNull()
  })
})

describe('Export CSV is ADMIN on the API (GET /audit-dashboard/export)', () => {
  // The page is QA lead; a QA lead's Export answered 403.
  const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

  it('is disabled for a QA lead, naming the role it needs', () => {
    signInAs('QA_LEAD')
    renderPage()
    const exportButton = screen.getByRole('button', { name: 'Export CSV' })
    expect(exportButton).toBeDisabled()
    expect(exportButton).toHaveAttribute('title', 'Admin role required')
    fireEvent.click(exportButton)
    expect(exportAuditCSV).not.toHaveBeenCalled()
  })

  it('exports for an admin', () => {
    signInAs('ADMIN')
    renderPage()
    const exportButton = screen.getByRole('button', { name: 'Export CSV' })
    expect(exportButton).toBeEnabled()
    fireEvent.click(exportButton)
    expect(exportAuditCSV).toHaveBeenCalledTimes(1)
  })
})
