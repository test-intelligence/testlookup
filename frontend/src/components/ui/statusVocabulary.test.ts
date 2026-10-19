/**
 * W29a UIX-03: the ONE status and decision vocabulary — sentence-case words,
 * every spelling the API and the pages send, and the tokens they paint with
 * (conditional is the WARNING hue, never skipped-gold).
 */
import { describe, expect, it } from 'vitest'
import { FLAKY_MARKER, STATUS_ENCODING } from '@/components/charts/tokens'
import {
  DECISION_LABEL,
  DECISION_TOKEN,
  RESULT_STATUS_TOKEN,
  STATUS_LABEL,
  decisionKey,
  decisionLabel,
  resultStatusToken,
  statusKey,
  statusLabel,
} from './statusVocabulary'

describe('status words', () => {
  it('every STATUS_LABEL value is sentence case (first letter upper, never all caps)', () => {
    for (const [key, word] of Object.entries(STATUS_LABEL)) {
      expect(word.charAt(0), key).toBe(word.charAt(0).toUpperCase())
      expect(word, key).not.toBe(word.toUpperCase())
      expect(word.slice(1), key).toBe(word.slice(1).toLowerCase())
    }
  })

  it("the result labels ARE the chart kit's (a legend and a badge never disagree)", () => {
    for (const s of ['passed', 'failed', 'broken', 'skipped', 'unknown'] as const) {
      expect(STATUS_LABEL[s]).toBe(STATUS_ENCODING[s].label)
    }
    expect(STATUS_LABEL.flaky).toBe(FLAKY_MARKER.label)
  })

  it.each([
    ['PASSED', 'Passed'], ['FAILED', 'Failed'], ['BROKEN', 'Broken'], ['SKIPPED', 'Skipped'],
    ['UNKNOWN', 'Unknown'], ['FLAKY', 'Flaky'], ['IN_PROGRESS', 'In progress'], ['STOPPED', 'Stopped'],
    ['PASSED_RUN', 'Passed'], ['passed', 'Passed'], ['failed', 'Failed'], ['not_run', 'Not run'],
    ['blocked', 'Blocked'], ['in-progress', 'In progress'], [' In Progress ', 'In progress'],
    ['error', 'Error'], ['pending', 'Pending'],
  ])('statusLabel(%j) is %j', (raw, word) => {
    expect(statusLabel(raw)).toBe(word)
  })

  it('an unknown value prints its own words in sentence case; empty is the dash', () => {
    expect(statusLabel('raw_value')).toBe('Raw value')
    expect(statusLabel('SOME-NEW STATE')).toBe('Some new state')
    expect(statusLabel('constructor')).toBe('Constructor')
    for (const empty of [null, undefined, '', '   ']) expect(statusLabel(empty)).toBe('—')
  })

  it('statusKey trims, lower-cases and joins words with _', () => {
    expect(statusKey('  In-Progress ')).toBe('in_progress')
    expect(statusKey('NOT RUN')).toBe('not_run')
    expect(statusKey(null)).toBe('')
  })
})

describe('result tokens', () => {
  it('each result paints with its own hue, tint, border and ink', () => {
    for (const s of ['passed', 'failed', 'broken', 'skipped', 'flaky'] as const) {
      expect(RESULT_STATUS_TOKEN[s]).toEqual({
        hue: `var(--status-${s})`,
        tint: `var(--status-${s}-bg)`,
        border: `var(--status-${s}-bd)`,
        ink: `var(--status-${s}-ink)`,
      })
    }
    expect(RESULT_STATUS_TOKEN.skipped.hue).toBe('var(--status-skipped)')
    expect(RESULT_STATUS_TOKEN.unknown.ink).toBe('var(--color-text-muted)')
  })

  it('resultStatusToken reads results only (passed_run is passed); anything else is null', () => {
    expect(resultStatusToken('PASSED_RUN')).toBe(RESULT_STATUS_TOKEN.passed)
    expect(resultStatusToken('Failed')).toBe(RESULT_STATUS_TOKEN.failed)
    expect(resultStatusToken('flaky')).toBe(RESULT_STATUS_TOKEN.flaky)
    expect(resultStatusToken('unknown')).toBe(RESULT_STATUS_TOKEN.unknown)
    for (const other of ['IN_PROGRESS', 'stopped', 'deprecated', 'constructor', '', null]) {
      expect(resultStatusToken(other), String(other)).toBeNull()
    }
  })
})

describe('gate decisions', () => {
  it('Go / Conditional / No-Go', () => {
    expect(DECISION_LABEL).toEqual({ go: 'Go', conditional: 'Conditional', no_go: 'No-Go' })
  })

  it.each([
    ['GO', 'go'], ['go', 'go'], [' Go ', 'go'],
    ['CONDITIONAL_GO', 'conditional'], ['CONDITIONAL', 'conditional'], ['conditional', 'conditional'],
    ['conditional_go', 'conditional'], ['Conditional Go', 'conditional'],
    ['NO_GO', 'no_go'], ['NO-GO', 'no_go'], ['no_go', 'no_go'], ['no-go', 'no_go'], ['NOGO', 'no_go'], ['No Go', 'no_go'],
  ])('decisionKey(%j) is %j', (raw, key) => {
    expect(decisionKey(raw)).toBe(key)
  })

  it('anything else is no decision, and decisionLabel falls back to its sentence case', () => {
    for (const other of ['PENDING', 'GOOD', 'NO', 'ungated', '', null, undefined]) {
      expect(decisionKey(other), String(other)).toBeNull()
    }
    expect(decisionLabel('NO_GO')).toBe('No-Go')
    expect(decisionLabel('CONDITIONAL_GO')).toBe('Conditional')
    expect(decisionLabel('PENDING_REVIEW')).toBe('Pending review')
    expect(decisionLabel(null)).toBe('—')
  })

  it('conditional is the WARNING hue, never skipped-gold; each decision reads its result ink', () => {
    expect(JSON.stringify(DECISION_TOKEN.conditional)).not.toContain('--status-skipped')
    expect(DECISION_TOKEN.conditional).toEqual({
      hue: 'var(--gate-conditional)',
      tint: 'var(--status-broken-bg)',
      border: 'var(--status-broken-bd)',
      ink: 'var(--status-broken-ink)',
    })
    expect(DECISION_TOKEN.go.ink).toBe('var(--status-passed-ink)')
    expect(DECISION_TOKEN.no_go.ink).toBe('var(--status-failed-ink)')
    expect(DECISION_TOKEN.go.hue).toBe('var(--gate-go)')
    expect(DECISION_TOKEN.no_go.hue).toBe('var(--gate-no-go)')
  })
})
