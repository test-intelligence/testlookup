import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage, { InvestigationKpiStrip } from './DeepInvestigationPage'

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

// AI-1 Investigator cockpit hooks — mocked so its SWR reads never hit the
// network from jsdom. The cockpit renders its empty state.
vi.mock('@/hooks/useInvestigation', () => ({
  useInvestigation: vi.fn(() => ({ data: undefined, mutate: vi.fn() })),
  useInvestigations: vi.fn(() => ({ data: { items: [], total: 0 }, mutate: vi.fn() })),
}))

vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentPolicies: vi.fn(() => ({ policies: [], investigatorPolicy: null })),
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

  // ── US-15.1 AI trust chrome ────────────────────────────────────────────
  it('renders the trust chrome with the finding origin and confidence basis', async () => {
    await renderWithFinding({
      origin: 'pipeline',
      confidence_basis: 'heuristic_estimate',
    })

    expect(screen.getAllByTestId('ai-suggested-badge').length).toBeGreaterThan(0)
    // origin + confidence_basis were already on the wire and never rendered.
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('deep-analysis pipeline')
    expect(screen.getByTestId('ai-basis-chip')).toHaveTextContent('estimated')
    expect(screen.getByTestId('ai-confidence')).toHaveTextContent('91%')
  })

  it('calls out seeded demo rows instead of passing them off as pipeline output', async () => {
    await renderWithFinding({ origin: 'seed', confidence_basis: 'heuristic_estimate' })
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('seeded demo data')
  })

  it('renders no AI chrome for a cluster with no recorded finding', async () => {
    await renderWithFinding(null)
    expect(screen.queryByTestId('ai-suggested-badge')).not.toBeInTheDocument()
    expect(screen.getByText(/No AI finding recorded for this cluster yet/i)).toBeInTheDocument()
  })
})

/** Render the page with one cluster and (optionally) one deep finding. */
async function renderWithFinding(
  findingExtras: Record<string, unknown> | null,
): Promise<void> {
  const { useFailureClusters, useDeepFindings, usePipelineStatus } = await import('@/hooks/useDeepInvestigation')
  const { useRuns, useRun } = await import('@/hooks/useRuns')

  ;(usePipelineStatus as ReturnType<typeof vi.fn>).mockReturnValue({ data: null })
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [{ id: 'run-1' }] } })
  ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
  ;(useFailureClusters as ReturnType<typeof vi.fn>).mockReturnValue({
    data: [{
      cluster_id: 'cl-1',
      label: 'DB Timeouts',
      representative_error: 'TimeoutError',
      member_test_ids: ['t1', 't2'],
      size: 2,
      cohesion_score: 0.88,
    }],
    isLoading: false,
    mutate: vi.fn(),
  })
  ;(useDeepFindings as ReturnType<typeof vi.fn>).mockReturnValue({
    data: findingExtras === null ? [] : [{
      cluster_id: 'cl-1',
      root_cause: 'Connection pool exhaustion',
      failure_category: 'INFRASTRUCTURE',
      confidence_score: 91,
      causal_chain: null,
      evidence: null,
      affected_services: ['payments-api'],
      contract_violations: [],
      recommended_actions: [],
      ...findingExtras,
    }],
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
  await screen.findByText(/Investigation Workflow/i)
}

// ── Wave 2.5 (VIZ-104, OD-1): the five "Investigation inputs" glyphs draw only
// real numbers. Three decorated a real scalar (a GaugeBar each); two were the
// same literal polyline (gone). ────────────────────────────────────────────
describe('InvestigationKpiStrip — glyphs from real numbers only', () => {
  type StripModel = Parameters<typeof InvestigationKpiStrip>[0]['model']

  /** The fields the strip reads; everything else in the page model is irrelevant here. */
  function stripModel(over: Record<string, unknown> = {}): StripModel {
    return {
      eligibleFailures: 12,
      evidenceConnected: 2,
      evidenceTotal: 3,
      windowLabel: '7d',
      severityCounts: { p0: 1, p1: 2, p2: 0, p3: 1 },
      preScanClusters: 4,
      preScanAvgConfidence: 0.74,
      proposedClusters: [{ confidence: 0.9 }, { confidence: 0.6 }],
      lastRunAgeHours: 5,
      lastRunSummary: null,
      spendMtdDollars: 18.42,
      spendBudgetDollars: 50,
      ...over,
    } as unknown as StripModel
  }

  function kpiCell(label: string): HTMLElement {
    const cell = screen.getByText(label).closest('div')?.parentElement
    if (!cell) throw new Error(`no KPI cell labelled ${label}`)
    return cell
  }

  it('gauges spend against the budget', () => {
    render(<InvestigationKpiStrip model={stripModel()} />)
    const meter = within(kpiCell('Spend MTD')).getByRole('meter', { name: 'Spend this month against the monthly budget' })
    expect(meter).toHaveAttribute('aria-valuenow', '18.42')
    expect(meter).toHaveAttribute('aria-valuemax', '50')
    expect(meter).toHaveAttribute('aria-valuetext', '$18.42 of $50.00')
  })

  it.each([
    ['no budget', null],
    ['a zero budget', 0],
  ])('draws NO spend bar with %s — not a bar at 0, not eight placeholder bars', (_name, budget) => {
    render(<InvestigationKpiStrip model={stripModel({ spendBudgetDollars: budget })} />)
    const cell = kpiCell('Spend MTD')
    expect(within(cell).queryByRole('meter')).toBeNull()
    expect(within(cell).queryByRole('img')).toBeNull()
    expect(cell.querySelector('svg:not(.lucide)')).toBeNull()
    expect(cell.querySelector('[data-gauge-bar]')).toBeNull()
    expect(cell).toHaveTextContent('no budget set')
  })

  // R2 F8: the bar splits the proposed CLUSTERS, the set "Likely clusters"
  // counts (`preScanClusters = proposedClusters.length`). Under "Eligible
  // failures 12" a three-segment bar read as a split of the 12 failures.
  it('stacks the proposed clusters by severity, P0 to P3, under "Likely clusters"', () => {
    render(<InvestigationKpiStrip model={stripModel()} />)
    const meter = within(kpiCell('Likely clusters')).getByRole('meter', { name: 'Proposed clusters by severity' })
    expect(meter).toHaveAttribute('aria-valuetext', '4 of 4; P0 1, P1 2, P2 0, P3 1')
    expect(within(kpiCell('Eligible failures')).queryByRole('meter')).toBeNull()
  })

  it('names the severity bar, and its counts, in visible text — not only for a screen reader', () => {
    render(<InvestigationKpiStrip model={stripModel()} />)
    const cell = kpiCell('Likely clusters')
    const name = within(cell).getByText('Proposed clusters by severity')
    expect(name).toBeVisible()
    expect(name.closest('.sr-only')).toBeNull()
    // The segments are not colour-only: the counts are written out.
    expect(within(cell).getByText('1 P0 · 2 P1 · 1 P3')).toBeVisible()
  })

  it('draws no severity bar when analysis proposed no cluster', () => {
    render(<InvestigationKpiStrip model={stripModel({ severityCounts: { p0: 0, p1: 0, p2: 0, p3: 0 } })} />)
    expect(within(kpiCell('Likely clusters')).queryByRole('meter')).toBeNull()
    expect(within(kpiCell('Eligible failures')).queryByRole('meter')).toBeNull()
  })

  it('states no average confidence as "—", never "avg conf 0.00", when there is no cluster', () => {
    render(<InvestigationKpiStrip model={stripModel({
      preScanClusters: 0, preScanAvgConfidence: 0, proposedClusters: [], severityCounts: { p0: 0, p1: 0, p2: 0, p3: 0 },
    })} />)
    const likely = kpiCell('Likely clusters')
    expect(likely).toHaveTextContent('pre-scan · avg conf —')
    expect(likely).not.toHaveTextContent('0.00')
    // "Avg cluster confidence" says "—" in the neutral tone: not measured is not a bad reading.
    const value = within(kpiCell('Avg cluster confidence')).getByText('—')
    expect(value).toHaveStyle({ color: 'var(--color-text)' })
  })

  it('gauges the average confidence on 0-1 against the 0.7 target, toned by the KPI bands', () => {
    render(<InvestigationKpiStrip model={stripModel()} />)
    const meter = within(kpiCell('Avg cluster confidence')).getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuetext', '0.74 of 1.00; target 0.70')
    expect(meter).toHaveAttribute('data-tone', 'warn')
  })

  it('says the confidence is not measured before analysis proposed a cluster — never a bar at 0', () => {
    render(<InvestigationKpiStrip model={stripModel({ preScanClusters: 0, preScanAvgConfidence: 0 })} />)
    const cell = kpiCell('Avg cluster confidence')
    expect(within(cell).queryByRole('meter')).toBeNull()
    expect(within(cell).getByRole('img', { name: 'Average cluster confidence: not measured' })).toBeInTheDocument()
  })

  it('deletes the two literal polylines: Likely clusters, Last analysis', () => {
    render(<InvestigationKpiStrip model={stripModel()} />)
    for (const label of ['Likely clusters', 'Last analysis']) {
      const cell = kpiCell(label)
      expect(cell.querySelector('svg:not(.lucide)'), label).toBeNull()
      expect(cell.querySelector('polyline'), label).toBeNull()
    }
    // Likely clusters now carries the severity bar (R2 F8); Last analysis has no glyph at all.
    expect(within(kpiCell('Last analysis')).queryByRole('meter')).toBeNull()
    expect(within(kpiCell('Last analysis')).queryByRole('img')).toBeNull()
  })
})
