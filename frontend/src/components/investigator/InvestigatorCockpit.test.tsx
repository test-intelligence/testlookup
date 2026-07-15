/**
 * Investigator cockpit (AI-1) — hermetic render tests with mocked hooks,
 * per the established page-test pattern (see DeepInvestigationPage.test.tsx).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import InvestigatorCockpit from './InvestigatorCockpit'
import type { InvestigationDetail } from '@/types/investigator'

vi.mock('@/hooks/useInvestigation', () => ({
  useInvestigation: vi.fn(),
  useInvestigations: vi.fn(),
}))

vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentPolicies: vi.fn(),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: true })),
}))

vi.mock('@/services/investigatorService', () => ({
  investigatorService: {
    startInvestigation: vi.fn(),
    cancelInvestigation: vi.fn(),
    listInvestigations: vi.fn(),
    getInvestigation: vi.fn(),
  },
}))

const POLICY_ENABLED = {
  agent_id: 'investigator',
  enabled: true,
  mode: 'shadow' as const,
  budgets: { max_runs_per_day: 10, max_llm_calls_per_run: 20, max_tokens_per_run: 100000, max_seconds_per_run: 300 },
  promotion: { shadow_runs_completed: 4, note: null },
}

const DETAIL: InvestigationDetail = {
  id: 'inv-1',
  run_id: 'run-1',
  project_id: 'proj-1',
  status: 'running',
  mode: 'shadow',
  triggered_by: 'manual',
  started_at: '2026-07-15T10:00:00Z',
  completed_at: null,
  cancelled_by: null,
  budget: { max_llm_calls: 20, max_tokens: 100000, max_seconds: 300 },
  spend: { llm_calls: 6, tokens: 34000, cost_usd: 0.04, seconds: 92 },
  hypotheses: [
    {
      id: 'infra',
      title: 'Infrastructure',
      status: 'validated',
      confidence: 88,
      confidence_basis: 'empirical',
      summary: 'Connection pool exhaustion on payments-db.',
      evidence: [
        { kind: 'log', label: 'Splunk span', url_path: '/runs/run-1', detail: 'Pool exhausted 41 times in window' },
        { kind: 'metric', label: 'DB saturation', url_path: null, detail: 'p99 latency 4.2s during run' },
      ],
      started_at: '2026-07-15T10:00:05Z',
      completed_at: '2026-07-15T10:01:00Z',
    },
    {
      id: 'commit',
      title: 'Commit-caused',
      status: 'invalidated',
      confidence: 12,
      confidence_basis: 'heuristic_estimate',
      summary: 'No overlapping code paths in the diff.',
      evidence: [],
      started_at: '2026-07-15T10:00:05Z',
      completed_at: '2026-07-15T10:00:50Z',
    },
    {
      id: 'environment',
      title: 'Environment',
      status: 'running',
      confidence: 40,
      confidence_basis: 'llm_weighted',
      summary: 'Comparing env fingerprints…',
      evidence: [],
      started_at: '2026-07-15T10:01:00Z',
      completed_at: null,
    },
    {
      id: 'known_flaky',
      title: 'Known-flaky',
      status: 'inconclusive',
      confidence: 55,
      confidence_basis: 'heuristic_estimate',
      summary: 'Two of nine failures have flake history.',
      evidence: [],
      started_at: '2026-07-15T10:00:05Z',
      completed_at: '2026-07-15T10:00:40Z',
    },
    // 'regression' intentionally absent → the matrix renders a pending card.
  ],
  verdict: null,
  prompt_versions: { investigator_plan: 'v2@ab12cd' },
  model: { provider: 'ollama', model: 'qwen2.5:14b' },
}

async function mockHooks({
  detail = undefined as InvestigationDetail | undefined,
  policy = POLICY_ENABLED as typeof POLICY_ENABLED | null,
  items = [] as Array<Record<string, unknown>>,
} = {}) {
  const { useInvestigation, useInvestigations } = await import('@/hooks/useInvestigation')
  const { useAgentPolicies } = await import('@/hooks/useAgentGovernance')
  ;(useInvestigation as ReturnType<typeof vi.fn>).mockReturnValue({ data: detail, mutate: vi.fn() })
  ;(useInvestigations as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items, total: items.length }, mutate: vi.fn() })
  ;(useAgentPolicies as ReturnType<typeof vi.fn>).mockReturnValue({
    policies: policy ? [policy] : [],
    investigatorPolicy: policy,
  })
}

function renderCockpit() {
  return render(
    <MemoryRouter>
      <InvestigatorCockpit runId="run-1" projectId="proj-1" />
    </MemoryRouter>,
  )
}

describe('InvestigatorCockpit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders all five hypothesis cards with their live statuses and basis chips', async () => {
    await mockHooks({
      detail: DETAIL,
      items: [{ id: 'inv-1', run_id: 'run-1', run_build_number: 812, status: 'running', mode: 'shadow', triggered_by: 'manual', primary_cause: null, confidence: null, started_at: '2026-07-15T10:00:00Z', completed_at: null }],
    })
    renderCockpit()

    // All five fixed cards render, including the one the API hasn't started.
    for (const id of ['infra', 'commit', 'environment', 'known_flaky', 'regression']) {
      expect(screen.getByTestId(`hypothesis-${id}`)).toBeInTheDocument()
    }
    expect(screen.getByLabelText('validated')).toBeInTheDocument()
    expect(screen.getByLabelText('invalidated')).toBeInTheDocument()
    expect(screen.getByLabelText('running')).toBeInTheDocument()
    expect(screen.getByLabelText('inconclusive')).toBeInTheDocument()
    // The absent 'regression' hypothesis renders as pending.
    expect(screen.getByLabelText('pending')).toBeInTheDocument()
    expect(screen.getByText('awaiting agent')).toBeInTheDocument()

    // Confidence + calibration-basis chips (AIAnalysisPanel vocabulary).
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getByText('calibrated')).toBeInTheDocument()
    expect(screen.getAllByText('estimated').length).toBe(2)
    expect(screen.getByText('llm-weighted')).toBeInTheDocument()

    // Spend-vs-budget bars + details footer.
    expect(screen.getByText('LLM calls')).toBeInTheDocument()
    expect(screen.getByText('6 / 20')).toBeInTheDocument()
    expect(screen.getByText(/ollama\/qwen2\.5:14b/)).toBeInTheDocument()
    expect(screen.getByText(/investigator_plan@v2@ab12cd/)).toBeInTheDocument()
  })

  it('expands a hypothesis evidence list with internal links', async () => {
    await mockHooks({ detail: DETAIL })
    renderCockpit()

    const toggle = screen.getByRole('button', { name: /2 evidence items/i })
    expect(screen.queryByText('Splunk span')).not.toBeInTheDocument()
    fireEvent.click(toggle)

    expect(screen.getByText('Splunk span')).toBeInTheDocument()
    expect(screen.getByText('Pool exhausted 41 times in window')).toBeInTheDocument()
    expect(screen.getByText('DB saturation')).toBeInTheDocument()
    // url_path renders as an internal link; null url_path renders none.
    const link = screen.getByRole('link', { name: /view/ })
    expect(link).toHaveAttribute('href', '/runs/run-1')
  })

  it('shows cancel while active and calls the cancel endpoint', async () => {
    await mockHooks({
      detail: DETAIL,
      items: [{ id: 'inv-1', run_id: 'run-1', run_build_number: null, status: 'running', mode: 'shadow', triggered_by: 'manual', primary_cause: null, confidence: null, started_at: '2026-07-15T10:00:00Z', completed_at: null }],
    })
    const { investigatorService } = await import('@/services/investigatorService')
    ;(investigatorService.cancelInvestigation as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'cancelling' })
    renderCockpit()

    fireEvent.click(screen.getByTestId('cancel-investigation'))
    expect(investigatorService.cancelInvestigation).toHaveBeenCalledWith('inv-1')
  })

  it('renders the verdict panel with AI labeling and the shadow-mode note when complete', async () => {
    await mockHooks({
      detail: {
        ...DETAIL,
        status: 'completed',
        completed_at: '2026-07-15T10:05:00Z',
        verdict: {
          primary_cause: 'infra',
          narrative: 'The failures trace to connection-pool exhaustion on payments-db.',
          confidence: 84,
          recommended_actions: ['Raise the pool ceiling to 50', 'Add a saturation alert'],
        },
      },
    })
    renderCockpit()

    const verdict = screen.getByTestId('investigation-verdict')
    expect(verdict).toHaveTextContent('Primary cause · Infrastructure')
    expect(verdict).toHaveTextContent('84% confidence')
    expect(verdict).toHaveTextContent('AI-generated')
    expect(verdict).toHaveTextContent('connection-pool exhaustion')
    expect(verdict).toHaveTextContent('Raise the pool ceiling to 50')
    expect(verdict).toHaveTextContent('Shadow mode — no actions were taken')
    // No action buttons this slice — recommendations are text only.
    expect(screen.queryByRole('button', { name: /Raise the pool ceiling/ })).not.toBeInTheDocument()
    // Terminal status → no cancel control.
    expect(screen.queryByTestId('cancel-investigation')).not.toBeInTheDocument()
  })

  it('disables the investigate button and explains when the policy is disabled', async () => {
    await mockHooks({ policy: { ...POLICY_ENABLED, enabled: false } })
    renderCockpit()

    const btn = screen.getByTestId('investigate-run')
    expect(btn).toBeDisabled()
    expect(screen.getByTestId('policy-disabled-note')).toHaveTextContent(/disabled for this project/i)
    expect(screen.getByRole('link', { name: /Settings → AI Agents/i })).toHaveAttribute('href', '/settings/ai-agents')
  })

  it('starts an investigation and attaches to it', async () => {
    await mockHooks({})
    const { investigatorService } = await import('@/services/investigatorService')
    ;(investigatorService.startInvestigation as ReturnType<typeof vi.fn>).mockResolvedValue({ investigation_id: 'inv-9' })
    renderCockpit()

    fireEvent.click(screen.getByTestId('investigate-run'))
    expect(investigatorService.startInvestigation).toHaveBeenCalledWith('run-1')
  })
})
