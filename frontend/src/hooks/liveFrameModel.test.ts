/**
 * W25e HYG-06 E-3 (T-C1): the live frame model.
 *
 * The server folds every event of one interval into a `live_batch` (one entry
 * per run and event type, last-update order, the latest absolute counters,
 * the results it listed + a count of those it did not). The model must turn a
 * batch back into one feed row per listed result plus an explicit "N more
 * results not listed" row, and merge it into the sessions table with the same
 * end state as applying every original event one by one with the hook's
 * pre-W25e logic (kept below, verbatim, as the reference).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LiveSessionState } from '@/types/live-stream'
import {
  LIVE_BATCH_FRAME,
  LIVE_COALESCED_EVENT_TYPES,
  LIVE_RECONCILE_FRAME,
  type LiveBatchEntry,
  type LiveBatchFrame,
} from '@/types/liveFrames'
import {
  LIVE_RESULTS_OMITTED,
  applyLiveUpdates,
  expandLiveFrame,
  omittedResultsSentence,
} from './liveFrameModel'
import type { LiveEvent } from './useLiveExecution'

const NOW = 1_780_000_000_000
const RUN_A = 'zz-run-alpha'
const RUN_B = 'zz-run-bravo'
const RUN_C = 'zz-run-charlie'

/** The hook's three session branches before W25e (useLiveExecution.ts:232-284 at aa7e272a), one event at a time. */
function legacyApply(prev: LiveSessionState[], event: LiveEvent): LiveSessionState[] {
  const type = event.type
  if (type === 'live_run_started') {
    const exists = prev.some(s => s.run_id === event.run_id)
    if (exists) return prev
    return [
      {
        run_id: event.run_id ?? '',
        project_id: event.project_id ?? '',
        build_number: event.build_number ?? '',
        status: 'running',
        total: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
        pass_rate: 0,
      },
      ...prev,
    ]
  } else if (type === 'live_test_result' && event.run_id) {
    return prev.map(s =>
      s.run_id === event.run_id
        ? {
            ...s,
            passed:     event.passed  ?? s.passed,
            failed:     event.failed  ?? s.failed,
            skipped:    event.skipped ?? s.skipped,
            broken:     event.broken  ?? s.broken,
            total:      event.total   ?? s.total,
            pass_rate:  event.pass_rate ?? s.pass_rate,
            current_test: event.last_test ?? s.current_test,
            status: 'running',
          }
        : s,
    )
  } else if (type === 'live_run_complete' && event.run_id) {
    return prev.map(s =>
      s.run_id === event.run_id
        ? {
            ...s,
            status: 'completed',
            completed_at: s.completed_at ?? new Date().toISOString(),
            pass_rate: event.pass_rate ?? s.pass_rate,
          }
        : s,
    )
  }
  return prev
}

/** What the server's pacer does to one interval's events (live_frames.py P-1, P-2, P-6, P-7). */
function foldIntoBatch(events: LiveBatchEntry[]): LiveBatchFrame {
  const coalesced: readonly string[] = LIVE_COALESCED_EVENT_TYPES
  const entries = new Map<string, LiveBatchEntry>()
  events.forEach((event, i) => {
    const folds = coalesced.includes(event.type) && typeof event.run_id === 'string'
    const key = folds ? `${event.type}|${event.run_id}` : `own|${i}`
    const prev = entries.get(key)
    entries.delete(key) // an updated entry moves to the end (last-update order)
    if (!folds) {
      entries.set(key, event)
      return
    }
    const entry: LiveBatchEntry = { ...event, coalesced: (prev?.coalesced ?? 0) + 1 }
    if (event.type === 'live_test_result') {
      entry.results = [
        ...(prev?.results ?? []),
        { test_name: String(event.last_test), status: String(event.last_status), sequence_id: event.sequence_id },
      ]
      entry.results_dropped = 0
    }
    entries.set(key, entry)
  })
  const list = [...entries.values()]
  const listed = list.reduce((n, e) => n + (e.results?.length ?? 0), 0)
  return {
    type: LIVE_BATCH_FRAME,
    protocol: 2,
    sequence_id: String(list[list.length - 1]?.sequence_id),
    events: list,
    counts: { events: events.length, entries: list.length, results_listed: listed, results_dropped: 0 },
  }
}

function result(runId: string, seq: number, lastTest: string, lastStatus: string, counts: Partial<LiveEvent>): LiveBatchEntry {
  return {
    type: 'live_test_result', run_id: runId, project_id: 'zz-project', sequence_id: `1730460000${seq}-0`,
    last_test: lastTest, last_status: lastStatus, status: 'running', ...counts,
  }
}

/** A fixed 3-run interval: two runs start, results interleave, a warning repeats, run A completes. */
function threeRunSample(): LiveBatchEntry[] {
  return [
    { type: 'live_run_started', run_id: RUN_A, project_id: 'zz-project', build_number: 'b-1', sequence_id: '1730460000101-0' },
    result(RUN_A, 102, 'test_a1', 'PASSED', { passed: 1, failed: 0, skipped: 0, broken: 0, total: 1, pass_rate: 100 }),
    { type: 'live_run_started', run_id: RUN_B, project_id: 'zz-project', build_number: 'b-2', sequence_id: '1730460000103-0' },
    result(RUN_B, 104, 'test_b1', 'FAILED', { passed: 0, failed: 1, skipped: 0, broken: 0, total: 1, pass_rate: 0 }),
    result(RUN_A, 105, 'test_a2', 'FAILED', { passed: 1, failed: 1, skipped: 0, broken: 0, total: 2, pass_rate: 50 }),
    { type: 'live_warning', run_id: RUN_A, message: 'High failure rate: 50% of tests failing', sequence_id: '1730460000106-0' },
    result(RUN_C, 107, 'test_c9', 'PASSED', { passed: 9, failed: 0, skipped: 1, broken: 0, total: 10, pass_rate: 100 }),
    result(RUN_A, 108, 'test_a3', 'BROKEN', { passed: 1, failed: 1, skipped: 0, broken: 1, total: 3, pass_rate: 33.33 }),
    { type: 'live_warning', run_id: RUN_A, message: 'High failure rate: 67% of tests failing', sequence_id: '1730460000109-0' },
    { type: 'live_run_complete', run_id: RUN_A, project_id: 'zz-project', pass_rate: 33.33, sequence_id: '1730460000110-0' },
    result(RUN_B, 111, 'test_b2', 'PASSED', { passed: 1, failed: 1, skipped: 0, broken: 0, total: 2, pass_rate: 50 }),
    result(RUN_C, 112, 'test_c10', 'SKIPPED', { passed: 9, failed: 0, skipped: 2, broken: 0, total: 11, pass_rate: 100 }),
  ]
}

/** Run C was already on the table when the interval began. */
const INITIAL: LiveSessionState[] = [{
  run_id: RUN_C, project_id: 'zz-project', build_number: 'b-0', status: 'running',
  total: 8, passed: 8, failed: 0, skipped: 0, broken: 0, pass_rate: 100, started_at: '2026-11-01T10:00:00Z',
}]

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('omittedResultsSentence', () => {
  it('says one result in the singular and several in the plural', () => {
    expect(omittedResultsSentence(1)).toBe('1 more result not listed')
    expect(omittedResultsSentence(2)).toBe('2 more results not listed')
    expect(omittedResultsSentence(137)).toBe('137 more results not listed')
  })
})

describe('expandLiveFrame', () => {
  it('a batch entry with 3 listed results and 2 dropped → 3 "Test result" rows + 1 omitted row, 1 update', () => {
    const entry = {
      ...result(RUN_A, 140, 'test_c', 'BROKEN', { passed: 3, failed: 1, skipped: 0, broken: 1, total: 5, pass_rate: 60 }),
      coalesced: 5,
      results: [
        { test_name: 'test_a', status: 'PASSED', sequence_id: '1730460000120-0' },
        { test_name: 'test_b', status: 'FAILED', sequence_id: '1730460000130-0' },
        { test_name: 'test_c', status: 'BROKEN', sequence_id: '1730460000140-0' },
      ],
      results_dropped: 2,
    }
    const frame = {
      type: LIVE_BATCH_FRAME, protocol: 2, sequence_id: '1730460000140-0', events: [entry],
      counts: { events: 5, entries: 1, results_listed: 3, results_dropped: 2 },
    }

    const { feed, updates } = expandLiveFrame(frame, NOW)

    expect(feed).toHaveLength(4)
    expect(feed.slice(0, 3).map(e => [e.type, e.last_test, e.last_status, e.sequence_id])).toEqual([
      ['live_test_result', 'test_a', 'PASSED', '1730460000120-0'],
      ['live_test_result', 'test_b', 'FAILED', '1730460000130-0'],
      ['live_test_result', 'test_c', 'BROKEN', '1730460000140-0'],
    ])
    // Each row carries the entry's (latest) state and the frame's arrival time; no result list in the feed.
    for (const row of feed.slice(0, 3)) {
      expect(row).toMatchObject({ run_id: RUN_A, passed: 3, failed: 1, broken: 1, total: 5, timestamp: NOW })
      expect(row).not.toHaveProperty('results')
    }
    expect(feed[3]).toMatchObject({
      type: LIVE_RESULTS_OMITTED, run_id: RUN_A, omitted: 2, message: '2 more results not listed', timestamp: NOW,
    })
    expect(updates).toHaveLength(1)
    expect(updates[0]).toMatchObject({ type: 'live_test_result', run_id: RUN_A, last_test: 'test_c', passed: 3, coalesced: 5 })
  })

  it('a raw legacy live_test_result (an older server, or a lone event) → 1 row, 1 update, as before W25e', () => {
    const legacy = result(RUN_B, 150, 'test_x', 'FAILED', { passed: 4, failed: 1, total: 5, pass_rate: 80 })

    const { feed, updates } = expandLiveFrame(legacy, NOW)

    expect(feed).toEqual([{ ...legacy, timestamp: NOW }])
    expect(updates).toEqual([{ ...legacy, timestamp: NOW }])
  })

  it('an entry with nothing listed but some dropped → only the omitted row; a warning entry keeps its count', () => {
    const frame = {
      type: LIVE_BATCH_FRAME, protocol: 2, sequence_id: '1730460000170-0',
      events: [
        { ...result(RUN_C, 160, 'test_late', 'PASSED', { total: 40 }), coalesced: 7, results: [], results_dropped: 7 },
        { type: 'live_warning', run_id: RUN_C, message: 'High failure rate: 60% of tests failing', coalesced: 4, sequence_id: '1730460000170-0' },
      ],
      counts: { events: 11, entries: 2, results_listed: 0, results_dropped: 7 },
    }

    const { feed, updates } = expandLiveFrame(frame, NOW)

    expect(feed.map(e => e.type)).toEqual([LIVE_RESULTS_OMITTED, 'live_warning'])
    expect(feed[0].message).toBe('7 more results not listed')
    expect(feed[1].coalesced).toBe(4)
    expect(updates.map(e => e.type)).toEqual(['live_test_result', 'live_warning'])
  })

  it('a reconcile entry folded into a batch is an update (the hook re-reads), never a feed row', () => {
    const frame = {
      type: LIVE_BATCH_FRAME, protocol: 2, sequence_id: '1730460000180-0',
      events: [{ type: LIVE_RECONCILE_FRAME, sequence_id: '1730460000180-0', dropped: 30 }, 'not-an-entry', null],
      counts: { events: 31, entries: 1, results_listed: 0, results_dropped: 0 },
    }

    const { feed, updates } = expandLiveFrame(frame, NOW)

    expect(feed).toEqual([])
    expect(updates.map(e => e.type)).toEqual([LIVE_RECONCILE_FRAME])
  })

  it('the 3-run sample: every original result is a feed row, oldest first within its run', () => {
    const { feed } = expandLiveFrame(foldIntoBatch(threeRunSample()) as unknown as Record<string, unknown>, NOW)

    const rows = (runId: string) => feed.filter(e => e.type === 'live_test_result' && e.run_id === runId).map(e => e.last_test)
    expect(rows(RUN_A)).toEqual(['test_a1', 'test_a2', 'test_a3'])
    expect(rows(RUN_B)).toEqual(['test_b1', 'test_b2'])
    expect(rows(RUN_C)).toEqual(['test_c9', 'test_c10'])
    expect(feed.filter(e => e.type === LIVE_RESULTS_OMITTED)).toEqual([])
  })
})

describe('applyLiveUpdates', () => {
  it('a folded batch ends in the same sessions as applying every original event one by one (pre-W25e logic)', () => {
    const originals = threeRunSample()
    const expected = originals.reduce(
      (prev, event) => legacyApply(prev, { ...(event as unknown as LiveEvent), timestamp: NOW }),
      INITIAL,
    )

    const { updates } = expandLiveFrame(foldIntoBatch(originals) as unknown as Record<string, unknown>, NOW)
    expect(updates.length).toBeLessThan(originals.length) // it really was folded
    const { sessions, completed } = applyLiveUpdates(INITIAL, updates)

    expect(sessions).toEqual(expected)
    expect(completed).toBe(true)
    // Spot-check the end state the reference produced.
    const byRun = Object.fromEntries(sessions.map(s => [s.run_id, s]))
    expect(byRun[RUN_A]).toMatchObject({ status: 'completed', total: 3, broken: 1, pass_rate: 33.33, current_test: 'test_a3' })
    expect(byRun[RUN_A].completed_at).toBe(new Date(NOW).toISOString())
    expect(byRun[RUN_B]).toMatchObject({ status: 'running', total: 2, failed: 1, current_test: 'test_b2' })
    expect(byRun[RUN_C]).toMatchObject({ status: 'running', total: 11, skipped: 2, build_number: 'b-0' })
  })

  it('completed is true only when a live_run_complete was applied', () => {
    const withoutComplete = threeRunSample().filter(e => e.type !== 'live_run_complete')
    const { updates } = expandLiveFrame(foldIntoBatch(withoutComplete) as unknown as Record<string, unknown>, NOW)
    expect(applyLiveUpdates(INITIAL, updates).completed).toBe(false)

    const complete = { type: 'live_run_complete', run_id: RUN_C, pass_rate: 100, timestamp: NOW }
    expect(applyLiveUpdates(INITIAL, [complete]).completed).toBe(true)
    // A complete for a run not on the table still asks for a refetch (as before: mutate() ran regardless).
    expect(applyLiveUpdates([], [{ ...complete, run_id: 'zz-run-unknown' }]).completed).toBe(true)
    expect(applyLiveUpdates(INITIAL, [{ ...complete, run_id: undefined }]).completed).toBe(false)
  })

  it('no updates, or a start for a run already on the table, keeps the same array', () => {
    expect(applyLiveUpdates(INITIAL, []).sessions).toBe(INITIAL)
    const again = { type: 'live_run_started', run_id: RUN_C, timestamp: NOW }
    expect(applyLiveUpdates(INITIAL, [again]).sessions).toBe(INITIAL)
  })
})
