/**
 * W05 "release-gate" on the releases page, as a QA lead:
 *
 * - REL-01: Evaluate gate (a card) records a verdict through
 *   `POST /releases/{id}/gate/evaluate` and re-reads the list and that
 *   release's gate; Evaluate all in progress walks every in-progress release,
 *   keeps going past a failure and says what happened in ONE toast; a
 *   `#gate` deep link scrolls the gate panel into view.
 * - REL-10: a phase-gate 409 on Completed / Skipped opens the reason dialog
 *   quoting the server, and the reason is resent under the server's exact
 *   field name; any other failure toasts the server's own sentence.
 * - REL-02: the editor sends `owner_user_id` only when it changed; search
 *   matches the owner's name, never the project's.
 * - REL-08: Set as active release (confirm first), and Sync releases.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'
import { appMutate } from '@/utils/swrCacheMutate'
import { useAuthStore } from '@/store/authStore'
import toast from 'react-hot-toast'

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
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: vi.fn(() => ({
    data: [
      { id: 'm-1', user_id: 'u-priya', project_id: 'proj-1', role: 'QA_LEAD', created_at: '2026-01-01T00:00:00Z', email: 'priya@example.test', username: 'priya', full_name: 'Priya Nair' },
      { id: 'm-2', user_id: 'u-bot', project_id: 'proj-1', role: 'QA_LEAD', created_at: '2026-01-01T00:00:00Z', email: 'bot@example.test', username: 'qa-lead-proj-1', full_name: 'QA lead', is_synthetic: true },
    ],
    isLoading: false,
  })),
}))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate: vi.fn(() => Promise.resolve(undefined)) }))
vi.mock('@/services/releasesService', () => ({
  releasesService: {
    list: vi.fn(), get: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(),
    addPhase: vi.fn(), updatePhase: vi.fn(), deletePhase: vi.fn(), linkRun: vi.fn(), unlinkRun: vi.fn(),
    markOutcome: vi.fn(), getGate: vi.fn(), getGateBaseline: vi.fn(), getPhaseGates: vi.fn(),
    evaluateGate: vi.fn(), evaluatePhaseGate: vi.fn(), activate: vi.fn(), sync: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const svc = vi.mocked(releasesService)

const phase = (id: string, name: string, status: string) => ({
  id, release_id: 'rel-a', name, phase_type: 'qa_testing', status, description: null, order_index: 1,
  planned_start: null, planned_end: null, actual_start: null, actual_end: null, exit_criteria: null,
  notes: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
})
const base = {
  project_id: 'proj-1', project_name: 'Project One', description: null, planned_date: null,
  released_at: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  test_run_count: 0, phases: [] as ReturnType<typeof phase>[],
}
const REL_A = {
  ...base, id: 'rel-a', name: 'Checkout 2.5', version: '2.5.0', status: 'in_progress',
  owner: { id: 'u-priya', name: 'Priya Nair' }, owner_user_id: 'u-priya', gate: null,
  phases: [phase('ph-1', 'Regression', 'in_progress')],
}
const REL_B = { ...base, id: 'rel-b', name: 'Search 1.4', version: '1.4.0', status: 'in_progress', owner: null, gate: null }
/** Planned, no owner, in a project whose NAME holds "Priya": search must not find it by that. */
const REL_C = { ...base, id: 'rel-c', name: 'Billing 3.0', version: '3.0.0', status: 'planning', project_name: 'Priya Labs', owner: null, gate: null }
const RELEASES = [REL_A, REL_B, REL_C]

const listMutate = vi.fn(() => Promise.resolve(undefined))
const detailMutate = vi.fn(() => Promise.resolve(undefined))

beforeEach(async () => {
  vi.clearAllMocks()
  // `clearAllMocks` keeps queued `mock*Once` answers: a test that failed
  // before consuming them would hand them to the next one.
  for (const fn of Object.values(svc)) vi.mocked(fn).mockReset()
  const { useRelease, useReleases } = await import('@/hooks/useReleases')
  vi.mocked(useReleases).mockReturnValue({ data: { items: RELEASES, total: 3 }, isLoading: false, mutate: listMutate } as never)
  vi.mocked(useRelease).mockImplementation(((id: string | null) => ({
    data: id
      ? { ...RELEASES.find(r => r.id === id), linked_runs: [], outcomes: [], metrics: { total_runs: 0, total_tests: 0, total_passed: 0, total_failed: 0, avg_pass_rate: null } }
      : undefined,
    isLoading: false,
    mutate: detailMutate,
  })) as never)
  useAuthStore.setState({ user: { id: 'lead', role: 'QA_LEAD' } as never })
})

afterEach(() => {
  vi.restoreAllMocks()
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

const card = (name: RegExp) => screen.getByRole('article', { name })
const httpError = (status: number, detail: string) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })
const GO_RESULT = {
  release_id: 'rel-a', verdict: 'GO', blocking_reasons: [], recorded: true, decision_id: 'd-1',
  scorecard: { pass_rate: 97.5, evidence_count: 40, denominator: 40, insufficient_reason: null },
}
const NO_GO_RESULT = {
  release_id: 'rel-b', verdict: 'NO_GO', recorded: true, decision_id: 'd-2',
  blocking_reasons: ['checkout::a is FAILED', 'checkout::b is FAILED', 'checkout::c is BROKEN'],
  scorecard: { pass_rate: 80, evidence_count: 30, denominator: 30, insufficient_reason: null },
}

describe('REL-01: Evaluate gate on a card', () => {
  it('records the verdict once, toasts it, and re-reads the list and that release', async () => {
    svc.evaluateGate.mockResolvedValue(GO_RESULT as never)
    renderAt('/releases')
    fireEvent.click(within(card(/^Checkout 2\.5 2\.5\.0/)).getByTestId('release-evaluate'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Checkout 2.5: Go — pass rate 97.5% over 40 tests'))
    expect(svc.evaluateGate).toHaveBeenCalledTimes(1)
    expect(svc.evaluateGate).toHaveBeenCalledWith('rel-a')
    expect(listMutate).toHaveBeenCalled()
    for (const key of [['release-detail', 'rel-a'], ['release-gate', 'rel-a'], ['release-phase-gates', 'rel-a']]) {
      expect(appMutate, String(key)).toHaveBeenCalledWith(key)
    }
    // The button does not open the card's side panel.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('a No-Go verdict names how many items block it', async () => {
    svc.evaluateGate.mockResolvedValue(NO_GO_RESULT as never)
    renderAt('/releases')
    fireEvent.click(within(card(/^Search 1\.4 1\.4\.0/)).getByTestId('release-evaluate'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Search 1.4: No-Go — 3 blocking items'))
  })

  it('a failed evaluation adds no toast of its own (the interceptor shows the server sentence)', async () => {
    svc.evaluateGate.mockRejectedValue(httpError(500, 'boom'))
    renderAt('/releases')
    fireEvent.click(within(card(/^Checkout 2\.5 2\.5\.0/)).getByTestId('release-evaluate'))
    await waitFor(() => expect(within(card(/^Checkout 2\.5 2\.5\.0/)).getByTestId('release-evaluate')).toBeEnabled())
    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('REL-01: Evaluate all in progress', () => {
  it('evaluates every in-progress release in turn, quietly, and sums them up in one toast', async () => {
    let releaseFirst: (v: unknown) => void = () => {}
    svc.evaluateGate
      .mockImplementationOnce(() => new Promise(resolve => { releaseFirst = resolve }) as never)
      .mockResolvedValueOnce(NO_GO_RESULT as never)
    renderAt('/releases')
    const all = screen.getByTestId('release-evaluate-all')
    expect(all).toHaveTextContent('Evaluate all in progress')
    fireEvent.click(all)
    await waitFor(() => expect(all).toHaveTextContent('Evaluating 1 of 2…'))
    expect(all).toBeDisabled()
    releaseFirst(GO_RESULT)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Evaluated 2 releases: 1 Go, 1 No-Go, 0 not evaluated.'))
    // Only the in-progress releases, each quiet (the one toast is the page's).
    expect(svc.evaluateGate.mock.calls).toEqual([
      ['rel-a', { suppressToast: true }],
      ['rel-b', { suppressToast: true }],
    ])
    expect(all).toHaveTextContent('Evaluate all in progress')
    expect(listMutate).toHaveBeenCalled()
    expect(appMutate).toHaveBeenCalledWith(['release-gate', 'rel-a'])
    expect(appMutate).toHaveBeenCalledWith(['release-gate', 'rel-b'])
  })

  it('keeps going past a failing release and reports k of n with the first server detail', async () => {
    svc.evaluateGate
      .mockRejectedValueOnce(httpError(500, 'Rollup timed out'))
      .mockResolvedValueOnce(NO_GO_RESULT as never)
    renderAt('/releases')
    fireEvent.click(screen.getByTestId('release-evaluate-all'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Evaluated 1 of 2 releases; 1 failed — Rollup timed out'))
    expect(svc.evaluateGate).toHaveBeenCalledTimes(2)
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('is not on a release\'s own page, which has its gate panel instead', () => {
    renderAt('/releases/rel-a')
    expect(screen.queryByTestId('release-evaluate-all')).not.toBeInTheDocument()
    expect(screen.getByTestId('gate-panel-evaluate')).toHaveTextContent('Evaluate gate')
  })
})

describe('REL-01: the #gate deep link', () => {
  it('scrolls the gate panel into view on the release page', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
    renderAt('/releases/rel-a#gate')
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start' }))
    const target = scrollIntoView.mock.instances[0] as unknown as HTMLElement
    expect(target.id).toBe('gate')
    expect(within(target).getByRole('heading', { name: 'Release gate' })).toBeInTheDocument()
  })

  it('the gate panel sits above Release Phases in the detail', () => {
    const { container } = renderAt('/releases/rel-a')
    const gate = container.querySelector('section#gate') as HTMLElement
    const phases = screen.getByRole('heading', { name: 'Release Phases' })
    expect(gate.compareDocumentPosition(phases) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('REL-10: a phase-gate 409 asks for the reason', () => {
  const NO_GO_DETAIL = "This phase's gate says NO_GO: test_pay (checkout-e2e) is FAILED. Fix it, or supply gate_override_reason to complete it anyway."
  const SKIP_DETAIL = "Skipping a phase needs a reason — it counts as done in the release's all-phases check, so an unexplained skip is indistinguishable from work that passed."

  function openPanelPhaseSelect() {
    renderAt('/releases')
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    const panel = screen.getByRole('dialog', { name: 'Checkout 2.5 2.5.0' })
    return within(panel).getByRole('combobox', { name: 'Status of phase Regression' })
  }

  it('Completed: the dialog quotes the server, and the reason goes as gate_override_reason', async () => {
    svc.updatePhase
      .mockRejectedValueOnce(httpError(409, NO_GO_DETAIL))
      .mockResolvedValueOnce({ ...phase('ph-1', 'Regression', 'completed'), all_phases_completed: true } as never)
    fireEvent.change(openPanelPhaseSelect(), { target: { value: 'completed' } })
    const dialog = await screen.findByRole('dialog', { name: 'Complete "Regression" anyway?' })
    expect(dialog.querySelector('[data-phase-gate-detail]')).toHaveTextContent(NO_GO_DETAIL)
    // The first attempt is quiet: no generic toast fires before the dialog.
    expect(svc.updatePhase).toHaveBeenNthCalledWith(1, 'rel-a', 'ph-1', { status: 'completed' }, { suppressToast: true })
    expect(toast.error).not.toHaveBeenCalled()

    fireEvent.change(within(dialog).getByLabelText('Reason (recorded in the audit log)'), { target: { value: '  Customer waiver CAB-142 ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete with override' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Phase "Regression" completed with an override'))
    expect(svc.updatePhase).toHaveBeenCalledTimes(2)
    expect(svc.updatePhase.mock.calls[1]).toEqual(['rel-a', 'ph-1', { status: 'completed', gate_override_reason: 'Customer waiver CAB-142' }])
    expect(screen.queryByRole('dialog', { name: /anyway\?/ })).not.toBeInTheDocument()
    expect(detailMutate).toHaveBeenCalled()
    expect(appMutate).toHaveBeenCalledWith(['release-phase-gates', 'rel-a'])
    expect(toast.success).toHaveBeenCalledWith('All phases completed! You can now mark this release as Released.')
  })

  it('Skipped: the reason goes as skip_reason', async () => {
    svc.updatePhase
      .mockRejectedValueOnce(httpError(409, SKIP_DETAIL))
      .mockResolvedValueOnce({ ...phase('ph-1', 'Regression', 'skipped'), all_phases_completed: false } as never)
    fireEvent.change(openPanelPhaseSelect(), { target: { value: 'skipped' } })
    const dialog = await screen.findByRole('dialog', { name: 'Skip "Regression"?' })
    expect(dialog.querySelector('[data-phase-gate-detail]')).toHaveTextContent(/^Skipping a phase needs a reason — it counts as done/)
    fireEvent.change(within(dialog).getByLabelText('Reason (recorded in the audit log)'), { target: { value: 'Deferred to 2026.12' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Skip phase' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Phase "Regression" skipped'))
    expect(svc.updatePhase.mock.calls[1]).toEqual(['rel-a', 'ph-1', { status: 'skipped', skip_reason: 'Deferred to 2026.12' }])
  })

  it('Cancel leaves the phase as it was, with one request', async () => {
    svc.updatePhase.mockRejectedValueOnce(httpError(409, NO_GO_DETAIL))
    const select = openPanelPhaseSelect()
    fireEvent.change(select, { target: { value: 'completed' } })
    const dialog = await screen.findByRole('dialog', { name: 'Complete "Regression" anyway?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: /anyway\?/ })).not.toBeInTheDocument()
    expect(svc.updatePhase).toHaveBeenCalledTimes(1)
    expect(select).toHaveValue('in_progress')
  })

  it.each([
    [403, 'QA lead role required'],
    [500, 'Internal error while evaluating the phase'],
  ])('a %s toasts the server detail and opens no dialog', async (status, detail) => {
    svc.updatePhase.mockRejectedValueOnce(httpError(status, detail))
    fireEvent.change(openPanelPhaseSelect(), { target: { value: 'completed' } })
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail))
    expect(toast.error).not.toHaveBeenCalledWith('Failed to update phase')
    expect(screen.queryByRole('dialog', { name: /anyway\?/ })).not.toBeInTheDocument()
  })

  it('a 409 on a transition that is not Completed or Skipped is a toast, not the dialog', async () => {
    svc.updatePhase.mockRejectedValueOnce(httpError(409, 'Some other conflict'))
    fireEvent.change(openPanelPhaseSelect(), { target: { value: 'pending' } })
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Some other conflict'))
    expect(screen.queryByRole('dialog', { name: /anyway\?|Skip/ })).not.toBeInTheDocument()
  })
})

describe('REL-01: phase rows on the detail', () => {
  it('show each phase\'s live gate chip, and a QA lead can record a phase verdict', async () => {
    const { usePhaseGates } = await import('@/hooks/useReleaseGate')
    vi.mocked(usePhaseGates).mockReturnValue({
      data: {
        release_id: 'rel-a', status: 'FAILING', failing_phases: ['Regression'], unevaluated_phases: [], phase_count: 1,
        phases: [{ phase_id: 'ph-1', phase_name: 'Regression', verdict: 'NO_GO', blocking_reasons: ['checkout::a is FAILED'], may_exit: false, recorded: false, scorecard: null }],
      },
    } as never)
    svc.evaluatePhaseGate.mockResolvedValue({ phase_id: 'ph-1', phase_name: 'Regression', verdict: 'NO_GO', blocking_reasons: [], may_exit: false, recorded: true, scorecard: null } as never)
    renderAt('/releases/rel-a')
    const row = document.getElementById('phase-ph-1') as HTMLElement
    const chip = within(row).getByTestId('phase-gate-chip')
    expect(chip).toHaveTextContent('Gate: No-Go')
    expect(chip).toHaveAttribute('data-verdict', 'NO_GO')
    fireEvent.click(within(row).getByRole('button', { name: 'Record gate verdict' }))
    await waitFor(() => expect(svc.evaluatePhaseGate).toHaveBeenCalledWith('rel-a', 'ph-1'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Recorded the gate verdict for "Regression": No-Go'))
    expect(appMutate).toHaveBeenCalledWith(['release-phase-gates', 'rel-a'])
  })
})

describe('REL-02: the owner', () => {
  function openEditor() {
    renderAt('/releases')
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    const panel = screen.getByRole('dialog', { name: 'Checkout 2.5 2.5.0' })
    fireEvent.click(within(panel).getByRole('button', { name: 'Edit' }))
    return screen.getByRole('dialog', { name: 'Edit Release' })
  }

  it('the editor offers the project\'s people (not the synthetic QA lead) and keeps the current owner', () => {
    const editor = openEditor()
    const owner = within(editor).getByRole('combobox', { name: 'Owner' })
    expect(owner).toHaveValue('u-priya')
    expect(within(owner).getAllByRole('option').map(o => o.textContent)).toEqual(['No owner', 'Priya Nair'])
  })

  it('Save sends no owner when it did not change', async () => {
    svc.update.mockResolvedValue({} as never)
    const editor = openEditor()
    fireEvent.click(within(editor).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(svc.update).toHaveBeenCalledTimes(1))
    expect(svc.update.mock.calls[0][1]).not.toHaveProperty('owner_user_id')
  })

  it('choosing No owner sends owner_user_id: null', async () => {
    svc.update.mockResolvedValue({} as never)
    const editor = openEditor()
    fireEvent.change(within(editor).getByRole('combobox', { name: 'Owner' }), { target: { value: '' } })
    fireEvent.click(within(editor).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(svc.update).toHaveBeenCalledTimes(1))
    expect(svc.update.mock.calls[0]).toEqual(['rel-a', expect.objectContaining({ owner_user_id: null })])
  })

  it('a new release has no Owner field (the server makes its creator the owner)', () => {
    renderAt('/releases')
    fireEvent.click(screen.getByRole('button', { name: /New release/ }))
    const editor = screen.getByRole('dialog', { name: 'New Release' })
    expect(within(editor).queryByRole('combobox', { name: 'Owner' })).not.toBeInTheDocument()
  })

  it("search matches the owner's name, never the project's or the No owner placeholder", () => {
    renderAt('/releases')
    const search = screen.getByPlaceholderText('Search releases, owners, versions…')
    fireEvent.change(search, { target: { value: 'priya' } })
    expect(screen.getByRole('article', { name: /^Checkout 2\.5/ })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: /^Billing 3\.0/ })).not.toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'owner' } })
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })
})

describe('REL-08: the active release and sync', () => {
  function chooseSetActive(name: RegExp) {
    fireEvent.click(within(card(name)).getByRole('button', { name: 'Release actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set as active release' }))
  }

  it('Set as active release asks first, then activates and re-reads the list', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    svc.activate.mockResolvedValue({ release_id: 'rel-c', is_active: true, deactivated: 'rel-a', changed: true } as never)
    renderAt('/releases')
    chooseSetActive(/^Billing 3\.0/)
    expect(confirm).toHaveBeenCalledWith('Make "Billing 3.0" the active release? Runs that no other rule matches will land here.')
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('"Billing 3.0" is now the active release'))
    expect(svc.activate).toHaveBeenCalledWith('rel-c')
    expect(listMutate).toHaveBeenCalled()
    expect(appMutate).toHaveBeenCalledWith(['release-detail', 'rel-a'])
    // The menu does not open the card's side panel.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('a declined confirm sends nothing; an already-active release says so', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    svc.activate.mockResolvedValue({ release_id: 'rel-c', is_active: true, deactivated: null, changed: false } as never)
    renderAt('/releases')
    chooseSetActive(/^Billing 3\.0/)
    expect(svc.activate).not.toHaveBeenCalled()
    chooseSetActive(/^Billing 3\.0/)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('"Billing 3.0" was already the active release'))
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('Sync releases opens the dialog; a sync that ran closes it and re-reads the list', async () => {
    svc.sync.mockResolvedValue({ source: 'github', created: 1, updated: 0, skipped: 2 } as never)
    const { container } = renderAt('/releases')
    const header = container.querySelector('[data-page-header]') as HTMLElement
    fireEvent.click(within(header).getByRole('button', { name: /Sync releases/ }))
    const dialog = screen.getByRole('dialog', { name: 'Sync releases' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Sync releases' })).not.toBeInTheDocument())
    expect(svc.sync).toHaveBeenCalledWith({ project_id: 'proj-1', source: 'github' })
    expect(toast.success).toHaveBeenCalledWith('Synced from GitHub: 1 created, 0 updated, 2 skipped.')
    expect(listMutate).toHaveBeenCalled()
  })

  it("is not offered on a release's own page", () => {
    const { container } = renderAt('/releases/rel-a')
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).queryByRole('button', { name: /Sync releases/ })).not.toBeInTheDocument()
  })
})

describe('a failed phase reads Failed in its status select', () => {
  it('shows "Failed", not the first option ("Pending")', async () => {
    const failed = { ...REL_A, phases: [phase('ph-1', 'Regression', 'failed')] }
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    vi.mocked(useReleases).mockReturnValue({ data: { items: [failed, REL_B, REL_C], total: 3 }, isLoading: false, mutate: listMutate } as never)
    vi.mocked(useRelease).mockImplementation(((id: string | null) => ({
      data: id === 'rel-a'
        ? { ...failed, linked_runs: [], outcomes: [], metrics: { total_runs: 0, total_tests: 0, total_passed: 0, total_failed: 0, avg_pass_rate: null } }
        : undefined,
      isLoading: false,
      mutate: detailMutate,
    })) as never)
    renderAt('/releases')
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    const panel = screen.getByRole('dialog', { name: 'Checkout 2.5 2.5.0' })
    const select = within(panel).getByRole('combobox', { name: 'Status of phase Regression' }) as HTMLSelectElement
    expect(select.value).toBe('failed')
    expect(select.selectedOptions[0]).toHaveTextContent('Failed')
  })
})
