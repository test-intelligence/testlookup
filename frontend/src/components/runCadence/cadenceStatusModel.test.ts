/**
 * W23a TRND-03 (contract §4.4 G-D): the Trends callout and the Coverage run
 * dimension say nothing about a schedule nobody configured; with a configured
 * cadence they say the exact count.
 */
import { describe, expect, it } from 'vitest'
import type { TrendSummaryGap } from '@/types/trends'
import { missedLine } from '@/types/runCadence'
import { cadence, cadenceStatus, missedWindow, statusItem } from '@/test/insightCadenceMocks'
import {
  ACTION_NEEDED,
  CADENCE_LOAD_ERROR,
  DAYS_WITH_RUNS_LABEL,
  EXPECTED_RUNS_PATH,
  MISSED_TILE,
  NO_CADENCE_CONFIGURE,
  NO_CADENCE_PLAIN,
  RUN_CADENCE_LABEL,
  SILENT_DAYS,
  TITLE_GAP,
  TITLE_MISSED,
  TITLE_PAUSED,
  cadenceDetail,
  cadenceDimension,
  calloutModel,
  configureLine,
  coverageCadenceRows,
  dayLabel,
  daysWithRunsDetail,
  noRunsSentence,
  pausedLine,
} from './cadenceStatusModel'

const gap = (ongoing: boolean): TrendSummaryGap => ({
  silent_days: 9, start: '2026-09-10', end: '2026-09-18', ongoing, expected_runs_missed: 9,
})

/** A cadence of `suite` with `missed` misses (one labelled window each). */
const missing = (suite: string | null, missed: number) => statusItem({
  cadence: cadence({ suite_name: suite, paused: false, paused_until: null, paused_reason: null, paused_by_name: null }),
  expected: 5, met: 5 - missed, missed, paused: 0, pending: 0,
  missed_windows: Array.from({ length: missed }, (_, i) => missedWindow(i + 1)),
})
const pausedItem = () => statusItem({
  cadence: cadence({
    suite_name: 'nightly-regression', timezone: 'Europe/Berlin', paused: true,
    paused_until: '2026-10-30T22:59:00Z', paused_reason: 'release freeze', paused_by_name: 'Alice Lead',
  }),
  expected: 0, met: 0, missed: 0, paused: 2, pending: 0,
})
/** The status answer of these cadences (`configured: true`, the sums derived). */
const configured = (items: ReturnType<typeof statusItem>[]) => cadenceStatus(items)

describe('calloutModel — no cadence configured', () => {
  it.each([true, false])('a gap (ongoing %s) is "Run gap in this window": Silent days only, the Configure line, no Action needed', (ongoing) => {
    const model = calloutModel(gap(ongoing), cadenceStatus('none'), true)
    expect(model.show).toBe(true)
    expect(model.title).toBe(TITLE_GAP)
    expect(model.tiles).toEqual([{ label: SILENT_DAYS, value: 9 }])
    expect(model.tiles.map((t) => t.label)).not.toContain(MISSED_TILE)
    expect(model.lines).toEqual([{ kind: 'configure', text: 'No expected cadence configured — ', linkLabel: 'Configure', to: EXPECTED_RUNS_PATH }])
    expect(model.actionNeeded).toBe(false)
  })

  it('reads as the plain sentence for someone who cannot configure', () => {
    expect(calloutModel(gap(true), cadenceStatus('none'), false).lines).toEqual([{ kind: 'text', text: NO_CADENCE_PLAIN }])
    expect(NO_CADENCE_CONFIGURE).toBe('No expected cadence configured — Configure')
    expect(configureLine(false)).toEqual({ kind: 'text', text: 'No expected cadence configured — a QA lead can set one in Settings' })
  })

  it('no gap and no cadence → no callout at all', () => {
    expect(calloutModel(null, cadenceStatus('none'), true).show).toBe(false)
  })

  it('while the status loads: the gap part only; on a status error: the gap part and the load sentence', () => {
    const loading = calloutModel(gap(true), null, true)
    expect(loading).toMatchObject({ show: true, title: TITLE_GAP, tiles: [{ label: SILENT_DAYS, value: 9 }], lines: [], actionNeeded: false })
    const failed = calloutModel(gap(false), null, true, true)
    expect(failed.lines).toEqual([{ kind: 'text', text: CADENCE_LOAD_ERROR }])
    expect(calloutModel(null, null, true, true).show).toBe(false)
  })
})

describe('calloutModel — a configured cadence', () => {
  it('two misses → "Expected runs missed", the exact tile (no ~), the scoped miss line, Action needed', () => {
    const item = missing('nightly-regression', 2)
    const model = calloutModel(gap(true), configured([item]), true)
    expect(model.title).toBe(TITLE_MISSED)
    expect(model.tiles).toEqual([{ label: SILENT_DAYS, value: 9 }, { label: MISSED_TILE, value: 2 }])
    expect(model.lines).toEqual([{ kind: 'text', text: `nightly-regression: ${missedLine(item)}` }])
    expect(model.actionNeeded).toBe(true)
    expect(ACTION_NEEDED).toBe('Action needed')
  })

  it('misses with no gap still show the callout (no Silent days tile)', () => {
    const model = calloutModel(null, configured([missing(null, 1)]), true)
    expect(model.show).toBe(true)
    expect(model.tiles).toEqual([{ label: MISSED_TILE, value: 1 }])
    expect(model.lines[0]).toEqual({ kind: 'text', text: `Any run of the project: ${missedLine(missing(null, 1))}` })
  })

  it('only a pause → "Expected runs paused", the pause line, no Action needed', () => {
    const model = calloutModel(null, configured([pausedItem()]), true)
    expect(model.show).toBe(true)
    expect(model.title).toBe(TITLE_PAUSED)
    expect(model.tiles).toEqual([{ label: MISSED_TILE, value: 0 }])
    expect(model.lines).toEqual([{ kind: 'text', text: 'nightly-regression: Paused until Fri Oct 30 (release freeze) — set by Alice Lead' }])
    expect(model.actionNeeded).toBe(false)
  })

  it('a miss on a paused cadence is not Action needed; a configured cadence without misses adds no line', () => {
    const missedThenPaused = { ...pausedItem(), missed: 1, expected: 3, met: 2 }
    expect(calloutModel(gap(true), configured([missedThenPaused]), true).actionNeeded).toBe(false)
    const quiet = calloutModel(gap(false), configured([missing('api', 0)]), true)
    expect(quiet).toMatchObject({ title: TITLE_GAP, lines: [], actionNeeded: false })
    expect(quiet.tiles).toEqual([{ label: SILENT_DAYS, value: 9 }, { label: MISSED_TILE, value: 0 }])
  })

  it('the pause line names the day in the app\'s one date format and drops what it does not know', () => {
    // vitest runs in America/Chicago (vitest.config.ts): 22:59Z on Fri 30 Oct is that Friday there.
    expect(dayLabel('2026-10-30T22:59:00Z')).toBe('Fri Oct 30')
    expect(dayLabel('not a date')).toBe('')
    const bare = { ...pausedItem(), cadence: { ...pausedItem().cadence, paused_reason: null, paused_by_name: null } }
    expect(pausedLine(bare)).toBe('nightly-regression: Paused until Fri Oct 30')
  })
})

describe('coverageCadenceRows', () => {
  it('no cadence: the measured no-runs row and the Configure line; nothing inferred', () => {
    const rows = coverageCadenceRows(cadenceStatus('none'), 14, 0, true)
    expect(rows).toEqual([
      { tone: 'warn', line: { kind: 'text', text: 'No runs in the last 14 days' } },
      { tone: 'info', line: configureLine(true) },
    ])
    expect(noRunsSentence(30)).toBe('No runs in the last 30 days')
    expect(noRunsSentence(1)).toBe('No runs in the last 24 hours')
    const text = JSON.stringify(coverageCadenceRows(cadenceStatus('none'), 14, 3, false))
    expect(text).not.toMatch(/nightly schedule|may be paused|below 50/)
  })

  it('configured: at most two miss rows, then "and n more", then each pause', () => {
    const items = [missing('a', 1), missing('b', 2), missing('c', 1), missing('d', 3), pausedItem()]
    const rows = coverageCadenceRows(configured(items), 14, 10, true)
    expect(rows.map((r) => r.line.kind === 'text' ? r.line.text : '')).toEqual([
      `a: ${missedLine(items[0])}`,
      `b: ${missedLine(items[1])}`,
      'and 2 more cadences with missed runs',
      'nightly-regression: Expected runs paused until Fri Oct 30',
    ])
    expect(rows.map((r) => r.tone)).toEqual(['warn', 'warn', 'warn', 'info'])
  })

  it('a status not read yet adds nothing; a failed one says so', () => {
    expect(coverageCadenceRows(null, 14, 5)).toEqual([])
    expect(coverageCadenceRows(null, 14, 5, true, true)).toEqual([{ tone: 'info', line: { kind: 'text', text: CADENCE_LOAD_ERROR } }])
  })
})

describe('cadenceDimension', () => {
  it('is { expected, met } only for a configured cadence that expected a run', () => {
    expect(cadenceDimension(configured([missing('a', 1)]))).toEqual({ expected: 5, met: 4 })
    expect(cadenceDimension(cadenceStatus('none'))).toBeNull()
    expect(cadenceDimension(configured([pausedItem()]))).toBeNull()
    expect(cadenceDimension(null)).toBeNull()
    expect(cadenceDetail({ expected: 5, met: 4 })).toBe('4 of 5 expected runs')
    expect(cadenceDetail({ expected: 1, met: 0 })).toBe('0 of 1 expected run')
    expect(daysWithRunsDetail(7, 14)).toBe('7 of 14 days')
    expect(daysWithRunsDetail(1, 1)).toBe('1 of 1 day')
    expect([RUN_CADENCE_LABEL, DAYS_WITH_RUNS_LABEL]).toEqual(['Run cadence', 'Days with runs'])
  })
})
