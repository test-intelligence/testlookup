import { describe, expect, it } from 'vitest'

import type { FlakyCoachEntry, FlakyScoreBrief } from '@/services/testHealthService'
import {
  COMPONENT_LABELS,
  componentLabel,
  componentLine,
  evidenceLine,
  formatScoredAt,
  insufficientLine,
  parseFlakyScore,
  pendingLine,
  scoreCell,
  sortEntries,
  weightsLine,
  windowLine,
} from './flakyScoreModel'

const SCORED = {
  status: 'scored',
  score: 0.62,
  confidence: 'medium',
  observations: 23,
  min_observations: 5,
  window_days: 30,
  computed_at: '2026-10-16T02:10:00+00:00',
  components: [
    { name: 'result_volatility', value: 0.7, weight: 0.45 },
    { name: 'retry_rate', value: 0.5, weight: 0.25 },
    { name: 'environment_instability', value: 0.4, weight: 0.2 },
    { name: 'duration_variance', value: 0.1, weight: 0.1 },
  ],
}

const INSUFFICIENT = {
  status: 'insufficient', score: null, confidence: 'none', observations: 3, min_observations: 5,
  window_days: 30, computed_at: null, components: [],
}

const PENDING = {
  status: 'pending', score: null, confidence: 'none', observations: 7, min_observations: 5,
  window_days: 30, computed_at: null, components: [],
}

describe('parseFlakyScore', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['an array', [SCORED]],
    ['a string', 'scored'],
    ['an unknown status', { ...SCORED, status: 'great' }],
    ['a non-string status', { ...SCORED, status: 1 }],
    ['a scored answer without a score', { ...SCORED, score: null }],
    ['a string score', { ...SCORED, score: '0.62' }],
    ['a score above 1', { ...SCORED, score: 1.5 }],
    ['a negative score', { ...SCORED, score: -0.1 }],
    ['a NaN score', { ...SCORED, score: Number.NaN }],
    ['a string observation count', { ...INSUFFICIENT, observations: '3' }],
    ['a numeric confidence', { ...SCORED, confidence: 2 }],
    ['components that are not a list', { ...SCORED, components: { a: 1 } }],
    ['a component without a weight', { ...SCORED, components: [{ name: 'retry_rate', value: 0.1 }] }],
    ['a component with a string value', { ...SCORED, components: [{ name: 'retry_rate', value: '0.1', weight: 0.25 }] }],
    ['a non-string computed_at', { ...SCORED, computed_at: 12 }],
  ])('answers null for %s', (_label, raw) => {
    expect(parseFlakyScore(raw)).toBeNull()
  })

  it('keeps a valid scored answer', () => {
    expect(parseFlakyScore(SCORED)).toEqual(SCORED)
  })

  it('never carries a score or components on an unscored answer', () => {
    expect(parseFlakyScore({ ...INSUFFICIENT, score: 0.4, components: SCORED.components })).toEqual(INSUFFICIENT)
    expect(parseFlakyScore(PENDING)).toEqual(PENDING)
  })

  it('fills the schema defaults for absent optional fields', () => {
    expect(parseFlakyScore({ status: 'pending' })).toEqual({
      status: 'pending', score: null, confidence: 'none', observations: 0, min_observations: 5,
      window_days: 30, computed_at: null, components: [],
    })
  })
})

function entry(fp: string, flaky_score: unknown): FlakyCoachEntry {
  return {
    test_fingerprint: fp, test_name: fp, suite_name: null, failure_rate: 0.5, total_runs: 10, failed_runs: 5,
    flaky_since: null, last_failure_at: null, quarantine_recommendation: 'INVESTIGATE',
    stabilization_actions: [], impact_score: 1, status_history: [],
    flaky_score: flaky_score as FlakyScoreBrief | null,
  }
}

describe('sortEntries', () => {
  const rows = [
    entry('a', INSUFFICIENT),
    entry('b', { ...SCORED, score: 0.3 }),
    entry('c', null),
    entry('d', { ...SCORED, score: 0.9 }),
    entry('e', { ...SCORED, score: 0.3 }),
    entry('f', PENDING),
    entry('g', { status: 'scored', score: 'high' }),
  ]

  it('Impact keeps the server order (a copy)', () => {
    const sorted = sortEntries(rows, 'impact')
    expect(sorted.map((r) => r.test_fingerprint)).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'g'])
    expect(sorted).not.toBe(rows)
  })

  it('Flakiness score puts the most flaky first; ties keep the server order; unscored rows follow in it', () => {
    expect(sortEntries(rows, 'score').map((r) => r.test_fingerprint)).toEqual(['d', 'b', 'e', 'a', 'c', 'f', 'g'])
  })

  it('handles no rows', () => {
    expect(sortEntries([], 'score')).toEqual([])
  })
})

describe('scoreCell', () => {
  it.each([
    ['scored', SCORED, { text: '0.62', band: 'medium', title: 'Flakiness score 0.62 · medium confidence · 23 observations' }],
    ['insufficient', INSUFFICIENT, { text: 'insufficient', band: null, title: '3 of 5 observations' }],
    ['pending', PENDING, { text: 'not scored yet', band: null, title: 'Scores are computed nightly' }],
  ])('%s', (_label, raw, expected) => {
    expect(scoreCell(parseFlakyScore(raw))).toEqual(expected)
  })

  it('a missing or failed enrichment is a dash, never 0.00', () => {
    expect(scoreCell(null)).toEqual({ text: '—', band: null, title: 'Score not available' })
    expect(scoreCell(parseFlakyScore({ status: 'scored' }))).toEqual({ text: '—', band: null, title: 'Score not available' })
  })

  it('chips only the low / medium / high bands', () => {
    expect(scoreCell(parseFlakyScore({ ...SCORED, confidence: 'high' })).band).toBe('high')
    expect(scoreCell(parseFlakyScore({ ...SCORED, confidence: 'low' })).band).toBe('low')
    expect(scoreCell(parseFlakyScore({ ...SCORED, confidence: 'none' })).band).toBeNull()
    expect(scoreCell(parseFlakyScore({ ...SCORED, confidence: '<b>x</b>' })).band).toBeNull()
  })
})

describe('the details section lines', () => {
  const brief = parseFlakyScore(SCORED) as FlakyScoreBrief

  it('labels the four signals and renders an unknown one as itself', () => {
    expect(Object.keys(COMPONENT_LABELS)).toEqual([
      'result_volatility', 'retry_rate', 'environment_instability', 'duration_variance',
    ])
    expect(componentLabel('result_volatility')).toBe('Result volatility (pass/fail flips)')
    expect(componentLabel('cosmic_rays')).toBe('cosmic_rays')
    expect(componentLabel('constructor')).toBe('constructor')
    expect(componentLine(brief.components[0])).toBe('Result volatility (pass/fail flips) 0.70 × 0.45')
  })

  it('sums the stored weights', () => {
    expect(weightsLine(brief.components)).toBe('Weights sum to 1.00')
    expect(weightsLine([{ name: 'retry_rate', value: 0.5, weight: 0.25 }])).toBe('Weights sum to 0.25')
  })

  it('states the evidence and the window', () => {
    expect(evidenceLine(brief)).toBe('medium · 23 observations')
    expect(windowLine(brief)).toBe('over the last 30 days, scored 16 Oct 02:10 UTC')
    expect(windowLine({ ...brief, computed_at: 'not a date' })).toBe('over the last 30 days')
    expect(formatScoredAt(null)).toBeNull()
  })

  it('says insufficient and pending in words', () => {
    expect(insufficientLine(parseFlakyScore(INSUFFICIENT) as FlakyScoreBrief)).toBe('insufficient: 3 of 5 observations')
    expect(pendingLine(parseFlakyScore(PENDING) as FlakyScoreBrief)).toBe(
      'Not scored yet: the score is computed nightly from the last 30 days.',
    )
  })
})
