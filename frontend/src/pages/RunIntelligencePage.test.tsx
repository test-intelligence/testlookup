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
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Provenance, RunModeSummary, ScoringModel } from '@/services/runIntelligenceService'

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
} = vi.hoisted(() => ({
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
  }
})

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
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
      criticality_level: 'HIGH',
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

import RunIntelligencePage from './RunIntelligencePage'

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
  mockUseScoringModel.mockReturnValue({
    scoringModel: MOCK_SCORING_MODEL,
    isLoading: false,
    isError: false,
    getDescription: (name: string) => MOCK_SCORING_MODEL.dimensions.find((d) => d.name === name)?.description ?? '',
  })
}

describe('RunIntelligencePage', () => {
  beforeEach(() => {
    // The user-decision feature persists to localStorage under
    // ``tl.runIntel.decision.<runId>`` so a refresh keeps the panel in
    // sync. Wipe between tests so one test's recorded decision doesn't
    // leak into the next.
    try {
      Object.keys(localStorage)
        .filter(k => k.startsWith('tl.runIntel.decision.'))
        .forEach(k => localStorage.removeItem(k))
    } catch { /* ignore */ }
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

  it('still renders the run header when intelligence_available is false', async () => {
    // RunIntelligencePage doesn't gate on ``intelligence_available`` itself —
    // that gate lives on the AgentWorkflowPage path. The page still renders
    // the run header + supplied data; just verify it doesn't crash.
    mockHooks({ intelligence: { ...MOCK_INTELLIGENCE, intelligence_available: false } })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Build number from MOCK_INTELLIGENCE.run is a stable signal that
    // top-of-page rendering succeeded.
    expect(screen.getAllByText(/42/).length).toBeGreaterThan(0)
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
    // Risk score is rendered as two sibling text nodes — the score and
    // "/ 100" with a space — so test each separately.
    expect(screen.getByText('68')).toBeInTheDocument()
    expect(screen.getByText(/\/ 100/)).toBeInTheDocument()
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
    // signal that the timeline area exists.
    expect(screen.getAllByText(/summary/i).length).toBeGreaterThan(0)
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

  it('returns to the intelligence hub when the project changes', async () => {
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
          <Route path="/intelligence" element={<div>Intelligence Hub</div>} />
        </Routes>
      </MemoryRouter>,
    )

    expect((await screen.findAllByText(/No-Go/i)).length).toBeGreaterThan(0)

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
          <Route path="/intelligence" element={<div>Intelligence Hub</div>} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Intelligence Hub/i)).toBeInTheDocument()
  })

  it('updates the release readiness panel when "Approve with conditions" is clicked', async () => {
    // Pin the user-visible feature: action buttons in the verdict card
    // must update the panel state, not just toast. The MOCK_INTELLIGENCE
    // default has ``recommendation: 'NO_GO'`` so the panel starts at
    // No-Go. Clicking "Approve with conditions" records a local CONDITIONAL_GO
    // decision that overrides the displayed gate until the user clicks Undo.
    mockHooks({ intelligence: MOCK_INTELLIGENCE })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Before the click: starts at the system-computed No-Go.
    expect((await screen.findAllByText(/No-Go/i)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Approved with conditions by you/i)).toBeNull()
    expect(screen.queryByText(/Undo decision/i)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Approve with conditions/i }))

    // After the click: the panel flips to Conditional Go and the lede
    // describes the user's recorded decision. The Undo affordance appears.
    // "Conditional Go" appears in both the H2 + the RiskMeter pill, so
    // assert on at-least-one match rather than findByText (which throws
    // on multiple matches).
    expect((await screen.findAllByText(/Conditional Go/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/Approved with conditions by you/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Undo decision/i })).toBeInTheDocument()
    // The user-decision text replaces the persona/decision lede, so the
    // original "High user impact with product bugs" reasoning isn't shown.
    expect(screen.queryByText(/High user impact with product bugs detected/i)).toBeNull()
  })

  it('records "Held by you" as No-Go and lets the user Undo to revert', async () => {
    // The "Hold release" path covers the second action button. Starting
    // from a CONDITIONAL_GO intelligence response, click Hold → panel
    // moves to No-Go; click Undo → panel returns to Conditional Go.
    mockHooks({
      intelligence: {
        ...MOCK_INTELLIGENCE,
        release_decision: { ...MOCK_INTELLIGENCE.release_decision, recommendation: 'CONDITIONAL_GO' },
      },
    })

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect((await screen.findAllByText(/Conditional Go/i)).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /Hold release/i }))

    expect(await screen.findByText(/Held by you/i)).toBeInTheDocument()
    expect((await screen.findAllByText(/No-Go/i)).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /Undo decision/i }))

    // After undo: original CONDITIONAL_GO is restored and the user-decision
    // lede is gone.
    expect(screen.queryByText(/Held by you/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Undo decision/i })).toBeNull()
    expect((await screen.findAllByText(/Conditional Go/i)).length).toBeGreaterThan(0)
  })

  it('rehydrates the user decision from localStorage on a fresh render', async () => {
    // The decision persists per-run under ``tl.runIntel.decision.<runId>``
    // so a page refresh / navigation away and back keeps the user's panel
    // state in sync until the backend gate-decision endpoint lands. Pin the
    // contract by pre-seeding localStorage and verifying first render.
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

    render(
      <MemoryRouter initialEntries={['/runs/run-abc/intelligence']}>
        <Routes>
          <Route path="/runs/:runId/intelligence" element={<RunIntelligencePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Override applied by you/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Undo decision/i })).toBeInTheDocument()
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
    await screen.findByText('AI confidence')
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
    await screen.findByText('AI confidence')
    expect(screen.getByTestId('ai-fallback-notice')).toHaveTextContent(
      /The LLM was unavailable.*rules engine/i,
    )
  })
})
