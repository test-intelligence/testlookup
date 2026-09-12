/**
 * E7.5: every /agents pipeline card shows one of exactly four statuses.
 *
 * The API returns six internal states (pending, running, retry_wait,
 * completed, passed, failed). Before E7.5 the card printed
 * `pipeline.status.toUpperCase()`, so users saw RETRY_WAIT and PENDING. The
 * chip now renders the public projection, prefers the server's
 * `public_status`, and falls back to projecting `status` for a payload that
 * predates it (a cached SWR response is exactly that).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(),
  usePipelineTimeline: vi.fn(),
  useRunSummary: vi.fn(),
  useActiveLiveRuns: vi.fn(),
}))
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: false })),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const LABELS = ['IN PROGRESS', 'COMPLETED', 'FAILED', 'PASSED']

function pipeline(i: number, fields: Record<string, unknown>) {
  return {
    id: `pipe-${i}`,
    test_run_id: `run-${i}`,
    workflow_type: 'offline',
    started_at: '2026-09-12T10:00:00Z',
    completed_at: null,
    error: null,
    created_at: `2026-09-12T10:0${i}:00Z`,
    execution_metadata: {},
    provenance_metadata: null,
    build_number: `b-${i}`,
    run_seq: i + 1,
    suite_name: null,
    ...fields,
  }
}

async function renderWith(pipelines: unknown[]) {
  const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
    await import('@/hooks/useAgentRuns')
  ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({ data: pipelines, isLoading: false })
  ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
  ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
  ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false, error: undefined })
  render(
    <MemoryRouter initialEntries={['/agents']}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AgentStatusPage — four-value status chips (E7.5)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('never shows an internal status, with or without public_status on the payload', async () => {
    const internal = ['pending', 'running', 'retry_wait', 'completed', 'passed', 'failed']
    await renderWith([
      // server-projected
      ...internal.map((status, i) => pipeline(i, { status, public_status: {
        pending: 'in_progress', running: 'in_progress', retry_wait: 'in_progress',
        completed: 'completed', passed: 'passed', failed: 'failed',
      }[status] })),
      // legacy payloads with no public_status: the client must project them
      ...internal.map((status, i) => pipeline(i + 6, { status })),
    ])

    const chips = await screen.findAllByTestId('pipeline-status-chip')
    expect(chips).toHaveLength(12)
    for (const chip of chips) {
      expect(LABELS, `chip text "${chip.textContent}"`).toContain(chip.textContent)
    }
    const texts = chips.map(c => c.textContent)
    expect(texts.filter(t => t === 'IN PROGRESS')).toHaveLength(6)
    expect(texts).not.toContain('RETRY_WAIT')
    expect(texts).not.toContain('PENDING')
    expect(texts).not.toContain('RUNNING')
  })

  it('shows the attempt count on a run waiting to retry', async () => {
    await renderWith([pipeline(0, { status: 'retry_wait', attempt: 2, max_attempts: 5 })])
    expect(await screen.findByTestId('pipeline-retry-detail')).toHaveTextContent('retrying · 2/5')
    expect(screen.getByTestId('pipeline-status-chip')).toHaveTextContent('IN PROGRESS')
  })

  it('marks a running run that was asked to stop, and not a finished one', async () => {
    await renderWith([
      pipeline(0, { status: 'running', cancel_requested: true }),
      pipeline(1, { status: 'failed', cancel_requested: true }),
    ])
    expect(await screen.findAllByTestId('pipeline-stopping')).toHaveLength(1)
  })

  it('keeps degradation as a separate tag next to a COMPLETED chip', async () => {
    await renderWith([
      pipeline(0, { status: 'completed', public_status: 'completed', execution_metadata: { stage_quality: 'degraded' } }),
    ])
    expect(await screen.findByTestId('pipeline-quality-tag')).toHaveTextContent('DEGRADED')
    expect(screen.getByTestId('pipeline-status-chip')).toHaveTextContent('COMPLETED')
  })
})
