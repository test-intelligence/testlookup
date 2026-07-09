import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage from './DeepInvestigationPage'

vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: vi.fn(),
  useDeepFindings: vi.fn(),
  // Pipeline status moved from a load-on-mount effect to this SWR hook; the
  // mock must export it or the page throws on render.
  usePipelineStatus: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  // The page added a ``useRun`` fallback fetch (run-detail KPIs populate
  // even when the run isn't in the recent list). Mock must export it.
  useRun: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  // ``ALL_PROJECTS_ID`` is read at module-import time by several pages —
  // the mock must export it even when the test doesn't exercise All-Projects
  // mode, or vitest raises "No ALL_PROJECTS_ID export is defined".
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

// The intelligence panels are wired to real endpoints (integration health,
// LLM usage/quota, decision trail) — mock the hooks so the test stays
// hermetic instead of letting SWR fire network requests from jsdom.
vi.mock('@/hooks/useIntegrationHealth', () => ({
  useIntegrationStatus: vi.fn(() => ({
    statuses: [
      {
        provider: 'github',
        status: 'healthy',
        last_checked_at: '2026-04-03T15:00:00Z',
        message: 'Rate limit remaining: 4990',
        response_ms: 120,
        consecutive_failures: 0,
        last_success_at: '2026-04-03T15:00:00Z',
      },
      {
        provider: 'jira',
        status: 'down',
        last_checked_at: '2026-04-03T15:00:00Z',
        message: 'Connection refused',
        response_ms: null,
        consecutive_failures: 3,
        last_success_at: null,
      },
    ],
    isLoading: false,
    isError: false,
  })),
}))

vi.mock('@/hooks/useLlmBudget', () => ({
  useProjectUsage: vi.fn(() => ({
    usage: {
      project_id: 'proj-1',
      period_start: '2026-04-01T00:00:00Z',
      period_end: '2026-05-01T00:00:00Z',
      total_cost_usd: 3.21,
      total_input_tokens: 1000,
      total_output_tokens: 500,
      total_llm_calls: 12,
      cap_hits: 0,
      // No quota configured (quota mock below is null) → the meter reports
      // null caps, and the page must render its "no budget set" state.
      included_usd: null,
      hard_cap_usd: null,
      utilization_pct: null,
      status: 'UNLIMITED',
    },
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  })),
  useProjectQuota: vi.fn(() => ({
    quota: null,
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  })),
}))

vi.mock('@/hooks/useDecisionTrail', () => ({
  useDecisionTrail: vi.fn(() => ({
    data: {
      run_id: 'run-1',
      pipeline_run_id: 'pipe-1',
      workflow_type: 'deep',
      pipeline_status: 'completed',
      started_at: null,
      completed_at: null,
      total_cost_usd: 0.42,
      total_tokens: 1234,
      stages: [
        {
          stage_name: 'failure_clustering',
          status: 'completed',
          started_at: null,
          completed_at: null,
          duration_seconds: 12,
          analysis_mode: 'ml',
          fallback_used: false,
          fallback_reason: null,
          route_rationale: null,
          error_category: null,
          skipped_reason: null,
          execution_path: 'embedding',
          confidence_score: 0.9,
          evidence_count: 2,
          input_tokens: null,
          output_tokens: null,
          cost_usd: 0.1,
          decision_log: [],
        },
      ],
      workflow_events: [],
      per_test: [],
      mode_distribution: { ml: 1 },
      fallback_count: 0,
    },
    isLoading: false,
  })),
}))

describe('DeepInvestigationPage', () => {
  it('renders the investigation workflow strip above the cluster view', async () => {
    const { useFailureClusters, useDeepFindings, usePipelineStatus } = await import('@/hooks/useDeepInvestigation')
    const { useRuns, useRun } = await import('@/hooks/useRuns')

    ;(usePipelineStatus as ReturnType<typeof vi.fn>).mockReturnValue({ data: null })

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ id: 'run-1' }] },
    })
    ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
    })
    ;(useFailureClusters as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          cluster_id: 'cl-1',
          label: 'DB Timeouts',
          representative_error: 'TimeoutError',
          member_test_ids: ['t1', 't2'],
          size: 2,
          cohesion_score: 0.88,
        },
      ],
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useDeepFindings as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [
        {
          cluster_id: 'cl-1',
          root_cause: 'Connection pool exhaustion',
          failure_category: 'INFRASTRUCTURE',
          confidence_score: 91,
          causal_chain: [
            { step: 1, service: 'payments-api', finding: 'Pool exhausted under load' },
          ],
          evidence: [
            { source: 'log', excerpt: 'Pool exhausted' },
          ],
          affected_services: ['payments-api'],
          contract_violations: [],
          recommended_actions: ['Increase pool size'],
        },
      ],
      isLoading: false,
      mutate: vi.fn(),
    })

    render(
      <MemoryRouter initialEntries={['/deep-investigate/run-1']}>
        <Routes>
          <Route path="/deep-investigate/:runId" element={<DeepInvestigationPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Investigation Workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Failure Clustering/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Deep Investigation/i).length).toBeGreaterThan(0)

    // Evidence sources come from the real integration-health hook: the
    // intrinsic "Test results" row plus the mocked GitHub (live) and
    // Jira (off) providers — no fabricated telemetry/log rows.
    expect(screen.getByText('Test results')).toBeInTheDocument()
    expect(screen.getByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('Jira context')).toBeInTheDocument()
    expect(screen.queryByText(/Lagging 18s/)).not.toBeInTheDocument()

    // Spend MTD reads the real usage meter ($3.21), not summed fake run costs.
    expect(screen.getByText('$3.21')).toBeInTheDocument()
    // No quota configured → honest empty-budget state, not a fake $40 cap.
    expect(screen.getAllByText(/no budget set/i).length).toBeGreaterThan(0)

    // Model routing renders the actual decision-trail stage + engine.
    expect(screen.getAllByText(/failure clustering/i).length).toBeGreaterThan(0)
    expect(screen.getByText('ml')).toBeInTheDocument()
  })
})
