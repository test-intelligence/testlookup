import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

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

    const state = {
      council: null as any,
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
