/**
 * Investigator cockpit (AI-1) — hermetic render tests with mocked hooks,
 * per the established page-test pattern (see DeepInvestigationPage.test.tsx).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
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

// ── W15 AGT-02: the verdict's typed Proposed actions ───────────────────────

const QUARANTINE_ACTION = {
  action_type: 'quarantine.apply',
  target_type: 'test',
  target_id: 'fpA1b2C3d4',
  title: 'Quarantine checkout-retry',
  payload: { test_fingerprint: 'fpA1b2C3d4', test_name: 'checkout-retry', suite_name: 'checkout' },
}
const REASSIGN_ACTION = {
  action_type: 'failure.reassign',
  target_type: 'test_case',
  target_id: 'tc-7',
  title: 'Reassign checkout-total to the checkout owner',
  payload: { test_case_id: 'tc-7', suite_name: 'checkout' },
}

function proposalRow(over: Record<string, unknown>) {
  return {
    id: 'act-1', action_type: 'quarantine.apply', canonical_type: 'quarantine.apply', target_type: 'test',
    target_id: 'fpA1b2C3d4', status: 'pending_review', error_code: null, outcome: 'awaiting_approval',
    outcome_text: "Waiting for a QA lead's approval.", idempotency_key: 'inv:inv-1:quarantine.apply:a1',
    title: 'Quarantine checkout-retry', created_at: '2026-10-25T10:00:00Z', ...over,
  }
}

function completedDetail(over: Partial<InvestigationDetail> = {}): InvestigationDetail {
  return {
    ...DETAIL,
    status: 'completed',
    mode: 'suggest',
    completed_at: '2026-07-15T10:05:00Z',
    verdict: {
      primary_cause: 'known_flaky',
      narrative: 'Known flaky offenders explain the failures.',
      confidence: 84,
      recommended_actions: ['Propose quarantine for the recurring flaky offenders (Flaky Quarantine workflow).'],
      structured_actions: [QUARANTINE_ACTION, REASSIGN_ACTION],
    },
    proposals: [
      proposalRow({}),
      proposalRow({
        id: 'act-2', action_type: 'failure.reassign', canonical_type: 'failure.reassign', target_type: 'test_case',
        target_id: 'tc-7', status: 'executed', outcome: 'executed', outcome_text: 'Executed · approved by Lee · assigned to Dana.',
      }),
    ],
    ...over,
  } as InvestigationDetail
}

async function asRole(isQaLead: boolean) {
  const { usePermissions } = await import('@/hooks/usePermissions')
  ;(usePermissions as ReturnType<typeof vi.fn>).mockReturnValue({ isQaEngineer: true, isQaLead })
}

describe('InvestigatorCockpit — proposed actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lists each typed action with the state of its ledger row and a Review link for a QA lead', async () => {
    await asRole(true)
    await mockHooks({ detail: completedDetail() })
    renderCockpit()

    const rows = screen.getAllByTestId('cockpit-action-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Quarantine checkout-retry — Waiting for approval')
    expect(rows[1]).toHaveTextContent('Reassign checkout-total to the checkout owner — Executed')
    expect(within(rows[1]).getByTestId('cockpit-action-state')).toHaveAttribute('title', 'Executed · approved by Lee · assigned to Dana.')
    // Review only on the pending row; the executed one is decided.
    expect(within(rows[0]).getByRole('link', { name: 'Review' })).toHaveAttribute('href', '/my-failures?tab=approvals&source=actions')
    expect(within(rows[1]).queryByRole('link', { name: 'Review' })).not.toBeInTheDocument()
    // The text recommendations stay; the suggest line explains the mode.
    const verdict = screen.getByTestId('investigation-verdict')
    expect(verdict).toHaveTextContent('Propose quarantine for the recurring flaky offenders')
    expect(verdict).toHaveTextContent(
      'Suggest mode — these proposals wait in Inbox › Approvals; nothing runs until a person approves it.',
    )
    expect(verdict).not.toHaveTextContent('Shadow mode — no actions were taken')
  })

  it('mounts the existing in-page Propose quarantine form under the row — no dialog', async () => {
    await asRole(true)
    await mockHooks({ detail: completedDetail() })
    renderCockpit()

    expect(screen.queryByRole('region', { name: 'Propose quarantine' })).not.toBeInTheDocument()
    fireEvent.click(within(screen.getAllByTestId('cockpit-action-row')[0]).getByRole('button', { name: 'Do it myself' }))
    const form = screen.getByRole('region', { name: 'Propose quarantine' })
    expect(form.closest('li')).toHaveAttribute('data-in-page-row')
    expect(form).toHaveTextContent('checkout-retry')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows a QA engineer the states but no Review or Do it myself', async () => {
    await asRole(false)
    await mockHooks({ detail: completedDetail() })
    renderCockpit()

    expect(screen.getAllByTestId('cockpit-action-row')).toHaveLength(2)
    expect(screen.queryByRole('link', { name: 'Review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Do it myself' })).not.toBeInTheDocument()
  })

  it('reads a shadow action as not proposed and keeps the shadow sentence', async () => {
    await asRole(true)
    await mockHooks({ detail: completedDetail({ mode: 'shadow', proposals: [] }) })
    renderCockpit()

    const rows = screen.getAllByTestId('cockpit-action-row')
    expect(rows[0]).toHaveTextContent('Quarantine checkout-retry — Shadow mode — not proposed')
    expect(within(rows[1]).getByRole('link', { name: 'Open the failure' })).toHaveAttribute('href', '/runs/run-1?tab=tests')
    expect(screen.getByTestId('investigation-verdict')).toHaveTextContent('Shadow mode — no actions were taken')
  })

  it('renders a title holding markup as text', async () => {
    await asRole(true)
    const hostile = '<img src=x onerror=alert(1)>'
    const base = completedDetail()
    const detail = completedDetail({
      verdict: { ...(base.verdict as NonNullable<InvestigationDetail['verdict']>), structured_actions: [{ ...QUARANTINE_ACTION, title: hostile }] },
    })
    await mockHooks({ detail })
    const { container } = renderCockpit()

    expect(screen.getByTestId('cockpit-action-row')).toHaveTextContent(hostile)
    expect(container.querySelector('img')).toBeNull()
  })

  it('keeps the text list only on an old detail without proposals', async () => {
    await asRole(true)
    await mockHooks({ detail: completedDetail({ proposals: undefined }) })
    renderCockpit()

    expect(screen.queryByTestId('cockpit-proposed-actions')).not.toBeInTheDocument()
    expect(screen.getByTestId('investigation-verdict')).toHaveTextContent('Recommended actions')
  })
})
