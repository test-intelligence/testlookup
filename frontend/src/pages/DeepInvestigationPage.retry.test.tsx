/**
 * W04 AGT-04: Deep Investigation's FAILED card promised "retry from last
 * successful stage" while its CTA started a fresh run (`triggerDeep`).
 *
 * Now the CTA retries the deep pipeline itself (`POST /agents/pipelines/{id}/
 * retry`). The SERVER decides resume vs rerun and, for a rerun, has already
 * queued the fresh deep run, so the page only changes its copy and
 * revalidates the status: it never posts a second trigger. A 409 shows the
 * server's message and "Start a new run" (`deep-investigate/{run}`). The run
 * page's Analysis tab (`RunDeepClusters`) gets the same retry beside
 * "Analyze failures".
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage, { RunDeepClusters } from './DeepInvestigationPage'

const { hooks, permissions, triggerDeep, retryPipeline, mutate, aiMode } = vi.hoisted(() => ({
  hooks: {
    useFailureClusters: vi.fn(),
    useDeepFindings: vi.fn(),
    usePipelineStatus: vi.fn(),
    useRuns: vi.fn(),
    useRun: vi.fn(),
  },
  permissions: { isQaEngineer: true, canAccessManagement: true },
  triggerDeep: vi.fn(),
  retryPipeline: vi.fn(),
  mutate: vi.fn(),
  aiMode: { current: { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true } as Record<string, unknown> | undefined },
}))

vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: hooks.useFailureClusters,
  useDeepFindings: hooks.useDeepFindings,
  usePipelineStatus: hooks.usePipelineStatus,
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: hooks.useRuns, useRun: hooks.useRun }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useAIConfig')>()),
  useAIConfig: vi.fn(() => ({ data: aiMode.current })),
}))
vi.mock('@/services/deepInvestigationService', () => ({ deepInvestigationService: { triggerDeep } }))
vi.mock('@/services/agentService', () => ({ default: { retryPipeline } }))
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

const FAILED = {
  pipeline_run_id: 'pipe-1',
  workflow_type: 'deep',
  status: 'failed',
  started_at: '2026-03-31T10:01:00Z',
  completed_at: '2026-03-31T10:04:00Z',
  error: 'release_risk: timeout',
  stage_summary: { completed: 4, failed: 1, skipped: 0, pending: 3 },
}

const RESUME = {
  mode: 'resume', pipeline_run_id: 'pipe-1', rerun_of: null, attempt: 2, max_attempts: 5,
  reason: 'config_unchanged', status: 'failed', public_status: 'failed', links: { poll: '/api/v1/agents/pipelines/pipe-1' },
}

const httpError = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })

beforeEach(() => {
  for (const fn of Object.values(hooks)) fn.mockReset()
  triggerDeep.mockReset()
  triggerDeep.mockResolvedValue({})
  retryPipeline.mockReset()
  mutate.mockReset()
  permissions.isQaEngineer = true
  aiMode.current = { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true }
  hooks.useFailureClusters.mockReturnValue({
    data: [{ cluster_id: 'cl-1', label: 'DB Timeouts', representative_error: 'TimeoutError', member_test_ids: ['t1', 't2'], size: 2, cohesion_score: 0.88 }],
  })
  hooks.useDeepFindings.mockReturnValue({ data: [] })
  hooks.usePipelineStatus.mockReturnValue({ data: FAILED, mutate })
  hooks.useRuns.mockReturnValue({ data: { items: [RUN] }, isLoading: false, isValidating: false })
  hooks.useRun.mockReturnValue({ data: undefined, isLoading: false })
})

async function renderPage() {
  render(
    <MemoryRouter initialEntries={['/deep-investigate?run=run-1']}>
      <Routes>
        <Route path="/deep-investigate" element={<DeepInvestigationPage />} />
      </Routes>
    </MemoryRouter>,
  )
  return screen.findByRole('region', { name: 'Investigation verdict' })
}

/** The `follow` predicate the page handed the status hook on its latest render. */
function latestFollow(): (d: unknown) => boolean {
  const options = hooks.usePipelineStatus.mock.lastCall?.[2] as { follow?: (d: unknown) => boolean } | undefined
  return options?.follow ?? (() => false)
}

describe('the FAILED card retries the deep pipeline', () => {
  it('the CTA reads "Retry from last successful stage" and posts the pipeline\'s retry, not a new deep investigation', async () => {
    retryPipeline.mockResolvedValue(RESUME)
    const verdict = await renderPage()
    expect(within(verdict).queryByRole('button', { name: /Run on all/ })).toBeNull()
    const cta = within(verdict).getByTestId('deep-retry')
    expect(cta).toHaveTextContent('Retry from last successful stage')

    fireEvent.click(cta)
    await waitFor(() => expect(retryPipeline).toHaveBeenCalledWith('pipe-1'))
    expect(retryPipeline).toHaveBeenCalledTimes(1)
    expect(triggerDeep).not.toHaveBeenCalled()
  })

  it('resume: the attempt note, a revalidated status, and no second click until the run moves', async () => {
    retryPipeline.mockResolvedValue(RESUME)
    const verdict = await renderPage()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    expect(await within(verdict).findByTestId('deep-retry-note')).toHaveTextContent(
      'Resuming from the last successful stage (attempt 2/5).',
    )
    expect(mutate).toHaveBeenCalled()
    // The worker has not claimed it yet (the status still reads this failed
    // pipeline): the button stays disabled and the status keeps polling.
    expect(within(verdict).getByTestId('deep-retry')).toBeDisabled()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    expect(retryPipeline).toHaveBeenCalledTimes(1)
    const follow = latestFollow()
    expect(follow(FAILED)).toBe(true)
    expect(follow({ ...FAILED, status: 'running' })).toBe(false)
    expect(follow({ ...FAILED, pipeline_run_id: 'pipe-2', status: 'pending' })).toBe(false)
    // The resumed pipeline failed again (a new completed_at): the button is offered again.
    expect(follow({ ...FAILED, completed_at: '2026-03-31T11:00:00Z' })).toBe(false)
  })

  it('rerun: copy only — the server already queued the new deep run, so no second POST', async () => {
    retryPipeline.mockResolvedValue({ ...RESUME, mode: 'rerun', pipeline_run_id: null, rerun_of: 'pipe-1', attempt: 1, reason: 'config_changed' })
    const verdict = await renderPage()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    expect(await within(verdict).findByTestId('deep-retry-note')).toHaveTextContent(
      'The configuration changed, so a new deep run was started instead.',
    )
    expect(triggerDeep).not.toHaveBeenCalled()
    expect(retryPipeline).toHaveBeenCalledTimes(1)
    // Followed until the new pipeline shows up under the run.
    expect(latestFollow()(FAILED)).toBe(true)
  })

  it('409: the server\'s message and "Start a new run", which posts deep-investigate for the run', async () => {
    retryPipeline.mockRejectedValue(httpError(409, {
      message: 'Pipeline reached its attempt ceiling (5/5). Start a new run instead.',
      attempt: 5, max_attempts: 5, links: { rerun: '/api/v1/agents/pipelines/trigger' },
    }))
    const verdict = await renderPage()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    const note = await within(verdict).findByTestId('deep-retry-note')
    expect(note).toHaveTextContent('Pipeline reached its attempt ceiling (5/5). Start a new run instead.')
    expect(note).toHaveAttribute('data-tone', 'error')
    fireEvent.click(within(verdict).getByTestId('deep-start-new-run'))
    await waitFor(() => expect(triggerDeep).toHaveBeenCalledWith('run-1', 'deep'))
    await waitFor(() => expect(within(verdict).queryByTestId('deep-start-new-run')).toBeNull())
  })

  it('409 review rejected reads "Rejected in review — start a new run."', async () => {
    retryPipeline.mockRejectedValue(httpError(409, {
      message: 'Pipeline was rejected in review; start a new run instead',
      reason: 'review_rejected', links: { rerun: '/api/v1/agents/pipelines/trigger' },
    }))
    const verdict = await renderPage()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    expect(await within(verdict).findByTestId('deep-retry-note')).toHaveTextContent('Rejected in review — start a new run.')
    expect(within(verdict).getByTestId('deep-start-new-run')).toBeInTheDocument()
  })

  it('a failure with no pipeline id keeps the fresh-run CTA', async () => {
    hooks.usePipelineStatus.mockReturnValue({ data: { ...FAILED, pipeline_run_id: null }, mutate })
    const verdict = await renderPage()
    expect(within(verdict).queryByTestId('deep-retry')).toBeNull()
    expect(within(verdict).getByRole('button', { name: /Run on all/ })).toBeInTheDocument()
  })

  it('a viewer sees the retry, disabled, and asks nothing of it', async () => {
    permissions.isQaEngineer = false
    const verdict = await renderPage()
    const cta = within(verdict).getByTestId('deep-retry')
    expect(cta).toBeDisabled()
    expect(cta).toHaveAttribute('title', 'QA Engineer role required')
  })

  it('the retry is not hidden when deep investigation is switched off (a resume is not a new investigation)', async () => {
    aiMode.current = { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: false }
    retryPipeline.mockRejectedValue(httpError(422, 'Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.'))
    const verdict = await renderPage()
    fireEvent.click(within(verdict).getByTestId('deep-retry'))
    // A rerun the gate refuses: the server's words, not silence.
    expect(await within(verdict).findByTestId('deep-retry-note')).toHaveTextContent(
      'Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.',
    )
  })
})

describe('RunDeepClusters — the run page\'s Analysis tab', () => {
  const renderClusters = () =>
    render(
      <MemoryRouter>
        <RunDeepClusters runId="run-1" run={RUN as never} />
      </MemoryRouter>,
    )

  it('a failed investigation offers "Retry from last successful stage" beside "Analyze failures"; it retries, never re-triggers', async () => {
    let resolve: (v: unknown) => void = () => {}
    retryPipeline.mockReturnValue(new Promise((r) => { resolve = r }))
    renderClusters()
    const section = screen.getByRole('region', { name: 'Deep investigation' })
    expect(within(section).getByText('last investigation failed')).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: /Analyze failures/ })).toBeInTheDocument()
    const retry = within(section).getByTestId('deep-retry')
    fireEvent.click(retry)
    expect(within(section).getByTestId('deep-retry')).toBeDisabled()
    await act(async () => { resolve(RESUME) })
    expect(retryPipeline).toHaveBeenCalledWith('pipe-1')
    expect(triggerDeep).not.toHaveBeenCalled()
    expect(within(section).getByTestId('deep-retry-note')).toHaveTextContent('Resuming from the last successful stage (attempt 2/5).')
  })

  it('no retry while the investigation is not failed', () => {
    hooks.usePipelineStatus.mockReturnValue({ data: { ...FAILED, status: 'completed' }, mutate })
    renderClusters()
    expect(screen.queryByTestId('deep-retry')).toBeNull()
  })
})
