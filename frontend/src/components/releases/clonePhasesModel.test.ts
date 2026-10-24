/**
 * W11 REL-05: the Copy phases form's model — which releases may be copied
 * from, "First phase starts" prefilled, and the preview with the server's
 * shift (the earliest planned start moves; every date moves by the same days).
 */
import { describe, expect, it } from 'vitest'

import type { Release, ReleasePhase } from '@/types/releases'
import {
  cloneSources,
  copiedToast,
  defaultFirstStart,
  hasDatedPhases,
  shiftTo,
  shiftedPreview,
  sourceAnchor,
  sourceSpanDays,
} from './clonePhasesModel'

function phase(name: string, order: number, start: string | null, end: string | null, type = 'qa_testing'): ReleasePhase {
  return {
    id: `ph-${name}`, release_id: 'r', name, phase_type: type, status: 'completed', description: null,
    order_index: order, planned_start: start, planned_end: end, actual_start: null, actual_end: null,
    exit_criteria: null, notes: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
  }
}

function rel(id: string, overrides: Partial<Release> & { cutoff_start_at?: string | null } = {}) {
  return {
    id, project_id: 'proj-1', name: id, version: null, description: null, status: 'planning',
    planned_date: null, released_at: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    phases: [], ...overrides,
  } as Release & { cutoff_start_at?: string | null }
}

const FOUR = [
  phase('Smoke', 0, '2026-09-01T00:00:00Z', '2026-09-05T00:00:00Z'),
  phase('Regression', 1, '2026-09-06T00:00:00Z', '2026-09-10T00:00:00Z'),
  phase('UAT', 2, '2026-09-11T00:00:00Z', '2026-09-15T00:00:00Z', 'uat'),
  phase('Cutover', 3, '2026-09-16T00:00:00Z', '2026-09-20T00:00:00Z', 'production'),
]

describe('cloneSources', () => {
  it('lists the same project\'s other releases with phases: active / in progress first, then newest planned', () => {
    const target = rel('2026.12')
    const releases = [
      target,
      rel('2026.08', { phases: [FOUR[0]], planned_date: '2026-08-20T00:00:00Z', status: 'released' }),
      rel('2026.10', { phases: FOUR, planned_date: '2026-10-20T00:00:00Z', status: 'released' }),
      rel('2026.11', { phases: [FOUR[0], FOUR[1]], status: 'in_progress' }),
      rel('2026.09', { phases: [FOUR[0]], is_active: true, planned_date: '2026-09-01T00:00:00Z' }),
      rel('empty', { phases: [] }),
      rel('foreign', { project_id: 'proj-2', phases: FOUR }),
    ]
    expect(cloneSources(target, releases).map(s => s.label)).toEqual([
      '2026.09 · 1 phase', '2026.11 · 2 phases', '2026.10 · 4 phases', '2026.08 · 1 phase',
    ])
  })

  it('never offers the target itself, even when it has phases', () => {
    const target = rel('2026.12', { phases: FOUR })
    expect(cloneSources(target, [target])).toEqual([])
  })
})

describe('the anchor and the span (the server\'s rule)', () => {
  it('anchors on the earliest planned start; ends-only phases anchor on their earliest end', () => {
    expect(new Date(sourceAnchor(FOUR) as number).toISOString()).toBe('2026-09-01T00:00:00.000Z')
    const endsOnly = [phase('a', 0, null, '2026-09-09T00:00:00Z'), phase('b', 1, null, '2026-09-03T00:00:00Z')]
    expect(new Date(sourceAnchor(endsOnly) as number).toISOString()).toBe('2026-09-03T00:00:00.000Z')
    expect(sourceAnchor([phase('x', 0, null, null)])).toBeNull()
    expect(sourceSpanDays(FOUR)).toBe(19)
  })

  it('a source with no dated phase has nothing to move', () => {
    const undated = rel('u', { phases: [phase('x', 0, null, null)] })
    expect(hasDatedPhases(undated)).toBe(false)
    expect(shiftTo(undated, '2026-11-03')).toBeNull()
  })
})

describe('defaultFirstStart', () => {
  const today = new Date('2026-10-23T15:00:00Z')
  const source = rel('2026.10', { phases: FOUR })

  it('is the target\'s cutoff start when it has one', () => {
    expect(defaultFirstStart(rel('t', { cutoff_start_at: '2026-11-03T09:00:00Z' }), source, today)).toBe('2026-11-03')
  })
  it('else its planned date minus the source\'s span, so the copy ends on it', () => {
    expect(defaultFirstStart(rel('t', { planned_date: '2026-12-20T00:00:00Z' }), source, today)).toBe('2026-12-01')
  })
  it('else today (a UTC day)', () => {
    expect(defaultFirstStart(rel('t'), source, today)).toBe('2026-10-23')
  })
})

describe('shiftedPreview', () => {
  it('moves every date by the same days, keeps the order and counts each window\'s days', () => {
    const preview = shiftedPreview(rel('2026.10', { phases: [FOUR[2], FOUR[0], FOUR[3], FOUR[1]] }), '2026-11-03')
    expect(preview.map(r => [r.name, r.phaseType, r.days, r.start, r.end])).toEqual([
      ['Smoke', 'qa_testing', 5, '2026-11-03', '2026-11-07'],
      ['Regression', 'qa_testing', 5, '2026-11-08', '2026-11-12'],
      ['UAT', 'uat', 5, '2026-11-13', '2026-11-17'],
      ['Cutover', 'production', 5, '2026-11-18', '2026-11-22'],
    ])
  })

  it('an undated phase stays undated; no first start, no move', () => {
    const source = rel('s', { phases: [phase('Dated', 0, '2026-09-01T00:00:00Z', null), phase('Open', 1, null, null)] })
    expect(shiftedPreview(source, '2026-09-15').map(r => [r.start, r.end, r.days])).toEqual([
      ['2026-09-15', null, 1], [null, null, null],
    ])
    expect(shiftedPreview(source, null)[0].start).toBe('2026-09-01')
  })
})

describe('the request and the toast', () => {
  it('shift_to is the chosen day at 00:00 UTC', () => {
    expect(shiftTo(rel('s', { phases: FOUR }), '2026-11-03')).toBe('2026-11-03T00:00:00Z')
    expect(shiftTo(rel('s', { phases: FOUR }), '')).toBeNull()
  })
  it('Copied 4 phases from 2026.10.', () => {
    expect(copiedToast(4, '2026.10')).toBe('Copied 4 phases from 2026.10.')
    expect(copiedToast(1, '2026.10')).toBe('Copied 1 phase from 2026.10.')
  })
})
