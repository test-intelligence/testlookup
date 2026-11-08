/**
 * W39 STRAT-07: the "Observability evidence" layout model. Nothing is invented: an unreadable instant is listed
 * without a time (never placed at 0 / the axis start / now), a `null` sample is a gap (never 0), the axis covers every
 * drawn item, and there is no axis when nothing in a lane with evidence is timed.
 */
import { describe, expect, it } from 'vitest'
import { LANE_SOURCES } from '@/types/observabilityTimeline'
import { formatClock } from '@/utils/formatters'
import { epoch, evidenceTimeline, lane, mixedTimeline, T, timeline } from '@/test/observabilityTimelineMocks'
import {
  AXIS_TICKS_MAX,
  SINGLE_INSTANT_PAD_MS,
  SPAN_ROWS_MAX,
  axisFrom,
  buildEvidenceView,
  laneView,
  markText,
  parseInstant,
  xOf,
} from './evidenceLanes.model'

const ev = (at: string, end: string | null = null) => ({
  at, end, zone_assumed: false, title: `item ${at}`, detail: null, level: null, cluster_id: null, test_case_id: null,
  test_name: null, count: null,
})

describe('evidenceLanes.model — instants and the axis', () => {
  it('reads ISO instants and nothing else', () => {
    expect(parseInstant(T('10:00:00'))).toBe(Date.parse('2026-10-20T10:00:00Z'))
    expect(parseInstant('2026-10-20T10:00:00+00:00')).toBe(Date.parse('2026-10-20T10:00:00Z'))
    // The answer's instants are UTC: a zone-less one is read as UTC, never as local time (the suite runs in Chicago).
    expect(parseInstant('2026-10-20T10:00:05')).toBe(Date.parse('2026-10-20T10:00:05Z'))
    expect(parseInstant('2026-10-20T10:00:05.250')).toBe(Date.parse('2026-10-20T10:00:05.250Z'))
    for (const bad of ['', '   ', '?', '[REDACTED_PHONE]', 'not a time', null, undefined, 1760954405]) {
      expect(parseInstant(bad), String(bad)).toBeNull()
    }
  })

  it('ticks on whole steps inside the window, at most AXIS_TICKS_MAX of them', () => {
    const axis = axisFrom(Date.parse(T('09:58:00')), Date.parse(T('10:10:00')))
    expect(axis.ticks.length).toBeGreaterThan(1)
    expect(axis.ticks.length).toBeLessThanOrEqual(AXIS_TICKS_MAX)
    expect(axis.ticks.map((t) => new Date(t).toISOString().slice(11, 19))).toEqual(['10:00:00', '10:05:00', '10:10:00'])
    expect(xOf(axis, axis.startMs, 600)).toBe(0)
    expect(xOf(axis, axis.endMs, 600)).toBe(600)
  })

  it('pads a single instant (axis room, never an item time)', () => {
    const at = Date.parse(T('10:00:00'))
    const axis = axisFrom(at, at)
    expect(axis.startMs).toBe(at - SINGLE_INSTANT_PAD_MS)
    expect(axis.endMs).toBe(at + SINGLE_INSTANT_PAD_MS)
  })
})

describe('evidenceLanes.model — lanes', () => {
  it('an event whose instant cannot be read joins the untimed list as time_unreadable, never placed', () => {
    const view = laneView(lane('splunk_trace', 'evidence', { events: [ev('[REDACTED_PHONE]'), ev(T('10:03:05'))] }))
    expect(view.marks.map((m) => m.event.at)).toEqual([T('10:03:05')])
    expect(view.untimed).toEqual([
      { title: 'item [REDACTED_PHONE]', detail: null, test_case_id: null, test_name: null, reason: 'time_unreadable' },
    ])
    expect(view.timed).toBe(true)
  })

  it('an OCP first → last is a span; an end not after the start is an instant', () => {
    const view = laneView(lane('ocp_events', 'evidence', { events: [ev(T('10:01:00'), T('10:07:30')), ev(T('10:02:00'), T('10:02:00'))] }))
    expect(view.marks.map((m) => m.kind)).toEqual(['span', 'instant'])
    expect(view.marks[0].endMs - view.marks[0].startMs).toBe(390_000)
  })

  it('packs overlapping spans into rows, capped at SPAN_ROWS_MAX', () => {
    const events = Array.from({ length: SPAN_ROWS_MAX + 2 }, () => ev(T('10:00:00'), T('10:05:00')))
    const view = laneView(lane('ocp_events', 'evidence', { events }))
    expect(view.rows).toBe(SPAN_ROWS_MAX)
    expect(Math.max(...view.marks.map((m) => m.row))).toBe(SPAN_ROWS_MAX - 1)
    const apart = laneView(lane('ocp_events', 'evidence', { events: [ev(T('10:00:00'), T('10:01:00')), ev(T('10:02:00'), T('10:03:00'))] }))
    expect(apart.rows).toBe(1)
  })

  it('a null sample splits the line (a gap, never 0); a sample with no finite time is dropped', () => {
    const view = laneView(lane('prometheus', 'evidence', {
      series: [{
        cluster_id: null, label: 'p95', unit: 'seconds', sentence: 's', rose: false,
        points: [[epoch('10:00:00'), 1], [epoch('10:01:00'), null], [epoch('10:02:00'), 3], [Number.NaN, 9], [epoch('10:03:00'), 2]],
      }],
    }))
    const [series] = view.series
    expect(series.segments.map((s) => s.map(([, v]) => v))).toEqual([[1], [3, 2]])
    expect(series.drawn).toBe(3)
    expect([series.min, series.max]).toEqual([1, 3])
  })

  it('names the item, its level, detail, test, count and an assumed zone in its title', () => {
    const view = laneView(lane('splunk_per_test', 'evidence', {
      events: [{ ...ev('2026-10-20T10:04:10+00:00'), title: 'Connection refused', level: 'ERROR', detail: 'payments', test_name: 'test_pay', count: 3, zone_assumed: true }],
    }))
    // The page's clock (local time; the unit suite runs in America/Chicago, the e2e suites in UTC).
    const clock = formatClock(Date.parse('2026-10-20T10:04:10Z'))
    expect(markText(view.marks[0], 600_000)).toBe(
      `${clock} [ERROR] — Connection refused — payments — Test: test_pay — Seen 3× — No time zone was stored; read as UTC.`,
    )
    const span = laneView(lane('ocp_events', 'evidence', { events: [{ ...ev(T('10:01:00'), T('10:07:30')), title: 'BackOff' }] }))
    expect(markText(span.marks[0], 600_000)).toBe(
      `${formatClock(Date.parse(T('10:01:00')))} → ${formatClock(Date.parse(T('10:07:30')))} — BackOff`,
    )
  })
})

describe('evidenceLanes.model — the view', () => {
  it('evidence: five lanes in vocabulary order, the axis widened to every item and the run window', () => {
    const view = buildEvidenceView(evidenceTimeline())
    expect(view.lanes.map((l) => l.lane.source)).toEqual([...LANE_SOURCES])
    expect(view.axisState).toBe('timed')
    expect(view.axis?.startMs).toBe(Date.parse(T('09:58:00')))
    expect(view.axis?.endMs).toBe(Date.parse(T('10:10:00')))
    expect(view.runWindow).toEqual({ startMs: Date.parse(T('10:00:00')), endMs: Date.parse(T('10:09:00')) })
    const narrow = buildEvidenceView({ ...evidenceTimeline(), axis: { start: T('10:02:00'), end: T('10:03:00') } })
    expect(narrow.axis?.startMs).toBe(Date.parse(T('09:58:00')))
  })

  it('only untimed items: no axis, state "untimed"; nothing at all: "none"', () => {
    const untimedOnly = timeline({
      axis: { start: T('10:00:00'), end: T('10:09:00') },
      lanes: LANE_SOURCES.map((s) => s === 'ocp_per_test'
        ? lane(s, 'evidence', { untimed: [{ title: 'Pod: x', detail: null, test_case_id: null, test_name: null, reason: 'no_time_stored' }] })
        : lane(s, 'not_recorded')),
    })
    expect(buildEvidenceView(untimedOnly)).toMatchObject({ axis: null, axisState: 'untimed' })
    expect(buildEvidenceView(timeline())).toMatchObject({ axis: null, axisState: 'none' })
  })

  it('items on a lane that is not "evidence" never make an axis; an unknown source is never drawn', () => {
    const stray = timeline({
      lanes: [
        ...LANE_SOURCES.map((s) => (s === 'splunk_trace' ? lane(s, 'unavailable', { events: [ev(T('10:03:05'))] }) : lane(s, 'not_analysed'))),
        { ...lane('splunk_trace', 'evidence'), source: 'splunk' as never },
      ],
    })
    const view = buildEvidenceView(stray)
    expect(view.axis).toBeNull()
    expect(view.lanes).toHaveLength(5)
    expect(view.lanes.map((l) => l.lane.source)).not.toContain('splunk')
  })

  it('mixed honest states keep their order and their states', () => {
    const view = buildEvidenceView(mixedTimeline())
    expect(view.lanes.map((l) => l.lane.state)).toEqual(['none_found', 'unavailable', 'not_recorded', 'evidence', 'not_analysed'])
    expect(view.axisState).toBe('timed')
  })
})
