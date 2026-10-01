/**
 * Where the release markers' labels go over a time-series plot (Wave 2.6
 * R2-10).
 *
 * Each marker is a dashed vertical line with the release name centred above
 * the plot. Two releases a week apart on a 375 px page are ~45 px apart, and
 * "2026.09" and "2026.10" were drawn edge to edge — read as one string,
 * "2026.092026.10". The pass here, left to right over the markers in axis
 * order, puts each label on the first of two rows where it clears the label
 * before it by `MARKER_LABEL_GAP`:
 *
 *   - row 0 is where every label has always been (just above the plot), so a
 *     chart whose labels never touched draws exactly what it drew before;
 *   - row 1 is one line higher, for a label that would touch its neighbour;
 *   - a label that fits neither row is DROPPED, and so is a raised one whose
 *     own marker line would run up under a neighbour's label (the line would
 *     then point at the wrong name). The day's tooltip and the release table
 *     still name a dropped release, and the chart says one is unlabelled.
 *
 * Widths are MEASURED (`textMeasure.ts`) in the font the label is drawn in,
 * so the pass holds on a Linux runner's DejaVu Sans as on Segoe UI. Pure.
 */

/** The labels' size, px: what the plot has always drawn them at. */
export const MARKER_LABEL_FONT_SIZE = 10
/** One row's height, px: a raised label sits this much above the first row. */
export const MARKER_LABEL_ROW_HEIGHT = 12
/** The least clear space between two labels on a row, px. Less reads as one string. */
export const MARKER_LABEL_GAP = 4
/** Clearance kept between a raised label's marker line and a first-row label, px. */
const LINE_CLEARANCE = 2

/** 0: the usual row; 1: one row up; `null`: not drawn. */
export type MarkerLabelRow = 0 | 1 | null

export interface MarkerLabelItem {
  /** The marker line's x, px. */
  x: number
  /** The label's measured width, px. */
  width: number
}

/** A marker's label: every release that lands on its day. */
export function markerLabelText(marker: { names: readonly string[] }): string {
  return marker.names.join(', ')
}

/** The row of each label, index-aligned with `items` (in axis order). */
export function layoutMarkerLabels(items: readonly MarkerLabelItem[]): MarkerLabelRow[] {
  const rows: MarkerLabelRow[] = []
  const rowRight = [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY]
  for (const item of items) {
    const left = item.x - item.width / 2
    const right = item.x + item.width / 2
    if (left >= rowRight[0] + MARKER_LABEL_GAP) {
      rows.push(0)
      rowRight[0] = right
    } else if (left >= rowRight[1] + MARKER_LABEL_GAP) {
      rows.push(1)
      rowRight[1] = right
    } else {
      rows.push(null)
    }
  }
  // A raised label's line runs up through the first row: under a label there,
  // it would lead the eye to the wrong name. Such a label is dropped.
  const firstRow = items.filter((_, i) => rows[i] === 0)
  return rows.map((row, i) => {
    if (row !== 1) return row
    const { x } = items[i]
    const covered = firstRow.some((other) => Math.abs(x - other.x) <= other.width / 2 + LINE_CLEARANCE)
    return covered ? null : row
  })
}
