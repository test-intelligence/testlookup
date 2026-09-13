/**
 * Settings → AI Agents (AI-3 governance): PUT payload shape + trust-ladder
 * act-mode gating. Hermetic — hooks and service mocked.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AIAgentsPage from './AIAgentsPage'

vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentPolicies: vi.fn(),
}))

vi.mock('@/hooks/useProjectScopedSWR', () => ({
  useActiveProjectId: vi.fn(() => 'proj-1'),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
}))

vi.mock('@/services/agentGovernanceService', () => ({
  agentGovernanceService: {
    updatePolicy: vi.fn(),
    listPolicies: vi.fn(),
    listAgentRuns: vi.fn(),
  },
}))

// The Fixer card (AI-2) has its own config resource + tests — stub it here so
// this file stays a hermetic test of the governance policies.
vi.mock('@/components/fixer/FixerConfigCard', () => ({
  default: () => <div data-testid="fixer-config-card-stub" />,
}))

// The agent configuration panel (E4.3) has its own tests.
vi.mock('@/components/agents/AgentConfigPanel', () => ({
  default: ({ projectId }: { projectId: string }) => <div data-testid="agent-config-panel-stub">{projectId}</div>,
}))

const POLICY = {
  agent_id: 'investigator',
  enabled: true,
  mode: 'shadow' as const,
  budgets: {
    max_runs_per_day: 10,
    max_llm_calls_per_run: 20,
    max_tokens_per_run: 100000,
    max_seconds_per_run: 300,
  },
  promotion: { shadow_runs_completed: 7, note: 'promotion review at 10' },
}

async function mockPolicies(policy = POLICY) {
  const { useAgentPolicies } = await import('@/hooks/useAgentGovernance')
  ;(useAgentPolicies as ReturnType<typeof vi.fn>).mockReturnValue({
    policies: [policy],
    investigatorPolicy: policy,
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  })
}

describe('AIAgentsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the Investigator card with trust-ladder modes; act is disabled with the later-wave tooltip', async () => {
    await mockPolicies()
    render(<AIAgentsPage />)

    expect(screen.getByTestId('agent-policy-investigator')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Shadow' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Suggest' })).toHaveAttribute('aria-checked', 'false')

    const act = screen.getByRole('radio', { name: 'Act' })
    expect(act).toBeDisabled()
    expect(act).toHaveAttribute('title', 'Gated-act mode is coming in a later wave')

    // Promotion status line.
    expect(screen.getByText(/7/)).toBeInTheDocument()
    expect(screen.getByText(/shadow runs completed/)).toBeInTheDocument()
    expect(screen.getByText(/promotion review at 10/)).toBeInTheDocument()

    // The Fixer card (AI-2) coexists alongside the Investigator card.
    expect(screen.getByTestId('fixer-config-card-stub')).toBeInTheDocument()
    expect(screen.getByTestId('agent-config-panel-stub')).toHaveTextContent('proj-1')
  })

  it('PUTs the exact contract payload {enabled, mode, budgets} on save', async () => {
    await mockPolicies()
    const { agentGovernanceService } = await import('@/services/agentGovernanceService')
    ;(agentGovernanceService.updatePolicy as ReturnType<typeof vi.fn>).mockResolvedValue({ ...POLICY, mode: 'suggest' })
    render(<AIAgentsPage />)

    // Change mode shadow → suggest and bump one budget.
    fireEvent.click(screen.getByRole('radio', { name: 'Suggest' }))
    fireEvent.change(screen.getByLabelText('Max LLM calls / run'), { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))

    await waitFor(() =>
      expect(agentGovernanceService.updatePolicy).toHaveBeenCalledWith('proj-1', 'investigator', {
        enabled: true,
        mode: 'suggest',
        budgets: {
          max_runs_per_day: 10,
          max_llm_calls_per_run: 25,
          max_tokens_per_run: 100000,
          max_seconds_per_run: 300,
        },
      }),
    )
  })

  it('blocks saving on invalid budgets', async () => {
    await mockPolicies()
    const { agentGovernanceService } = await import('@/services/agentGovernanceService')
    render(<AIAgentsPage />)

    fireEvent.change(screen.getByLabelText('Max runs / day'), { target: { value: '0' } })
    expect(screen.getByText(/positive whole numbers/i)).toBeInTheDocument()

    const save = screen.getByRole('button', { name: /save policy/i })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(agentGovernanceService.updatePolicy).not.toHaveBeenCalled()
  })

  it('asks for a specific project in all-projects mode', async () => {
    await mockPolicies()
    const { useActiveProjectId } = await import('@/hooks/useProjectScopedSWR')
    ;(useActiveProjectId as ReturnType<typeof vi.fn>).mockReturnValue('__ALL__')
    render(<AIAgentsPage />)

    expect(screen.getByText(/select a specific project/i)).toBeInTheDocument()
    expect(screen.queryByTestId('agent-policy-investigator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('fixer-config-card-stub')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-config-panel-stub')).not.toBeInTheDocument()
  })
})
