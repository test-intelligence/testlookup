/**
 * W25a HYG-04 phase 1 — the cutover card's words (contract §4.1 / §4.4): ONE status line per history, the 7-day list
 * (the LAST run of a UTC date decides it; a date with no run is "Not run", never "No differences"), the inline
 * differences, the sentence of a refused "Compare now", and a label for every reason. The unit suite runs in
 * America/Chicago (west of UTC), so a local-time slip shows here as the wrong date.
 *
 * W25d HYG-04 (contract §4.1 / §4.4): the card's four cut-over states from the history builders — before the cutover
 * the view is EQUAL to the W25a model's output for the same input — the cut-over sentence with / without the streak
 * and the name, the 7 dates ending at the cut-over date, and the sentence of a refused cutover.
 */
import { describe, expect, it } from 'vitest'

import {
  parityCutOverHistory,
  parityDetail,
  parityHistory,
  parityHistoryOf,
  parityLegacyOn,
  parityNotWrittenHistory,
  parityReadyHistory,
  parityRun,
} from '@/test/suiteParityMocks'
import { BLOCKING_REASONS, EXPECTED_REASONS, REASON_LABEL } from '@/types/suiteParity'
import {
  cardView,
  cutOverByLine,
  cutoverRefusalSentence,
  cutOverSentence,
  cutoverState,
  dayRows,
  differencesNoun,
  formatUtcDay,
  formatUtcTime,
  PARITY_TEXT,
  queueRefusalSentence,
  restartLine,
  sampleView,
  statusLine,
} from './suiteParityModel'

describe('formatting', () => {
  it('days and times are UTC with fixed English names', () => {
    expect(formatUtcDay('2026-10-31T06:15:00Z')).toBe('Sat 31 Oct')
    expect(formatUtcDay('2026-09-18T00:30:00Z')).toBe('Fri 18 Sep')   // 19:30 on the 17th in Chicago
    expect(formatUtcTime('2026-09-18T06:15:00Z')).toBe('06:15')
  })

  it('a count of 1 reads in the singular', () => {
    expect(differencesNoun(1)).toBe('1 difference')
    expect(differencesNoun(1234)).toBe('1,234 differences')
    expect(differencesNoun(0)).toBe('0 differences')
  })
})

describe('statusLine', () => {
  it('no runs: "No comparison has run yet." — never "0 differences"', () => {
    const line = statusLine(parityHistory({ newest: 'none', now: new Date('2026-09-18T12:00:00Z') }))
    expect(line).toEqual({ kind: 'none', text: 'No comparison has run yet.', detail: null })
  })

  it('the newest run still running: "Comparing… (started 06:15)"', () => {
    const line = statusLine(parityHistory({ streak: 3, newest: 'running', now: new Date('2026-09-18T12:00:00Z') }))
    expect(line).toEqual({ kind: 'running', text: 'Comparing… (started 06:15)', detail: null })
  })

  it('a streak of 3: "Day 3 of 7 with no differences."; 1: "Day 1 of 7"', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    expect(statusLine(parityHistory({ streak: 3, now })).text).toBe('Day 3 of 7 with no differences.')
    expect(statusLine(parityHistory({ streak: 1, now })).text).toBe('Day 1 of 7 with no differences.')
    expect(statusLine(parityHistory({ streak: 3, now })).detail).toBeNull()
  })

  it('7 clean days (and only when cutover_ready): "Ready for the cutover: 7 clean days in a row."', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    const ready = parityHistory({ streak: 7, now })
    expect(ready.cutover_ready).toBe(true)
    expect(statusLine(ready)).toEqual({ kind: 'ready', text: 'Ready for the cutover: 7 clean days in a row.', detail: null })
    const six = parityHistory({ streak: 6, now })
    expect(six.cutover_ready).toBe(false)
    expect(statusLine(six).text).toBe('Day 6 of 7 with no differences.')
  })

  it('the newest finished run found differences: the count (singular for 1) and the restart line', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    expect(statusLine(parityHistory({ streak: 2, newest: 'diffs', blocking: 1, now }))).toEqual({
      kind: 'diffs', text: 'The last comparison found 1 difference.', detail: 'The count restarts at the next clean day.',
    })
    expect(statusLine(parityHistory({ streak: 2, newest: 'diffs', blocking: 1234, now })).text)
      .toBe('The last comparison found 1,234 differences.')
  })

  it('the newest run failed: "The last comparison did not finish." and the restart line (with a TYPE when known)', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    expect(statusLine(parityHistory({ streak: 5, newest: 'failed', now }))).toEqual({
      kind: 'failed', text: 'The last comparison did not finish.', detail: 'The count restarts at the next clean day.',
    })
    expect(restartLine('TimeoutError')).toBe('The count restarts at the next clean day (TimeoutError).')
  })

  it('a clean run followed by a missed day breaks the streak: "has not run since …"', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    const history = parityHistoryOf([{ daysAgo: 2, status: 'clean' }, { daysAgo: 3, status: 'clean' }], { now })
    expect(history.streak_days).toBe(0)
    expect(statusLine(history)).toEqual({
      kind: 'stale', text: 'The daily comparison has not run since Wed 16 Sep.', detail: 'The count restarts at the next clean day.',
    })
  })
})

describe('dayRows', () => {
  it('the visual history: 7 UTC dates newest first, "Not run" for the dates that never ran, "12 differences" plural', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    const rows = dayRows(parityHistory({ streak: 4, now }), now)
    expect(rows.map((r) => [r.label, r.result])).toEqual([
      ['Fri 18 Sep', 'No differences · 338 expected'],
      ['Thu 17 Sep', 'No differences · 341 expected'],
      ['Wed 16 Sep', 'No differences · 339 expected'],
      ['Tue 15 Sep', 'No differences · 342 expected'],
      ['Mon 14 Sep', '12 differences · 340 expected'],
      ['Sun 13 Sep', 'Not run'],
      ['Sat 12 Sep', 'Not run'],
    ])
    expect(rows[4].kind).toBe('diffs')
    expect(rows[4].run?.id).toBeTruthy()
    expect(rows.slice(5).every((r) => r.kind === 'not_run' && r.run === null)).toBe(true)
  })

  it('a missing date in the middle is "Not run" — absence is not health', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    const rows = dayRows(parityHistoryOf([{ daysAgo: 0, status: 'clean', expected: 0 }, { daysAgo: 2, status: 'clean' }], { now }), now)
    expect(rows[0].result).toBe('No differences')
    expect(rows[1]).toMatchObject({ date: '2026-09-17', kind: 'not_run', result: 'Not run' })
    expect(rows[2].kind).toBe('clean')
  })

  it('today before its beat reads "Not run yet"; a running run "Comparing…"; a failed one "Did not finish"', () => {
    const now = new Date('2026-09-18T03:00:00Z')   // 22:00 on the 17th in Chicago: today is still the 18th (UTC)
    const rows = dayRows(parityHistoryOf([{ daysAgo: 1, status: 'failed' }, { daysAgo: 2, status: 'running' }], { now }), now)
    expect(rows[0]).toMatchObject({ date: '2026-09-18', kind: 'not_run_yet', result: 'Not run yet' })
    expect(rows[1]).toMatchObject({ date: '2026-09-17', kind: 'failed', result: 'Did not finish' })
    expect(rows[2]).toMatchObject({ kind: 'running', result: 'Comparing…' })
  })

  it('an admin run, then a later beat run on one date: the LATER one decides the date (either order in the list)', () => {
    const now = new Date('2026-09-18T12:00:00Z')
    const admin = parityRun({ daysAgo: 1, status: 'diffs', trigger: 'admin', hour: 4, minute: 0 }, now)
    const beat = parityRun({ daysAgo: 1, status: 'clean', expected: 7 }, now)
    for (const runs of [[beat, admin], [admin, beat]]) {
      const row = dayRows({ runs, streak_days: 1, cutover_ready: false }, now)[1]
      expect(row).toMatchObject({ kind: 'clean', result: 'No differences · 7 expected' })
      expect(row.run?.id).toBe(beat.id)
    }
    const later = parityRun({ daysAgo: 1, status: 'diffs', blocking: 1, trigger: 'admin', hour: 9, minute: 30 }, now)
    expect(dayRows({ runs: [later, beat], streak_days: 0, cutover_ready: false }, now)[1].result).toBe('1 difference · 340 expected')
  })
})

describe('sampleView', () => {
  it('the blocking rows only, as labels; the counts per reason, the expected ones as information', () => {
    const view = sampleView(parityDetail(parityRun({ daysAgo: 4, status: 'diffs' }, new Date('2026-09-18T12:00:00Z'))))
    expect(view.rows).toHaveLength(12)
    expect(view.rows[0]).toMatchObject({ reason: 'Missing from the catalog', project: 'Payment Service', suite: 'Checkout Regression', test: 'test_checkout_pays_with_voucher_1' })
    expect(view.rows[11].reason).toBe('Name differs')
    expect(view.moreLine).toBeNull()
    expect(view.blockingLine).toBe('By reason: Missing from the catalog 10 · Name differs 2.')
    expect(view.expectedLine).toBe('Expected, not counted: Absent from one run only 300 · Moved to another suite 40.')
  })

  it('the first 50 rows, then how many of the run’s differences are not listed', () => {
    const run = parityRun({ daysAgo: 0, status: 'diffs' }, new Date('2026-09-18T12:00:00Z'))
    const detail = parityDetail(run, { blocking: { missing_in_canonical: 1000, retire_pending: 200, name_drift: 34 }, expected: {} })
    const view = sampleView(detail)
    expect(view.rows).toHaveLength(50)                    // of 60 sampled (20 per reason)
    expect(view.moreLine).toBe('… and 1,184 more in this comparison.')
    expect(view.expectedLine).toBeNull()
  })

  it('a project deleted since reads "Deleted project"; a nameless test its fingerprint', () => {
    const detail = parityDetail(parityRun({ daysAgo: 0, status: 'diffs' }), { blocking: { retired_but_seen: 1 }, expected: {} })
    detail.sample[0] = { ...detail.sample[0], project_name: null, test_name: null }
    expect(sampleView(detail).rows[0]).toMatchObject({ reason: 'Retired, but ran recently', project: PARITY_TEXT.deletedProject, test: 'fp-retired_but_seen-1' })
  })
})

describe('queueRefusalSentence', () => {
  const refusal = (status: number, detail?: unknown) =>
    Object.assign(new Error(`Request failed with status code ${status}`), { isAxiosError: true, response: { status, data: detail === undefined ? {} : { detail } } })

  it("reads the server's sentence — never 'Request failed with status code …'", () => {
    expect(queueRefusalSentence(refusal(409, 'A comparison is already running.'))).toBe('A comparison is already running.')
    expect(queueRefusalSentence(refusal(503, 'Could not queue the comparison.'))).toBe('Could not queue the comparison.')
  })

  it('without a body: the same sentences by status; a network error: could not queue', () => {
    expect(queueRefusalSentence(refusal(409))).toBe(PARITY_TEXT.alreadyRunning)
    expect(queueRefusalSentence(refusal(500))).toBe(PARITY_TEXT.couldNotQueue)
    expect(queueRefusalSentence(new Error('Network Error'))).toBe(PARITY_TEXT.couldNotQueue)
  })
})

describe('REASON_LABEL', () => {
  it('has a sentence for EVERY reason of types/suiteParity.ts, none longer than the one the column is sized for', () => {
    // The Reason column was measured under DejaVu for "Missing from the old tables" (27 characters, the longest).
    for (const reason of [...BLOCKING_REASONS, ...EXPECTED_REASONS]) {
      expect(REASON_LABEL[reason], reason).toMatch(/^[A-Z].+[^.]$/)
      expect(REASON_LABEL[reason].length, reason).toBeLessThanOrEqual('Missing from the old tables'.length)
    }
    expect(Object.keys(REASON_LABEL).sort()).toEqual([...BLOCKING_REASONS, ...EXPECTED_REASONS].sort())
  })
})

describe('W25d HYG-04: the cut-over states (cutoverState / cardView)', () => {
  const now = new Date('2026-09-18T12:00:00Z')
  const labelsAndResults = (rows: ReturnType<typeof dayRows>) => rows.map((r) => [r.label, r.result])

  it('before the cutover — no `cutover` (a W25a server), or legacy writes on and not ready — the view IS the W25a output', () => {
    const histories = [
      parityHistory({ streak: 4, now }),
      parityHistory({ streak: 2, newest: 'diffs', now }),
      parityHistory({ streak: 5, newest: 'failed', now }),
      parityHistory({ streak: 3, newest: 'running', now }),
      parityHistory({ newest: 'none', now }),
    ]
    for (const w25a of histories) {
      for (const history of [w25a, parityLegacyOn(w25a)]) {
        expect(cutoverState(history)).toBe('before')
        expect(cardView(history, now)).toEqual({
          state: 'before',
          line: statusLine(w25a),
          byLine: null,
          canCompare: true,
          canCutOver: false,
          days: dayRows(w25a, now),
          daysHeading: null,
        })
      }
    }
    // And literally, for the existing visual region's history ("Day 4 of 7", the diffs day, two dates never run).
    const view = cardView(parityHistory({ streak: 4, now }), now)
    expect(view.line).toEqual({ kind: 'streak', text: 'Day 4 of 7 with no differences.', detail: null })
    expect(labelsAndResults(view.days).slice(4)).toEqual([['Mon 14 Sep', '12 differences · 340 expected'], ['Sun 13 Sep', 'Not run'], ['Sat 12 Sep', 'Not run']])
  })

  it('a W25a server that says ready (no `cutover` object) keeps the W25a card: the ready line, no "Cut over now"', () => {
    const history = parityHistory({ streak: 7, now })
    expect(history.cutover).toBeUndefined()
    expect(cardView(history, now)).toMatchObject({ state: 'before', canCutOver: false, line: { kind: 'ready' } })
  })

  it('ready — legacy writes on and cutover_ready: "Cut over now" beside "Compare now", the W25a ready line, 7 clean days', () => {
    const view = cardView(parityReadyHistory({ now }), now)
    expect(view).toMatchObject({ state: 'ready', canCompare: true, canCutOver: true, byLine: null, daysHeading: null })
    expect(view.line).toEqual({ kind: 'ready', text: 'Ready for the cutover: 7 clean days in a row.', detail: null })
    expect(labelsAndResults(view.days)).toEqual([
      ['Fri 18 Sep', 'No differences · 338 expected'],
      ['Thu 17 Sep', 'No differences · 341 expected'],
      ['Wed 16 Sep', 'No differences · 339 expected'],
      ['Tue 15 Sep', 'No differences · 342 expected'],
      ['Mon 14 Sep', 'No differences · 340 expected'],
      ['Sun 13 Sep', 'No differences · 338 expected'],
      ['Sat 12 Sep', 'No differences · 341 expected'],
    ])
    // Legacy writes on but not ready (day 6): no button.
    const six = parityLegacyOn(parityHistory({ streak: 6, now }))
    expect(cardView(six, now)).toMatchObject({ state: 'before', canCutOver: false, line: { text: 'Day 6 of 7 with no differences.' } })
  })

  it('cut over: the sentence, the retired detail, who did it; no button; the heading; the 7 dates ending at the cut-over date', () => {
    const view = cardView(parityCutOverHistory({ now }), now)
    expect(view.state).toBe('cut_over')
    expect(view.line).toEqual({
      kind: 'cut_over',
      text: 'Cut over on Fri 18 Sep after 7 clean days.',
      detail: 'The old tables are no longer written; Test Management reads the test catalog. The comparison is retired.',
    })
    expect(view).toMatchObject({ byLine: 'Cut over by admin.', canCompare: false, canCutOver: false, daysHeading: 'The 7 days before the cutover (UTC)' })
    expect(view.days.map((r) => r.kind)).toEqual(Array(7).fill('clean'))
    expect(view.days[0].label).toBe('Fri 18 Sep')
  })

  it('cut over 12 days ago: the list still ends at the cut-over date — the dates after it never read as missed days', () => {
    const later = new Date('2026-09-30T12:00:00Z')
    const view = cardView(parityCutOverHistory({ now: later, daysAgo: 12 }), later)
    expect(view.line.text).toBe('Cut over on Fri 18 Sep after 7 clean days.')
    expect(view.days.map((r) => r.label)).toEqual(['Fri 18 Sep', 'Thu 17 Sep', 'Wed 16 Sep', 'Tue 15 Sep', 'Mon 14 Sep', 'Sun 13 Sep', 'Sat 12 Sep'])
    expect(view.days.every((r) => r.kind === 'clean')).toBe(true)
  })

  it('cut over before that day’s comparison ran: the list ends the day before (the streak does not count it either)', () => {
    const history = parityCutOverHistory({ now, hour: 5, minute: 0, cleanDaysBefore: 8 })
    expect(history.cutover?.streak_days_at_cutover).toBe(7)
    const view = cardView(history, now)
    expect(view.days[0].label).toBe('Thu 17 Sep')
    expect(view.days.map((r) => r.kind)).toEqual(Array(7).fill('clean'))
  })

  it('the cut-over sentence without the streak, with 12 and with 1; the by-line only with a name', () => {
    const at = '2026-11-06T10:12:00Z'
    expect(cutOverSentence({ legacy_writes: false, cut_over_at: at, streak_days_at_cutover: null })).toBe('Cut over on Fri 6 Nov.')
    expect(cutOverSentence({ legacy_writes: false, cut_over_at: at })).toBe('Cut over on Fri 6 Nov.')
    expect(cutOverSentence({ legacy_writes: false, cut_over_at: '2026-11-26T09:00:00Z', streak_days_at_cutover: 12 })).toBe('Cut over on Thu 26 Nov after 12 clean days.')
    expect(cutOverSentence({ legacy_writes: false, cut_over_at: at, streak_days_at_cutover: 1 })).toBe('Cut over on Fri 6 Nov after 1 clean day.')
    expect(cutOverSentence({ legacy_writes: false, cut_over_at: '2026-09-18T00:30:00Z', streak_days_at_cutover: 7 })).toBe('Cut over on Fri 18 Sep after 7 clean days.')   // 19:30 on the 17th in Chicago
    expect(cutOverByLine('Avery Admin')).toBe('Cut over by Avery Admin.')
    expect(cutOverByLine('  ')).toBeNull()
    expect(cutOverByLine(null)).toBeNull()
    expect(cardView(parityCutOverHistory({ now, byName: null, streakAtCutover: null }), now)).toMatchObject({
      byLine: null,
      line: { text: 'Cut over on Fri 18 Sep.' },
    })
  })

  it('not written — legacy writes off and no time (or an unreadable one): the loud line, no button, today’s list', () => {
    const history = parityNotWrittenHistory({ now })
    for (const h of [history, { ...history, cutover: { ...history.cutover, legacy_writes: false, cut_over_at: 'not a date' } }]) {
      expect(cutoverState(h)).toBe('not_written')
      const view = cardView(h, now)
      expect(view.line).toEqual({
        kind: 'not_written',
        text: 'The old suite-membership tables are not written.',
        detail: 'The cut-over switch suite_membership_legacy_writes is off or missing; the comparison is retired.',
      })
      expect(view).toMatchObject({ byLine: null, canCompare: false, canCutOver: false, daysHeading: null })
      expect(view.days).toEqual(dayRows(h, now))
      expect(view.line.text).not.toMatch(/Cut over on|clean days/)
    }
  })
})

describe('cutoverRefusalSentence', () => {
  const refusal = (status: number, detail?: unknown) =>
    Object.assign(new Error(`Request failed with status code ${status}`), { isAxiosError: true, response: { status, data: detail === undefined ? {} : { detail } } })

  it("reads the server's fixed sentence — never 'Request failed with status code 409'", () => {
    for (const sentence of [
      'Not ready: day 3 of 7 with no differences.',
      'Already cut over: the old suite-membership tables are not written.',
      'A comparison is running; cut over when it has finished.',
    ]) {
      expect(cutoverRefusalSentence(refusal(409, sentence))).toBe(sentence)
    }
  })

  it('without a body: "Not ready for the cutover." for a 409, "Could not cut over." for a 503 or a network error', () => {
    expect(cutoverRefusalSentence(refusal(409))).toBe('Not ready for the cutover.')
    expect(cutoverRefusalSentence(refusal(503))).toBe('Could not cut over.')
    expect(cutoverRefusalSentence(new Error('Network Error'))).toBe('Could not cut over.')
    expect(PARITY_TEXT.cutOverNotReady).toBe('Not ready for the cutover.')
  })
})
