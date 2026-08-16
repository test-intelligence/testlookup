import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ReleaseCouncilDecision } from '@/services/releaseCouncilService'

import ReleaseGatePage from './ReleaseGatePage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useReleaseCouncil', () => ({
  useReleaseCouncil: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
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

describe('ReleaseGatePage', () => {
  it('renders the release workflow strip and decision summary', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: {
        run_id: 'run-1',
        recommendation: 'CONDITIONAL_GO',
        risk_score: 42,
        composite_risk: 41,
        dimension_scores: [],
        blocking_issues: ['Need one more smoke pass'],
        conditions_for_go: ['Approve smoke run'],
        reasoning: 'Policy accepted with conditions',
        score_model_version: 2,
        input_snapshot: null,
        cluster_insights: [],
        baseline_diff: null,
        open_defects_by_component: [],
        human_override: null,
        overridden_by: null,
        original_recommendation: null,
        original_risk_score: null,
        override_audit: [],
        pass_rate: 86.2,
        build_number: '42',
        policy_id: 'policy-1',
        policy_version: 3,
        policy_level: 'project',
        rule_evaluations: [],
      },
      isLoading: false,
      isError: false,
      refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Release decision flow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Policy Evaluation/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Release Decision/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/CONDITIONAL GO/i).length).toBeGreaterThan(0)
  })

  it('stays stable when the release council transitions from loading to loaded', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    const state: {
      council: ReleaseCouncilDecision | null
      isLoading: boolean
      isError: boolean
      refresh: () => void
    } = {
      council: null,
      isLoading: true,
      isError: false,
      refresh: vi.fn(),
    }

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockImplementation(() => state)
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(document.querySelector('.animate-spin') ?? screen.queryByText(/loading/i)).toBeTruthy()

    state.council = {
      run_id: 'run-1',
      recommendation: 'GO',
      risk_score: 12,
      composite_risk: 12,
      dimension_scores: [],
      blocking_issues: [],
      conditions_for_go: [],
      reasoning: 'Low risk and healthy baseline.',
      score_model_version: 2,
      input_snapshot: null,
      cluster_insights: [],
      baseline_diff: null,
      open_defects_by_component: [],
      human_override: null,
      overridden_by: null,
      original_recommendation: null,
      original_risk_score: null,
      override_audit: [],
      pass_rate: 98.2,
      build_number: '43',
      policy_id: 'policy-1',
      policy_version: 3,
      policy_level: 'project',
      rule_evaluations: [],
    }
    state.isLoading = false

    rerender(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Release decision flow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^GO$/i).length).toBeGreaterThan(0)
  })

  it('returns to the release gate overview when the project changes', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: {
        run_id: 'run-1',
        recommendation: 'NO_GO',
        risk_score: 78,
        composite_risk: 78,
        dimension_scores: [],
        blocking_issues: ['Database saturation'],
        conditions_for_go: [],
        reasoning: 'Release blocked by critical risk.',
        score_model_version: 2,
        input_snapshot: null,
        cluster_insights: [],
        baseline_diff: null,
        open_defects_by_component: [],
        human_override: null,
        overridden_by: null,
        original_recommendation: null,
        original_risk_score: null,
        override_audit: [],
        pass_rate: 72.4,
        build_number: '42',
        policy_id: 'policy-1',
        policy_version: 3,
        policy_level: 'project',
        rule_evaluations: [],
      },
      isLoading: false,
      isError: false,
      refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/NO GO/i)).toBeInTheDocument()

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/No run selected/i)).toBeInTheDocument()
  })
})

// ── The gauge vs the dimension breakdown ───────────────────────────────────
//
// Measured live 2026-08-16 on build ui-6 (pass rate 44.4%):
//
//     RiskGauge              60
//     Risk Dimension Breakdown   17/100   (weighted contributions sum to 16.67)
//
// Both styled as a risk out of 100, on one page, with nothing connecting them.
// The verdict is right — a pass-rate hard floor raises the composite to 60
// without touching any dimension — but the page never said so. The backend has
// published `input_snapshot.verdict_driver = "pass_rate_floor"` all along and
// no code read it: the value-nothing-consumes pattern this repo keeps finding.

function flooredDecision(
  overrides: Partial<ReleaseCouncilDecision> = {},
): ReleaseCouncilDecision {
  return {
    run_id: 'run-1',
    recommendation: 'NO_GO',
    // Floored: not the weighted total of the dimensions below.
    risk_score: 60,
    composite_risk: 60,
    dimension_scores: [
      { name: 'reproducibility', label: 'Reproducibility', score: 55.6, weight: 0.15, contribution: 8.34 },
      { name: 'blast_radius', label: 'Blast Radius', score: 22.2, weight: 0.15, contribution: 3.33 },
      { name: 'diagnosis_confidence', label: 'Diagnosis Confidence', score: 100, weight: 0.05, contribution: 5.0 },
    ],
    blocking_issues: [],
    conditions_for_go: [],
    reasoning: 'Quick-look decision derived from this run aggregates',
    score_model_version: 1,
    input_snapshot: { verdict_driver: 'pass_rate_floor', no_go_floor_pct: 63.0 },
    cluster_insights: [],
    baseline_diff: null,
    open_defects_by_component: [],
    human_override: null,
    overridden_by: null,
    original_recommendation: null,
    original_risk_score: null,
    override_audit: [],
    pass_rate: 44.4,
    build_number: 'ui-6',
    policy_id: null,
    policy_version: null,
    policy_level: 'hardcoded',
    rule_evaluations: [],
    ...overrides,
  } as ReleaseCouncilDecision
}

async function renderGate(decision: ReleaseCouncilDecision) {
  const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
  const { useRuns } = await import('@/hooks/useRuns')
  ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
    council: decision, isLoading: false, isError: false, refresh: vi.fn(),
  })
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
  render(
    <MemoryRouter initialEntries={['/release-gate/run-1']}>
      <Routes>
        <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ReleaseGatePage — the risk score explains itself', () => {
  it('discloses that the score came from the pass-rate floor', async () => {
    await renderGate(flooredDecision())
    expect(await screen.findByText(/raised to the NO-GO floor/i)).toBeInTheDocument()
  })

  it('states the floor percentage it actually applied', async () => {
    await renderGate(flooredDecision())
    expect(await screen.findByText(/63%/)).toBeInTheDocument()
  })

  it('stays silent when the score really is the dimension total', async () => {
    // The disclosure must not become permanent furniture — a reader who sees it
    // on every verdict stops reading it.
    await renderGate(flooredDecision({
      input_snapshot: { verdict_driver: 'composite_risk' },
      risk_score: 17,
      composite_risk: 17,
      recommendation: 'GO',
      pass_rate: 96.0,
    }))
    expect(await screen.findByText(/ui-6/)).toBeInTheDocument()
    expect(screen.queryByText(/raised to the NO-GO floor/i)).not.toBeInTheDocument()
  })

  it('does not present the dimension total as the verdict risk score', async () => {
    await renderGate(flooredDecision())
    // The breakdown header used to read a bare "17/100" beside a gauge of 60.
    expect(await screen.findByText(/17\/100 weighted/)).toBeInTheDocument()
  })
})
