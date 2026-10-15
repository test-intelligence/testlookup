/**
 * EXEC-04: the waterfall's model — lane packing (by worker when the report
 * named one, else first-fit), offsets, truncation and the cases where a value
 * is missing (never drawn as 0, never a negative bar).
 */
import { describe, expect, it } from 'vitest'
import type { RunTimeline, RunTimelineItem } from '@/types/executionMetrics'
import {
  MAX_TIMELINE_LANES,
  buildExecutionTimelineModel,
  compareWorkerIds,
  packIntervals,
  timelineBarTipContent,
  vizStatusOf,
  type ExecutionTimelineModel,
} from './executionTimeline.model'
import { tooltipText } from './tooltip'

/** The model, which these cases expect to exist. */
function built(...args: Parameters<typeof buildExecutionTimelineModel>): ExecutionTimelineModel {
  const model = buildExecutionTimelineModel(...args)
  if (!model) throw new Error('expected a model')
  return model
}

const T0 = Date.parse('2026-10-14T10:00:00.000Z')
const iso = (ms: number) => new Date(T0 + ms).toISOString()

function item(id: string, start: number, end: number, extra: Partial<RunTimelineItem> = {}): RunTimelineItem {
  return {
    test_id: id,
    test_name: `test_${id}`,
    suite_name: 'checkout',
    status: 'PASSED',
    worker_id: null,
    started_at: iso(start),
    finished_at: iso(end),
    offset_ms: start,
    duration_ms: end - start,
    ...extra,
  }
}

function timeline(items: RunTimelineItem[], extra: Partial<RunTimeline> = {}): RunTimeline {
  const ends = items.map((i) => Date.parse(i.finished_at))
  return {
    measured: true,
    reason: null,
    limit: 2000,
    total: items.length,
    truncated: false,
    origin: iso(0),
    end: ends.length ? new Date(Math.max(...ends)).toISOString() : null,
    items,
    ...extra,
  }
}

describe('packIntervals (first-fit by start)', () => {
  it('puts overlapping intervals on separate rows and reuses a row once it is free', () => {
    const rows = packIntervals([
      { start: 0, end: 10 },
      { start: 5, end: 15 },
      { start: 10, end: 20 }, // touches the first: same row
      { start: 12, end: 14 },
    ])
    expect(rows).toEqual([0, 1, 0, 2])
  })

  it('answers in INPUT order whatever order the intervals come in', () => {
    expect(packIntervals([{ start: 10, end: 20 }, { start: 0, end: 10 }])).toEqual([0, 0])
    expect(packIntervals([{ start: 5, end: 15 }, { start: 0, end: 10 }])).toEqual([1, 0])
  })

  it('uses the fewest rows: the maximum number running at once', () => {
    // At t=5 three intervals run at once; nowhere more.
    const rows = packIntervals([
      { start: 0, end: 6 },
      { start: 1, end: 7 },
      { start: 4, end: 9 },
      { start: 7, end: 12 },
      { start: 8, end: 13 },
    ])
    expect(Math.max(...(rows as number[])) + 1).toBe(3)
  })

  it('returns null for an interval that would need a row past the cap', () => {
    expect(packIntervals([{ start: 0, end: 10 }, { start: 1, end: 9 }, { start: 2, end: 8 }], 2)).toEqual([0, 1, null])
  })
})

describe('buildExecutionTimelineModel — lanes', () => {
  it('one lane per worker, in natural worker order, bars at their offsets', () => {
    const model = buildExecutionTimelineModel(
      timeline([
        item('a', 0, 1500, { worker_id: 'gw10' }),
        item('b', 100, 2100, { worker_id: 'gw2' }),
        item('c', 1560, 2760, { worker_id: 'gw10', status: 'FAILED' }),
        item('d', 2140, 3240, { worker_id: 'gw2' }),
      ]),
    )
    expect(model).not.toBeNull()
    const m = model as ExecutionTimelineModel
    expect(m.mode).toBe('worker')
    expect(m.lanes.map((l) => l.label)).toEqual(['Worker gw2', 'Worker gw10'])
    expect(m.lanes.every((l) => !l.packed)).toBe(true)
    const byId = Object.fromEntries(m.bars.map((b) => [b.testId, b]))
    expect(byId.a).toMatchObject({ lane: 1, start: 0, end: 1500, spanMs: 1500, status: 'passed' })
    expect(byId.b).toMatchObject({ lane: 0, start: 100, end: 2100 })
    expect(byId.c).toMatchObject({ lane: 1, start: 1560, status: 'failed', rawStatus: 'FAILED' })
    expect(m.spanMs).toBe(3240)
    expect(m.notes).toEqual([])
    expect(m.shown).toBe(4)
  })

  it('a worker that ran overlapping tests gets an extra row of its own, and the chart says so', () => {
    const m = built(
      timeline([item('a', 0, 1000, { worker_id: '0' }), item('b', 500, 1500, { worker_id: '0' }), item('c', 0, 900, { worker_id: '1' })]),
    )
    expect(m.lanes.map((l) => l.label)).toEqual(['Worker 0', 'Worker 0 · 2', 'Worker 1'])
    expect(m.notes.some((n) => /1 worker ran overlapping tests/.test(n))).toBe(true)
  })

  it('no worker named: greedy-packed rows that are NOT workers, said in words', () => {
    const m = built(timeline([item('a', 0, 1000), item('b', 200, 800), item('c', 1000, 1400)]))
    expect(m.mode).toBe('packed')
    expect(m.lanes.map((l) => l.label)).toEqual(['Lane 1', 'Lane 2'])
    expect(m.lanes.every((l) => l.packed)).toBe(true)
    expect(m.bars.find((b) => b.testId === 'c')?.lane).toBe(0)
    expect(m.notes[0]).toMatch(/did not say which worker ran each test/)
    expect(m.notes[0]).toMatch(/not workers/)
  })

  it('mixed: worker lanes first, then packed "No worker" rows', () => {
    const m = built(timeline([item('a', 0, 1000, { worker_id: '1' }), item('b', 0, 1000), item('c', 100, 900)]))
    expect(m.mode).toBe('mixed')
    expect(m.lanes.map((l) => l.label)).toEqual(['Worker 1', 'No worker · 1', 'No worker · 2'])
    expect(m.notes[0]).toMatch(/Some tests did not name their worker/)
  })

  it('caps the rows and counts what it could not draw', () => {
    const m = built(timeline([item('a', 0, 10), item('b', 1, 9), item('c', 2, 8)]), { maxLanes: 2 })
    expect(m.lanes).toHaveLength(2)
    expect(m.shown).toBe(2)
    expect(m.overflow).toBe(1)
    expect(m.notes.some((n) => /1 test is not drawn: more than 2 rows/.test(n))).toBe(true)
  })

  it('worker lanes past the cap are not drawn, and counted', () => {
    const items = Array.from({ length: MAX_TIMELINE_LANES + 2 }, (_, i) => item(`t${i}`, 0, 100, { worker_id: String(i) }))
    const m = built(timeline(items))
    expect(m.lanes).toHaveLength(MAX_TIMELINE_LANES)
    expect(m.overflow).toBe(2)
  })
})

describe('buildExecutionTimelineModel — offsets and missing values', () => {
  it('without offset_ms, the offset is the start minus the origin', () => {
    const raw = item('a', 2500, 3000)
    const m = built(timeline([{ ...raw, offset_ms: undefined as unknown as number }]))
    expect(m.bars[0].start).toBe(2500)
    expect(m.bars[0].end).toBe(3000)
  })

  it('the bar is the clock (finish - start); a null duration is kept null, not 0', () => {
    const m = built(timeline([item('a', 0, 1200, { duration_ms: null })]))
    expect(m.bars[0].spanMs).toBe(1200)
    expect(m.bars[0].durationMs).toBeNull()
    const tip = tooltipText(timelineBarTipContent(m.bars[0], m.lanes[0]))
    expect(tip).toContain('Took: 1.2s')
    // No "Reported duration: 0ms" (or "—") for a duration the report did not carry.
    expect(tip).not.toMatch(/Reported duration/)
  })

  it('states the reported duration when it differs from the clock', () => {
    const m = built(timeline([item('a', 0, 2000, { duration_ms: 1600, worker_id: '3' })]))
    const tip = tooltipText(timelineBarTipContent(m.bars[0], m.lanes[0]))
    expect(tip).toContain('Took: 2.0s')
    expect(tip).toContain('Reported duration: 1.6s')
    expect(tip).toContain('Worker: 3')
  })

  it('a finish before the start (clock skew) is an instant, never a negative bar', () => {
    const raw = item('a', 1000, 1000)
    const m = built(timeline([{ ...raw, finished_at: iso(400), duration_ms: null }]))
    expect(m.bars[0].spanMs).toBe(0)
    expect(m.bars[0].end).toBe(1000)
  })

  it('unreadable timestamps: not drawn at 0, counted and said', () => {
    const bad = { ...item('bad', 0, 10), offset_ms: Number.NaN, started_at: 'not a time' }
    const m = built(timeline([item('ok', 0, 500), bad]))
    expect(m.bars.map((b) => b.testId)).toEqual(['ok'])
    expect(m.invalid).toBe(1)
    expect(m.notes.some((n) => /1 test has unreadable timestamps/.test(n))).toBe(true)
  })

  it('the span reaches the reported end even past the last bar', () => {
    const m = built(timeline([item('a', 0, 500)], { end: iso(9000) }))
    expect(m.spanMs).toBe(9000)
  })
})

describe('buildExecutionTimelineModel — truncation and unmeasured', () => {
  it('a cut list says it shows the longest N of the total', () => {
    const m = built(timeline([item('a', 0, 500), item('b', 0, 400)], { truncated: true, total: 2412, limit: 2 }))
    expect(m.truncated).toBe(true)
    expect(m.total).toBe(2412)
    expect(m.notes).toContain('Showing the 2 longest of 2,412 timed tests.')
  })

  it('measured:false, no items or nothing drawable is no model (the page shows the reason instead)', () => {
    expect(buildExecutionTimelineModel(timeline([], { measured: false, reason: 'report carried no timestamps' }))).toBeNull()
    expect(buildExecutionTimelineModel(timeline([]))).toBeNull()
    expect(buildExecutionTimelineModel(null)).toBeNull()
    const bad = { ...item('bad', 0, 10), offset_ms: Number.NaN, started_at: '' }
    expect(buildExecutionTimelineModel(timeline([bad]))).toBeNull()
  })
})

describe('helpers', () => {
  it('maps stored statuses onto the kit vocabulary', () => {
    expect(['PASSED', 'failed', 'BROKEN', 'SKIPPED', 'UNKNOWN', 'XFAIL', ''].map(vizStatusOf)).toEqual([
      'passed', 'failed', 'broken', 'skipped', 'unknown', 'unknown', 'unknown',
    ])
  })

  it('orders worker ids naturally', () => {
    expect(['gw10', 'gw2', 'gw1', '10', '9'].sort(compareWorkerIds)).toEqual(['9', '10', 'gw1', 'gw2', 'gw10'])
  })

  it('a hostile test name is tooltip DATA (its title), not markup', () => {
    const name = '<img src=x onerror=alert(1)>'
    const m = built(timeline([item('a', 0, 10, { test_name: name })]))
    expect(timelineBarTipContent(m.bars[0]).title).toBe(name)
  })
})
