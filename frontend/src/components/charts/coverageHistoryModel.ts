/**
 * W08 TRND-07: the pure model behind the Coverage History chart.
 *
 * The axis is every UTC day of the window the snapshots can cover: a snapshot
 * describes a FINISHED day, so the newest possible one is yesterday, and the
 * read asks `snapshot_day >= today - days`. A day with no snapshot is a GAP in
 * both series (`null`), never 0: the beat may not have run that night.
 *
 * Two series:
 *   - "Tests in the catalogue" (bars, the right axis): a count;
 *   - "Exercised in the last 30 days (%)" (a line, the left axis, 0-100): the
 *     share of the catalogue that executed in the 30 days ending that day.
 *     `null` when the catalogue was empty (a share of nothing is not 0 %).
 *
 * Timeline notes (TRND-04) are placed with the time-series kit's own
 * `placeNotes`, so the History chart and the Trends hero agree on what a note
 * covers.
 */
import type { SeriesChart } from '@/lib/viz/contracts'
import type { CoverageHistoryPoint } from '@/types/analytics'
import { placeNotes, utcDayRange, type NoteInput, type NoteMarker } from './timeSeriesModel'

export const CATALOGUE_LABEL = 'Tests in the catalogue'
export const EXERCISED_LABEL = 'Exercised in the last 30 days (%)'
export const NO_SNAPSHOT_REASON = 'no coverage snapshot was stored for this day'
export const EMPTY_CATALOGUE_REASON = 'the catalogue was empty that day, so no share is measured'

export interface CoverageHistoryRow {
  /** UTC day, `YYYY-MM-DD`. */
  x: string
  catalogue: number | null
  exercisedPct: number | null
  exercised: number | null
  suites: number | null
  /** A snapshot exists for the day. */
  stored: boolean
}

export interface CoverageHistoryModel {
  rows: CoverageHistoryRow[]
  notes: NoteMarker[]
  notesOutsideWindow: number
  /** Days of the axis with a snapshot. */
  stored: number
  /** The largest catalogue count drawn (0 when none). */
  largest: number
}

const DAY_MS = 86_400_000

const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10)

/** The window's axis: `days` UTC days ending yesterday (the newest day a snapshot can describe). */
export function historyAxis(days: number, todayIso: string): string[] {
  const today = Date.parse(`${todayIso.slice(0, 10)}T00:00:00Z`)
  const span = Math.max(1, Math.floor(Number.isFinite(days) ? days : 1))
  if (!Number.isFinite(today)) return []
  return utcDayRange(isoDay(today - span * DAY_MS), isoDay(today - DAY_MS))
}

export function buildCoverageHistoryModel({
  points,
  days,
  todayIso,
  notes = [],
}: {
  points: readonly CoverageHistoryPoint[]
  days: number
  todayIso: string
  notes?: readonly NoteInput[]
}): CoverageHistoryModel {
  const byDay = new Map(points.map((point) => [point.day.slice(0, 10), point]))
  const axis = historyAxis(days, todayIso)
  let stored = 0
  let largest = 0
  const rows = axis.map<CoverageHistoryRow>((x) => {
    const point = byDay.get(x)
    if (!point) return { x, catalogue: null, exercisedPct: null, exercised: null, suites: null, stored: false }
    stored += 1
    if (point.catalogue_tests > largest) largest = point.catalogue_tests
    return {
      x,
      catalogue: point.catalogue_tests,
      exercisedPct: point.catalogue_tests > 0 ? point.exercised_pct : null,
      exercised: point.exercised_tests,
      suites: point.suites,
      stored: true,
    }
  })
  const placed = placeNotes(notes, axis)
  return { rows, notes: placed.notes, notesOutsideWindow: placed.outsideWindow, stored, largest }
}

/** The model as a C3 series, for the frame's summary, table view and CSV (a gap is "—" there too). */
export function coverageHistoryToChartSeries(model: CoverageHistoryModel): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      {
        key: 'catalogue_tests',
        label: CATALOGUE_LABEL,
        points: model.rows.map((row) =>
          row.catalogue === null
            ? { x: row.x, y: null, n: 0, measured: false, reason: NO_SNAPSHOT_REASON }
            : { x: row.x, y: row.catalogue, n: row.catalogue },
        ),
      },
      {
        key: 'exercised_pct',
        label: EXERCISED_LABEL,
        points: model.rows.map((row) =>
          row.exercisedPct === null
            ? {
                x: row.x,
                y: null,
                n: row.catalogue ?? 0,
                measured: false,
                reason: row.stored ? EMPTY_CATALOGUE_REASON : NO_SNAPSHOT_REASON,
              }
            : { x: row.x, y: row.exercisedPct, n: row.catalogue ?? 0 },
        ),
      },
    ],
  }
}
