/**
 * S-D-2: the Strategy tab offered every control to every role. The API's
 * floors (`backend/app/routers/test_management_strategies.py`): Generate
 * spends model budget (QA_ENGINEER), a status change is a sign-off (QA_LEAD).
 * Below them the tab reads the strategy: no Generate button, and the status as
 * a pill, not a select. The Word and PDF exports are reads, so they stay.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/store/authStore'
import StrategyTab from './StrategyTab'

const strategies = vi.hoisted(() => ({ data: [] as unknown[] }))
vi.mock('@/hooks/useTestManagement', () => ({
  useStrategies: () => ({ data: strategies.data, isLoading: false, mutate: vi.fn() }),
}))
vi.mock('@/services/testManagementService', () => ({
  testManagementService: {
    updateStrategy: vi.fn(),
    aiGenerateStrategyAsync: vi.fn(),
    downloadStrategyWord: vi.fn(),
    downloadStrategyPdf: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const STRATEGY = {
  id: 'strategy-1', project_id: 'project-1', name: 'Checkout strategy', version_label: '1.0', status: 'draft',
  objective: 'Ship checkout safely', scope: null, out_of_scope: null, test_approach: null, risk_assessment: null,
  test_types: null, entry_criteria: null, exit_criteria: null, environments: null, automation_approach: null,
  defect_management: null, ai_generated: false, ai_model_used: null, created_by_id: null, approved_by_id: null,
  approved_at: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
}

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

beforeEach(() => {
  strategies.data = []
})
afterEach(() => useAuthStore.setState({ user: null }))

describe('StrategyTab controls follow the role (S-D-2)', () => {
  it.each(['VIEWER', 'TESTER'])('a %s with no strategy is offered no Generate Strategy', (role) => {
    signInAs(role)
    render(<StrategyTab projectId="project-1" />)
    expect(screen.getByText('No test strategy')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Generate Strategy/ })).toBeNull()
  })

  it('a QA_ENGINEER is offered Generate Strategy', () => {
    signInAs('QA_ENGINEER')
    render(<StrategyTab projectId="project-1" />)
    expect(screen.getAllByRole('button', { name: /Generate Strategy/ }).length).toBeGreaterThan(0)
  })

  it.each(['VIEWER', 'TESTER', 'QA_ENGINEER'])('a %s reads the status as a pill, not a select, and keeps the exports', (role) => {
    signInAs(role)
    strategies.data = [STRATEGY]
    render(<StrategyTab projectId="project-1" />)
    expect(screen.getByTestId('strategy-status-readonly')).toHaveTextContent('Draft')
    expect(screen.queryByRole('combobox', { name: 'Strategy status' })).toBeNull()
    expect(screen.getByRole('button', { name: /Word/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /PDF/ })).toBeInTheDocument()
  })

  it('a QA_LEAD gets the status select and Generate Strategy (the control)', () => {
    signInAs('QA_LEAD')
    strategies.data = [STRATEGY]
    render(<StrategyTab projectId="project-1" />)
    expect(screen.getByRole('combobox', { name: 'Strategy status' })).toHaveValue('draft')
    expect(screen.queryByTestId('strategy-status-readonly')).toBeNull()
    expect(screen.getByRole('button', { name: /Generate Strategy/ })).toBeInTheDocument()
  })
})
