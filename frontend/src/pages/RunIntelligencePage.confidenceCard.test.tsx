import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { AIConfidenceCard } from './RunIntelligencePage'

// Batch 6 review (D-13): `generated_by` is "ai_pipeline" or "deterministic", not an engine id, so
// passing it as the engine made every recommendation read a bare "Suggestion".
function renderCard(props: { llmUsed: boolean; generatedBy?: string | null; fallbackUsed?: boolean }) {
  render(
    <MemoryRouter>
      <AIConfidenceCard
        confidencePct={80}
        evidenceCount={3}
        toolCount={2}
        hasBaseline
        criticalityScored
        riskAssessed
        fallbackUsed={props.fallbackUsed ?? false}
        llmUsed={props.llmUsed}
        generatedBy={props.generatedBy}
      />
    </MemoryRouter>,
  )
}

describe('AIConfidenceCard: the recommendation names what produced it (D-13)', () => {
  it('a model-written recommendation keeps "AI-suggested"', () => {
    renderCard({ llmUsed: true, generatedBy: 'ai_pipeline' })
    expect(screen.getByText('AI-suggested')).toBeInTheDocument()
  })

  it.each([
    { llmUsed: true, generatedBy: 'deterministic' },
    { llmUsed: false, generatedBy: 'ai_pipeline', fallbackUsed: true },
  ])('a rules recommendation reads "Rule-based suggestion" (%o)', (props) => {
    renderCard(props)
    expect(screen.getByText('Rule-based suggestion')).toBeInTheDocument()
    expect(screen.queryByText('AI-suggested')).toBeNull()
  })
})
