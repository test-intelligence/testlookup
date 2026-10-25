/**
 * W04 AIC-01: the deep-investigation triggers follow Settings › AI.
 *
 * No deep CTA was gated by the "Deep Investigation" toggle or by the mode, so
 * with the toggle off (or the mode on Rules) every button led to a 422. Now,
 * from `/settings/ai/mode` (the config every role may read):
 *  - the header's "Run Deep Analysis" and the verdict card's "Run on all N
 *    failures" are REPLACED by a note saying why (not merely disabled);
 *  - the run page's "Analyze failures" (`RunDeepClusters`) likewise;
 *  - while the mode is loading the CTAs render as they always did.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage, { RunDeepClusters } from './DeepInvestigationPage'

const { hooks, aiMode } = vi.hoisted(() => ({
  hooks: {
    useFailureClusters: vi.fn(),
    useDeepFindings: vi.fn(),
    usePipelineStatus: vi.fn(),
    useRuns: vi.fn(),
    useRun: vi.fn(),
  },
  aiMode: { current: undefined as Record<string, unknown> | undefined },
}))

vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: hooks.useFailureClusters,
  useDeepFindings: hooks.useDeepFindings,
  usePipelineStatus: hooks.usePipelineStatus,
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: hooks.useRuns, useRun: hooks.useRun }))
vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: true, canAccessManagement: true }) }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useAIConfig')>()),
  useAIConfig: vi.fn(() => ({ data: aiMode.current })),
}))
vi.mock('@/services/deepInvestigationService', () => ({ deepInvestigationService: { triggerDeep: vi.fn() } }))
vi.mock('@/services/agentService', () => ({ default: { retryPipeline: vi.fn() } }))
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

const OFF = { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: false }
const RULES = { analysis_mode: 'rules', ai_offline_mode: true, deep_investigation_enabled: true }
const ON = { analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true }
const OFF_NOTE = 'Deep investigation is turned off in Settings › AI.'
const RULES_NOTE = 'Deep investigation needs LLM or Auto mode (current mode: Rules).'

beforeEach(() => {
  for (const fn of Object.values(hooks)) fn.mockReset()
  aiMode.current = undefined
  hooks.useFailureClusters.mockReturnValue({
    data: [{ cluster_id: 'cl-1', label: 'DB Timeouts', representative_error: 'TimeoutError', member_test_ids: ['t1', 't2'], size: 2, cohesion_score: 0.88 }],
  })
  hooks.useDeepFindings.mockReturnValue({ data: [] })
  hooks.usePipelineStatus.mockReturnValue({ data: null, mutate: vi.fn() })
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

describe('/deep-investigate', () => {
  it.each([
    ['deep investigation is off', OFF, OFF_NOTE],
    ['the mode is Rules', RULES, RULES_NOTE],
  ])('when %s, both triggers are replaced by the note', async (_why, mode, note) => {
    aiMode.current = mode
    const verdict = await renderPage()
    expect(screen.queryByRole('button', { name: /Run Deep Analysis/ })).toBeNull()
    expect(within(verdict).queryByRole('button', { name: /Run on all/ })).toBeNull()
    const notes = screen.getAllByTestId('deep-disabled-note')
    // One in the header toolbar, one on the verdict card.
    expect(notes).toHaveLength(2)
    for (const n of notes) expect(n).toHaveTextContent(note)
    expect(notes[0].closest('[data-page-toolbar]')).not.toBeNull()
    expect(verdict.contains(notes[1])).toBe(true)
  })

  it('the toggle wins over the mode: off AND Rules reads the toggle\'s note', async () => {
    aiMode.current = { ...RULES, deep_investigation_enabled: false }
    await renderPage()
    for (const n of screen.getAllByTestId('deep-disabled-note')) expect(n).toHaveTextContent(OFF_NOTE)
  })

  it.each([
    ['on, in Auto mode', ON],
    ['still loading', undefined],
    ['from a server that does not send the toggle yet', { analysis_mode: 'llm', ai_offline_mode: false }],
  ])('offers both triggers when deep investigation is %s', async (_why, mode) => {
    aiMode.current = mode
    const verdict = await renderPage()
    expect(screen.getByRole('button', { name: /Run Deep Analysis/ })).toBeInTheDocument()
    expect(within(verdict).getByRole('button', { name: /Run on all/ })).toBeInTheDocument()
    expect(screen.queryByTestId('deep-disabled-note')).toBeNull()
  })
})

describe('RunDeepClusters (the run page\'s Analysis tab)', () => {
  const renderClusters = () =>
    render(
      <MemoryRouter>
        <RunDeepClusters runId="run-1" run={RUN as never} />
      </MemoryRouter>,
    )

  it.each([
    ['off', OFF, OFF_NOTE],
    ['in Rules mode', RULES, RULES_NOTE],
  ])('"Analyze failures" is replaced by the note when deep investigation is %s', (_why, mode, note) => {
    aiMode.current = mode
    renderClusters()
    const section = screen.getByRole('region', { name: 'Deep investigation' })
    expect(within(section).queryByRole('button', { name: /Analyze failures/ })).toBeNull()
    expect(within(section).getByTestId('deep-disabled-note')).toHaveTextContent(note)
  })

  it('"Analyze failures" stays when deep investigation is on', () => {
    aiMode.current = ON
    renderClusters()
    expect(screen.getByRole('button', { name: /Analyze failures/ })).toBeEnabled()
    expect(screen.queryByTestId('deep-disabled-note')).toBeNull()
  })
})
