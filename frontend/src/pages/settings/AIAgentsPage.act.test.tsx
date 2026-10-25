/**
 * W15 AGT-03 §2.3-9 (owner decision D16): Settings › AI Agents — Act is
 * enabled for the Investigator; ONLY while the card is in Act, a fieldset
 * ticks the action types Act may run; Save writes the ticks through
 * `updateInvestigatorAct` (a fresh GET-then-PUT) when they changed, and the
 * copy promises no autonomy. Hermetic — hooks and service mocked.
 */
import { act as actNow, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const governance = vi.hoisted(() => ({
  useAgentPolicies: vi.fn(),
  refreshAgentGovernance: vi.fn(() => Promise.resolve(undefined)),
}))
vi.mock('@/hooks/useAgentGovernance', () => governance)
vi.mock('@/hooks/useProjectScopedSWR', () => ({ useActiveProjectId: () => 'proj-1' }))
vi.mock('@/store/projectStore', () => ({ ALL_PROJECTS_ID: '__ALL__' }))
const service = vi.hoisted(() => ({
  updatePolicy: vi.fn(),
  updateInvestigatorAct: vi.fn(),
  listPolicies: vi.fn(),
  listAgentRuns: vi.fn(),
}))
vi.mock('@/services/agentGovernanceService', () => ({ agentGovernanceService: service }))
vi.mock('@/components/fixer/FixerConfigCard', () => ({ default: () => null }))
vi.mock('@/components/agents/AgentConfigPanel', () => ({ default: () => null }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import AIAgentsPage, { ACT_ACK_NOTE, ACT_NO_TICK_NOTE, MODE_COPY } from './AIAgentsPage'
import type { AgentMode } from '@/types/investigator'

const FIELDSET = 'Action types Act may run'
const LABELS = ['Quarantine flaky offenders', 'Reassign failures to the suite owner', 'Promote an internal defect']

function policy(mode: AgentMode, ticks: string[] = []) {
  return {
    agent_id: 'investigator',
    enabled: true,
    mode,
    budgets: { max_runs_per_day: 10, max_llm_calls_per_run: 20, max_tokens_per_run: 100000, max_seconds_per_run: 300 },
    promotion: { shadow_runs_completed: 3, note: null },
    act: { allowed_action_types: ticks },
  }
}

function renderWith(p: ReturnType<typeof policy>) {
  governance.useAgentPolicies.mockReturnValue({
    policies: [p], investigatorPolicy: p, isLoading: false, error: undefined, mutate: vi.fn(),
  })
  return render(<AIAgentsPage />)
}

const save = () => screen.getByRole('button', { name: /save policy/i })
const fieldset = () => screen.queryByRole('group', { name: FIELDSET })

beforeEach(() => {
  vi.clearAllMocks()
  service.updatePolicy.mockResolvedValue({})
  service.updateInvestigatorAct.mockResolvedValue({})
})

describe('AIAgentsPage — Act (D16)', () => {
  it('enables Act with honest copy: a person approves each run, audited and undoable, no later-wave tooltip', () => {
    renderWith(policy('shadow'))
    const act = screen.getByRole('radio', { name: 'Act' })
    expect(act).toBeEnabled()
    expect(act).not.toHaveAttribute('title')
    expect(screen.queryByText('later wave')).toBeNull()
    expect(act).toHaveTextContent(MODE_COPY.act.copy)
    expect(MODE_COPY.act.copy).toMatch(/a person approves each one/)
    expect(MODE_COPY.act.copy).not.toMatch(/autonomous/i)
    expect(screen.getByRole('radio', { name: 'Suggest' })).toHaveTextContent(MODE_COPY.suggest.copy)
  })

  it('shows the fieldset only while the card is in Act', () => {
    renderWith(policy('suggest'))
    expect(fieldset()).toBeNull()
    fireEvent.click(screen.getByRole('radio', { name: 'Act' }))
    const group = fieldset() as HTMLElement
    expect(within(group).getAllByRole('checkbox').map((c) => c.closest('label')?.textContent)).toEqual(LABELS)
    expect(within(group).getByText(ACT_ACK_NOTE)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Shadow' }))
    expect(fieldset()).toBeNull()
  })

  it('says Act runs nothing until a type is ticked (a note, not a block)', () => {
    renderWith(policy('shadow'))
    fireEvent.click(screen.getByRole('radio', { name: 'Act' }))
    expect(screen.getByText(ACT_NO_TICK_NOTE)).toBeInTheDocument()
    expect(save()).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox', { name: LABELS[0] }))
    expect(screen.queryByText(ACT_NO_TICK_NOTE)).toBeNull()
  })

  it('Save writes the mode, then the ticks through updateInvestigatorAct', async () => {
    renderWith(policy('shadow'))
    fireEvent.click(screen.getByRole('radio', { name: 'Act' }))
    fireEvent.click(screen.getByRole('checkbox', { name: LABELS[0] }))
    await actNow(async () => {
      fireEvent.click(save())
    })
    expect(service.updatePolicy).toHaveBeenCalledWith('proj-1', 'investigator', expect.objectContaining({ mode: 'act' }))
    expect(service.updateInvestigatorAct).toHaveBeenCalledWith('proj-1', ['quarantine.apply'])
    expect(service.updatePolicy.mock.invocationCallOrder[0]).toBeLessThan(service.updateInvestigatorAct.mock.invocationCallOrder[0])
    expect(governance.refreshAgentGovernance).toHaveBeenCalledWith('proj-1')
  })

  it('a tick change alone writes only the ticks', async () => {
    renderWith(policy('act', ['quarantine.apply']))
    expect(screen.getByRole('checkbox', { name: LABELS[0] })).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: LABELS[2] }))
    await actNow(async () => {
      fireEvent.click(save())
    })
    expect(service.updatePolicy).not.toHaveBeenCalled()
    expect(service.updateInvestigatorAct).toHaveBeenCalledWith('proj-1', ['defect.promote', 'quarantine.apply'])
  })

  it('a budget change alone never writes the ticks', async () => {
    renderWith(policy('act', ['quarantine.apply']))
    fireEvent.change(screen.getByLabelText('Max runs / day'), { target: { value: '12' } })
    await actNow(async () => {
      fireEvent.click(save())
    })
    expect(service.updatePolicy).toHaveBeenCalled()
    expect(service.updateInvestigatorAct).not.toHaveBeenCalled()
  })

  it('keeps the ticks when the mode leaves Act and comes back', () => {
    renderWith(policy('act', ['failure.reassign']))
    fireEvent.click(screen.getByRole('radio', { name: 'Suggest' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Act' }))
    expect(screen.getByRole('checkbox', { name: LABELS[1] })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: LABELS[0] })).not.toBeChecked()
  })
})
