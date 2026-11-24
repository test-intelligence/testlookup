/**
 * Contract tests for the shared AI trust chrome (US-15.1).
 *
 * The honesty invariants, in order of how badly they hurt when broken:
 *  - the provenance badge renders ALWAYS ("AI-suggested" only for a model, D-13);
 *  - a confidence renders ONLY when one is supplied, and never without its
 *    calibration basis;
 *  - an absent/unknown basis reads "estimated", never "calibrated";
 *  - a fallback notice appears whenever `fallbackFrom` is set;
 *  - the low-confidence state is explicit;
 *  - confirm/correct render only with an analysisId to attach feedback to.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AISuggestion, { AIBadge, basisLabel, engineLabel, normalizeProvenance, producedByWord } from './AISuggestion'

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
    // D-160: a reason that is not a known code is kept in the tooltip, not printed in the sentence
    // (this assertion used to pin the reason inside the visible text).
    expect(notice).not.toHaveTextContent('Ollama timed out.')
    expect(notice).toHaveAttribute('title', 'Reason recorded: Ollama timed out.')
  })

  // ── D-149 / D-160 / S-A-1: the fallback sentence and the provenance line say true, plain things ──
  it('D-149: renders no fallback notice when the engine "fell back" to itself', () => {
    render(<AISuggestion provenance={{ modeUsed: 'llm', fallbackFrom: 'llm', fallbackReason: 'react_timeout' }} />)
    expect(screen.queryByTestId('ai-fallback-notice')).not.toBeInTheDocument()
    expect(screen.queryByText(/came from the LLM instead/)).not.toBeInTheDocument()
  })

  it('D-149: a ReAct timeout answered by the fast classifier reads as the full investigation running out of time', () => {
    const reason = 'react_timeout: full investigation timed out after 120 s'
    render(<AISuggestion provenance={{ modeUsed: 'llm', fallbackFrom: 'react_agent', fallbackReason: reason }} />)
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent('The full investigation ran out of time, so this came from the LLM instead.')
    expect(notice.textContent).not.toMatch(/_|120 s/)
    // The full raw "<code>: <detail>" string is kept in the tooltip only.
    expect(notice).toHaveAttribute('title', `Reason recorded: ${reason}`)
  })

  it('a tier-3 timeout stub (no engine) says the model did not answer in time and that nothing else answered', () => {
    render(<AISuggestion provenance={{ modeUsed: null, fallbackFrom: 'llm', fallbackReason: 'llm_timeout: LLM timed out after 120 s' }} />)
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('No engine answered')
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent('The LLM did not answer in time, and no other engine answered.')
    expect(notice.textContent).not.toMatch(/_|timed out after/)
    expect(notice).toHaveAttribute('title', 'Reason recorded: llm_timeout: LLM timed out after 120 s')
  })

  it('D-160: the small-model fallback has no "slm" and no underscore in the sentence', () => {
    render(<AISuggestion provenance={{ modeUsed: 'rules', fallbackFrom: 'slm', fallbackReason: 'slm_call_failed' }} />)
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent('The small model did not answer, so this came from the rules engine instead.')
    expect(notice.textContent).not.toMatch(/slm/i)
    expect(notice.textContent).not.toMatch(/_/)
  })

  it.each([
    ['llm_unavailable', 'could not be reached'],
    ['llm_error: LLMPolicyViolation: blocked', 'returned an error'],
    ['llm_timeout', 'did not answer in time'],
    ['budget', 'is over its usage budget'],
    ['step_llm_budget', 'used up its allowed model calls'],
    ['tier_pinned', 'is not used, because the AI settings fix the engine'],
  ])('D-160: the reason code %s reads in plain words', (reason, words) => {
    render(<AISuggestion provenance={{ modeUsed: 'rules', fallbackFrom: 'llm', fallbackReason: reason }} />)
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent(`The LLM ${words}, so this came from the rules engine instead.`)
    expect(notice.textContent).not.toMatch(/_|LLMPolicyViolation/)
    expect(notice).toHaveAttribute('title', `Reason recorded: ${reason}`)
  })

  it('D-160: an unknown reason code goes to the tooltip, never into the sentence', () => {
    render(<AISuggestion provenance={{ modeUsed: 'rules', fallbackFrom: 'llm', fallbackReason: 'brand_new_code' }} />)
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice.textContent).not.toMatch(/brand|_/)
    expect(notice).toHaveAttribute('title', 'Reason recorded: brand_new_code')
  })

  it('S-A-1: a stub with no engine and an "unknown" provider/model reads "No engine answered"', () => {
    const provenance = normalizeProvenance({
      llm_provider: 'unknown',
      llm_model: 'unknown',
      provenance: { mode_used: null, mode_requested: 'auto', fallback_reason: 'llm_error' },
    })
    render(<AISuggestion provenance={provenance} />)
    const line = screen.getByTestId('ai-provenance')
    expect(line).toHaveTextContent('No engine answered')
    expect(line).not.toHaveTextContent(/unknown|Produced by|LLM/)
  })

  it('S-A-1: a null engine after a fallback does not claim the rules engine answered', () => {
    render(<AISuggestion provenance={{ modeUsed: null, fallbackFrom: 'llm', fallbackReason: 'llm_unavailable' }} />)
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('No engine answered')
    const notice = screen.getByTestId('ai-fallback-notice')
    expect(notice).toHaveTextContent('The LLM could not be reached, and no other engine answered.')
    expect(notice).not.toHaveTextContent('rules engine')
  })

  it('D-06: a human correction reads "Produced by human review"', () => {
    render(<AISuggestion provenance={{ modeUsed: 'human_corrected' }} />)
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('Produced by human review')
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

  it('W29d UIX-04c: the AI badge is an eyebrow in both variants, with no inline font-size; compact keeps only its padding', () => {
    const { rerender } = render(<AIBadge />)
    for (const compact of [false, true]) {
      rerender(<AIBadge compact={compact} />)
      const badge = screen.getByTestId('ai-suggested-badge')
      expect(badge).toHaveClass('eyebrow')
      expect(badge.style.fontSize).toBe('')
      expect(badge.style.letterSpacing).toBe('')
      expect(badge.style.padding).toBe(compact ? '1px 6px' : '2px 8px')
    }
  })
})

describe('basisLabel', () => {
  it('maps the known basis values', () => {
    expect(basisLabel('empirical').label).toBe('calibrated')
    expect(basisLabel('human_corrected').label).toBe('human-corrected')
    expect(basisLabel('llm_weighted').label).toBe('llm-weighted')
    expect(basisLabel('heuristic_estimate').label).toBe('estimated')
  })

  it("FAIL-10: names a project hint as a QA lead's rule, not a model", () => {
    expect(basisLabel('project_hint')).toEqual({
      label: 'project hint',
      title: "Classification set by a project hint (a QA lead's rule), not by a model",
    })
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

  it('S-A-1: drops the "unknown" filler the stored-analysis read puts in a missing provider / model', () => {
    expect(normalizeProvenance({ llm_provider: 'unknown', llm_model: 'unknown' })).toBeNull()
    expect(
      normalizeProvenance({ llm_provider: 'unknown', llm_model: 'unknown', provenance: { fallback_reason: 'llm_error' } }),
    ).toMatchObject({ modeUsed: null, llmProvider: null, llmModel: null, fallbackReason: 'llm_error' })
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

  it('D-149 / D-160 / D-06: names the small model, the full investigation and a human review', () => {
    expect(engineLabel('slm')).toBe('small model')
    expect(engineLabel('react_agent')).toBe('full investigation')
    expect(engineLabel('human_corrected')).toBe('human review')
  })

  it('FAIL-10: the hint engine (`mode_used: hint`) reads "project hint"', () => {
    expect(engineLabel('hint')).toBe('project hint')
    expect(engineLabel('HINT')).toBe('project hint')
  })
})

describe('D-13: producedByWord / the badge names what answered', () => {
  it('a rules provenance gives no "AI" in the badge, its tooltip or the section name', () => {
    render(<AISuggestion provenance={{ modeUsed: 'rules' }} />)
    const badge = screen.getByTestId('ai-suggested-badge')
    expect(badge).toHaveTextContent('Rule-based suggestion')
    expect(badge.textContent).not.toMatch(/\bAI\b/)
    expect(badge.getAttribute('title')).not.toMatch(/\bAI\b/)
    expect(screen.getByTestId('ai-suggestion').getAttribute('aria-label')).toBe('Rule-based suggestion')
  })

  it('an llm provenance gives "AI-suggested"', () => {
    render(<AISuggestion provenance={{ modeUsed: 'llm' }} />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('AI-suggested')
    expect(screen.getByTestId('ai-suggestion').getAttribute('aria-label')).toBe('AI-suggested output')
  })

  it('a classifier, a hint and a stub that no engine wrote each get their own words', () => {
    const { rerender } = render(<AISuggestion provenance={{ modeUsed: 'ml' }} />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('Classifier suggestion')
    rerender(<AISuggestion provenance={{ modeUsed: 'hint' }} />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('From a project hint')
    rerender(<AISuggestion provenance={{ modeUsed: null, fallbackFrom: 'llm', fallbackReason: 'llm_unavailable' }} />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('Needs review')
  })

  it('maps every engine id; unknown ids are a plain "Suggestion", no id at all keeps "AI-suggested"', () => {
    expect(producedByWord('heuristic').badge).toBe('Rule-based suggestion')
    expect(producedByWord('ML_CLASSIFIER').badge).toBe('Classifier suggestion')
    expect(producedByWord('local_llm').badge).toBe('AI-suggested')
    expect(producedByWord('slm').badge).toBe('AI-suggested')
    expect(producedByWord('human_corrected').badge).toBe('Corrected by a person')
    expect(producedByWord('seeded demo data').badge).toBe('Suggestion')
    expect(producedByWord(null).badge).toBe('AI-suggested')
  })

  it('an explicit badgeText still wins', () => {
    render(<AISuggestion provenance={{ modeUsed: 'rules' }} badgeText="Hedged" />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('Hedged')
  })
})
