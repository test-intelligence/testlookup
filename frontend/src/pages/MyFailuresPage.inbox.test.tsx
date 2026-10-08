/**
 * UX redesign P4 item 5 (§5 "Inbox", owner decision D4): /my-failures is the
 * Inbox, tabs Assigned to me · Approvals.
 *
 *   - layout: header (with the tabs) → toolbar → the assigned failures
 *     (`data-primary`); no Status column, no static status chip; two-line rows;
 *   - `?tab=approvals` selects Approvals: AI reports (the old /reviews body),
 *     quarantine proposals and test-case approvals, filtered by source
 *     (`?source=`), the waiting count in the tab label;
 *   - in All Projects mode Approvals shows the project prompt /reviews showed
 *     and asks for none of its queues;
 *   - links point at the new URLs (the run's Analysis tab, Flaky tests ›
 *     Proposed), not the redirected old ones.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MyFailureListResponse } from '@/types/myFailures'

const state = vi.hoisted(() => ({ projectId: 'p1' as string, isQaLead: true }))

const failures = vi.hoisted(() => ({ list: vi.fn(), count: vi.fn() }))
vi.mock('@/services/myFailuresService', () => ({ myFailuresService: failures }))

const reviews = vi.hoisted(() => ({ list: vi.fn(), accept: vi.fn(), reject: vi.fn() }))
vi.mock('@/services/reviewService', () => ({ reviewService: reviews }))

const quarantine = vi.hoisted(() => ({ list: vi.fn(), stats: vi.fn(), approve: vi.fn(), reject: vi.fn(), release: vi.fn() }))
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: quarantine }))

const cases = vi.hoisted(() => ({ listCases: vi.fn(), transitionCase: vi.fn(), reviewAction: vi.fn() }))
vi.mock('@/services/testManagementService', () => ({ testManagementService: cases }))

vi.mock('@/hooks/useFeatureFlags', () => ({ useFeatureEnabled: () => false }))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: state.isQaLead, canAccessManagement: state.isQaLead, isQaEngineer: true }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      activeProjectId: state.projectId,
      activeProject: state.projectId === '__ALL__' ? null : { id: 'p1', name: 'Checkout' },
      projects: [{ id: 'p1', name: 'Checkout' }],
      setActiveProject: vi.fn(),
    }),
  ),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { user: { username: string } | null }) => unknown) =>
    selector({ user: { username: 'qalead' } })),
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import MyFailuresPage from './MyFailuresPage'

const FAILURE = {
  id: 'cc1',
  test_name: 'test_checkout_pays',
  suite_name: 'Checkout',
  status: 'BROKEN',
  severity: 'major',
  error_message: 'AssertionError: expected 200',
  last_failure_step: 'Click "Pay"',
  assignment_reason: 'via CODEOWNERS: src/checkout/**',
  created_at: new Date(Date.now() - 3_600_000).toISOString(),
  test_run_id: 'run-12345678',
  build_number: '42',
  run_seq: 7,
  project_id: 'p1',
  project_name: 'Checkout',
  navigation_url: '/runs/run-12345678/tests/cc1',
  class_name: null,
  failure_category: null,
  duration_ms: null,
  failure_count: 3,
}

const assigned = (items: MyFailureListResponse['items'], unresolved = items.length): MyFailureListResponse => ({
  items, total: items.length, page: 1, size: 25, pages: items.length > 0 ? 1 : 0, unresolved_total: unresolved,
})

function review(id: string) {
  return {
    id, project_id: 'p1', kind: 'report', subject_type: 'pipeline_run', subject_id: `pipe-${id}`,
    pipeline_run_id: `pipe-${id}`, test_run_id: `run-${id}`, workflow_type: 'deep', state: 'pending_review',
    reviewed: false, reviewed_at: null, reason_code: null, notes: null, evidence_bundle_sha256: null,
    superseded_by: null, created_at: '2026-09-12T10:00:00Z', requires_human_review: true,
    ai_disclaimer: 'AI-generated content. Verify before acting.', ai_disclaimer_version: 'v1',
  }
}

function proposal(id: string, status = 'PROPOSED') {
  return {
    id, project_id: 'p1', test_fingerprint: `fp-${id}`, test_name: `test_quarantine_${id}`, suite_name: 'Checkout',
    status, flip_rate: 0.3, flip_window_size: 10, owner_name: null, defect_id: null, defect_jira_key: null,
    stale: false, ready_to_promote: false, consecutive_passes: 0, reviewer_notes: null,
    updated_at: '2026-09-02T00:00:00Z',
  }
}

function testCase(id: string, status: string) {
  return {
    id, project_id: 'p1', title: `Case ${id}`, test_type: 'functional', priority: 'high', severity: 'major',
    test_suite_id: null, status, version: 2, is_automated: false, automation_status: 'manual',
    ai_generated: false, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-03T00:00:00Z',
  }
}

/** The router's current URL, rendered so a test can read it. */
function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="location">{`${loc.pathname}${loc.search}`}</output>
}

const currentUrl = () => screen.getByTestId('location').textContent

function renderPage(url = '/my-failures') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/my-failures" element={<><MyFailuresPage /><LocationProbe /></>} />
          <Route path="/runs/:rid/tests/:cid" element={<div>RUN PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  state.projectId = 'p1'
  state.isQaLead = true
  failures.list.mockResolvedValue(assigned([FAILURE], 12))
  reviews.list.mockResolvedValue([review('r1'), review('r2')])
  quarantine.list.mockResolvedValue([proposal('q1'), proposal('q2', 'QUARANTINED')])
  quarantine.stats.mockResolvedValue({
    detected: 0, proposed: 1, approved: 0, quarantined: 1, recheck_scheduled: 0, re_quarantined: 0,
    released: 0, rejected: 0, expired: 0, total_live: 2,
  })
  cases.listCases.mockImplementation((_project: string | null, params: { status?: string }) =>
    Promise.resolve({
      items: params.status === 'review_requested' ? [testCase('c1', 'review_requested')] : [testCase('c2', 'under_review')],
      total: 1, page: 1, size: 50, pages: 1,
    }),
  )
})

const tab = (name: RegExp) => screen.getByRole('tab', { name })

describe('Inbox — Assigned to me', () => {
  it('puts the failures first: header and tabs, toolbar, then [data-primary]; no other tab bar or Disclosure before it', async () => {
    const { container } = renderPage()
    await screen.findByText('test_checkout_pays')
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0]
    expect(within(primary as HTMLElement).getByRole('table', { name: 'Assigned failures' })).toBeInTheDocument()
    const pageTabs = within(container.querySelector('[data-page-header]') as HTMLElement).getByRole('tablist', { name: 'Inbox' })
    expect(pageTabs.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    for (const other of container.querySelectorAll('[role="tablist"], [data-disclosure]')) {
      if (other === pageTabs) continue
        expect(other.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    }
    expect(screen.getByRole('heading', { level: 1, name: 'Inbox' })).toBeInTheDocument()
  })

  it('has no Status column and no static status chip (§5 Delete)', async () => {
    renderPage()
    await screen.findByText('test_checkout_pays')
    expect(screen.queryByRole('columnheader', { name: 'Status' })).toBeNull()
    expect(screen.queryByText('FAILED, BROKEN')).toBeNull()
    expect(screen.queryByText('BROKEN')).toBeNull()
  })

  it('draws a row in two lines: the test over its error, failing step and assignment reason', async () => {
    renderPage()
    const name = await screen.findByText('test_checkout_pays')
    const cell = name.closest('td') as HTMLElement
    expect(cell.children).toHaveLength(2)
    const detail = cell.querySelector('[data-row-detail]') as HTMLElement
    expect(detail).toHaveTextContent('AssertionError: expected 200 · failed at: Click "Pay" · via CODEOWNERS: src/checkout/**')
    expect(detail).toHaveAttribute('title', 'AssertionError: expected 200 · failed at: Click "Pay" · via CODEOWNERS: src/checkout/**')
    // The run cell: Run #N over its time and id, one line each.
    const run = screen.getByText('Run #7').closest('td') as HTMLElement
    expect(run).toHaveTextContent(/Run #7.+ · run-1234$/)
  })

  // Browser E2E pass (2026-10-08): a lead opens on the team scope, and the tab
  // read "Assigned to me 62" over the team's 62 while none were theirs (the
  // sidebar badge said 0). The tab and the count are named for the list shown.
  it('a lead opens on the team: the tab is "Team failures" with the team count, selected by default', async () => {
    renderPage()
    await waitFor(() => expect(tab(/^Team failures/)).toHaveTextContent('Team failures12'))
    expect(tab(/^Team failures/)).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: /^Assigned to me/ })).toBeNull()
    expect(screen.getByText('unresolved')).toBeInTheDocument()
    expect(failures.list).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'team' }))
  })

  it('a lead who picks Mine: "Assigned to me", with their own count', async () => {
    renderPage()
    await screen.findByText('test_checkout_pays')
    failures.list.mockResolvedValue(assigned([FAILURE], 3))
    fireEvent.click(screen.getByText('mine'))
    await waitFor(() => expect(tab(/^Assigned to me/)).toHaveTextContent('Assigned to me3'))
    expect(screen.getByText('assigned')).toBeInTheDocument()
    expect(failures.list).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'mine' }))
  })

  it('anyone else sees only their own: "Assigned to me"', async () => {
    state.isQaLead = false
    renderPage()
    await waitFor(() => expect(tab(/^Assigned to me/)).toHaveTextContent('Assigned to me12'))
    expect(screen.queryByRole('tab', { name: /^Team failures/ })).toBeNull()
  })

  it('keeps the shared window control: picking 24h re-fetches page 1', async () => {
    renderPage()
    await screen.findByText('test_checkout_pays')
    fireEvent.click(screen.getByRole('radio', { name: '24h' }))
    await waitFor(() => expect(failures.list).toHaveBeenCalledWith(expect.objectContaining({ days: 1, page: 1 })))
  })
})

describe('Inbox — Approvals (?tab=approvals)', () => {
  it('?tab=approvals selects Approvals and renders the three sources under "All"', async () => {
    const { container } = renderPage('/my-failures?tab=approvals')
    expect(tab(/^Approvals/)).toHaveAttribute('aria-selected', 'true')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(within(primary).getByRole('region', { name: 'AI reports' })).toBeInTheDocument()
    expect(within(primary).getByRole('region', { name: 'Quarantine proposals' })).toBeInTheDocument()
    expect(within(primary).getByRole('region', { name: 'Test cases' })).toBeInTheDocument()
    expect(await screen.findAllByTestId('review-row')).toHaveLength(2)
    expect(await screen.findByText('test_quarantine_q1')).toBeInTheDocument()
    // Only the proposal is an approval; the quarantined request is not.
    expect(screen.queryByText('test_quarantine_q2')).toBeNull()
    expect(await screen.findByText('Case c1')).toBeInTheDocument()
    expect(screen.getByText('Case c2')).toBeInTheDocument()
    // The assigned list is not on this tab.
    expect(screen.queryByRole('table', { name: 'Assigned failures' })).toBeNull()
  })

  it('counts what is waiting in the tab label and per source (2 reports + 1 proposal + 2 cases)', async () => {
    renderPage()
    await waitFor(() => expect(tab(/^Approvals/)).toHaveTextContent('Approvals5'))
    fireEvent.click(tab(/^Approvals/))
    expect(currentUrl()).toBe('/my-failures?tab=approvals')
    await waitFor(() => expect(screen.getByRole('radio', { name: 'AI reports 2' })).toBeInTheDocument())
    expect(screen.getByRole('radio', { name: 'Quarantine proposals 1' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Test cases 2' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'All 5' })).toHaveAttribute('aria-checked', 'true')
  })

  it('filters by source, kept in ?source=', async () => {
    renderPage('/my-failures?tab=approvals&source=quarantine')
    expect(await screen.findByText('test_quarantine_q1')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'AI reports' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Test cases' })).toBeNull()
    fireEvent.click(screen.getByRole('radio', { name: /^AI reports/ }))
    expect(currentUrl()).toBe('/my-failures?tab=approvals&source=reports')
    expect(await screen.findAllByTestId('review-row')).toHaveLength(2)
    expect(screen.queryByRole('region', { name: 'Quarantine proposals' })).toBeNull()
  })

  it('links to the new URLs: a report to its run\'s Analysis tab, proposals to Flaky tests › Proposed', async () => {
    renderPage('/my-failures?tab=approvals')
    const [first] = await screen.findAllByRole('link', { name: 'Open report' })
    expect(first).toHaveAttribute('href', '/runs/run-r1?tab=analysis')
    expect(screen.getByRole('link', { name: 'Open in Flaky tests' })).toHaveAttribute('href', '/flaky?tab=proposed')
    for (const link of screen.getAllByRole('link')) {
      expect(link.getAttribute('href')).not.toMatch(/\/intelligence|\/reviews|\/quarantine|\/flaky-coach/)
    }
  })

  it('accepts an AI report from the inbox', async () => {
    reviews.accept.mockResolvedValue({})
    renderPage('/my-failures?tab=approvals&source=reports')
    const [accept] = await screen.findAllByTestId('review-accept')
    fireEvent.click(accept)
    await act(async () => {
      fireEvent.click(screen.getByTestId('review-confirm'))
    })
    expect(reviews.accept).toHaveBeenCalledWith('r1', undefined)
  })

  it('decides a test case with an audit note', async () => {
    cases.reviewAction.mockResolvedValue(testCase('c1', 'approved'))
    renderPage('/my-failures?tab=approvals&source=cases')
    const row = (await screen.findByText('Case c1')).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Approve' }))
    const dialog = screen.getByRole('dialog', { name: 'Approve' })
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Steps are complete' } })
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
    })
    expect(cases.reviewAction).toHaveBeenCalledWith('c1', 'approve', 'Steps are complete')
    await waitFor(() => expect(screen.queryByText('Case c1')).toBeNull())
  })

  it('approves a quarantine proposal from the inbox', async () => {
    quarantine.approve.mockResolvedValue({})
    renderPage('/my-failures?tab=approvals&source=quarantine')
    await screen.findByText('test_quarantine_q1')
    fireEvent.click(screen.getByTestId('quarantine-approve'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Confirm approve' }))
    })
    expect(quarantine.approve).toHaveBeenCalledWith('q1', { notes: undefined })
  })

  it('shows the project prompt in All Projects mode and asks for no queue', async () => {
    state.projectId = '__ALL__'
    renderPage('/my-failures?tab=approvals')
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(tab(/^Approvals/)).toHaveTextContent(/^Approvals$/)
    await waitFor(() => expect(failures.list).toHaveBeenCalled())
    expect(reviews.list).not.toHaveBeenCalled()
    expect(quarantine.list).not.toHaveBeenCalled()
    expect(cases.listCases).not.toHaveBeenCalled()
  })

  it('opens the reports help section on the Approvals tab', () => {
    renderPage('/my-failures?tab=approvals')
    const help = screen.getByRole('button', { name: 'Help: Inbox' })
    expect(help).toHaveAttribute('data-help-topic', 'reports')
    expect(help).toHaveAttribute('data-help-anchor', 'the-decision-report-is-verified-before-publication')
  })
})
