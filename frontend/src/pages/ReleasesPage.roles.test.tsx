/**
 * Releases: deleting a release, removing a phase and unlinking a run are
 * ADMIN on the API (`routers/releases.py`: DELETE /releases/{id},
 * DELETE .../phases/{id}, DELETE .../test-runs/{run}). The page is QA lead,
 * and a QA lead was offered all three and got a 403 from each. They stay in
 * place, disabled, naming the role they need; an admin uses them as before.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/hooks/useReleases', () => ({ useReleases: vi.fn(), useRelease: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(() => ({ data: { items: [] } })) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: object) => unknown) => selector({
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }),
}))
// Its own requests and tests; not what this file is about.
vi.mock('@/components/compliance/CompliancePackPanel', () => ({ default: () => null }))
vi.mock('@/hooks/useReleaseGate', () => ({
  useReleaseGate: vi.fn(() => ({ data: null, isLoading: false, mutate: vi.fn() })),
  useReleaseBaseline: vi.fn(() => ({ data: { comparable: false, reason: 'this release has no baseline' } })),
  usePhaseGates: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useProjectCompliancePacks', () => ({
  PROJECT_COMPLIANCE_PACKS_KEY: 'project-compliance-packs',
  useProjectCompliancePacks: () => ({ packs: [], total: 0, isLoading: false, isError: false, refresh: vi.fn() }),
  refreshProjectCompliancePacks: vi.fn(),
}))
vi.mock('@/hooks/useActivityFeed', () => ({
  useActivityFeed: () => ({ events: [], isLoading: false, error: undefined }),
}))
vi.mock('@/services/releasesService', () => ({
  releasesService: {
    delete: vi.fn().mockResolvedValue(undefined),
    deletePhase: vi.fn().mockResolvedValue(undefined),
    unlinkRun: vi.fn().mockResolvedValue(undefined),
    update: vi.fn(), create: vi.fn(), updatePhase: vi.fn(), markOutcome: vi.fn(),
    // W05: the gate, the active release and sync (the hooks that read the gate are mocked above).
    evaluateGate: vi.fn(), evaluatePhaseGate: vi.fn(), activate: vi.fn(), sync: vi.fn(),
    getGate: vi.fn(), getGateBaseline: vi.fn(), getPhaseGates: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const RELEASE = {
  id: 'release-1', project_id: 'proj-1', project_name: 'Project One',
  name: 'Checkout 2.5', version: '2.5.0', description: null, status: 'in_progress',
  planned_date: '2026-04-10T00:00:00Z', released_at: null,
  created_at: '2026-04-01T00:00:00Z', updated_at: '2026-04-01T00:00:00Z',
  phases: [], test_run_count: 1,
}
const DETAIL = {
  ...RELEASE,
  phases: [{
    id: 'phase-1', release_id: 'release-1', name: 'Smoke', phase_type: 'qa_testing',
    status: 'in_progress', description: null, order_index: 0, planned_start: null,
    planned_end: null, actual_start: null, actual_end: null, exit_criteria: null,
    notes: null, created_at: '2026-04-01T00:00:00Z', updated_at: '2026-04-01T00:00:00Z',
  }],
  linked_runs: [{
    id: 'run-1', build_number: 'b42', status: 'completed', total_tests: 10,
    passed_tests: 9, failed_tests: 1, broken_tests: 0, skipped_tests: 0,
    pass_rate: 90, created_at: '2026-04-02T00:00:00Z', phase_id: null,
  }],
  outcomes: [],
  metrics: { total_runs: 1, total_tests: 10, total_passed: 9, total_failed: 1, avg_pass_rate: 90 },
}

beforeEach(async () => {
  vi.clearAllMocks()
  const { useRelease, useReleases } = await import('@/hooks/useReleases')
  vi.mocked(useRelease).mockImplementation(((id: string | null) => ({
    data: id ? DETAIL : undefined, isLoading: false, mutate: vi.fn(),
  })) as never)
  vi.mocked(useReleases).mockReturnValue({
    data: { items: [RELEASE] }, isLoading: false, mutate: vi.fn(),
  } as never)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/releases" element={<ReleasesPage />} />
        <Route path="/releases/:releaseId" element={<ReleasesPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

function openPanel() {
  renderAt('/releases')
  fireEvent.click(screen.getByRole('article', { name: /^Checkout 2\.5 2\.5\.0/ }))
  return screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })
}

describe('a QA lead', () => {
  beforeEach(() => signInAs('QA_LEAD'))

  it('cannot delete the release (side panel and full page), naming the role it needs', () => {
    const panel = openPanel()
    const remove = within(panel).getByRole('button', { name: 'Delete' })
    expect(remove).toBeDisabled()
    expect(remove).toHaveAttribute('title', 'Admin role required')
    fireEvent.click(remove)
    expect(releasesService.delete).not.toHaveBeenCalled()
    // Edit stays: PUT /releases/{id} is QA lead.
    expect(within(panel).getByRole('button', { name: 'Edit' })).toBeEnabled()
  })

  it('cannot remove a phase or unlink a run', () => {
    const panel = openPanel()
    const phase = within(panel).getByRole('button', { name: 'Remove phase Smoke' })
    const unlink = within(panel).getByRole('button', { name: 'Unlink run b42' })
    for (const button of [phase, unlink]) {
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('title', 'Admin role required')
      fireEvent.click(button)
    }
    expect(releasesService.deletePhase).not.toHaveBeenCalled()
    expect(releasesService.unlinkRun).not.toHaveBeenCalled()
  })

  it("is not offered Delete on the release's own page either", () => {
    renderAt('/releases/release-1')
    const remove = screen.getByRole('button', { name: 'Delete' })
    expect(remove).toBeDisabled()
    expect(remove).toHaveAttribute('title', 'Admin role required')
  })
})

describe('an admin (the control)', () => {
  beforeEach(() => signInAs('ADMIN'))

  it('deletes the release, removes the phase and unlinks the run', () => {
    const panel = openPanel()
    // W27 PAN-02: Remove phase and Delete ask in an inline strip (no native
    // confirm); only the strip's button sends the request.
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove phase Smoke' }))
    expect(releasesService.deletePhase).not.toHaveBeenCalled()
    fireEvent.click(within(within(panel).getByRole('group', { name: 'Remove phase "Smoke"?' })).getByRole('button', { name: 'Remove' }))
    expect(releasesService.deletePhase).toHaveBeenCalledWith('release-1', 'phase-1')
    fireEvent.click(within(panel).getByRole('button', { name: 'Unlink run b42' }))
    expect(releasesService.unlinkRun).toHaveBeenCalledWith('release-1', 'run-1')
    fireEvent.click(within(panel).getByRole('button', { name: 'Delete' }))
    expect(releasesService.delete).not.toHaveBeenCalled()
    const ask = within(panel).getByRole('group', { name: 'Delete this release and all its phases? This cannot be undone.' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }))
    expect(releasesService.delete).toHaveBeenCalledWith('release-1')
    expect(window.confirm).not.toHaveBeenCalled()
  })
})

/**
 * W05: recording a gate verdict (REL-01), making a release active and syncing
 * releases (REL-08) are QA lead on the API. A QA lead sees Evaluate gate,
 * Evaluate all in progress, Release actions, Sync releases and Record gate
 * verdict; a QA engineer and a viewer see none of them, and still see the
 * verdict itself.
 */
describe('W05 gate, activation and sync controls follow the role', () => {
  const GO_GATE = {
    verdict: 'GO', decided_at: '2026-10-14T09:30:00Z', pass_rate: 97.5, denominator: 40, evidence_count: 40,
    run_count: 2, status_counts: { PASSED: 39, FAILED: 0, BROKEN: 0, SKIPPED: 1, UNKNOWN: 0 },
    measured: true, insufficient_reason: null, blocking_reasons: [], blocking_total: 0,
  }

  beforeEach(async () => {
    const { useReleases } = await import('@/hooks/useReleases')
    vi.mocked(useReleases).mockReturnValue({
      data: { items: [{ ...RELEASE, gate: GO_GATE }] }, isLoading: false, mutate: vi.fn(),
    } as never)
  })

  it('a QA lead is offered all of them', () => {
    signInAs('QA_LEAD')
    renderAt('/releases')
    const card = screen.getByRole('article', { name: /^Checkout 2\.5 2\.5\.0/ })
    expect(within(card).getByTestId('release-evaluate')).toHaveTextContent('Re-evaluate')
    expect(within(card).getByRole('button', { name: 'Release actions' })).toBeInTheDocument()
    expect(screen.getByTestId('release-evaluate-all')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sync releases/ })).toBeInTheDocument()
    fireEvent.click(card)
    const panel = screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })
    expect(within(panel).getByTestId('gate-panel-evaluate')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Record gate verdict' })).toBeInTheDocument()
  })

  it.each(['QA_ENGINEER', 'VIEWER'])('a %s sees the verdict and none of them', (role) => {
    signInAs(role)
    renderAt('/releases')
    const card = screen.getByRole('article', { name: /^Checkout 2\.5 2\.5\.0/ })
    expect(within(card).getByText('Go')).toBeInTheDocument()
    expect(within(card).queryByTestId('release-evaluate')).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Release actions' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('release-evaluate-all')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Sync releases/ })).not.toBeInTheDocument()
    fireEvent.click(card)
    const panel = screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })
    expect(within(panel).getByRole('heading', { name: 'Release gate' })).toBeInTheDocument()
    expect(within(panel).queryByTestId('gate-panel-evaluate')).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Record gate verdict' })).not.toBeInTheDocument()
    expect(releasesService.evaluateGate).not.toHaveBeenCalled()
  })
})
