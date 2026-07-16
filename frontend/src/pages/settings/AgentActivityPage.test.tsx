/**
 * Settings → Agent Activity ledger (AI-3): table render, row expansion,
 * agent filter, and empty state. Hermetic — hooks mocked.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentActivityPage from './AgentActivityPage'
import type { AgentRunEntry } from '@/types/investigator'

vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentRuns: vi.fn(),
}))

vi.mock('@/hooks/useProjectScopedSWR', () => ({
  useActiveProjectId: vi.fn(() => 'proj-1'),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
}))

// The Fix Attempts section (AI-2) has its own hooks + tests — stub it here so
// this file stays a hermetic test of the governance ledger.
vi.mock('@/components/fixer/FixAttemptsSection', () => ({
  default: () => <div data-testid="fix-attempts-section-stub" />,
}))

const ENTRY: AgentRunEntry = {
  id: 'ar-1',
  agent_id: 'investigator',
  project_id: 'proj-1',
  run_id: 'run-1234abcd',
  mode: 'shadow',
  trigger: 'manual',
  status: 'completed',
  summary: 'Validated infrastructure hypothesis (88%) — pool exhaustion on payments-db.',
  actions_proposed: ['Raise DB pool ceiling to 50'],
  actions_taken: [],
  tokens: 34120,
  cost_usd: 0.04,
  duration_ms: 92000,
  prompt_registry_digest: 'sha256:ab12cd34',
  created_at: '2026-07-15T10:05:00Z',
  details_path: '/deep-investigate/run-1234abcd',
}

async function mockRuns(items: AgentRunEntry[], total = items.length) {
  const { useAgentRuns } = await import('@/hooks/useAgentGovernance')
  ;(useAgentRuns as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items, total },
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  })
  return useAgentRuns as ReturnType<typeof vi.fn>
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentActivityPage />
    </MemoryRouter>,
  )
}

describe('AgentActivityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders ledger rows with mode chip, spend, and duration', async () => {
    await mockRuns([ENTRY])
    renderPage()

    const row = screen.getByTestId('ledger-row-ar-1')
    expect(row).toHaveTextContent('investigator')
    expect(row).toHaveTextContent('shadow')
    expect(row).toHaveTextContent('manual')
    expect(row).toHaveTextContent('completed')
    expect(row).toHaveTextContent('34,120')
    expect(row).toHaveTextContent('$0.04')
    expect(screen.getByText('1–1 of 1')).toBeInTheDocument()
  })

  it('expands a row to show proposed/taken actions, prompt digest, and the details link', async () => {
    await mockRuns([ENTRY])
    renderPage()

    expect(screen.queryByTestId('ledger-detail-ar-1')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('ledger-row-ar-1'))

    const detail = screen.getByTestId('ledger-detail-ar-1')
    expect(detail).toHaveTextContent('Raise DB pool ceiling to 50')
    // Shadow run — actions taken is explicitly "none".
    expect(detail).toHaveTextContent('Actions taken')
    expect(detail).toHaveTextContent('none')
    expect(detail).toHaveTextContent('sha256:ab12cd34')
    expect(screen.getByRole('link', { name: /open details/i })).toHaveAttribute('href', '/deep-investigate/run-1234abcd')
  })

  it('re-queries with the selected agent filter and resets pagination', async () => {
    const useAgentRunsMock = await mockRuns([ENTRY])
    renderPage()

    fireEvent.change(screen.getByLabelText('Filter by agent'), { target: { value: 'investigator' } })

    const calls = useAgentRunsMock.mock.calls
    const lastCall = calls[calls.length - 1]
    expect(lastCall?.[0]).toBe('proj-1')
    expect(lastCall?.[1]).toEqual({ agentId: 'investigator', limit: 50, offset: 0 })
  })

  it('shows an explanatory empty state when there is no activity', async () => {
    await mockRuns([])
    renderPage()

    expect(screen.getByText('No agent activity yet')).toBeInTheDocument()
    expect(screen.getByText(/Every run by a governed AI agent/i)).toBeInTheDocument()
  })

  it('renders the Fix Attempts section for a scoped project', async () => {
    await mockRuns([ENTRY])
    renderPage()

    expect(screen.getByTestId('fix-attempts-section-stub')).toBeInTheDocument()
  })
})
