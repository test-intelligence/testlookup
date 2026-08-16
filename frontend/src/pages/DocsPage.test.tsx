/**
 * In-app documentation (B-2).
 *
 * These tests pin two things:
 *
 *  1. the page renders every section the owner asked for, and is reachable;
 *  2. the NUMBERS it states match the implementation.
 *
 * (2) matters more than it looks. Documentation that drifts from the code is
 * the same defect class this codebase keeps producing — a value published to a
 * reader that nothing in the system actually produces. A doc page claiming a
 * threshold the engine no longer uses is a lie with a nice font. The
 * backend-side companion (`backend/tests/regression/test_docs_match_the_engine.py`)
 * asserts these same constants from the Python side, so the pair fails if
 * either half moves.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import DocsPage from './DocsPage'

function renderDocs() {
  return render(
    <MemoryRouter>
      <DocsPage />
    </MemoryRouter>,
  )
}

const SECTIONS = [
  'Start a new project',
  'Using AI reports',
  'How the AI agents work',
  'How flaky tests are determined',
  'How GO / NO-GO is decided',
]

describe('DocsPage', () => {
  it('offers every section the documentation is required to cover', () => {
    renderDocs()
    for (const label of SECTIONS) {
      expect(screen.getByRole('button', { name: new RegExp(label, 'i') })).toBeInTheDocument()
    }
  })

  it('opens on the new-project walkthrough', () => {
    renderDocs()
    expect(screen.getByText(/Setting up a new project/i)).toBeInTheDocument()
  })

  it.each(SECTIONS)('renders content for %s', (label) => {
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(label, 'i') }))
    // Each section renders its own heading region.
    expect(screen.getAllByRole('heading').length).toBeGreaterThan(0)
  })

  // ── The numbers must match the engine ────────────────────────────────────

  it('states the flakiness weights the scorer actually uses', () => {
    // flaky_score_service.DEFAULT_WEIGHTS
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /flaky/i }))
    for (const weight of ['0.45', '0.25', '0.20', '0.10']) {
      expect(screen.getByText(weight)).toBeInTheDocument()
    }
  })

  it('states the observation floor and confidence bands correctly', () => {
    // MIN_OBSERVATIONS = 5, LOW_CONFIDENCE_MAX = 9, MEDIUM_CONFIDENCE_MAX = 19
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /flaky/i }))
    expect(screen.getByText(/Fewer than 5/i)).toBeInTheDocument()
    expect(screen.getByText(/5 – 9/)).toBeInTheDocument()
    expect(screen.getByText(/10 – 19/)).toBeInTheDocument()
    expect(screen.getByText(/20 or more/i)).toBeInTheDocument()
  })

  it('does not present a missing flakiness score as zero', () => {
    // The honest-state contract: below MIN_OBSERVATIONS the score is None, not 0.
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /flaky/i }))
    // Stated twice on purpose — in the confidence table and in the callout —
    // so getAllByText, not getByText.
    expect(screen.getAllByText(/insufficient/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/would read as evidence of/i)).toBeInTheDocument()
  })

  it('lists all seven risk dimensions', () => {
    // criticality_service._weights()
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /GO \/ NO-GO/i }))
    for (const dim of [
      /User impact/i, /Environment sensitivity/i, /Reproducibility/i,
      /Regression likelihood/i, /Historical recurrence/i, /Blast radius/i,
      /Diagnosis confidence/i,
    ]) {
      expect(screen.getByText(dim)).toBeInTheDocument()
    }
  })

  it('states the decision rules in the order the engine evaluates them', () => {
    // score_to_recommendation: NO_GO rules first so nothing below softens them.
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /GO \/ NO-GO/i }))
    expect(screen.getByText(/70% of your configured bar/i)).toBeInTheDocument()
    const body = document.body.textContent ?? ''
    const hardFloor = body.indexOf('70% of your configured bar')
    const otherwise = body.indexOf('Otherwise')
    expect(hardFloor).toBeGreaterThan(-1)
    expect(otherwise).toBeGreaterThan(hardFloor)
  })

  it('says bands can only tighten the verdict, never loosen it', () => {
    // release_council_service._apply_band_floor is fail-closed.
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /GO \/ NO-GO/i }))
    expect(screen.getByText(/never unblock/i)).toBeInTheDocument()
  })

  it('tells the reader AI output is advisory, not authoritative', () => {
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /AI reports/i }))
    expect(screen.getByText(/advisory, not authoritative/i)).toBeInTheDocument()
  })

  it('explains that a summary may be deterministic rather than model-written', () => {
    // The #606 contract: fallback_used says so, and empty is never "all clear".
    renderDocs()
    fireEvent.click(screen.getByRole('button', { name: /AI reports/i }))
    expect(screen.getByText(/fallback_used/)).toBeInTheDocument()
  })
})
