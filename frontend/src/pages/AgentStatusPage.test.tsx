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
  useAIConfig: vi.fn(() => ({
    data: {
      analysis_mode: 'auto',
      deep_investigation_enabled: true,
    },
  })),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({
    data: { items: [] },
    isLoading: false,
  })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({
    isQaEngineer: false,
  })),
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
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

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

    // Selecting a pipeline is enough — the AI report is expanded by default now
    // (no "View AI report" click required).
    fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))

    await waitFor(() => {
      expect(screen.getAllByText('Executive Summary').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText(/DB timeout regression/i).length).toBeGreaterThan(0)
  })

  it('shows the AI report by default (expanded) once a pipeline is selected', async () => {
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
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockImplementation((runId: string | null) => ({
      data: runId === 'run-1'
        ? {
            test_run_id: 'run-1',
            project_id: 'proj-1',
            build_number: '42',
            executive_summary: 'Default-expanded AI report content.',
            markdown_report: '## Executive Summary\nDefault-expanded AI report content.',
            anomaly_count: 0,
            is_regression: false,
            analysis_count: 1,
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

    // Report content is visible WITHOUT any "View AI report" click...
    await waitFor(() => {
      expect(screen.getAllByText(/Default-expanded AI report content/i).length).toBeGreaterThan(0)
    })
    // ...and the toggle now offers to HIDE it (proving it starts expanded).
    expect(screen.getByRole('button', { name: /hide report/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /view ai report/i })).not.toBeInTheDocument()
  })

  it('shows run/suite context (Run #N + suite) on each pipeline card', async () => {
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
          build_number: 'build-42',
          run_seq: 7,
          suite_name: 'API Regression',
        },
      ],
      isLoading: false,
    })
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

    // Context is visible on the card without selecting it.
    expect(await screen.findByText('Run #7')).toBeInTheDocument()
    expect(screen.getByText('API Regression')).toBeInTheDocument()
  })

  it('falls back to Build <n> on a pipeline card when run_seq is absent', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          id: 'pipe-2',
          test_run_id: 'run-2',
          workflow_type: 'offline',
          status: 'completed',
          started_at: '2026-03-31T10:00:00Z',
          completed_at: '2026-03-31T10:01:00Z',
          error: null,
          created_at: '2026-03-31T10:00:00Z',
          build_number: 'nightly-9',
          run_seq: null,
          suite_name: null,
        },
      ],
      isLoading: false,
    })
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

    expect(await screen.findByText('Build nightly-9')).toBeInTheDocument()
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
    // AI report is expanded by default — no "View AI report" click needed.

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

  it('surfaces observability cost signals and alert routing for a selected pipeline', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } = await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          id: 'pipe-obs',
          test_run_id: 'run-obs',
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
        pipeline_run_id: 'pipe-obs',
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-03-31T10:00:00Z',
        completed_at: '2026-03-31T10:01:00Z',
        duration_seconds: 60,
        summary: {
          total_stages: 2,
          completed_stages: 1,
          running_stages: 0,
          failed_stages: 1,
          skipped_stages: 0,
          pending_stages: 0,
          progress_percent: 100,
        },
        cost_summary: {
          total_cost_usd: 5.25,
          total_input_tokens: 1000,
          total_output_tokens: 500,
          total_tokens: 1500,
          total_llm_calls: 4,
          stages: [],
        },
        agent_observability: {
          schema_version: 1,
          stage_count: 2,
          status_counts: { completed: 1, failed: 1 },
          latency: {
            total_stage_duration_seconds: 15,
            max_stage_duration_seconds: 10,
            avg_stage_duration_seconds: 7.5,
          },
          tokens: {
            input: 1000,
            output: 500,
            total: 1500,
            llm_calls: 4,
          },
          cost: {
            total_usd: 5.25,
            budget_usd: 5,
          },
          fallback: {
            count: 1,
            rate: 0.5,
            stages: ['summary'],
          },
          errors: {
            count: 1,
            by_category: { timeout: 1 },
          },
          quality: {
            avg_confidence_score: 82,
            total_evidence_count: 6,
          },
          alerts: {
            count: 1,
            by_type: { repeated_failure: 1 },
          },
          per_agent: [],
        },
        stages: [],
        events: [],
        alerts: [
          {
            type: 'repeated_failure',
            severity: 'warning',
            message: "Stage 'summary' has failed 3 times in the last 24h",
            detail: { stage_name: 'summary', failure_count: 3 },
            routing: {
              primary_owner: 'qa_lead',
              escalation_owner: 'stage_owner',
              priority: 'p2',
              recommended_action: 'Review the failed agent stage and recent decision trail.',
            },
          },
        ],
      },
      isLoading: false,
    })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
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

    expect(screen.getByText('Pipeline Observability')).toBeInTheDocument()
    expect(screen.getByText('$5.25')).toBeInTheDocument()
    expect(screen.getByText('1,500')).toBeInTheDocument()
    expect(screen.getByText('7.5s')).toBeInTheDocument()
    expect(screen.getByText(/owner: qa_lead/i)).toBeInTheDocument()
    expect(screen.getByText(/escalate: stage_owner/i)).toBeInTheDocument()
    expect(screen.getByText('p2')).toBeInTheDocument()
    expect(screen.getByText(/Review the failed agent stage/i)).toBeInTheDocument()
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
    // AI report is expanded by default — no "View AI report" click needed.

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
