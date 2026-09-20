/**
 * Settings → AI Agents → Agent configuration (E4.3). Hermetic: hook and
 * service mocked. Pins the PUT payload (the whole document with only the
 * edited fields changed), inline server refusals, the stored-invalid banner,
 * and the review-policy coupling.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentConfigPanel from './AgentConfigPanel'
import { agentLabel, saveErrorLines } from '@/utils/agentConfig'
import type { AgentConfigDocument, AgentConfigView } from '@/types/agentConfig'

vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentConfigs: vi.fn(),
  // Revalidation moved to this shared helper (2026-09-20). The panel and the
  // policy cards above it read ONE AgentConfig document under two SWR keys, and
  // each used to refresh only its own -- so the page showed the same config
  // twice with two answers, and the panel then sent an If-Match from its stale
  // cache, making the NEXT save fail on a precondition the user cannot see.
  refreshAgentGovernance: vi.fn(),
}))

vi.mock('@/services/agentGovernanceService', () => ({
  agentGovernanceService: {
    updateAgentConfig: vi.fn(),
  },
}))

function doc(agentId: string, over: Partial<AgentConfigDocument> = {}): AgentConfigDocument {
  return {
    agent_id: agentId,
    enabled: true,
    mode: 'shadow',
    model: {
      tier: 'auto',
      slm: null,
      llm: null,
      escalation: { on_validation_failure: true, on_confidence_below: 70, max_escalations: 1 },
    },
    thresholds: { confidence_min: 80, max_failures_analyzed: 50, degraded_ratio: 0.3 },
    retry: { max_attempts: 5, base_seconds: 30, cap_seconds: 600, jitter: 0.2, retry_on: ['model_unavailable', 'timeout', 'tool_error'] },
    timeout_seconds: 60,
    tools: { allowlist: ['check_test_flakiness', 'query_splunk_logs'] },
    budget: { max_llm_calls_per_run: 30, max_tokens_per_run: 60000, max_cost_usd_per_run: 5, max_runs_per_day: 10 },
    shadow: { sample_rate: 0.1, daily_token_budget: 200000 },
    review: { policy: 'human_required', auto_reviewer: false, second_model_check: false },
    override_policy: { allow_tier_downgrade: true, allow_retry_decrease: true, allow_tool_narrowing: true },
    ...over,
  }
}

function view(agentId: string, over: Partial<AgentConfigView> = {}): AgentConfigView {
  return {
    agent_id: agentId,
    source: 'default',
    config_version: 0,
    valid: true,
    errors: [],
    updated_at: null,
    updated_by: null,
    config: doc(agentId),
    ...over,
  }
}

const TOOLS = { check_test_flakiness: 'read_only', query_splunk_logs: 'read_only', fetch_app_metrics: 'read_only' } as const

async function mockConfigs(configs: AgentConfigView[], extra: Record<string, unknown> = {}) {
  const { useAgentConfigs } = await import('@/hooks/useAgentGovernance')
  const mutate = vi.fn()
  ;(useAgentConfigs as ReturnType<typeof vi.fn>).mockReturnValue({
    configs,
    tools: TOOLS,
    isLoading: false,
    error: undefined,
    mutate,
    ...extra,
  })
  return mutate
}

describe('agentLabel', () => {
  it('turns an agent id into a readable name', () => {
    expect(agentLabel('agent.root_cause_analysis.v1')).toBe('Root cause analysis')
    expect(agentLabel('agent.summary.v2')).toBe('Summary')
  })
})

describe('saveErrorLines', () => {
  it('lists Pydantic errors with their field and plain string details as they are', () => {
    const pydantic = {
      isAxiosError: true,
      message: 'Request failed',
      response: { status: 422, data: { detail: [{ loc: ['body', 'timeout_seconds'], msg: 'Value error, 10 x 300 = 3000 s' }] } },
    }
    expect(saveErrorLines(pydantic)).toEqual(['timeout_seconds: Value error, 10 x 300 = 3000 s'])
    const strings = { isAxiosError: true, message: 'x', response: { status: 422, data: { detail: ["model.llm.provider='openai' is not a local provider"] } } }
    expect(saveErrorLines(strings)).toEqual(["model.llm.provider='openai' is not a local provider"])
    expect(saveErrorLines(new Error('boom'))).toEqual(['Could not save the agent configuration.'])
  })
})

describe('AgentConfigPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows one tab per agent and switches between them', async () => {
    await mockConfigs([
      view('agent.summary.v1'),
      view('agent.triage.v1', { source: 'project', config_version: 3, updated_at: '2026-09-13T10:00:00Z' }),
    ])
    render(<AgentConfigPanel projectId="proj-1" />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Summary', 'Triagev3'])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('agent-config-agent.summary.v1')).toBeInTheDocument()
    expect(screen.getByText(/Using defaults/)).toBeInTheDocument()

    fireEvent.click(tabs[1])
    expect(screen.getByTestId('agent-config-agent.triage.v1')).toBeInTheDocument()
    expect(screen.getAllByRole('tab')[1]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText(/Version/)).toBeInTheDocument()
  })

  it('switching agents re-seeds the form, so edits never carry over to another agent', async () => {
    const triage = view('agent.triage.v1', { source: 'project', config_version: 1 })
    triage.config.retry.max_attempts = 2
    await mockConfigs([view('agent.summary.v1'), triage])
    render(<AgentConfigPanel projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '9' } })
    fireEvent.click(screen.getAllByRole('tab')[1])
    expect(screen.getByLabelText('Max attempts')).toHaveValue(2)
    fireEvent.click(screen.getAllByRole('tab')[0])
    expect(screen.getByLabelText('Max attempts')).toHaveValue(5)
  })

  it('PUTs the whole document with only the edited fields changed, then revalidates', async () => {
    const mutate = await mockConfigs([view('agent.summary.v1')])
    const { agentGovernanceService } = await import('@/services/agentGovernanceService')
    ;(agentGovernanceService.updateAgentConfig as ReturnType<typeof vi.fn>).mockResolvedValue(view('agent.summary.v1'))
    render(<AgentConfigPanel projectId="proj-1" />)

    const save = screen.getByRole('button', { name: /save configuration/i })
    expect(save).toBeDisabled()

    fireEvent.click(screen.getByRole('radio', { name: 'Suggest' }))
    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Model tier'), { target: { value: 'slm' } })
    fireEvent.click(screen.getByLabelText('query_splunk_logs'))
    fireEvent.click(screen.getByLabelText('fetch_app_metrics'))
    fireEvent.change(screen.getByLabelText('Max cost (USD) / run'), { target: { value: '1.25' } })
    fireEvent.click(save)

    const base = doc('agent.summary.v1')
    await waitFor(() =>
      expect(agentGovernanceService.updateAgentConfig).toHaveBeenCalledWith('proj-1', 'agent.summary.v1', {
        ...base,
        mode: 'suggest',
        model: { ...base.model, tier: 'slm' },
        retry: { ...base.retry, max_attempts: 3 },
        tools: { allowlist: ['check_test_flakiness', 'fetch_app_metrics'] },
        budget: { ...base.budget, max_cost_usd_per_run: 1.25 },
      }, 0),
    )
    const { refreshAgentGovernance } = await import('@/hooks/useAgentGovernance')
    await waitFor(() => expect(refreshAgentGovernance).toHaveBeenCalled())
  })

  it('shows the attempts x timeout arithmetic as it is edited', async () => {
    await mockConfigs([view('agent.summary.v1')])
    render(<AgentConfigPanel projectId="proj-1" />)
    expect(screen.getByTestId('worst-case')).toHaveTextContent('5 × 60 s = 300 s')
    fireEvent.change(screen.getByLabelText('Timeout (seconds)'), { target: { value: '300' } })
    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '10' } })
    expect(screen.getByTestId('worst-case')).toHaveTextContent('10 × 300 s = 3000 s')
  })

  it('blocks saving invalid numbers', async () => {
    await mockConfigs([view('agent.summary.v1')])
    const { agentGovernanceService } = await import('@/services/agentGovernanceService')
    render(<AgentConfigPanel projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '0' } })
    const save = screen.getByRole('button', { name: /save configuration/i })
    expect(save).toBeDisabled()
    expect(screen.getByText(/must be at least 1/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Max runs / day'), { target: { value: '1.5' } })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(agentGovernanceService.updateAgentConfig).not.toHaveBeenCalled()
  })

  it('lists the server refusal inline and keeps the edits', async () => {
    const mutate = await mockConfigs([view('agent.summary.v1')])
    const { agentGovernanceService } = await import('@/services/agentGovernanceService')
    ;(agentGovernanceService.updateAgentConfig as ReturnType<typeof vi.fn>).mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed',
      response: { status: 422, data: { detail: [{ loc: ['body'], msg: 'Value error, retry.max_attempts x timeout_seconds = 10 x 300 = 3000 s' }] } },
    })
    render(<AgentConfigPanel projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Timeout (seconds)'), { target: { value: '300' } })
    fireEvent.change(screen.getByLabelText('Max attempts'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    expect(await screen.findByText(/The server refused this configuration/)).toBeInTheDocument()
    expect(screen.getByText(/10 x 300 = 3000 s/)).toBeInTheDocument()
    expect(screen.getByLabelText('Max attempts')).toHaveValue(10)
    const { refreshAgentGovernance } = await import('@/hooks/useAgentGovernance')
    expect(refreshAgentGovernance).not.toHaveBeenCalled()
  })

  it('warns when the stored configuration no longer validates', async () => {
    await mockConfigs([
      view('agent.summary.v1', {
        source: 'project', config_version: 2, valid: false,
        errors: ['timeout_seconds=900 exceeds this deployment\'s ceiling AGENT_MAX_TIMEOUT_CEILING=600'],
      }),
    ])
    render(<AgentConfigPanel projectId="proj-1" />)
    expect(screen.getByText(/no longer validates/)).toBeInTheDocument()
    expect(screen.getByText(/AGENT_MAX_TIMEOUT_CEILING=600/)).toBeInTheDocument()
  })

  it('choosing the auto-reviewer policy turns the reviewer on and locks it', async () => {
    await mockConfigs([view('agent.summary.v1')])
    render(<AgentConfigPanel projectId="proj-1" />)
    const reviewer = screen.getByLabelText('Automatic reviewer')
    expect(reviewer).not.toBeChecked()
    fireEvent.change(screen.getByLabelText('Review policy'), { target: { value: 'human_required_plus_auto_reviewer' } })
    expect(screen.getByLabelText('Automatic reviewer')).toBeChecked()
    expect(screen.getByLabelText('Automatic reviewer')).toBeDisabled()
  })

  it('shows loading and error states without a form', async () => {
    await mockConfigs([], { isLoading: false, error: new Error('x') })
    render(<AgentConfigPanel projectId="proj-1" />)
    expect(screen.getByText(/Could not load agent configurations/)).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })
})
