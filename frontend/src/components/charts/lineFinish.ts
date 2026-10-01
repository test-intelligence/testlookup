/**
 * A multi-series line's derived facts (gaps, past-range days, isolated points,
 * where the direct label points) and its dash: `finishLine`.
 *
 * A leaf module, apart from `multiSeriesModel.ts` on purpose: the VIZ-407 zoom
 * (in the time-series chunk Trends and Suite detail load with the flag off)
 * re-finishes a SLICE of a built line, and importing `finishLine` from the
 * model placed the whole model — and, through its release alignment,
 * `seriesAlignment` — in that shared chunk, so a flag-off page downloaded code
 * only a catalogue section runs (Wave 2.6 R1-3). `multiSeriesModel` re-exports
 * everything here for its callers. Type imports only (erased), so it stays a
 * leaf, and free of `@/` imports for the Playwright specs that read it in Node.
 */
import type { MultiSeriesLine, WorkingLine } from './multiSeriesModel'

/**
 * One `stroke-dasharray` per series slot (index 7 is "Other"). Every line
 * differs from every other by its dash as well as its hue — the categorical
 * hues are not all distinguishable to every reader.
 */
export const SERIES_DASHES: readonly (string | undefined)[] = [
  undefined,
  '9 4',
  '2 3',
  '10 3 2 3',
  '5 5',
  // Paired dashes. It was dash-dot-dot (`14 4 2 4 2 4`), which read as the
  // dash-dot of slot 3 wherever the two hues were equally light (lab).
  '6 2 6 10',
  '16 6',
  '1 4',
]

/** Executions over `points`; a non-finite count adds nothing. */
export const volumeOf = (points: readonly { n: number }[]) =>
  points.reduce((sum, p) => sum + (Number.isFinite(p.n) ? p.n : 0), 0)

/**
 * A line's derived facts (gaps, past-range days, isolated points, where the
 * direct label points) from its points. Exported for the VIZ-407 zoom, which
 * re-derives them over a SLICE of an already-built line rather than restating
 * the rules — the fold and the colours are never recomputed from a slice.
 */
export function finishLine(line: WorkingLine, styleIndex: number): MultiSeriesLine {
  let gaps = 0
  let pastRange = 0
  let isolated = false
  let last: MultiSeriesLine['last'] = null
  let lastPartial: MultiSeriesLine['last'] = null
  const { points } = line
  for (let i = 0; i < points.length; i++) {
    const y = points[i].y
    if (y === null) {
      if (points[i].pastRange) pastRange += 1
      else gaps += 1
      continue
    }
    // A still-filling day is no place to name a line: its value is still moving.
    if (points[i].partial) lastPartial = { index: i, y }
    else last = { index: i, y }
    const before = i > 0 ? points[i - 1].y : null
    const after = i < points.length - 1 ? points[i + 1].y : null
    if (before === null && after === null) isolated = true
  }
  return {
    key: line.key,
    label: line.label,
    other: line.other,
    styleIndex,
    dash: SERIES_DASHES[styleIndex],
    points,
    volume: volumeOf(points),
    gaps,
    pastRange,
    isolated,
    last: last ?? lastPartial,
  }
}
