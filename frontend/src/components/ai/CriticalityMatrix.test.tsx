/**
 * GATE-05: "Why this score?" shows the weights the verdict applied.
 *
 * The stored dimension rows carry the default weights (user_impact 0.25)
 * whatever the project's release-gate policy says; the verdict sums the
 * policy's (here 0.40). The matrix asks the scoring model for the run's
 * project and, when that model names a policy, shows and sums its weights and
 * says which policy they came from.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { DimensionScore, ScoringModel } from '@/services/runIntelligenceService'
import CriticalityMatrix from './CriticalityMatrix'

const { mockUseScoringModel } = vi.hoisted(() => ({ mockUseScoringModel: vi.fn() }))

vi.mock('@/hooks/useRunIntelligence', () => ({ useScoringModel: mockUseScoringModel }))

const ROWS: DimensionScore[] = [
  { name: 'user_impact', label: 'User Impact', score: 50, weight: 0.25, contribution: 12.5 },
  { name: 'blast_radius', label: 'Blast Radius', score: 20, weight: 0.15, contribution: 3 },
]

const DESCRIPTIONS = { user_impact: 'Proportion of PRODUCT_BUG failures.', blast_radius: 'Failure breadth.' }

function model(overrides: Partial<ScoringModel> = {}): ScoringModel {
  return {
    version: 1,
    go_threshold: 15,
    no_go_threshold: 50,
    dimensions: [
      { name: 'user_impact', weight: 0.4, description: DESCRIPTIONS.user_impact },
      { name: 'blast_radius', weight: 0.15, description: DESCRIPTIONS.blast_radius },
    ],
    policy: { policy_id: 'p-1', name: 'Checkout strict', version: 2, level: 'project' },
    ...overrides,
  }
}

function useModel(m: ScoringModel | undefined) {
  mockUseScoringModel.mockReturnValue({
    scoringModel: m,
    isLoading: false,
    isError: false,
    getDescription: (name: string) => m?.dimensions.find((d) => d.name === name)?.description ?? '',
  })
}

function renderOnRunRoute(ui: React.ReactElement, runId = 'run-1') {
  return render(
    <MemoryRouter initialEntries={[`/release-gate/${runId}`]}>
      <Routes>
        <Route path="/release-gate/:runId" element={ui} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CriticalityMatrix weights (GATE-05)', () => {
  beforeEach(() => mockUseScoringModel.mockReset())

  it('shows and sums the policy weights, and names the policy', () => {
    useModel(model())
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} defaultExpanded />)

    expect(screen.getByTestId('criticality-weight-source')).toHaveTextContent('Weights from policy Checkout strict v2')
    expect(screen.getByTestId('criticality-weight-user_impact')).toHaveTextContent('×40%')
    expect(screen.getByTestId('criticality-weight-blast_radius')).toHaveTextContent('×15%')
    // 50 × 0.40 + 20 × 0.15 = 23, the verdict's composite (the stored rows sum to 15.5).
    expect(screen.getAllByText('23/100')).not.toHaveLength(0)
    expect(screen.getByText('23/100 weighted')).toBeInTheDocument()

    fireEvent.click(screen.getAllByTitle('Why this score?')[0])
    expect(screen.getByText(DESCRIPTIONS.user_impact)).toBeInTheDocument()
    expect(screen.getByTestId('criticality-why-weight-user_impact')).toHaveTextContent(
      'Weight 0.40 · Weights from policy Checkout strict v2',
    )
  })

  it('asks for the route run when no project or run is passed', () => {
    useModel(model())
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} />, 'run-42')
    expect(mockUseScoringModel).toHaveBeenCalledWith({ runId: 'run-42' })
  })

  it('prefers an explicit project, then an explicit run', () => {
    useModel(model())
    const { unmount } = renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} projectId="proj-9" runId="run-7" />)
    expect(mockUseScoringModel).toHaveBeenLastCalledWith({ projectId: 'proj-9' })
    unmount()
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} runId="run-7" />)
    expect(mockUseScoringModel).toHaveBeenLastCalledWith({ runId: 'run-7' })
  })

  it('says when no policy is active and the defaults decided', () => {
    useModel(model({
      dimensions: [
        { name: 'user_impact', weight: 0.25, description: DESCRIPTIONS.user_impact },
        { name: 'blast_radius', weight: 0.15, description: DESCRIPTIONS.blast_radius },
      ],
      policy: { policy_id: null, name: null, version: null, level: 'hardcoded' },
    }))
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} defaultExpanded />)
    expect(screen.getByTestId('criticality-weight-source')).toHaveTextContent(
      'Weights: deployment defaults (no active release-gate policy)',
    )
    expect(screen.getByTestId('criticality-weight-user_impact')).toHaveTextContent('×25%')
  })

  it('names the system default policy as such', () => {
    useModel(model({ policy: { policy_id: 'p-0', name: 'Org default', version: 5, level: 'system' } }))
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} defaultExpanded />)
    expect(screen.getByTestId('criticality-weight-source')).toHaveTextContent('Weights from the default policy Org default v5')
  })

  it('keeps the stored weights and claims no policy while the model is unscoped or loading', () => {
    useModel(undefined)
    renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} defaultExpanded />)
    expect(screen.queryByTestId('criticality-weight-source')).toBeNull()
    expect(screen.getByTestId('criticality-weight-user_impact')).toHaveTextContent('×25%')
    expect(screen.getByText('16/100 weighted')).toBeInTheDocument()
  })

  it('renders a policy name as text, never markup', () => {
    useModel(model({ policy: { policy_id: 'p-1', name: '<img src=x onerror=alert(1)>', version: 2, level: 'project' } }))
    const { container } = renderOnRunRoute(<CriticalityMatrix dimensionScores={ROWS} defaultExpanded />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('criticality-weight-source')).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})
