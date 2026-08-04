/**
 * Contract tests for the shared AI trust chrome (US-15.1).
 *
 * The honesty invariants, in order of how badly they hurt when broken:
 *  - the "AI-suggested" badge renders ALWAYS;
 *  - a confidence renders ONLY when one is supplied, and never without its
 *    calibration basis;
 *  - an absent/unknown basis reads "estimated", never "calibrated";
 *  - a fallback notice appears whenever `fallbackFrom` is set;
 *  - the low-confidence state is explicit;
 *  - confirm/correct render only with an analysisId to attach feedback to.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AISuggestion, { basisLabel, engineLabel, normalizeProvenance } from './AISuggestion'

describe('AISuggestion', () => {
  it('always renders the AI-suggested badge', () => {
    render(<AISuggestion />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('AI-suggested')
  })

  it('renders no confidence when none is supplied', () => {
    render(<AISuggestion>body</AISuggestion>)
    expect(screen.queryByTestId('ai-confidence')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ai-basis-chip')).not.toBeInTheDocument()
  })

  it('renders the confidence with its basis chip when supplied', () => {
    render(<AISuggestion confidence={82} confidenceBasis="heuristic_estimate" />)
    expect(screen.getByTestId('ai-confidence')).toHaveTextContent('82%')
    expect(screen.getByTestId('ai-basis-chip')).toHaveTextContent('estimated')
  })

  it('never reads as calibrated when the basis is unknown', () => {
    render(<AISuggestion confidence={91} />)
    expect(screen.getByTestId('ai-basis-chip')).toHaveTextContent('estimated')
    expect(screen.queryByText('calibrated')).not.toBeInTheDocument()
  })

  it('renders the routing provenance line when an engine is known', () => {
    render(
      <AISuggestion provenance={{ modeUsed: 'llm', llmProvider: 'ollama', llmModel: 'qwen2.5:7b' }} />,
    )
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('ollama · qwen2.5:7b')
  })

  it('renders no provenance line when nothing is known', () => {
    render(<AISuggestion provenance={null} />)
    expect(screen.queryByTestId('ai-provenance')).not.toBeInTheDocument()
  })

  it('renders a fallback notice when fallbackFrom is set', () => {
    render(
      <AISuggestion
        provenance={{ modeUsed: 'rules', fallbackFrom: 'llm', fallbackReason: 'Ollama timed out.' }}
      />,
    )
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent('The LLM was unavailable')
    expect(notice).toHaveTextContent('rules engine')
    expect(notice).toHaveTextContent('Ollama timed out.')
  })

  it('renders the low-confidence state only when flagged', () => {
    const { rerender } = render(<AISuggestion confidence={30} />)
    expect(screen.queryByTestId('ai-low-confidence')).not.toBeInTheDocument()
    rerender(<AISuggestion confidence={30} lowConfidence />)
    expect(screen.getByTestId('ai-low-confidence')).toHaveTextContent('needs human review')
  })

  it('renders evidence links, and plain text when no href exists', () => {
    render(
      <AISuggestion
        evidence={[
          { label: 'splunk', detail: 'ConnectionTimeoutException' },
          { label: 'commit abc1234', href: 'https://example.test/c/abc1234', external: true },
        ]}
      />,
    )
    expect(screen.getByTestId('ai-evidence')).toHaveTextContent('ConnectionTimeoutException')
    expect(screen.getByRole('link', { name: /commit abc1234/ })).toHaveAttribute(
      'href',
      'https://example.test/c/abc1234',
    )
    // No dead link for the href-less entry.
    expect(screen.queryByRole('link', { name: /splunk/ })).not.toBeInTheDocument()
  })

  it('omits confirm/correct when there is no analysis id to attach feedback to', () => {
    render(<AISuggestion onConfirm={vi.fn()} onCorrect={vi.fn()} />)
    expect(screen.queryByTestId('ai-feedback-actions')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Confirm/ })).not.toBeInTheDocument()
  })

  it('renders confirm/correct once an analysis id exists', () => {
    const onConfirm = vi.fn()
    render(<AISuggestion analysisId="an-1" onConfirm={onConfirm} onCorrect={vi.fn()} />)
    expect(screen.getByTestId('ai-feedback-actions')).toBeInTheDocument()
    screen.getByRole('button', { name: /Confirm/ }).click()
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('swaps confirm for a recorded note once confirmed', () => {
    render(<AISuggestion analysisId="an-1" confirmed onConfirm={vi.fn()} onCorrect={vi.fn()} />)
    expect(screen.getByTestId('ai-confirmed')).toHaveTextContent('Confirmed')
    expect(screen.queryByRole('button', { name: /^Correct$/ })).not.toBeInTheDocument()
  })
})

describe('basisLabel', () => {
  it('maps the known basis values', () => {
    expect(basisLabel('empirical').label).toBe('calibrated')
    expect(basisLabel('human_corrected').label).toBe('human-corrected')
    expect(basisLabel('llm_weighted').label).toBe('llm-weighted')
    expect(basisLabel('heuristic_estimate').label).toBe('estimated')
  })

  it('defaults unknown / absent values to estimated, not calibrated', () => {
    expect(basisLabel(null).label).toBe('estimated')
    expect(basisLabel(undefined).label).toBe('estimated')
    expect(basisLabel('something_new').label).toBe('estimated')
  })
})

describe('normalizeProvenance', () => {
  it('returns null when nothing usable is present', () => {
    expect(normalizeProvenance(null)).toBeNull()
    expect(normalizeProvenance({})).toBeNull()
    expect(normalizeProvenance({ confidence_score: 80 })).toBeNull()
  })

  it('reads the nested provenance block and the flat llm fields together', () => {
    const p = normalizeProvenance({
      llm_provider: 'ollama',
      llm_model: 'qwen2.5:7b',
      provenance: { mode_used: 'rules', mode_requested: 'llm', fallback_from: 'llm', fallback_reason: 'offline' },
    })
    expect(p).toEqual({
      modeUsed: 'rules',
      modeRequested: 'llm',
      fallbackFrom: 'llm',
      fallbackReason: 'offline',
      llmProvider: 'ollama',
      llmModel: 'qwen2.5:7b',
    })
  })

  it('tolerates a response that carries only the legacy flat fields', () => {
    expect(normalizeProvenance({ llm_provider: 'ollama', llm_model: 'phi3' })).toMatchObject({
      modeUsed: null,
      llmProvider: 'ollama',
      llmModel: 'phi3',
    })
  })
})

describe('engineLabel', () => {
  it('humanises known engines and echoes unknown ones', () => {
    expect(engineLabel('rules')).toBe('rules engine')
    expect(engineLabel('llm')).toBe('LLM')
    expect(engineLabel('some_new_engine')).toBe('some new engine')
    expect(engineLabel(null)).toBe('unknown engine')
  })
})
