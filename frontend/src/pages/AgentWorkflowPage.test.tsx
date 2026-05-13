import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import AgentWorkflowPage from './AgentWorkflowPage'

const RUN_1 = '11111111-1111-4111-8111-111111111111'
const RUN_2 = '22222222-2222-4222-8222-222222222222'
const RUN_3 = '33333333-3333-4333-8333-333333333333'

const runs = [
  {
    id: RUN_1,
    build_number: '101',
    status: 'failed',
    passed_tests: 95,
    failed_tests: 5,
    skipped_tests: 0,
    total_tests: 100,
    pass_rate: 95,
    created_at: '2026-05-10T10:00:00Z',
  },
  {
    id: RUN_2,
    build_number: '102',
    status: 'passed',
    passed_tests: 100,
    failed_tests: 0,
    skipped_tests: 0,
    total_tests: 100,
    pass_rate: 100,
    created_at: '2026-05-10T11:00:00Z',
  },
  {
    id: RUN_3,
    build_number: '103',
    status: 'failed',
    passed_tests: 90,
    failed_tests: 10,
    skipped_tests: 0,
    total_tests: 100,
    pass_rate: 90,
    created_at: '2026-05-10T12:00:00Z',
  },
]

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(() => ({
    data: [
      {
        id: 'pipe-1',
        test_run_id: RUN_1,
        workflow_type: 'offline',
        status: 'completed',
        started_at: '2026-05-10T10:00:00Z',
        completed_at: '2026-05-10T10:02:00Z',
        error: null,
        created_at: '2026-05-10T10:00:00Z',
        execution_metadata: null,
        provenance_metadata: null,
      },
    ],
    isLoading: false,
    mutate: vi.fn(),
  })),
  usePipelineStages: vi.fn(() => ({ data: [], isLoading: false })),
  usePipelineTimeline: vi.fn(() => ({
    data: {
      stages: [
        { stage_name: 'ingestion', status: 'completed', started_at: '2026-05-10T10:00:00Z', completed_at: '2026-05-10T10:00:05Z', result_data: null, error: null },
        { stage_name: 'anomaly', status: 'completed', started_at: '2026-05-10T10:00:05Z', completed_at: '2026-05-10T10:00:10Z', result_data: null, error: null },
        { stage_name: 'rca', status: 'completed', started_at: '2026-05-10T10:00:10Z', completed_at: '2026-05-10T10:00:30Z', result_data: null, error: null },
        { stage_name: 'failure_clustering', status: 'completed', started_at: '2026-05-10T10:00:30Z', completed_at: '2026-05-10T10:00:45Z', result_data: null, error: null },
        { stage_name: 'defect_triage', status: 'skipped', started_at: null, completed_at: null, result_data: null, error: null },
        { stage_name: 'flaky_sentinel', status: 'completed', started_at: '2026-05-10T10:00:45Z', completed_at: '2026-05-10T10:01:00Z', result_data: null, error: null },
        { stage_name: 'test_health', status: 'completed', started_at: '2026-05-10T10:01:00Z', completed_at: '2026-05-10T10:01:15Z', result_data: null, error: null },
        { stage_name: 'summary', status: 'completed', started_at: '2026-05-10T10:01:15Z', completed_at: '2026-05-10T10:01:30Z', result_data: null, error: null },
        { stage_name: 'release_risk', status: 'completed', started_at: '2026-05-10T10:01:30Z', completed_at: '2026-05-10T10:02:00Z', result_data: null, error: null },
      ],
    },
    isLoading: false,
  })),
  useActiveLiveRuns: vi.fn(() => ({ data: [] })),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({
    data: { items: runs, total: runs.length, page: 1, size: 100, pages: 1 },
    isLoading: false,
  })),
  useRun: vi.fn((runId: string | undefined) => ({
    data: runs.find(run => run.id === runId),
  })),
}))

vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto' } })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: true })),
}))

vi.mock('@/hooks/useProjectChange', () => ({
  useProjectChangeRedirect: vi.fn(),
  useProjectChangeReset: vi.fn(),
}))

vi.mock('@/hooks/useRunIntelligence', () => ({
  useRunIntelligence: vi.fn(() => ({ intelligence: undefined, isLoading: false, isError: false })),
}))

vi.mock('@/services/agentService', () => ({
  default: {
    triggerPipeline: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('AgentWorkflowPage', () => {
  it('shows the paged test-run selector instead of only pipeline-backed runs', () => {
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes>
          <Route path="/agents" element={<AgentWorkflowPage />} />
          {/* Clicking a run navigates to /agents/run/:runId — the test
              setup needs the parametrised route too or the page renders
              to an empty body after the click. */}
          <Route path="/agents/run/:runId" element={<AgentWorkflowPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('101')).toBeInTheDocument()
    expect(screen.getByText('102')).toBeInTheDocument()
    expect(screen.getByText('103')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /102/i }))

    expect(screen.getByText('No agent pipeline for this test run yet')).toBeInTheDocument()
  })

  it('renders the stage flow with resizable and collapsible work areas', () => {
    render(
      <MemoryRouter initialEntries={['/agents/run/11111111-1111-4111-8111-111111111111']}>
        <Routes>
          <Route path="/agents/run/:runId" element={<AgentWorkflowPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('Stage flow')).toBeInTheDocument()
    expect(screen.getByText('Failure Clustering')).toBeInTheDocument()
    expect(screen.getByText('Defect Triage')).toBeInTheDocument()
    expect(screen.getByText('Flaky Sentinel')).toBeInTheDocument()
    expect(screen.getByText('Test Health')).toBeInTheDocument()
    expect(screen.getByLabelText('Resize stage flow height')).toBeInTheDocument()
    expect(screen.getByLabelText('Resize stage details width')).toBeInTheDocument()
    expect(screen.getByLabelText('Collapse stage details')).toBeInTheDocument()
  })
})
