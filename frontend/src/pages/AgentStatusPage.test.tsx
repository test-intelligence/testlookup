import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

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

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('AgentStatusPage', () => {
  it('renders the AI report for the selected pipeline run', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

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
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        schema_version: 2,
        pipeline_run_id: 'pipe-1',
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:01:00Z',
        duration_seconds: 60,
        summary: {
          total_stages: 1,
          completed_stages: 1,
          running_stages: 0,
          failed_stages: 0,
          skipped_stages: 0,
          pending_stages: 0,
          progress_percent: 100,
        },
        cost_summary: {
          total_cost_usd: 0.01,
          total_input_tokens: 10,
          total_output_tokens: 20,
          total_tokens: 30,
          total_llm_calls: 1,
          stages: [],
        },
        stages: [
          {
            stage_name: 'summary',
            status: 'completed',
            started_at: '2026-03-31T10:00:10Z',
            completed_at: '2026-03-31T10:00:20Z',
            result_data: { summary_length: 120 },
            error: null,
            skipped_reason: null,
            execution_path: 'executed',
            fallback_used: false,
            input_tokens: 10,
            output_tokens: 20,
            total_tokens: 30,
            llm_calls_count: 1,
            cost_usd: 0.01,
            error_category: null,
            confidence_score: 92,
            evidence_count: 3,
            route_rationale: 'Summary generated from collected evidence',
          },
        ],
        events: [
          {
            event_type: 'stage_started',
            stage_name: 'summary',
            test_case_id: null,
            timestamp: '2026-03-31T10:00:10Z',
            detail: { message: 'Summary stage started' },
          },
        ],
        alerts: [],
      },
      isLoading: false,
    })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockImplementation((runId: string | null) => ({
      data: runId === 'run-1'
        ? {
            test_run_id: 'run-1',
            project_id: 'proj-1',
            build_number: '42',
            executive_summary: 'The AI pipeline found a DB timeout regression.',
            markdown_report: '## Executive Summary\nThe AI pipeline found a DB timeout regression.',
            anomaly_count: 1,
            is_regression: true,
            analysis_count: 3,
            generated_at: '2026-03-31T10:01:00Z',
          }
        : undefined,
      isLoading: false,
      error: undefined,
    }))

    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))
    fireEvent.click(screen.getByRole('button', { name: /view ai report/i }))

    await waitFor(() => {
      expect(screen.getAllByText('Executive Summary').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
  })

  it('surfaces the summary stage error when the report endpoint fails', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          id: 'pipe-2',
          test_run_id: 'run-2',
          workflow_type: 'offline',
          status: 'failed',
          started_at: '2026-03-31T10:00:00Z',
          completed_at: '2026-03-31T10:00:05Z',
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
          status: 'failed',
          started_at: '2026-03-31T10:00:01Z',
          completed_at: '2026-03-31T10:00:05Z',
          result_data: null,
          error: "Summary agent error: model 'qwen2.5:7b' not found (status code: 404)",
        },
      ],
      isLoading: false,
    })
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        schema_version: 2,
        pipeline_run_id: 'pipe-2',
        workflow_type: 'offline',
        status: 'failed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:00:05Z',
        duration_seconds: 5,
        summary: {
          total_stages: 1,
          completed_stages: 0,
          running_stages: 0,
          failed_stages: 1,
          skipped_stages: 0,
          pending_stages: 0,
          progress_percent: 100,
        },
        cost_summary: {
          total_cost_usd: 0,
          total_input_tokens: 0,
          total_output_tokens: 0,
          total_tokens: 0,
          total_llm_calls: 0,
          stages: [],
        },
        stages: [],
        events: [],
        alerts: [],
      },
      isLoading: false,
    })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('500 Internal Server Error'),
    })

    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))
    fireEvent.click(screen.getByRole('button', { name: /view ai report/i }))

    expect(screen.getByText('The AI report could not be loaded.')).toBeInTheDocument()
    expect(screen.getAllByText(/model 'qwen2.5:7b' not found/i).length).toBeGreaterThan(0)
  })

  it('renders workflow progress and event feed for a selected pipeline', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          id: 'pipe-3',
          test_run_id: 'run-3',
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
    ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        schema_version: 2,
        pipeline_run_id: 'pipe-3',
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:01:00Z',
        duration_seconds: 60,
        summary: {
          total_stages: 1,
          completed_stages: 1,
          running_stages: 0,
          failed_stages: 0,
          skipped_stages: 0,
          pending_stages: 0,
          progress_percent: 100,
        },
        cost_summary: {
          total_cost_usd: 0.01,
          total_input_tokens: 10,
          total_output_tokens: 20,
          total_tokens: 30,
          total_llm_calls: 1,
          stages: [],
        },
        stages: [
          {
            stage_name: 'summary',
            status: 'completed',
            started_at: '2026-03-31T10:00:10Z',
            completed_at: '2026-03-31T10:00:20Z',
            result_data: { summary_length: 120 },
            error: null,
            skipped_reason: null,
            execution_path: 'executed',
            fallback_used: false,
            input_tokens: 10,
            output_tokens: 20,
            total_tokens: 30,
            llm_calls_count: 1,
            cost_usd: 0.01,
            error_category: null,
            confidence_score: 92,
            evidence_count: 3,
            route_rationale: 'Summary generated from collected evidence',
          },
        ],
        events: [
          {
            event_type: 'stage_started',
            stage_name: 'summary',
            test_case_id: null,
            timestamp: '2026-03-31T10:00:10Z',
            detail: { message: 'Summary stage started' },
          },
        ],
        alerts: [],
      },
      isLoading: false,
    })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        executive_summary: 'Executive Summary',
        markdown_report: '## Executive Summary\nSummary body',
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

    expect(screen.getByText(/workflow progress/i)).toBeInTheDocument()
    expect(screen.getByText(/workflow event feed/i)).toBeInTheDocument()
    expect(screen.getByText(/summary started/i)).toBeInTheDocument()
  })

  it('clears the selected pipeline when the project changes', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          id: 'pipe-4',
          test_run_id: 'run-4',
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
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        schema_version: 2,
        pipeline_run_id: 'pipe-4',
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:01:00Z',
        duration_seconds: 60,
        summary: {
          total_stages: 1,
          completed_stages: 1,
          running_stages: 0,
          failed_stages: 0,
          skipped_stages: 0,
          pending_stages: 0,
          progress_percent: 100,
        },
        cost_summary: {
          total_cost_usd: 0.01,
          total_input_tokens: 10,
          total_output_tokens: 20,
          total_tokens: 30,
          total_llm_calls: 1,
          stages: [],
        },
        stages: [],
        events: [],
        alerts: [],
      },
      isLoading: false,
    })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockImplementation((runId: string | null) => ({
      data: runId === 'run-4'
        ? {
            test_run_id: 'run-4',
            project_id: 'proj-1',
            build_number: '42',
            executive_summary: 'Executive Summary',
            markdown_report: '## Executive Summary\nSummary body',
            anomaly_count: 1,
            is_regression: false,
            analysis_count: 1,
            generated_at: '2026-03-31T10:01:00Z',
          }
        : undefined,
      isLoading: false,
      error: undefined,
    }))

    const { rerender } = render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))
    fireEvent.click(screen.getByRole('button', { name: /view ai report/i }))

    await waitFor(() => {
      expect(screen.getAllByText('Executive Summary').length).toBeGreaterThan(0)
    })

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText(/Select a pipeline run to see agent stages/i)).toBeInTheDocument()
  })
})
