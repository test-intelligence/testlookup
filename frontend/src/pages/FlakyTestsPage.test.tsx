/**
 * UX redesign P4 item 4: Flaky tests (`/flaky`), the one page for the job that
 * was split over Flaky Coach and Quarantine.
 *
 *   - layout: header (with the tabs) → the primary content (`data-primary`,
 *     the Detected table); nothing else (no other tab bar, no Disclosure, no
 *     stat tiles) before it;
 *   - tabs Detected · Proposed · Quarantined · History, counts in the labels,
 *     `?tab=` selects one and each renders its section;
 *   - Detected: recommendation column, filter chips (were tiles), details in
 *     a side panel (was an inline expansion), and the row action "Propose
 *     quarantine" — or, for a test that already has a live request, its state
 *     linking to that tab;
 *   - All Projects mode keeps the project prompt; a failed analysis renders as
 *     unavailable, never as "no flaky tests".
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlakyCoachEntry } from '@/services/testHealthService'
import type { FlakyQuarantineRead } from '@/services/flakyQuarantineService'

const state = vi.hoisted(() => ({
  projectId: 'p1' as string,
  canAccessManagement: true,
  isQaEngineer: true,
  quarantineOn: true as boolean | undefined,
  coachError: null as unknown,
}))

const coachMock = vi.hoisted(() => ({ getFlakyCoach: vi.fn(), refreshFlakyCoach: vi.fn() }))
vi.mock('@/services/testHealthService', () => ({ testHealthService: coachMock }))

const quarantineMock = vi.hoisted(() => ({
  list: vi.fn(),
  stats: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  release: vi.fn(),
  propose: vi.fn(),
}))
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: quarantineMock }))

vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureFlagStatus: (key: string) => (key === 'flaky_auto_quarantine' ? state.quarantineOn : undefined),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: state.canAccessManagement, isQaEngineer: state.isQaEngineer }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      activeProjectId: state.projectId,
      activeProject: { id: 'p1', name: 'Checkout' },
      projects: [{ id: 'p1', name: 'Checkout' }],
      setActiveProject: vi.fn(),
    }),
  ),
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import FlakyTestsPage from './FlakyTestsPage'

function entry(i: number, recommendation: FlakyCoachEntry['quarantine_recommendation']): FlakyCoachEntry {
  return {
    test_fingerprint: `fp-${i}`,
    test_name: `test_flaky_${i}`,
    suite_name: 'Checkout',
    failure_rate: 0.4,
    total_runs: 20,
    failed_runs: 8,
    flaky_since: '2026-09-01T00:00:00Z',
    last_failure_at: '2026-09-10T00:00:00Z',
    quarantine_recommendation: recommendation,
    stabilization_actions: [`Stabilise test ${i}`],
    impact_score: 50 - i,
    status_history: ['PASSED', 'FAILED', 'PASSED'],
    flaky_likely_cause: i === 1 ? 'Timing: a wait races the response' : null,
  }
}

function request(id: string, status: FlakyQuarantineRead['status'], fingerprint = `q-${id}`): FlakyQuarantineRead {
  return {
    id,
    project_id: 'p1',
    test_fingerprint: fingerprint,
    test_name: `test_quarantine_${id}`,
    suite_name: 'Checkout',
    status,
    detection_method: 'flip_rate',
    flip_rate: 0.3,
    flip_window_size: 10,
    pass_count: 7,
    fail_count: 3,
    detected_at: '2026-09-01T00:00:00Z',
    last_failure_at: null,
    proposed_at: null,
    approved_at: null,
    approved_by_user_id: null,
    rejected_at: null,
    rejected_by_user_id: null,
    quarantine_start: null,
    quarantine_expires_at: null,
    quarantine_duration_days: 14,
    recheck_at: null,
    rationale: null,
    reviewer_notes: null,
    owner_user_id: null,
    owner_name: 'QA Lead',
    defect_id: null,
    defect_jira_key: null,
    defect_jira_url: null,
    defect_external_status: null,
    defect_external_status_conflict: false,
    sla_days: null,
    stale_at: null,
    stale: false,
    consecutive_passes: 0,
    ready_to_promote: false,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-02T00:00:00Z',
  }
}

// fp-1 already has a live proposal; fp-2 is quarantined; fp-3 has nothing.
const LIVE = [request('a', 'PROPOSED', 'fp-1'), request('b', 'QUARANTINED', 'fp-2'), request('c', 'PROPOSED')]
const SETTLED = [request('d', 'RELEASED'), request('e', 'REJECTED')]

/** The router's current URL, rendered so a test can read it. */
function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="location">{`${loc.pathname}${loc.search}`}</output>
}

const currentUrl = () => screen.getByTestId('location').textContent

function renderPage(url = '/flaky') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/flaky" element={<><FlakyTestsPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  state.projectId = 'p1'
  state.canAccessManagement = true
  state.isQaEngineer = true
  state.quarantineOn = true
  coachMock.getFlakyCoach.mockResolvedValue({
    project_id: 'p1',
    total_flaky: 2,
    quarantine_candidates: 1,
    entries: [entry(1, 'QUARANTINE'), entry(2, 'INVESTIGATE'), entry(3, 'MONITOR')],
  })
  quarantineMock.list.mockImplementation((params: { live_only?: boolean }) =>
    Promise.resolve(params.live_only ? LIVE : [...LIVE, ...SETTLED]),
  )
  quarantineMock.stats.mockResolvedValue({
    detected: 0, proposed: 2, approved: 0, quarantined: 1, recheck_scheduled: 0,
    re_quarantined: 0, released: 1, rejected: 1, expired: 0, total_live: 3,
  })
})

const tab = (name: RegExp) => screen.getByRole('tab', { name })

// Action sweep, homelab 2026-10-10: "Refresh" re-runs the analysis (QA engineer
// and above) and was offered to viewers on /flaky, /flaky-coach and /quarantine,
// who got a 403 and a toast blaming their role for clicking it.
describe('FlakyTestsPage — Refresh is offered only to who can run it', () => {
  it('a viewer or tester sees no Refresh', async () => {
    state.isQaEngineer = false
    renderPage()
    await screen.findByRole('tab', { name: /Detected/ })
    expect(screen.queryByRole('button', { name: /Refresh/ })).toBeNull()
    expect(coachMock.refreshFlakyCoach).not.toHaveBeenCalled()
  })

  it('a QA engineer sees it and it re-runs the analysis', async () => {
    coachMock.refreshFlakyCoach.mockResolvedValue({ flaky_tests_found: 2 })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Refresh/ }))
    await waitFor(() => expect(coachMock.refreshFlakyCoach).toHaveBeenCalledTimes(1))
  })
})

describe('FlakyTestsPage — the page', () => {
  it('shows the project prompt in All Projects mode and asks for nothing', () => {
    state.projectId = '__ALL__'
    renderPage()
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    expect(coachMock.getFlakyCoach).not.toHaveBeenCalled()
    expect(quarantineMock.list).not.toHaveBeenCalled()
  })

  it('puts the detected table first: header and tabs, then [data-primary], no tiles, no other tab bar or Disclosure before it', async () => {
    const { container } = renderPage()
    await screen.findByText('test_flaky_1')
    const primary = container.querySelector('[data-primary]')
    expect(primary).not.toBeNull()
    expect(container.querySelectorAll('[data-primary]')).toHaveLength(1)
    expect(within(primary as HTMLElement).getByRole('table', { name: 'Flaky tests' })).toBeInTheDocument()
    const header = container.querySelector('[data-page-header]') as HTMLElement
    const pageTabs = within(header).getByRole('tablist', { name: 'Flaky tests' })
    expect(pageTabs.compareDocumentPosition(primary as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    for (const other of container.querySelectorAll('[role="tablist"], [data-disclosure]')) {
      if (other === pageTabs) continue
        expect(other.compareDocumentPosition(primary as Node) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    }
    // The four stat tiles of each old page are gone (the counts are in the tab labels).
    expect(screen.queryByText('Awaiting review')).toBeNull()
    expect(screen.queryByText('Active quarantines')).toBeNull()
  })

  it('names the window and agrees in number in the subtitle', async () => {
    renderPage()
    expect(await screen.findByText('2 flaky tests · 1 quarantine candidate · last 30 days')).toBeInTheDocument()
  })

  it('carries the counts in the tab labels', async () => {
    renderPage()
    await waitFor(() => expect(tab(/^Detected/)).toHaveTextContent('Detected3'))
    expect(tab(/^Proposed/)).toHaveTextContent('Proposed2')
    expect(tab(/^Quarantined/)).toHaveTextContent('Quarantined1')
    expect(tab(/^History/)).toHaveTextContent('History2')
    expect(tab(/^Detected/)).toHaveAttribute('aria-selected', 'true')
  })

  it('opens the help topic that owns the flaky definition, not a copy of it', () => {
    renderPage()
    expect(screen.getByRole('button', { name: 'Help: Flaky tests' })).toHaveAttribute('data-help-topic', 'flaky')
  })

  it('renders a failed analysis as unavailable, not as "no flaky tests"', async () => {
    coachMock.getFlakyCoach.mockRejectedValue(new Error('Network Error'))
    renderPage()
    expect(await screen.findByTestId('data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText('No flaky tests found')).toBeNull()
  })
})

describe('FlakyTestsPage — tabs (?tab=)', () => {
  it.each([
    ['proposed', /^Proposed/, 'Quarantine proposals', ['test_quarantine_a', 'test_quarantine_c']],
    ['quarantined', /^Quarantined/, 'Quarantined tests', ['test_quarantine_b']],
    ['history', /^History/, 'Quarantine history', ['test_quarantine_d', 'test_quarantine_e']],
  ])('?tab=%s selects its tab and renders its section', async (id, name, section, rows) => {
    const { container } = renderPage(`/flaky?tab=${id}`)
    expect(tab(name)).toHaveAttribute('aria-selected', 'true')
    const region = await screen.findByRole('region', { name: section })
    expect(region).toHaveAttribute('data-primary')
    for (const row of rows) expect(await within(region).findByText(row)).toBeInTheDocument()
    expect(within(region).getAllByTestId('quarantine-row')).toHaveLength(rows.length)
    expect(container.querySelector('table[aria-label="Flaky tests"]')).toBeNull()
  })

  it('History asks for settled requests; the live tabs share the live list', async () => {
    renderPage('/flaky?tab=history')
    await screen.findByText('test_quarantine_d')
    expect(quarantineMock.list).toHaveBeenCalledWith(expect.objectContaining({ live_only: false }))
  })

  it('a tab click writes ?tab= and the default tab has the clean URL', async () => {
    renderPage()
    fireEvent.click(tab(/^Quarantined/))
    expect(currentUrl()).toBe('/flaky?tab=quarantined')
    fireEvent.click(tab(/^Detected/))
    expect(currentUrl()).toBe('/flaky')
  })

  it('an unknown ?tab= falls back to Detected', async () => {
    renderPage('/flaky?tab=coach')
    expect(tab(/^Detected/)).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('test_flaky_1')).toBeInTheDocument()
  })
})

describe('FlakyTestsPage — Detected', () => {
  const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement

  it('shows the recommendation column and filters by it (the old tiles)', async () => {
    renderPage()
    await screen.findByText('test_flaky_1')
    expect(screen.getByRole('columnheader', { name: 'Recommendation' })).toBeInTheDocument()
    expect(within(row('test_flaky_2')).getByText('Investigate')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /^Monitor/ }))
    expect(screen.queryByText('test_flaky_1')).toBeNull()
    expect(screen.getByText('test_flaky_3')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /^Healthy/ }))
    expect(screen.getByText('No Healthy tests')).toBeInTheDocument()
  })

  it('a test with a live request shows its state and links to its tab', async () => {
    renderPage()
    await screen.findByText('test_flaky_1')
    const proposed = await within(row('test_flaky_1')).findByRole('button', { name: /Proposed/ })
    expect(within(row('test_flaky_2')).getByRole('button', { name: /Quarantined/ })).toBeInTheDocument()
    expect(within(row('test_flaky_1')).queryByRole('button', { name: /Propose quarantine/ })).toBeNull()
    fireEvent.click(proposed)
    expect(currentUrl()).toBe('/flaky?tab=proposed')
  })

  it('"Propose quarantine" opens the proposal dialog, prefilled, and refreshes the live list after it', async () => {
    renderPage()
    await screen.findByText('test_flaky_1')
    const button = await within(row('test_flaky_3')).findByRole('button', { name: 'Propose quarantine' })
    fireEvent.click(button)
    const dialog = screen.getByRole('dialog', { name: 'Propose quarantine' })
    expect(within(dialog).getByText('test_flaky_3')).toBeInTheDocument()
    quarantineMock.propose.mockResolvedValue(request('f', 'PROPOSED', 'fp-3'))
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Flips on CI only' } })
    const listCalls = quarantineMock.list.mock.calls.length
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Propose quarantine' }))
    })
    expect(quarantineMock.propose).toHaveBeenCalledWith(
      expect.objectContaining({ project_id: 'p1', test_fingerprint: 'fp-3', test_name: 'test_flaky_3', fail_count: 8 }),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Propose quarantine' })).toBeNull())
    await waitFor(() => expect(quarantineMock.list.mock.calls.length).toBeGreaterThan(listCalls))
  })

  it('offers no proposal to someone who cannot propose (QA_LEAD+ on the server)', async () => {
    state.canAccessManagement = false
    renderPage()
    await screen.findByText('test_flaky_3')
    expect(screen.queryByRole('button', { name: 'Propose quarantine' })).toBeNull()
  })

  // Browser E2E pass (2026-10-08): every quarantine endpoint answers 503
  // while the flag is off, and every row offered a proposal that the dialog
  // could only fail on ("Flaky auto-quarantine is disabled").
  it('with quarantine off: no proposal on any row, and one line saying how to turn it on', async () => {
    state.quarantineOn = false
    renderPage()
    await screen.findByText('test_flaky_3')
    expect(screen.queryByRole('button', { name: 'Propose quarantine' })).toBeNull()
    expect(document.querySelector('[data-quarantine-off]')).toHaveTextContent(
      'Quarantine is off for this project, so no test can be proposed for it. An admin turns it on with the flaky_auto_quarantine flag in Settings › Feature flags.',
    )
  })

  it('while the flag is unknown: no proposal yet, and no claim that it is off', async () => {
    state.quarantineOn = undefined
    renderPage()
    await screen.findByText('test_flaky_3')
    expect(screen.queryByRole('button', { name: 'Propose quarantine' })).toBeNull()
    expect(document.querySelector('[data-quarantine-off]')).toBeNull()
  })

  it('opens a row in the side panel (it used to expand inline) and closes it', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'test_flaky_1' }))
    const panel = screen.getByRole('complementary', { name: 'test_flaky_1' })
    expect(within(panel).getByText('Timing: a wait races the response')).toBeInTheDocument()
    expect(within(panel).getByText('Stabilise test 1')).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Close panel' }))
    expect(screen.queryByRole('complementary', { name: 'test_flaky_1' })).toBeNull()
  })
})
