/**
 * UX redesign P4: Deep Investigation's per-run part moved onto the Run page's
 * Analysis tab (`/deep-investigate/:runId` redirects there), and the landing
 * page (`/deep-investigate`, the Failures section's "Root cause (AI)" tab)
 * keeps its focused run in `?run=`.
 *
 *  - `RunDeepClusters`: the run's proposed clusters and "Analyze failures",
 *    which queues a deep investigation of that run (QA engineers only; not
 *    for a run with no failures, nor while one is running).
 *  - The landing page no longer navigates to `/deep-investigate/:id` — that
 *    URL now leaves the page — for its newest-run focus or its recent-runs
 *    rows; it writes `?run=` and reads it back.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage, { RunDeepClusters } from './DeepInvestigationPage'

const { hooks, permissions, triggerDeep } = vi.hoisted(() => ({
  hooks: {
    useFailureClusters: vi.fn(),
    useDeepFindings: vi.fn(),
    usePipelineStatus: vi.fn(),
    useRuns: vi.fn(),
    useRun: vi.fn(),
  },
  permissions: { isQaEngineer: true },
  triggerDeep: vi.fn(),
}))

vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: hooks.useFailureClusters,
  useDeepFindings: hooks.useDeepFindings,
  usePipelineStatus: hooks.usePipelineStatus,
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: hooks.useRuns, useRun: hooks.useRun }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))
vi.mock('@/services/deepInvestigationService', () => ({ deepInvestigationService: { triggerDeep } }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))
vi.mock('@/hooks/useIntegrationHealth', () => ({
  useIntegrationStatus: vi.fn(() => ({ statuses: [], isLoading: false, isError: false })),
}))
vi.mock('@/hooks/useLlmBudget', () => ({
  useProjectUsage: vi.fn(() => ({ usage: undefined, isLoading: false, isError: false, refresh: vi.fn() })),
  useProjectQuota: vi.fn(() => ({ quota: null, isLoading: false, isError: false, refresh: vi.fn() })),
}))
vi.mock('@/hooks/useInvestigation', () => ({
  useInvestigation: vi.fn(() => ({ data: undefined, mutate: vi.fn() })),
  useInvestigations: vi.fn(() => ({ data: { items: [], total: 0 }, mutate: vi.fn() })),
}))
vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentPolicies: vi.fn(() => ({ policies: [], investigatorPolicy: null })),
}))
vi.mock('@/hooks/useDecisionTrail', () => ({ useDecisionTrail: vi.fn(() => ({ data: undefined, isLoading: false })) }))

const RUN = {
  id: 'run-1', build_number: '42', status: 'FAILED', passed_tests: 5, failed_tests: 3, broken_tests: 0,
  skipped_tests: 0, total_tests: 8, pass_rate: 62.5, created_at: '2026-03-31T10:00:00Z', run_seq: 7,
  primary_suite_name: 'Checkout', suite_names: ['Checkout'],
}
const OLDER = { ...RUN, id: 'run-0', build_number: '41', run_seq: 6, created_at: '2026-03-30T10:00:00Z' }

beforeEach(() => {
  for (const fn of Object.values(hooks)) fn.mockReset()
  triggerDeep.mockReset()
  triggerDeep.mockResolvedValue({})
  permissions.isQaEngineer = true
  hooks.useFailureClusters.mockReturnValue({
    data: [{ cluster_id: 'cl-1', label: 'DB Timeouts', representative_error: 'TimeoutError', member_test_ids: ['t1', 't2'], size: 2, cohesion_score: 0.88 }],
  })
  hooks.useDeepFindings.mockReturnValue({ data: [] })
  hooks.usePipelineStatus.mockReturnValue({ data: null, mutate: vi.fn() })
  hooks.useRuns.mockReturnValue({ data: { items: [RUN, OLDER] }, isLoading: false, isValidating: false })
  hooks.useRun.mockReturnValue({ data: undefined, isLoading: false })
})

describe('RunDeepClusters — the run\'s clusters on the Analysis tab', () => {
  const renderClusters = (run = RUN as typeof RUN | null) =>
    render(
      <MemoryRouter>
        <RunDeepClusters runId="run-1" run={run} />
      </MemoryRouter>,
    )

  it('lists the run\'s proposed clusters, asking for that run only', () => {
    renderClusters()
    const section = screen.getByRole('region', { name: 'Deep investigation' })
    expect(within(section).getByText('DB Timeouts')).toBeInTheDocument()
    expect(hooks.useFailureClusters).toHaveBeenCalledWith('run-1')
    expect(hooks.useDeepFindings).toHaveBeenCalledWith('run-1')
    expect(hooks.usePipelineStatus).toHaveBeenCalledWith('run-1', 'deep')
    // Nothing project-wide (recent runs) is asked for.
    expect(hooks.useRuns).not.toHaveBeenCalled()
    // No "View all failures" link: the run's tests are a tab away on the same page.
    expect(within(section).queryByRole('link', { name: /View all/ })).toBeNull()
  })

  it('"Analyze failures" queues a deep investigation of the run', async () => {
    const refresh = vi.fn()
    hooks.usePipelineStatus.mockReturnValue({ data: null, mutate: refresh })
    renderClusters()
    fireEvent.click(screen.getByRole('button', { name: /Analyze failures/ }))
    await waitFor(() => expect(triggerDeep).toHaveBeenCalledWith('run-1', 'deep'))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
  })

  it('is disabled for a non-QA user, for a run with no failures, and while an investigation runs', () => {
    permissions.isQaEngineer = false
    const { unmount } = renderClusters()
    expect(screen.getByRole('button', { name: /Analyze failures/ })).toBeDisabled()
    unmount()

    permissions.isQaEngineer = true
    const green = renderClusters({ ...RUN, failed_tests: 0, broken_tests: 0 })
    expect(screen.getByRole('button', { name: /Analyze failures/ })).toBeDisabled()
    green.unmount()

    hooks.usePipelineStatus.mockReturnValue({ data: { status: 'running', stage_summary: { completed: 1, failed: 0, skipped: 0, pending: 3 } }, mutate: vi.fn() })
    renderClusters()
    expect(screen.getByRole('button', { name: /Analyze failures/ })).toBeDisabled()
    expect(screen.getByText('investigation running')).toBeInTheDocument()
  })
})

describe('/deep-investigate keeps its focused run in ?run=', () => {
  let location = ''
  function Probe() {
    const { pathname, search } = useLocation()
    useEffect(() => {
      location = `${pathname}${search}`
    })
    return null
  }
  const renderLanding = (entry: string) =>
    render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/deep-investigate" element={<><DeepInvestigationPage /><Probe /></>} />
          <Route path="/deep-investigate/:runId" element={<div>left the page</div>} />
        </Routes>
      </MemoryRouter>,
    )

  it('focuses the newest run in ?run= — never /deep-investigate/:id, which redirects away', async () => {
    renderLanding('/deep-investigate')
    await waitFor(() => expect(location).toBe('/deep-investigate?run=run-1'))
    expect(screen.queryByText('left the page')).toBeNull()
    expect(hooks.useFailureClusters).toHaveBeenLastCalledWith('run-1')
  })

  it('reads ?run= and opens another recent run in place', async () => {
    renderLanding('/deep-investigate?run=run-0')
    await screen.findByRole('region', { name: 'Investigation verdict' })
    expect(hooks.useFailureClusters).toHaveBeenLastCalledWith('run-0')
    const rows = screen.getAllByRole('row').filter((row) => within(row).queryByText('Run #7'))
    fireEvent.click(rows[0])
    await waitFor(() => expect(location).toBe('/deep-investigate?run=run-1'))
    expect(hooks.useFailureClusters).toHaveBeenLastCalledWith('run-1')
  })
})
