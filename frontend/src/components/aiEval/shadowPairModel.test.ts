import { describe, expect, it } from 'vitest'

import {
  COPY,
  TIER_AGENTS,
  expiresIn,
  floorSentence,
  metricCell,
  outputText,
  samplesLine,
  serverMessage,
  sourceText,
  verdictBody,
  verdictText,
  verdictTone,
} from './shadowPairModel'

describe('the G2 floor and the sample line', () => {
  it('names the agent floor below it (100 root cause, 20 summary) and nothing once met', () => {
    expect(floorSentence('agent.root_cause_analysis.v1', 3)).toBe(
      'Not enough evidence yet: G2 needs 100 paired samples for this agent.',
    )
    expect(floorSentence('agent.summary.v1', 19)).toBe('Not enough evidence yet: G2 needs 20 paired samples for this agent.')
    expect(floorSentence('agent.summary.v1', 20)).toBeNull()
    expect(floorSentence('agent.root_cause_analysis.v1', 100)).toBeNull()
    expect(TIER_AGENTS.map((agent) => agent.capability)).toEqual(['root_cause_analysis', 'summary'])
  })

  it('reads a missing live count as 0 (the server omits it without live labels)', () => {
    expect(samplesLine(21, 3)).toBe('21 paired samples · 3 live labels')
    expect(samplesLine(18, undefined)).toBe('18 paired samples · 0 live labels')
  })
})

describe('verdicts', () => {
  it("uses the server's vocabulary and shows an unknown verdict as itself", () => {
    expect(verdictText('insufficient_samples')).toBe('Not enough evidence')
    expect(verdictText('pass')).toBe('PASS')
    expect(verdictText('weird')).toBe('weird')
    expect([verdictTone('pass'), verdictTone('fail'), verdictTone('insufficient_samples'), verdictTone('x')]).toEqual([
      'success', 'danger', 'warning', 'muted',
    ])
  })

  it('formats rates, the signed paired delta, cost and latency', () => {
    expect(metricCell({ name: 'candidate_accuracy' }, 2 / 3)).toBe('66.7%')
    expect(metricCell({ name: 'paired_accuracy_delta' }, -1 / 3)).toBe('−33.3%')
    expect(metricCell({ name: 'paired_accuracy_delta' }, 0.05)).toBe('+5.0%')
    expect(metricCell({ name: 'cost_delta_usd' }, -0.01)).toBe('−$0.0100')
    expect(metricCell({ name: 'latency_delta_ms' }, -20)).toBe('−20 ms')
    expect(metricCell({ name: 'candidate_accuracy' }, null)).toBe('—')
  })
})

describe('the queue card', () => {
  it('counts the days left from expires_at and a "now" built here', () => {
    const now = new Date('2026-10-25T09:00:00Z')
    expect(expiresIn('2026-11-24T09:00:00+00:00', now)).toBe('Expires in 30 days')
    expect(expiresIn('2026-10-26T08:00:00+00:00', now)).toBe('Expires in 1 day')
    expect(expiresIn('2026-10-25T08:00:00+00:00', now)).toBe('Expires today')
    expect(expiresIn(null, now)).toBeNull()
    expect(expiresIn('not a date', now)).toBeNull()
  })

  it('links a source as Run #{build}; no source → null (the card says the source is gone)', () => {
    expect(sourceText({ kind: 'test_case', id: 'tcA1', run_id: 'trA1b2C3d4', build_number: 'b-118', href: '/runs/trA1b2C3d4?tab=tests' }))
      .toBe('Run #b-118')
    expect(sourceText({ kind: 'test_run', id: 'trA1', run_id: 'trA1b2C3d4', build_number: null, href: '/runs/trA1b2C3d4' }))
      .toBe('Run trA1b2C3')
    expect(sourceText(null)).toBeNull()
    expect(COPY.sourceGone).toBe('Source no longer available.')
  })

  it('builds the label body: the category only when given, trimmed notes only when written', () => {
    expect(verdictBody('candidate', 'FLAKY', '  stale fixture ')).toEqual({ verdict: 'candidate', failure_category: 'FLAKY', notes: 'stale fixture' })
    expect(verdictBody('neither', '', '   ')).toEqual({ verdict: 'neither' })
    expect(verdictBody('both', null, undefined)).toEqual({ verdict: 'both' })
  })

  it('turns an output into text for a <pre>, a markup string included', () => {
    expect(outputText({ summary: '<img src=x onerror=alert(1)>' })).toBe('{\n  "summary": "<img src=x onerror=alert(1)>"\n}')
    expect(outputText(null)).toBe('—')
  })

  it("reads a refused write's sentence from the {code, message} detail", () => {
    const err = { response: { data: { detail: { code: 'shadow_pair_already_labelled', message: 'This pair is already labelled (golden_match).' } } } }
    expect(serverMessage(err, 'fallback')).toBe('This pair is already labelled (golden_match).')
    expect(serverMessage(new Error('network'), 'fallback')).toBe('fallback')
  })
})
