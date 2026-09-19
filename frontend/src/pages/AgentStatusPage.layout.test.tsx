/**
 * `/agents`: the AI report leads, and Agent Stages can be collapsed.
 *
 * BUG-008 / TL-2026-09-18-01-010 (user request, 2026-09-18). The report is the
 * pipeline's headline output — which is why `showSummary` defaults to true —
 * but it rendered *below* the stage detail, so a reader scrolled past the
 * mechanism to reach the conclusion. Agent Stages is now collapsible so the
 * conclusion stays on screen.
 *
 * Order is asserted with `compareDocumentPosition`, not by "both are present":
 * the defect was purely about which came first, so a presence check would have
 * passed against it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

async function renderWithPipeline() {
  const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
    await import('@/hooks/useAgentRuns')

  ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
    data: [
      {
        id: 'pipe-1',
        test_run_id: 'run-1',
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:01:00Z',
        error: null,
        created_at: '2026-03-31T10:00:00Z',
      },
    ],
    isLoading: false,
  })
  ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({
    data: [
      {
        stage_name: 'summary',
        status: 'completed',
        started_at: '2026-03-31T10:00:10Z',
        completed_at: '2026-03-31T10:00:20Z',
        result_data: { summary_length: 120 },
        error: null,
      },
    ],
    isLoading: false,
  })
  ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
  ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      test_run_id: 'run-1',
      project_id: 'proj-1',
      build_number: '42',
      executive_summary: 'The AI pipeline found a DB timeout regression.',
      markdown_report: '## Executive Summary\nThe AI pipeline found a DB timeout regression.',
      anomaly_count: 1,
      is_regression: true,
      analysis_count: 3,
      generated_at: '2026-03-31T10:01:00Z',
    },
    isLoading: false,
    error: undefined,
  })

  render(
    <MemoryRouter initialEntries={['/agents']}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )

  fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))
  await screen.findByRole('heading', { name: 'AI Report' })
}

/** True when `a` comes before `b` in document order. */
function precedes(a: Element, b: Element): boolean {
  // Node.DOCUMENT_POSITION_FOLLOWING === 4
  return (a.compareDocumentPosition(b) & 4) !== 0
}

describe('AgentStatusPage layout — report first, stages collapsible', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  it('puts the AI report above Agent Stages', async () => {
    await renderWithPipeline()

    const report = screen.getByRole('heading', { name: 'AI Report' })
    const stages = screen.getByRole('heading', { name: 'Agent Stages' })

    expect(
      precedes(report, stages),
      'the AI report is the headline output and must come before the stage detail',
    ).toBe(true)
  })

  it('renders the report content itself above the stage detail, not just the heading', async () => {
    await renderWithPipeline()

    const summaryText = screen.getAllByText(/DB timeout regression/i)[0]
    const stages = screen.getByRole('heading', { name: 'Agent Stages' })

    expect(
      precedes(summaryText, stages),
      'the heading moved but the report body did not',
    ).toBe(true)
  })

  it('collapses and re-expands Agent Stages without hiding the report', async () => {
    await renderWithPipeline()

    const toggle = screen.getByRole('button', { name: /hide stages/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Classic workflow timeline')).toBeInTheDocument()

    fireEvent.click(toggle)

    await waitFor(() => {
      expect(screen.queryByText('Classic workflow timeline')).toBeNull()
    })
    // Collapsing the mechanism must not take the conclusion with it.
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'AI Report' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /show stages/i }))
    await waitFor(() => {
      expect(screen.getByText('Classic workflow timeline')).toBeInTheDocument()
    })
  })

  it('still shows the report expanded by default', async () => {
    // Pinned by AgentStatusPage.test.tsx too; repeated here because the reorder
    // is exactly the kind of change that quietly flips a default.
    await renderWithPipeline()
    expect(screen.getByRole('button', { name: /hide report/i })).toBeInTheDocument()
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
  })
})
