/**
 * Tests for RunIntelligencePage.
 *
 * Verifies:
 * - Loading state shows spinner
 * - "AI analysis not yet available" state
 * - GO / CONDITIONAL_GO / NO_GO banner rendering
 * - Stats row (pass rate, total failures, clusters)
 * - Failure cluster cards
 * - Executive summary text from layer1
 */
import { render, screen, fireEvent, within } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FailureClusterIntel, Provenance, RunModeSummary, ScoringModel } from '@/services/runIntelligenceService'
import { useAuthStore } from '@/store/authStore'

// ── Mocks ────────────────────────────────────────────────────────────────────

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

const {
  mockUseRunIntelligence,
  mockUseRunModeSummary,
  mockUseScoringModel,
  mockUseDecisionReportVersions,
} = vi.hoisted(() => ({
  mockUseDecisionReportVersions: vi.fn(),
  mockUseRunIntelligence: vi.fn(),
  mockUseRunModeSummary: vi.fn(),
  mockUseScoringModel: vi.fn(),
}))

vi.mock('@/hooks/useRunIntelligence', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useRunIntelligence')>()
  return {
    ...actual,
    useRunIntelligence: mockUseRunIntelligence,
    useRunModeSummary: mockUseRunModeSummary,
    useScoringModel: mockUseScoringModel,
    useDecisionReportVersions: mockUseDecisionReportVersions,
  }
})

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

const { mockCompleteStep } = vi.hoisted(() => ({ mockCompleteStep: vi.fn() }))

vi.mock('@/services/onboardingService', () => ({
  onboardingService: { completeStep: mockCompleteStep },
}))

// ── Helpers ──────────────────────────────────────────────────────────────────

const MOCK_INTELLIGENCE = {
  intelligence_available: true,
  run: {
    id: 'run-abc',
    build_number: '42',
    status: 'FAILED',
    total_tests: 200,
    passed_tests: 185,
    failed_tests: 15,
    skipped_tests: 0,
    pass_rate: 92.5,
    branch: 'main',
    duration_ms: 120000,
    start_time: '2026-03-30T12:00:00Z',
    end_time: '2026-03-30T12:02:00Z',
    ocp_namespace: 'qa',
  },
  structured_summary: {
    executive_summary: '15 failures detected across 3 suites. Primary cause: DB connection pool exhaustion.',
    layer1_executive: '15 failures detected across 3 suites. Primary cause: DB connection pool exhaustion.',
    layer2_incident: {
      what_failed: 'PaymentSuite tests',
      likely_cause: 'DB connection pool',
      scope: 'payment-service',
      criticality: 'HIGH',
    },
    layer3_evidence: {},
    layer4_action_plan: {
      immediate_mitigation: 'Restart DB connection pool',
      fix_recommendations: ['Scale DB pods'],
      owner_hints: { sre: 'Check DB metrics', developer: 'Review pool config' },
    },
    generated_at: '2026-03-30T12:03:00Z',
    schema_version: 2,
  },
  failure_clusters: [
    {
      id: 'cluster-row-1',
      cluster_id: 'cl-1',
      label: 'DB Timeouts',
      size: 8,
      representative_error: 'ConnectionTimeoutException: Unable to acquire JDBC Connection',
      member_test_ids: ['t1', 't2', 't3', 't4', 't5', 't6', 't7', 't8'],
      cohesion_score: 0.85,
      // Widened so a test can supply an unscored (null) cluster.
      criticality_level: 'HIGH' as FailureClusterIntel['criticality_level'],
      dimension_scores: [],
    },
  ],
  category_breakdown: {
    INFRASTRUCTURE: 8,
    PRODUCT_BUG: 4,
    UNKNOWN: 3,
  },
  affected_suites: [
    { suite: 'PaymentSuite', failed_count: 8 },
    { suite: 'AuthSuite', failed_count: 4 },
  ],
  release_decision: {
    recommendation: 'NO_GO',
    risk_score: 68,
    reasoning: 'High user impact with product bugs detected.',
    blocking_issues: ['Resolve DB connection pool exhaustion'],
    conditions_for_go: [],
  },
  top_analyses: [],
  avg_confidence: 88,
  pipeline_stages: [
    {
      stage_name: 'summary',
      status: 'completed',
      started_at: '2026-03-30T12:01:00Z',
      completed_at: '2026-03-30T12:01:10Z',
      skipped_reason: null,
      execution_path: 'executed',
      fallback_used: false,
    },
  ],
  role_actions: {},
  all_green: false,
  dimension_scores: [
    {
      name: 'user_impact',
      label: 'User Impact',
      score: 80,
      weight: 0.2,
      contribution: 16,
    },
    {
      name: 'reproducibility',
      label: 'Reproducibility',
      score: 60,
      weight: 0.2,
      contribution: 12,
    },
  ],
  what_changed_since_last_good_run: null,
  defect_candidates: [],
  summary_modes: null,
  // US-15.1: widened so a test can supply a real provenance block (the
  // literal `null` narrowed the inferred type to `null`).
  provenance: null as Provenance | null,
}

const MOCK_MODE_SUMMARY: RunModeSummary = {
  test_run_id: 'run-abc',
  mode: 'developer',
  executive_summary: 'Developer summary',
  markdown_report: '## Summary\nDeveloper summary',
  layer1_executive: 'Developer summary',
  layer2_incident: {
    likely_cause: 'DB connection pool',
    scope: 'payment-service',
    criticality: 'HIGH',
    failure_breakdown: { INFRASTRUCTURE: 8 },
  },
  layer3_evidence: null,
  layer4_action_plan: {
    immediate_mitigation: 'Restart DB connection pool',
    fix_recommendations: ['Scale DB pods'],
    validation_steps: ['Re-run failed suites'],
  },
  fallback_used: false,
  generated_at: '2026-03-30T12:03:00Z',
  citations: [],
  similar_failures: [],
  provenance: null,
}

const MOCK_SCORING_MODEL: ScoringModel = {
  version: 1,
  go_threshold: 20,
  no_go_threshold: 50,
  dimensions: [
    { name: 'user_impact', weight: 0.2, description: 'Measures likely customer-facing risk.' },
    { name: 'reproducibility', weight: 0.2, description: 'Measures how consistently the issue can be reproduced.' },
  ],
}

import { RunDecisionReport, RunIntelligenceBody } from './RunIntelligencePage'

/**
 * What the retired standalone page (`/runs/:id/intelligence`, a redirect since
 * P4) rendered under its own header: the decision report, then the analysis
 * body. Both live on the Run page now (Evidence and Analysis tabs); these
 * tests keep exercising them through the same route shape.
 */
function RunIntelligencePage() {
  const { runId = '' } = useParams<{ runId: string }>()
  return (
    <>
      <RunDecisionReport runId={runId} />
      <RunIntelligenceBody runId={runId} />
    </>
  )
}

// ── Tests ────────────────────────────────────────────────────────────────────

function mockHooks(overrides?: {
  intelligence?: typeof MOCK_INTELLIGENCE | undefined
  isLoading?: boolean
  isError?: boolean
}) {
  mockUseRunIntelligence.mockReturnValue({
    intelligence: overrides?.intelligence,
    isLoading: overrides?.isLoading ?? false,
    isError: overrides?.isError ?? false,
  })
  mockUseRunModeSummary.mockReturnValue({
    summary: MOCK_MODE_SUMMARY,
    isLoading: false,
    isError: false,
  })
  mockUseDecisionReportVersions.mockReturnValue({ versions: [], isLoading: false, isError: false })
  mockUseScoringModel.mockReturnValue({
    scoringModel: MOCK_SCORING_MODEL,
    isLoading: false,
    isError: false,
    getDescription: (name: string) => MOCK_SCORING_MODEL.dimensions.find((d) => d.name === name)?.description ?? '',
  })
}

function renderRunIntel() {
  return render(
    <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
      <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
    </MemoryRouter>,
  )
}

// UX redesign P4: the score's working and the evidence are collapsed
// Disclosures below What failed; their content is not rendered until opened.
const openScore = () => fireEvent.click(screen.getByRole('button', { name: /^How this score is computed/ }))
const openEvidence = () => fireEvent.click(screen.getByRole('button', { name: /^Evidence behind the verdict/ }))

describe('RunIntelligencePage', () => {
  beforeEach(() => {
    // Some tests seed a stale ``tl.runIntel.decision.<runId>`` entry (left by
    // the removed local-only decision controls) to prove it is ignored. Wipe
    // between tests so one test's seed doesn't leak into the next.
    try {
      localStorage.removeItem('tl.runIntel.persona')
      Object.keys(localStorage)
        .filter(k => k.startsWith('tl.runIntel.decision.'))
        .forEach(k => localStorage.removeItem(k))
    } catch { /* ignore */ }
    // Reset the onboarding step mock + project selection between tests (one
    // test mutates activeProjectId to 'proj-2'). completeStep returns a
    // promise the page attaches `.catch()` to, so resolve by default.
    mockCompleteStep.mockReset()
    mockCompleteStep.mockResolvedValue(undefined)
    mockProjectState.activeProjectId = 'proj-1'
  })

  // Regression: the "View Run Intelligence" onboarding step has no DB signal
  // for auto_detect_progress and no other caller of completeStep, so opening
  // this page is the only thing that can complete it. Without the effect the
  // setup wizard's view_intelligence step stays pending for ever and setup
  // never reaches 100%.
  describe('view_intelligence onboarding step', () => {
    const renderPage = () =>
      render(
        <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
          <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
        </MemoryRouter>,
      )

    it('completes the step for the active project when intelligence loads', () => {
      mockHooks({ intelligence: structuredClone(MOCK_INTELLIGENCE) })
      renderPage()
      expect(mockCompleteStep).toHaveBeenCalledWith('proj-1', 'view_intelligence')
      expect(mockCompleteStep).toHaveBeenCalledTimes(1)
    })

    it('does not complete the step in All Projects mode (no real project id)', () => {
      mockProjectState.activeProjectId = '__ALL__'
      mockHooks({ intelligence: structuredClone(MOCK_INTELLIGENCE) })
      renderPage()
      expect(mockCompleteStep).not.toHaveBeenCalled()
    })

    it('does not complete the step while loading or on error', () => {
      mockHooks({ isLoading: true })
      renderPage()
      expect(mockCompleteStep).not.toHaveBeenCalled()

      mockHooks({ isError: true })
      renderPage()
      expect(mockCompleteStep).not.toHaveBeenCalled()
    })
  })

  it('wires terminal decision fields before the release verdict', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1,
      status: 'complete',
      generated_at: '2026-08-11T19:00:00Z',
      metrics: { total_tests: 200, failed_tests: 15, pass_rate: 92.5 },
      failure_clusters: [],
      deep_findings: {},
      flaky_findings: [],
      test_health_findings: [],
      release_decision: { recommendation: 'NO_GO', risk_score: 68 },
      quality_review: {
        missing_or_failed_specialists: [],
        contradictions: [],
        gap_report: null,
        refined_report: null,
        requires_human_review: false,
      },
      source_stages: ['decision_report'],
      evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = {
      status: 'passed', checks: [],
    }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-1',
      status: 'published',
      verification_status: 'passed',
      at: '2026-08-11T19:01:00Z',
    }
    mockHooks({ intelligence })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    const panel = screen.getByRole('status')
    const verdict = screen.getByText(/ship blocked/i).closest('section')
    expect(panel.compareDocumentPosition(verdict as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('Verified', { selector: 'span' })).toBeTruthy()
  })

  it('ignores a persisted local decision when terminal verification rejects the latest attempt', () => {
    localStorage.setItem('tl.runIntel.decision.run-abc', JSON.stringify({
      action: 'OVERRIDE', gate: 'GO', label: 'Override applied by you', at: '2026-08-11T19:00:00Z',
    }))
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = null
    intelligence.structured_summary.decision_report_verification = { status: 'failed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-2', status: 'rejected', verification_status: 'failed', at: '2026-08-11T19:02:00Z',
    }
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText(/No verified decision report is available/)).toBeInTheDocument()
    expect(screen.queryByText(/Override applied by you/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
    expect(screen.getByText(/awaiting evidence/i)).toBeInTheDocument()
  })

  it('uses only terminal-report verdict provenance and ignores persona and legacy decision fields', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
      metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
      release_decision: {
        recommendation: 'GO', risk_score: 12, reasoning: 'Terminal verified rationale.',
        dimension_scores: { criticality: 80, impact: 40 },
      },
      quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
      source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = { status: 'passed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-1', status: 'published', verification_status: 'passed', at: '2026-08-11T19:01:00Z',
    }
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    expect(screen.getAllByText(/^Go$/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Terminal verified rationale.').length).toBeGreaterThan(0)
    // P4 removed the persona tabs: there is no Developer view to switch to.
    expect(screen.queryByRole('tab', { name: 'Developer' })).toBeNull()
    expect(screen.queryByText('Developer summary')).toBeNull()
    expect(screen.queryByText(/PaymentSuite suite/)).toBeNull()
    openScore()
    expect(screen.getByText(/Dimension scores will appear/)).toBeInTheDocument()
  })

  it('does not let a saved legacy override mask a newly verified terminal report', () => {
    localStorage.setItem('tl.runIntel.decision.run-abc', JSON.stringify({
      action: 'OVERRIDE', gate: 'GO', label: 'Override applied by you', at: '2026-08-10T19:00:00Z',
    }))
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
      metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
      release_decision: { recommendation: 'NO_GO', risk_score: 80, reasoning: 'New verified verdict.' },
      quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
      source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = { status: 'passed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-3', status: 'published', verification_status: 'passed', at: '2026-08-11T19:01:00Z',
    }
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    expect(screen.getAllByText(/No-Go/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText('New verified verdict.').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Override applied by you/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
  })

  it('shows unavailable instead of zero when a verified terminal report has no risk score', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
      metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
      release_decision: { recommendation: 'NO_GO', reasoning: 'Risk source unavailable.' },
      quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
      source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = { status: 'passed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-1', status: 'published', verification_status: 'passed', at: '2026-08-11T19:01:00Z',
    }
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    openScore()
    // The header is page markup: its number is the dash.
    const meter = screen.getByText('Composite risk score').parentElement
    expect(within(meter as HTMLElement).getByText('—')).toBeInTheDocument()
    // …and the bar under it agrees: not measured (no fill, no reading), never a meter at 0.
    expect(screen.getByRole('img', { name: 'Composite risk score: not measured' })).toBeInTheDocument()
    expect(screen.queryByRole('meter', { name: 'Composite risk score' })).toBeNull()
  })

  it('does not expose local decision actions for a stale retained report', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
      metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
      release_decision: { recommendation: 'NO_GO', risk_score: 80, reasoning: 'Last verified rationale.' },
      quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
      source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = { status: 'failed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-2', status: 'rejected', verification_status: 'failed', at: '2026-08-11T19:02:00Z',
    }
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText(/Showing the last verified report/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
    expect(screen.getByRole('link', { name: /Review policy and overrides/i })).toHaveAttribute('href', '/release-gate/run-abc')
  })

  it('shows loading spinner while data is loading', async () => {
    mockHooks({ intelligence: undefined, isLoading: true, isError: false })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Should render loading state (spinner is present)
    expect(document.querySelector('.animate-spin') ?? screen.queryByText(/loading/i)).toBeTruthy()
  })

  it('renders NO_GO banner with risk score', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The verdict label is rendered as "No-Go" (kebab case) per the
    // verdict redesign; the underscore enum value is internal-only.
    expect(screen.getAllByText(/No-Go/i).length).toBeGreaterThan(0)
    // P4: the verdict is one StatusBanner line (NO-GO pill, risk in a fact)...
    const banner = document.querySelector('[data-status-banner]') as HTMLElement
    expect(banner).toHaveAttribute('data-status-banner', 'no_go')
    expect(within(banner).getByText('68/100')).toBeInTheDocument()
    // ...and the meter that reads it is in "How this score is computed".
    expect(screen.queryByRole('meter', { name: 'Composite risk score' })).toBeNull()
    openScore()
    // Risk score is rendered as two sibling text nodes — the score and
    // "/ 100" with a space — so test each separately.
    expect(screen.getByText('68')).toBeInTheDocument()
    expect(screen.getByText(/\/ 100/)).toBeInTheDocument()
    // The bar under it is a meter reading the same score, on the risk scale
    // (lower is better), with its band in words.
    const bar = screen.getByRole('meter', { name: 'Composite risk score' })
    expect(bar).toHaveAttribute('aria-valuenow', '68')
    expect(bar.getAttribute('aria-valuetext')).toMatch(/^68 of 100, No-Go$/i)
    expect(bar).toHaveAttribute('data-gauge-bar', 'fill')
    expect((bar.querySelector('[data-gauge-fill]') as HTMLElement).style.backgroundImage).toBe('var(--gradient-risk)')
    for (const tick of ['Safe · 0', 'Conditional · 30', 'Block · 70', '100']) expect(within(bar).getByText(tick)).toBeInTheDocument()
  })

  it('a PENDING gate draws no reading even when a score exists: the header says "—" and the bar agrees', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE)
    // A score with no recommendation: the gate is PENDING.
    ;(intelligence.release_decision as { recommendation?: string }).recommendation = undefined
    mockHooks({ intelligence })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes><Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} /></Routes>
      </MemoryRouter>,
    )
    expect(document.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'pending')
    openScore()
    const header = screen.getByText('Composite risk score').parentElement as HTMLElement
    expect(within(header).getByText('—')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Composite risk score: not measured' })).toBeInTheDocument()
    expect(screen.queryByRole('meter', { name: 'Composite risk score' })).toBeNull()
  })

  it('renders executive summary from layer1', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // For the default "executive" persona the lede prefers
    // ``release_decision.reasoning`` over structured_summary.executive_summary —
    // match the reasoning string from MOCK_INTELLIGENCE since that's what
    // actually renders today.
    expect(
      screen.getAllByText(/High user impact with product bugs detected/i).length,
    ).toBeGreaterThan(0)
  })

  it('renders failure cluster label and size', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText(/DB Timeouts/i)).toBeInTheDocument()
    expect(screen.getByText('8 tests in cluster')).toBeInTheDocument()
    // The category card (8 infrastructure failures) is in the Evidence disclosure.
    openEvidence()
    expect(screen.getAllByText(/8 failures/i).length).toBeGreaterThan(0)
  })

  it('renders workflow progress strip above the summary cards', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The detailed "workflow progress" strip + helper copy were folded into
    // the pipeline ribbon during the verdict-led redesign. Verify the
    // pipeline stage from MOCK_INTELLIGENCE renders so we still have a
    // signal that the timeline area exists. (P4: inside the Evidence disclosure.)
    openEvidence()
    expect(screen.getByRole('group', { name: /^Stage \d+: Summary, done$/ })).toBeInTheDocument()
  })

  it('renders CONDITIONAL_GO banner correctly', async () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        release_decision: {
          ...MOCK_INTELLIGENCE.release_decision,
          recommendation: 'CONDITIONAL_GO',
          risk_score: 35,
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Rendered label is "Conditional Go" (space, mixed case). Multiple
    // surfaces may render it (verdict pill + meter label) — getAllByText.
    expect(screen.getAllByText(/Conditional Go/i).length).toBeGreaterThan(0)
  })

  it('renders GO banner when risk is low', async () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        release_decision: {
          ...MOCK_INTELLIGENCE.release_decision,
          recommendation: 'GO',
          risk_score: 12,
          blocking_issues: [],
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Verdict label "Go" (plain) is shown (not "No-Go" or "Conditional Go").
    const goText = screen.getAllByText(/^Go$/).filter(
      (node) => node.textContent?.trim() === 'Go',
    )
    expect(goText.length).toBeGreaterThan(0)
  })

  // P4 (D2): `/intelligence` redirects to `/runs`, so the page goes there directly.
  it('renders no Hold / Override / Approve / Undo controls on the verdict', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderRunIntel()

    // The system-computed verdict and its reasoning still render.
    expect((await screen.findAllByText(/No-Go/i)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/High user impact with product bugs detected/i).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Hold release/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Approve with conditions/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Undo decision/i })).toBeNull()
  })

  it('renders no Hold control on a Conditional Go verdict either', async () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        release_decision: { ...MOCK_INTELLIGENCE.release_decision, recommendation: 'CONDITIONAL_GO' },
      },
    })
    renderRunIntel()
    expect((await screen.findAllByText(/Conditional Go/i)).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Hold release/i })).toBeNull()
    expect(screen.queryByText(/Held by you/i)).toBeNull()
  })

  it('ignores a decision left in localStorage by the removed controls: the gate is the system one', async () => {
    localStorage.setItem(
      'tl.runIntel.decision.run-abc',
      JSON.stringify({
        action: 'OVERRIDE',
        gate: 'GO',
        label: 'Override applied by you',
        at: '2026-05-16T10:00:00.000Z',
      }),
    )
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderRunIntel()

    expect((await screen.findAllByText(/No-Go/i)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Override applied by you/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Undo decision/i })).toBeNull()
  })

  // P2 item 7: the header said "N of N shown" while only three were rendered.
  it('"N of M shown" counts the failure blocks actually rendered', async () => {
    const base = MOCK_INTELLIGENCE.failure_clusters[0]
    const clusters = Array.from({ length: 5 }, (_, i) => ({
      ...base, id: `row-${i}`, cluster_id: `cl-${i}`, label: `Cluster number ${i}`,
    }))
    mockHooks({ intelligence: { ...MOCK_INTELLIGENCE, failure_clusters: clusters } })
    renderRunIntel()

    expect(await screen.findByText('3 of 5 failures shown')).toBeInTheDocument()
    expect(screen.getByText(/\+ 2 more failures not shown/)).toBeInTheDocument()
    expect(screen.getAllByText(/^Cluster number \d$/)).toHaveLength(3)
  })

  it('says "1 of 1" when every cluster is rendered', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderRunIntel()
    expect(await screen.findByText('1 of 1 failure shown')).toBeInTheDocument()
    expect(screen.queryByText(/more failures? not shown/)).toBeNull()
  })

  // P2 item 7: the cluster pill said "Product bug" whatever the data said.
  it('labels a cluster with its real criticality, never a fixed "Product bug"', async () => {
    const base = MOCK_INTELLIGENCE.failure_clusters[0]
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        failure_clusters: [
          { ...base, criticality_level: 'LOW' },
          { ...base, id: 'row-2', cluster_id: 'cl-2', label: 'Unscored', criticality_level: null },
        ],
      },
    })
    renderRunIntel()
    await screen.findByText('2 of 2 failures shown')
    const pills = screen.getAllByTestId('cluster-criticality')
    // One pill (the LOW one); the unscored cluster gets none.
    expect(pills).toHaveLength(1)
    expect(pills[0]).toHaveTextContent('Low criticality')
    // The old fixed pill text (the category card's real "Product Bug" row,
    // from category_breakdown, is a different string and stays).
    expect(screen.queryByText('Product bug')).toBeNull()
  })

  // P2 item 7: "Criticality scoring" and "Release risk assessment" were
  // always shown as passed checks.
  it('does not claim criticality scoring or risk assessment passed when the data does not show it', async () => {
    const base = MOCK_INTELLIGENCE.failure_clusters[0]
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        failure_clusters: [{ ...base, criticality_level: null }],
        // summary stage only; no release_risk stage and no composite risk.
      },
    })
    renderRunIntel()
    openEvidence()
    await screen.findByRole('heading', { name: 'AI confidence' })
    expect(screen.queryByText('Criticality scoring')).toBeNull()
    expect(screen.queryByText('Release risk assessment')).toBeNull()
  })

  it('shows those checks as passed when a cluster is scored and release_risk completed', async () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        pipeline_stages: [
          ...MOCK_INTELLIGENCE.pipeline_stages,
          {
            stage_name: 'release_risk',
            status: 'completed',
            started_at: '2026-03-30T12:01:10Z',
            completed_at: '2026-03-30T12:01:20Z',
            skipped_reason: null,
            execution_path: 'executed',
            fallback_used: false,
          },
        ],
      },
    })
    renderRunIntel()
    openEvidence()
    await screen.findByRole('heading', { name: 'AI confidence' })
    expect(screen.getByText('Criticality scoring')).toBeInTheDocument()
    expect(screen.getByText('Release risk assessment')).toBeInTheDocument()
  })

  // P2 item 1: the pipeline stage cell was a <button> whose onClick did nothing.
  it('renders pipeline stages as display-only cells, not dead buttons', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderRunIntel()
    openEvidence()
    const stage = await screen.findByRole('group', { name: /^Stage 8: Summary, done$/ })
    expect(stage.tagName).not.toBe('BUTTON')
    expect(screen.queryByRole('button', { name: /^Stage \d+:/ })).toBeNull()
  })

  // -- US-15.1 AI trust chrome --------------------------------------------
  it('renders the shared trust chrome on the AI confidence card', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )
    openEvidence()
    await screen.findByRole('heading', { name: 'AI confidence' })
    expect(screen.getAllByTestId('ai-suggested-badge').length).toBeGreaterThan(0)
    // A confidence never renders without its calibration basis.
    expect(screen.getByTestId('ai-basis-chip')).toHaveTextContent('estimated')
    expect(screen.getByTestId('ai-confidence')).toHaveTextContent('88%')
  })

  it('shows a fallback notice when the pipeline fell back off the LLM', async () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        provenance: {
          schema_version: 2,
          fallback_used: true,
          generated_by: 'rules',
          tools_used_count: 0,
          generated_at: null,
          confidence: null,
          confidence_reason: null,
          evidence_count: 0,
          sources_used: [],
          deterministic_checks_used: [],
        },
      },
    })
    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )
    openEvidence()
    await screen.findByRole('heading', { name: 'AI confidence' })
    expect(screen.getByTestId('ai-fallback-notice')).toHaveTextContent(
      /The LLM was unavailable.*rules engine/i,
    )
  })
})

// ── UX redesign P4: the body the Run page's Analysis tab hosts ──────────────
describe('RunIntelligenceBody — the page template (P4)', () => {
  beforeEach(() => {
    mockCompleteStep.mockReset()
    mockCompleteStep.mockResolvedValue(undefined)
    mockProjectState.activeProjectId = 'proj-1'
  })

  function renderBody(afterPrimary?: ReactNode) {
    return render(
      <MemoryRouter initialEntries={['/runs/run-abc?tab=analysis']}>
        <Routes>
          <Route path="/runs/:runId" element={<RunIntelligenceBody runId="run-abc" afterPrimary={afterPrimary} />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('orders verdict banner -> What failed (the primary content) -> host section -> the two collapsed disclosures', () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    const { container } = renderBody(<section aria-label="Host section">host</section>)
    const banner = container.querySelector('[data-status-banner]') as HTMLElement
    const primaries = container.querySelectorAll('[data-primary]')
    expect(primaries).toHaveLength(1)
    const primary = primaries[0] as HTMLElement
    expect(within(primary).getByText('What failed')).toBeInTheDocument()
    const host = screen.getByRole('region', { name: 'Host section' })
    const disclosures = Array.from(container.querySelectorAll('[data-disclosure]'))
    expect(disclosures.map((d) => d.querySelector('button')?.textContent)).toEqual([
      expect.stringMatching(/^How this score is computed/),
      expect.stringMatching(/^Evidence behind the verdict/),
    ])
    const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(banner, primary)).toBe(true)
    expect(follows(primary, host)).toBe(true)
    for (const d of disclosures) {
      expect(follows(host, d)).toBe(true)
      expect(d).toHaveAttribute('data-open', 'false')
    }
    // No tab bar of its own (the persona tabs were removed).
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  // Browser E2E pass (2026-10-08): with no clusters, any run with failures
  // read "per-test detail missing ... the per-test rows aren't available" --
  // every run not yet analysed, one with all 22 rows on its Tests tab.
  it('failures with their rows but no analysis: "not analysed yet", linking to the failed tests', () => {
    mockHooks({ intelligence: { ...MOCK_INTELLIGENCE, failure_clusters: [] } })
    renderBody()
    const primary = screen.getByRole('region', { name: 'What failed' })
    expect(primary).toHaveTextContent('15 failures · not analysed yet')
    expect(primary).not.toHaveTextContent(/per-test detail missing|aren't available/)
    expect(within(primary).getByRole('link', { name: 'Open the failed tests →' }))
      .toHaveAttribute('href', '/runs/run-abc?tab=tests&status=FAILED')
  })

  it('failures with no rows at all: the data gap, as before', () => {
    mockHooks({ intelligence: { ...MOCK_INTELLIGENCE, failure_clusters: [], affected_suites: [] } })
    renderBody()
    const primary = screen.getByRole('region', { name: 'What failed' })
    expect(primary).toHaveTextContent('15 failures · per-test detail missing')
  })

  it('no failures: no failures', () => {
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        failure_clusters: [],
        affected_suites: [],
        run: { ...MOCK_INTELLIGENCE.run, failed_tests: 0, passed_tests: 200 },
      },
    })
    renderBody()
    expect(screen.getByRole('region', { name: 'What failed' })).toHaveTextContent('No failures in this run.')
  })

  it('draws nothing of the score or the evidence until its disclosure is opened', () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderBody()
    expect(screen.queryByText('Composite risk score')).toBeNull()
    expect(screen.queryByRole('group', { name: /^Stage \d+:/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'AI confidence' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Failure category' })).toBeNull()
    openScore()
    expect(screen.getByText('Composite risk score')).toBeInTheDocument()
    openEvidence()
    expect(screen.getByRole('heading', { name: 'AI confidence' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Test outcome' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recommended actions' })).toBeInTheDocument()
  })

  it('states the verdict in the banner: gate and action, risk, blockers, confidence, and the first blocker by name', () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderBody()
    const verdict = screen.getByRole('region', { name: 'Release verdict' })
    expect(within(verdict).getByText('No-Go')).toBeInTheDocument()
    expect(within(verdict).getByText('ship blocked')).toBeInTheDocument()
    const facts = Array.from(verdict.querySelectorAll('[data-banner-fact]')).map((f) => f.textContent)
    expect(facts).toEqual(['Risk 68/100', 'Blocking issues 1', 'AI confidence 88%'])
    expect(within(verdict).getByText(/Resolve DB connection pool exhaustion/)).toBeInTheDocument()
    expect(within(verdict).getByText('High user impact with product bugs detected.')).toBeInTheDocument()
  })

  it('opens the decision trail from the banner', () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderBody()
    fireEvent.click(within(screen.getByRole('region', { name: 'Release verdict' })).getByRole('button', { name: /Decision trail/ }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('shows only the failure categories the data names: no "None detected this run" placeholder rows', () => {
    mockHooks({ intelligence: { ...MOCK_INTELLIGENCE, category_breakdown: { INFRASTRUCTURE: 8 } as unknown as typeof MOCK_INTELLIGENCE.category_breakdown } })
    renderBody()
    openEvidence()
    const card = screen.getByRole('heading', { name: 'Failure category' }).closest('div.overflow-hidden') as HTMLElement
    expect(within(card).getByText('Infrastructure')).toBeInTheDocument()
    expect(within(card).queryByText('None detected this run')).toBeNull()
    expect(within(card).queryByText('Flaky')).toBeNull()
    expect(within(card).queryByText('Test Data')).toBeNull()
  })

  it('keeps the host section when the analysis fails to load', () => {
    mockHooks({ isError: true })
    renderBody(<section aria-label="Host section">host</section>)
    expect(screen.getByText('Failed to load Run Intelligence')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Host section' })).toBeInTheDocument()
  })
})

describe('RunDecisionReport — the Evidence tab\'s decision report (P4)', () => {
  it('renders the verified report and writes a chosen version to ?report_version= without dropping ?tab=', () => {
    const intelligence = structuredClone(MOCK_INTELLIGENCE) as typeof MOCK_INTELLIGENCE & {
      structured_summary: typeof MOCK_INTELLIGENCE.structured_summary & Record<string, unknown>
    }
    intelligence.structured_summary.decision_intelligence = {
      schema_version: 1, status: 'complete', generated_at: '2026-08-11T19:00:00Z',
      metrics: {}, failure_clusters: [], deep_findings: {}, flaky_findings: [], test_health_findings: [],
      release_decision: { recommendation: 'NO_GO', risk_score: 80, reasoning: 'Verified.' },
      quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
      source_stages: ['decision_report'], evidence_bundle_sha256: 'a'.repeat(64),
      verification: { status: 'passed', checks: [], repairs: [] },
    }
    intelligence.structured_summary.decision_report_verification = { status: 'passed', checks: [] }
    intelligence.structured_summary.latest_decision_attempt = {
      pipeline_run_id: 'pipeline-1', status: 'published', verification_status: 'passed', at: '2026-08-11T19:01:00Z',
    }
    intelligence.structured_summary.decision_report = { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' }
    mockHooks({ intelligence })
    mockUseDecisionReportVersions.mockReturnValue({
      versions: [
        { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' },
        { report_id: 'report-1', report_version: 1, status: 'published', generated_at: '2026-08-11T19:00:00Z' },
      ],
      isLoading: false,
      isError: false,
    })
    let search = ''
    function Probe() {
      const current = useLocation().search
      useEffect(() => {
        search = current
      })
      return null
    }
    render(
      <MemoryRouter initialEntries={['/runs/run-abc?tab=evidence']}>
        <Routes>
          <Route path="/runs/:runId" element={<><RunDecisionReport runId="run-abc" /><Probe /></>} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Decision intelligence' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Decision report version'), { target: { value: '1' } })
    const params = new URLSearchParams(search)
    expect(params.get('report_version')).toBe('1')
    expect(params.get('tab')).toBe('evidence')
  })

  it('renders nothing for a run with no decision report envelope', () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    const { container } = render(
      <MemoryRouter>
        <RunDecisionReport runId="run-abc" />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})


describe('RunIntelligencePage — File defect is QA engineer and above (E2E 2026-10-10)', () => {
  // ``POST /deep-investigate/{run}/clusters/{id}/promote`` creates an OPEN
  // defect on the release: QA_ENGINEER, like ``/analytics/defects``.
  it.each([
    ['VIEWER', false],
    ['TESTER', false],
    ['QA_ENGINEER', true],
  ])('as %s the failure block offers File defect: %s', (role, offered) => {
    useAuthStore.setState({ user: { id: 'u', role } as never })
    mockHooks({ intelligence: MOCK_INTELLIGENCE })
    renderRunIntel()
    expect(screen.getByText(/DB Timeouts/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /File defect/i }) !== null).toBe(offered)
  })
})
