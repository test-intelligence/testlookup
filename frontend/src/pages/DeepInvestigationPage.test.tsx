import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import DeepInvestigationPage, { InvestigationKpiStrip } from './DeepInvestigationPage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

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
  it('renders the verdict, evidence sources, spend and model routing (and no workflow ribbon)', async () => {
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

    // UX P2: the 4-stage "Investigation workflow" ribbon (invented stages,
    // "~6 artifacts each", a stub stage drawer) is gone; the verdict is the
    // load sentinel.
    expect(await screen.findByRole('region', { name: 'Investigation verdict' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Deep Investigation' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Investigation workflow' })).toBeNull()
    expect(screen.queryByText(/Investigation Workflow/i)).toBeNull()
    expect(screen.queryByText(/artifacts each/i)).toBeNull()

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
  await screen.findByRole('region', { name: 'Investigation verdict' })
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

// ── UX P2 "remove the noise": stubs, fabricated values, honest scope ──────
describe('DeepInvestigationPage — only what it really does', () => {
  const NOW = Date.now()
  const hoursAgo = (h: number) => new Date(NOW - h * 3600_000).toISOString()

  async function renderPage(opts: {
    trailCompletedAt?: string | null
    clusters?: Record<string, unknown>[]
    findings?: Record<string, unknown>[]
    /** Extra fields on the focused run (run-1). */
    focused?: Record<string, unknown>
  } = {}) {
    const { useFailureClusters, useDeepFindings, usePipelineStatus } = await import('@/hooks/useDeepInvestigation')
    const { useRuns, useRun } = await import('@/hooks/useRuns')
    const { useDecisionTrail } = await import('@/hooks/useDecisionTrail')

    ;(usePipelineStatus as ReturnType<typeof vi.fn>).mockReturnValue({ data: { status: 'completed' } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { id: 'run-1', run_seq: 12, failed_tests: 7, created_at: hoursAgo(80), ...opts.focused },
          { id: 'run-2', run_seq: 11, failed_tests: 24, created_at: hoursAgo(90) },
          { id: 'run-3', run_seq: 10, failed_tests: 3, created_at: hoursAgo(100) },
          { id: 'run-4', run_seq: 9, failed_tests: 5, created_at: hoursAgo(110) },
          { id: 'run-5', run_seq: 8, failed_tests: 9, created_at: hoursAgo(120) },
        ],
      },
    })
    ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
    ;(useFailureClusters as ReturnType<typeof vi.fn>).mockReturnValue({
      data: opts.clusters ?? [{
        cluster_id: 'cl-1', label: 'DB Timeouts', representative_error: 'TimeoutError',
        member_test_ids: ['t1', 't2'], size: 2, cohesion_score: 0.88,
      }],
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useDeepFindings as ReturnType<typeof vi.fn>).mockReturnValue({
      data: opts.findings ?? [{
        cluster_id: 'cl-1', root_cause: 'Connection pool exhaustion', failure_category: 'INFRASTRUCTURE',
        confidence_score: 91, causal_chain: null, evidence: null, affected_services: ['payments-api'],
        contract_violations: [], recommended_actions: [],
      }],
      isLoading: false,
      mutate: vi.fn(),
    })
    const impl = (useDecisionTrail as ReturnType<typeof vi.fn>).getMockImplementation() as
      ((runId: string | null) => { data: Record<string, unknown> }) | undefined
    const base = impl?.('run-1') ?? { data: {} }
    ;(useDecisionTrail as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...base.data, completed_at: opts.trailCompletedAt ?? null },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/deep-investigate/run-1']}>
        <Routes>
          <Route path="/deep-investigate/:runId" element={<DeepInvestigationPage />} />
        </Routes>
      </MemoryRouter>,
    )
    return screen.findByRole('region', { name: 'Investigation verdict' })
  }

  it('renders no stub control: History, Configure, Run on selection, Dry run, Open cluster, Swap, Decision trail', async () => {
    await renderPage()
    for (const name of [/^History$/, /^Configure$/, /Run on selection/, /Dry run/, /Open cluster/, /Swap/, /Decision trail/]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    // The ⌘⏎ shortcut was advertised and never wired.
    expect(screen.queryByText('⌘⏎')).toBeNull()
    // Evidence-source rows were buttons whose only effect was a toast.
    expect(screen.queryByRole('button', { name: /GitHub/ })).toBeNull()
    expect(screen.getByText('GitHub')).toBeInTheDocument()
    // The one real CTA stays.
    expect(screen.getByRole('button', { name: /Run on all 7 failures/ })).toBeInTheDocument()
  })

  it('renders neither the Run settings card (never sent with triggerDeep) nor the provenance footer', async () => {
    await renderPage()
    expect(screen.queryByText('Run settings')).toBeNull()
    expect(screen.queryByText(/persists locally/)).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByText(/Clustering threshold/)).toBeNull()
    expect(screen.queryByText(/investigation pipeline v2/)).toBeNull()
    expect(screen.queryByText(/runner local/)).toBeNull()
    expect(screen.queryByText(/^Provenance$/)).toBeNull()
  })

  it('names the real scope (the focused run) where the deleted settings said "24h window"', async () => {
    const verdict = await renderPage()
    expect(screen.queryByText(/24h/)).toBeNull()
    expect(screen.queryByText(/window/)).toBeNull()
    // Failures eligible: 7, counted in Run #12 — and the Scope facet says so.
    expect(verdict).toHaveTextContent('Failures eligible7in Run #12')
    expect(verdict).toHaveTextContent(/ScopeRun #12/)
    // The headline's "across N suites" counted clusters; it now names the scope.
    expect(verdict).toHaveTextContent('7 failures in Run #12 — 1 cluster proposed')
    // No "since <commit>" built from the run's UUID.
    expect(verdict).not.toHaveTextContent(/since/)
    const inputs = screen.getByRole('region', { name: 'Investigation inputs' })
    expect(inputs).toHaveTextContent('2/3 sources · Run #12')
  })

  it('shows no invented owner handle on a proposed cluster, only the service its finding names', async () => {
    await renderPage()
    expect(screen.queryByText(/@team-/)).toBeNull()
    expect(screen.queryByText(/\d+ affected/)).toBeNull()
    expect(screen.getByText('payments-api')).toBeInTheDocument()
  })

  it('shows investigation data only for the focused run: no invented clusters, costs or "Failed at clustering" rows', async () => {
    await renderPage()
    const table = screen.getByRole('table')
    expect(screen.getByText('Recent runs')).toBeInTheDocument()
    expect(screen.queryByText('Past investigations')).toBeNull()
    expect(within(table).queryByText(/Failed at/)).toBeNull()
    // run-2 (24 failures) used to read 2 clusters / 2 defects / $0.42; run-5 was the demo "failed" row.
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    const focused = rows[0]
    expect(within(focused).getByText('Run #12')).toBeInTheDocument()
    expect(focused).toHaveTextContent('$0.42') // the decision trail's actual cost
    expect(focused).toHaveTextContent('Complete') // the focused run's pipeline status
    for (const other of rows.slice(1)) {
      expect(other).not.toHaveTextContent('$')
      expect(other).not.toHaveTextContent(/Complete|Failed|Running/)
      // Clusters, Findings, Avg conf, Cost, Status: unknown for a run that is not focused.
      const cells = Array.from(other.querySelectorAll('td'), (td) => td.textContent)
      expect(cells.slice(3)).toEqual(['—', '—', '—', '—', '—'])
    }
  })

  it('reads "Last analysis" from the decision trail, not from a run\'s age label', async () => {
    await renderPage({ trailCompletedAt: hoursAgo(5.5) })
    const inputs = screen.getByRole('region', { name: 'Investigation inputs' })
    const cell = within(inputs).getByText('Last analysis').closest('div')?.parentElement as HTMLElement
    // The focused run is 80h old ("3d ago"), which used to surface as "3 h ago".
    expect(cell).toHaveTextContent('5h ago')
    expect(cell).toHaveTextContent('1 cluster · 1 finding · $0.42')
  })

  // ── An unscored cluster is unscored (it used to get an invented 0.7) ──
  const cluster = (id: string, label: string, cohesion: number | null) => ({
    cluster_id: id, label, representative_error: 'E', member_test_ids: ['t1'], size: 3, cohesion_score: cohesion,
  })
  const kpi = (label: string) =>
    within(screen.getByRole('region', { name: 'Investigation inputs' })).getByText(label).closest('div')?.parentElement as HTMLElement

  it('shows an unscored cluster with no severity band and no confidence number', async () => {
    await renderPage({ clusters: [cluster('cl-u', 'Mystery Errors', null)], findings: [] })
    // The name is also in the verdict lede; the cluster row is the bordered grid.
    const row = screen.getAllByText('Mystery Errors')
      .map((el) => el.closest('.grid.rounded-md'))
      .find((el): el is HTMLElement => el != null) as HTMLElement
    expect(within(row).getByText('unscored')).toBeInTheDocument()
    expect(row).not.toHaveTextContent(/P[0-3] likely/)
    expect(row).not.toHaveTextContent(/Cluster cohesion/)
    expect(row).not.toHaveTextContent(/\d\.\d{2}/)
    expect(row).toHaveTextContent('No cohesion score.')
    // 0.7 used to land it in the P1 band.
    expect(screen.queryByText(/0\.70/)).toBeNull()
  })

  it('averages only the scored clusters (0.88 and 0.60 → 0.74), counting below-target among the scored', async () => {
    await renderPage({
      clusters: [cluster('cl-a', 'Alpha', 0.88), cluster('cl-u', 'Unscored One', null), cluster('cl-b', 'Beta', null)],
      // Beta's score comes from its finding, as a percent: normalised to 0.60.
      findings: [{
        cluster_id: 'cl-b', root_cause: 'Pool exhausted', failure_category: 'INFRASTRUCTURE', confidence_score: 60,
        causal_chain: null, evidence: null, affected_services: [], contract_violations: [], recommended_actions: [],
      }],
    })
    const avg = kpi('Avg cluster confidence')
    // With the invented 0.7 the mean of three was (0.88 + 0.7 + 0.6) / 3 = 0.73.
    expect(avg).toHaveTextContent('0.74')
    expect(avg).toHaveTextContent('target ≥ 0.7 · 1 below')
    expect(within(avg).getByRole('meter')).toHaveAttribute('aria-valuetext', '0.74 of 1.00; target 0.70')
    const likely = kpi('Likely clusters')
    expect(likely).toHaveTextContent('pre-scan · avg conf 0.74')
    expect(likely).toHaveTextContent('1 P0 · 1 P2 · 1 unscored')
  })

  it('says no proposed cluster has a score when none is scored — neutral, no number', async () => {
    await renderPage({ clusters: [cluster('cl-u', 'Mystery Errors', null), cluster('cl-v', 'Other Errors', null)], findings: [] })
    const avg = kpi('Avg cluster confidence')
    expect(avg).toHaveTextContent('no proposed cluster has a score')
    expect(avg).not.toHaveTextContent(/\d\.\d{2}/)
    expect(within(avg).getByText('—')).toHaveStyle({ color: 'var(--color-text)' })
    expect(within(avg).queryByRole('meter')).toBeNull()
    expect(kpi('Likely clusters')).toHaveTextContent('pre-scan · avg conf —')
    expect(screen.getByRole('region', { name: 'Investigation verdict' })).toHaveTextContent(/Pre-scan signal2 clustersunscored/)
  })

  // ── UX redesign P6: the page template's header, not a page-made heading ──
  const header = () => document.querySelector('[data-page-header]') as HTMLElement

  it('renders the template header: one compact h1, the route\'s help topic, the old crumb as its one-line subtitle', async () => {
    await renderPage({ trailCompletedAt: hoursAgo(5.5) })
    expectTemplateHeader('Deep Investigation', '/deep-investigate')
    expect(header()).toHaveTextContent(
      'Semantic clustering & multi-source root cause for Project One · 7 failures ready · 1 likely cluster · last analysis 5h ago',
    )
    // The toolbar shares the header row: the suite filter, then the one primary action.
    const toolbar = header().querySelector('[data-page-toolbar]') as HTMLElement
    expect(within(toolbar).getByRole('combobox', { name: 'Test suite' })).toBeInTheDocument()
    expect(within(toolbar).getByRole('button', { name: /Run Deep Analysis/ })).toBeInTheDocument()
    expect(within(header()).getAllByRole('button').filter(b => !b.getAttribute('aria-label')?.startsWith('Help'))).toHaveLength(1)
  })

  it('keeps the focused run\'s suite as a link in the header row (a chip in the old crumb)', async () => {
    await renderPage({ focused: { primary_suite_name: 'checkout', suite_names: ['checkout', 'payments'] } })
    const link = within(header()).getByRole('link', { name: /checkout/ })
    expect(link).toHaveAttribute('href', '/test-management?tab=Test+Suites&suite=checkout')
    expect(link).toHaveTextContent('checkout+1')
  })

  it('draws no suite chip in the header for a focused run with no suite (it said only "—")', async () => {
    await renderPage()
    expect(within(header()).queryByRole('link')).toBeNull()
    expect(header()).not.toHaveTextContent('—')
  })
})
