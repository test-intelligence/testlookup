/**
 * W14 AGT-07 (`W14-contract.md` §2.5 test 6, §0.14-§0.18): `/deep-investigate` reads three server answers.
 *
 *  - on load: the pre-scan of the focused run, page 1 of the project's history, and the estimate with NO query;
 *  - the Pre-scan facet is the server's (groups of failing results by error signature); the page computes no
 *    minutes (the formula and the stale price-book comments are gone);
 *  - Estimate re-asks the estimate with the selection's COUNT (a GET, nothing queued); Run on selection posts
 *    the two selected groups' failure ids; "already in progress" is shown as such;
 *  - the estimate's gate refusing → no Run CTA anywhere (header, verdict, configuration), the gate's sentence.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDeepEstimate, useDeepHistory, usePreScan } from '@/hooks/useAgentOps'
import { ESTIMATE_FIXTURE, HISTORY_FIXTURE, idleSwr, PRESCAN_FIXTURE } from '@/test/agentOpsMocks'
import DeepInvestigationPage from './DeepInvestigationPage'

/** The page's own source (the `App.routeTargets.test.ts` pattern), to prove the synthesis is gone. */
const pageSource = Object.values(
  import.meta.glob('./DeepInvestigationPage.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>,
)[0]

const { hooks, triggerDeep } = vi.hoisted(() => ({
  hooks: {
    useFailureClusters: vi.fn(), useDeepFindings: vi.fn(), usePipelineStatus: vi.fn(), useRuns: vi.fn(), useRun: vi.fn(),
  },
  triggerDeep: vi.fn(),
}))

vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())
vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: hooks.useFailureClusters, useDeepFindings: hooks.useDeepFindings, usePipelineStatus: hooks.usePipelineStatus,
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: hooks.useRuns, useRun: hooks.useRun }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useAIConfig')>()),
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true } })),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: true, canAccessManagement: true }) }))
vi.mock('@/services/deepInvestigationService', () => ({ deepInvestigationService: { triggerDeep } }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))
vi.mock('@/hooks/useIntegrationHealth', () => ({
  useIntegrationStatus: vi.fn(() => ({ statuses: [{ provider: 'github', status: 'healthy', last_checked_at: null, message: null, response_ms: 1, consecutive_failures: 0, last_success_at: null }], isLoading: false, isError: false })),
}))
vi.mock('@/hooks/useLlmBudget', () => ({
  useProjectUsage: vi.fn(() => ({ usage: undefined, isLoading: false, isError: false, refresh: vi.fn() })),
  useProjectQuota: vi.fn(() => ({ quota: null, isLoading: false, isError: false, refresh: vi.fn() })),
}))
vi.mock('@/hooks/useInvestigation', () => ({
  useInvestigation: vi.fn(() => ({ data: undefined, mutate: vi.fn() })),
  useInvestigations: vi.fn(() => ({ data: { items: [], total: 0 }, mutate: vi.fn() })),
}))
vi.mock('@/hooks/useAgentGovernance', () => ({ useAgentPolicies: vi.fn(() => ({ policies: [], investigatorPolicy: null })) }))
vi.mock('@/hooks/useDecisionTrail', () => ({ useDecisionTrail: vi.fn(() => ({ data: undefined, isLoading: false })) }))

const RUN = {
  id: 'run-1', project_id: 'proj-1', build_number: '42', status: 'FAILED', passed_tests: 5, failed_tests: 12, broken_tests: 0,
  skipped_tests: 0, total_tests: 17, pass_rate: 29, created_at: '2026-10-20T10:00:00Z', run_seq: 39,
}
const RULES = 'Deep investigation requires LLM or Auto mode. Current mode: rules.'

beforeEach(() => {
  for (const fn of Object.values(hooks)) fn.mockReset()
  triggerDeep.mockReset()
  triggerDeep.mockResolvedValue({ message: 'queued', run_id: 'run-1', task_id: 't', pipeline_run_id: 'p' })
  hooks.useFailureClusters.mockReturnValue({
    data: [{ cluster_id: 'cl-1', label: 'Navigation timeouts', representative_error: 'TimeoutError', member_test_ids: ['t1'], size: 7, cohesion_score: 0.9 }],
  })
  hooks.useDeepFindings.mockReturnValue({ data: [] })
  hooks.usePipelineStatus.mockReturnValue({ data: { status: 'completed' }, mutate: vi.fn() })
  hooks.useRuns.mockReturnValue({ data: { items: [RUN] }, isLoading: false, isValidating: false })
  hooks.useRun.mockReturnValue({ data: undefined, isLoading: false })
  vi.mocked(usePreScan).mockReturnValue(idleSwr({ data: PRESCAN_FIXTURE }))
  vi.mocked(useDeepHistory).mockReturnValue(idleSwr({ data: HISTORY_FIXTURE }))
  vi.mocked(useDeepEstimate).mockReturnValue(idleSwr({ data: ESTIMATE_FIXTURE }))
})

function renderPage() {
  render(<MemoryRouter initialEntries={['/deep-investigate?run=run-1']}><DeepInvestigationPage /></MemoryRouter>)
  return screen.getByRole('region', { name: 'Investigation verdict' })
}
const configCard = () => screen.getByTestId('deep-run-configuration')

describe('/deep-investigate — the three server answers', () => {
  it('on load: the pre-scan, page 1 of the history, and the estimate with no query', () => {
    renderPage()
    expect(usePreScan).toHaveBeenLastCalledWith('run-1')
    expect(useDeepHistory).toHaveBeenCalledWith('proj-1', 1)
    expect(vi.mocked(useDeepHistory).mock.calls.every(([, page]) => page === 1)).toBe(true)
    expect(useDeepEstimate).toHaveBeenLastCalledWith('run-1', {})
  })

  it('the Pre-scan facet is the server\'s groups; the lede names them beside the last analysis\'s clusters', () => {
    const verdict = renderPage()
    expect(verdict).toHaveTextContent('Pre-scan3 groupsby signature')
    // The facet is narrow: the full sentence rides the caption's title.
    expect(within(verdict).getByTitle('12 failing · by error signature · clusters shown')).toHaveTextContent('by signature')
    expect(verdict).toHaveTextContent(
      'The error-signature pre-scan finds 3 groups among 12 failing results; the last analysis proposed 1 cluster.',
    )
  })

  it('no failing result: the facet says so ("—"), never "0 groups"', () => {
    vi.mocked(usePreScan).mockReturnValue(idleSwr({ data: { ...PRESCAN_FIXTURE, measured: false, failing_results: 0, groups_total: 0, groups: [] } }))
    const verdict = renderPage()
    expect(verdict).toHaveTextContent('Pre-scan—no failing results')
    expect(verdict).not.toHaveTextContent('0 groups')
  })

  it('the page computes no minutes: the formula, the breakdown and the stale price-book comments are gone', () => {
    expect(pageSource).not.toMatch(/estimateMinutes|estimateBreakdown|EstimateRow/)
    expect(pageSource).not.toMatch(/price book/)
    expect(pageSource).not.toMatch(/0\.05 min|eligibleFailures \* 0\.05/)
    // An estimate that has not loaded shows no minutes at all; the measured one shows the server's p50.
    vi.mocked(useDeepEstimate).mockReturnValue(idleSwr())
    const { unmount } = render(<MemoryRouter initialEntries={['/deep-investigate?run=run-1']}><DeepInvestigationPage /></MemoryRouter>)
    expect(screen.getByRole('region', { name: 'Investigation verdict' })).not.toHaveTextContent(/~\d/)
    unmount()
    vi.mocked(useDeepEstimate).mockReturnValue(idleSwr({ data: ESTIMATE_FIXTURE }))
    expect(renderPage()).toHaveTextContent('~4 min')
  })
})

describe('/deep-investigate — the run configuration', () => {
  it('Estimate re-asks with the selection\'s COUNT (a read: nothing is queued)', () => {
    renderPage()
    fireEvent.click(within(configCard()).getByRole('checkbox', { name: /^Select 7 failures/ }))
    fireEvent.click(within(configCard()).getByRole('checkbox', { name: /^Select 3 failures/ }))
    // Ticking alone asks nothing new.
    expect(useDeepEstimate).toHaveBeenLastCalledWith('run-1', {})
    fireEvent.click(within(configCard()).getByRole('button', { name: 'Estimate' }))
    expect(useDeepEstimate).toHaveBeenLastCalledWith('run-1', { selected_failures: 10 })
    expect(triggerDeep).not.toHaveBeenCalled()
  })

  it('Run on selection posts the two groups\' failure ids and the tightened tier', async () => {
    renderPage()
    fireEvent.click(within(configCard()).getByRole('checkbox', { name: /^Select 7 failures/ }))
    fireEvent.click(within(configCard()).getByRole('checkbox', { name: /^Select 3 failures/ }))
    fireEvent.change(within(configCard()).getByRole('combobox', { name: 'Model tier' }), { target: { value: 'deterministic' } })
    fireEvent.click(within(configCard()).getByRole('button', { name: 'Run on selection' }))
    await waitFor(() => expect(triggerDeep).toHaveBeenCalledTimes(1))
    const [timeouts, asserts] = PRESCAN_FIXTURE.groups
    expect(triggerDeep).toHaveBeenCalledWith('run-1', 'deep', {
      failure_ids: [...timeouts.member_result_ids, ...asserts.member_result_ids],
      model_tier: 'deterministic',
    })
    expect(await within(configCard()).findByText('Deep analysis queued for the 10 selected failures.')).toBeInTheDocument()
  })

  it('a deep run already in progress is answered as such, not as a second queue', async () => {
    triggerDeep.mockResolvedValue({ message: 'Deep investigation already in progress', run_id: 'run-1', pipeline_run_id: 'p-0', already_in_progress: true })
    renderPage()
    fireEvent.click(within(configCard()).getByRole('checkbox', { name: /^Select 3 failures/ }))
    fireEvent.click(within(configCard()).getByRole('button', { name: 'Run on selection' }))
    expect(await within(configCard()).findByText('Deep investigation already in progress')).toHaveAttribute('role', 'note')
  })

  it('the estimate\'s gate refuses (Auto resolving to Rules): no Run CTA anywhere, the gate\'s sentence', () => {
    vi.mocked(useDeepEstimate).mockReturnValue(idleSwr({
      data: { ...ESTIMATE_FIXTURE, gate: { allowed: false, reason_code: 'rules_mode', message: RULES, analysis_mode: 'rules', deep_investigation_enabled: true } },
    }))
    const verdict = renderPage()
    expect(screen.queryByRole('button', { name: /Run Deep Analysis/ })).toBeNull()
    expect(within(verdict).queryByRole('button', { name: /Run on all/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run on selection' })).toBeNull()
    // The page's two notes (header + verdict), as for the toggle; the card states it too.
    expect(screen.getAllByTestId('deep-disabled-note').map((n) => n.textContent)).toEqual([RULES, RULES])
    expect(within(configCard()).getByTestId('deep-config-gate-note')).toHaveTextContent(RULES)
    // Estimating stays possible (a read).
    expect(within(configCard()).getByRole('button', { name: 'Estimate' })).toBeEnabled()
  })

  it('while the estimate loads the mode read decides (as before W14)', () => {
    vi.mocked(useDeepEstimate).mockReturnValue(idleSwr())
    renderPage()
    expect(screen.getByRole('button', { name: /Run Deep Analysis/ })).toBeInTheDocument()
    expect(screen.queryByTestId('deep-disabled-note')).toBeNull()
  })

  it('Investigated runs · 4 pages the history in the Recent runs panel', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Investigated runs · 4' }))
    expect(screen.getByRole('heading', { level: 3, name: /^Investigated runs/ })).toBeInTheDocument()
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(5)
  })
})
