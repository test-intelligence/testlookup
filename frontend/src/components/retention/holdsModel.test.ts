/**
 * W22a — the pure copy of retention holds and the keep-last floor.
 *
 * Every sentence the Holds card, the preview's kept lines, the release strip
 * and the pack controls show is pinned here once.
 */
import { describe, expect, it } from 'vitest'
import {
  baselinePurgedLine,
  ENTITY_TYPE_LABELS,
  entityHref,
  entityLabel,
  expiryToIso,
  floorLine,
  heldLine,
  keptLines,
  NOT_HELD_NOTE,
  placedLine,
  PROJECT_HELD_LINE,
  REASON_LENGTH_SENTENCE,
  referenceLines,
  REFERENCES_UNMEASURED,
  errorStatus,
  serverSentence,
  validateKeepLastRuns,
  validateReason,
  FLOOR_BOUNDS_SENTENCE,
} from './holdsModel'

const RELEASE_ID = '0b5f2c1e-7d1a-4c55-9a51-3f1e2d4c5b6a'
const RUN_ID = '9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4'
const fmt = (iso: string) => `D(${iso.slice(0, 10)})`

describe('heldLine / labels', () => {
  it('is the one refusal sentence', () => {
    expect(heldLine({ reason: 'Audit Q4 sentinel' })).toBe('Held: Audit Q4 sentinel')
  })

  it('labels every entity type', () => {
    expect(ENTITY_TYPE_LABELS).toEqual({
      project: 'Project',
      release: 'Release',
      run: 'Run',
      compliance_pack: 'Compliance pack',
    })
  })

  it('prefers the snapshot label, else the project label or type + id', () => {
    expect(entityLabel({ entity_type: 'release', entity_id: RELEASE_ID, entity_label: 'release 2026.10', reason: 'x' }))
      .toBe('release 2026.10')
    expect(entityLabel({ entity_type: 'project', entity_id: null, entity_label: null, reason: 'x' }))
      .toBe('the whole project')
    expect(entityLabel({ entity_type: 'run', entity_id: RUN_ID, entity_label: '  ', reason: 'x' }))
      .toBe('Run 9e8d7c6b')
  })
})

describe('entityHref — built from id + type only', () => {
  it('links a release and a run by uuid', () => {
    expect(entityHref({ entity_type: 'release', entity_id: RELEASE_ID, reason: 'r' })).toBe(`/releases/${RELEASE_ID}`)
    expect(entityHref({ entity_type: 'run', entity_id: RUN_ID, reason: 'r' })).toBe(`/runs/${RUN_ID}`)
  })

  it('links a pack only through its known release', () => {
    expect(entityHref({ entity_type: 'compliance_pack', entity_id: RUN_ID, reason: 'r' })).toBeNull()
    expect(entityHref({ entity_type: 'compliance_pack', entity_id: RUN_ID, release_id: RELEASE_ID, reason: 'r' }))
      .toBe(`/releases/${RELEASE_ID}`)
  })

  it('refuses a project hold, an unknown type and a non-uuid id', () => {
    expect(entityHref({ entity_type: 'project', entity_id: null, reason: 'r' })).toBeNull()
    expect(entityHref({ entity_type: 'defect', entity_id: RUN_ID, reason: 'r' })).toBeNull()
    expect(entityHref({ entity_type: 'run', entity_id: 'javascript:alert(1)', reason: 'r' })).toBeNull()
    expect(entityHref({ entity_type: 'release', entity_id: `${RELEASE_ID}/../admin`, reason: 'r' })).toBeNull()
  })
})

describe('placedLine', () => {
  it('reads placed / expires / no expiry / expired', () => {
    expect(placedLine({ placed_by_name: 'Ada Admin', placed_at: '2026-10-01T10:00:00Z', expires_at: null, state: 'live' }, fmt))
      .toBe('Placed by Ada Admin on D(2026-10-01) · No expiry')
    expect(placedLine({ placed_by_name: 'Ada Admin', placed_at: '2026-10-01T10:00:00Z', expires_at: '2026-12-31T22:59:59Z', state: 'live' }, fmt))
      .toBe('Placed by Ada Admin on D(2026-10-01) · Expires D(2026-12-31)')
    expect(placedLine({ placed_by_name: null, placed_at: '2026-10-01T10:00:00Z', expires_at: '2026-10-02T00:00:00Z', state: 'expired' }, fmt))
      .toBe('Placed on D(2026-10-01) · Expired D(2026-10-02)')
  })

  it('reads a released row as the release line', () => {
    expect(placedLine({
      placed_by_name: 'Lee Lead', placed_at: '2026-10-01T10:00:00Z', state: 'released',
      released_at: '2026-10-05T09:00:00Z', released_by_name: 'Ada Admin', release_reason: 'Audit closed',
    }, fmt)).toBe('Released by Ada Admin on D(2026-10-05): Audit closed')
  })
})

describe('floorLine', () => {
  it('names every part that applies', () => {
    expect(floorLine({ keep_last_runs: 10, runs_kept: 12, latest_passed_kept: true, suites_kept: 2 }))
      .toBe('Kept by the floor: 12 runs (the newest 10, the latest passing run, the latest passing run of 2 suites)')
  })

  it('drops absent parts and pluralises', () => {
    expect(floorLine({ keep_last_runs: 0, runs_kept: 1, latest_passed_kept: false, suites_kept: 1 }))
      .toBe('Kept by the floor: 1 run (the latest passing run of 1 suite)')
    expect(floorLine({ keep_last_runs: 0, runs_kept: 2, latest_passed_kept: false, suites_kept: 0 }))
      .toBe('Kept by the floor: 2 runs')
  })

  it('is null when the floor keeps nothing or is absent', () => {
    expect(floorLine({ keep_last_runs: 10, runs_kept: 0, latest_passed_kept: false, suites_kept: 0 })).toBeNull()
    expect(floorLine(null)).toBeNull()
    expect(floorLine(undefined)).toBeNull()
  })
})

describe('referenceLines', () => {
  const refs = [
    { table: 'run_baselines', column: 'baseline_run_id', count: 3, label: '3 runs lose their baseline link' },
    { table: 'widgets', column: 'run_id', count: 2, label: '' },
    { table: 'gadgets', column: 'run_id', count: 0, label: '0 gadgets' },
  ]

  it('heads the server labels, falling back to the generic copy', () => {
    expect(referenceLines(refs, true)).toEqual([
      'Reference warnings:',
      '3 runs lose their baseline link',
      '2 widgets rows lose their run_id link',
    ])
  })

  it('reads a fallback count of 1 in the singular', () => {
    expect(referenceLines([{ table: 'widgets', column: 'run_id', count: 1, label: '' }], true)).toEqual([
      'Reference warnings:',
      '1 widgets row loses its run_id link',
    ])
  })

  it('says "not measured" when the count failed — never "none"', () => {
    expect(referenceLines([], false)).toEqual([REFERENCES_UNMEASURED])
    expect(REFERENCES_UNMEASURED).toBe('Reference warnings were not measured.')
  })

  it('is empty when measured and nothing breaks, or the field is absent', () => {
    expect(referenceLines([], true)).toEqual([])
    expect(referenceLines(undefined, undefined)).toEqual([])
  })
})

describe('keptLines — the purge preview block', () => {
  const hold = {
    hold_id: 'h1', entity_type: 'release', entity_id: RELEASE_ID, entity_label: 'release 2026-Q4',
    reason: 'Audit Q4 sentinel', expires_at: null, runs_kept: 5, packs_kept: 1,
  }

  it('orders project, holds, floor, references, then the not-held note', () => {
    const lines = keptLines({
      holds: { project_held: true, runs_kept: 5, packs_kept: 1, active: [hold] },
      floor: { keep_last_runs: 10, runs_kept: 10, latest_passed_kept: false, suites_kept: 0 },
      references_broken: [{ table: 'run_baselines', column: 'baseline_run_id', count: 3, label: '3 runs lose their baseline link' }],
      references_measured: true,
    })
    expect(lines.map((l) => l.text)).toEqual([
      PROJECT_HELD_LINE,
      'Kept by hold: Audit Q4 sentinel — 5 runs, 1 pack',
      'Kept by the floor: 10 runs (the newest 10)',
      'Reference warnings:',
      '3 runs lose their baseline link',
      NOT_HELD_NOTE,
    ])
    expect(lines[1]).toMatchObject({ kind: 'hold', detail: 'release 2026-Q4' })
    expect(PROJECT_HELD_LINE).toBe('The whole project is on hold — nothing it covers will be purged.')
  })

  it('shows the not-held note only while a hold is active', () => {
    const noHolds = keptLines({
      holds: { project_held: false, runs_kept: 0, packs_kept: 0, active: [] },
      floor: { keep_last_runs: 10, runs_kept: 2, latest_passed_kept: true, suites_kept: 0 },
      references_measured: false,
    })
    expect(noHolds.map((l) => l.kind)).toEqual(['floor', 'reference-unmeasured'])
    expect(keptLines({ holds: { active: [{ ...hold, runs_kept: 0, packs_kept: 0 }] } }).map((l) => l.text))
      .toEqual(['Kept by hold: Audit Q4 sentinel — 0 runs, 0 packs', NOT_HELD_NOTE])
  })

  it('is empty for an older server that sends none of the fields', () => {
    expect(keptLines({})).toEqual([])
    expect(keptLines(null)).toEqual([])
  })
})

describe('expiryToIso — end of that local day', () => {
  it('is 23:59:59 local of the chosen day', () => {
    const iso = expiryToIso('2026-12-31')
    expect(iso).not.toBeNull()
    const d = new Date(iso as string)
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds()])
      .toEqual([2026, 11, 31, 23, 59, 59])
  })

  it('is null for an empty or impossible date', () => {
    expect(expiryToIso('')).toBeNull()
    expect(expiryToIso('2026-02-30')).toBeNull()
    expect(expiryToIso('tomorrow')).toBeNull()
  })
})

describe('serverSentence / errorStatus', () => {
  const err = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

  it('reads the hold routes {code, message}, a string detail and a 422 list', () => {
    expect(serverSentence(err(422, { code: 'reason_length', message: REASON_LENGTH_SENTENCE }), 'fb'))
      .toBe(REASON_LENGTH_SENTENCE)
    expect(serverSentence(err(403, 'Requires at least ADMIN role'), 'fb')).toBe('Requires at least ADMIN role')
    expect(serverSentence(err(422, [{ msg: 'Value error, expires_at must be in the future.' }]), 'fb'))
      .toBe('expires_at must be in the future.')
  })

  it('falls back for anything else', () => {
    expect(serverSentence(new Error('network'), 'Could not place the hold.')).toBe('Could not place the hold.')
    expect(serverSentence(err(500, { other: 1 }), 'fb')).toBe('fb')
    expect(errorStatus(err(409, {}))).toBe(409)
    expect(errorStatus(new Error('x'))).toBeNull()
  })
})

describe('validateReason / validateKeepLastRuns', () => {
  it('applies the server rule on the trimmed reason', () => {
    expect(validateReason('  ab  ')).toBe(REASON_LENGTH_SENTENCE)
    expect(REASON_LENGTH_SENTENCE).toBe('A reason of 3 to 500 characters is required.')
    expect(validateReason('abc')).toBeNull()
    expect(validateReason('x'.repeat(500))).toBeNull()
    expect(validateReason('x'.repeat(501))).toBe(REASON_LENGTH_SENTENCE)
  })

  it('accepts a whole number 0-1000 only', () => {
    expect(validateKeepLastRuns('0')).toBeNull()
    expect(validateKeepLastRuns('1000')).toBeNull()
    expect(validateKeepLastRuns('1001')).toBe(FLOOR_BOUNDS_SENTENCE)
    expect(validateKeepLastRuns('-1')).toBe(FLOOR_BOUNDS_SENTENCE)
    expect(validateKeepLastRuns('2.5')).toBe(FLOOR_BOUNDS_SENTENCE)
    expect(validateKeepLastRuns('')).toBe(FLOOR_BOUNDS_SENTENCE)
  })
})

describe('baselinePurgedLine', () => {
  it('names the build and the date', () => {
    expect(baselinePurgedLine({ build_number: '4471', purged_at: '2026-03-02T10:00:00Z' }, fmt))
      .toBe('Baseline build 4471 was purged on D(2026-03-02).')
  })

  it('reads without a build number', () => {
    expect(baselinePurgedLine({ build_number: null, purged_at: '2026-03-02T10:00:00Z' }, fmt))
      .toBe('The baseline run was purged on D(2026-03-02).')
  })
})
