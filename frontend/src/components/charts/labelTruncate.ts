/**
 * Shortening a category label to fit: by characters (`middleTruncate`) and by
 * measured width (`fitCategoryLabel`).
 *
 * A leaf module, apart from `BarChart.model.ts` on purpose: the stacked column
 * chart (in the frame chunk every chart page loads) cuts its labels the same
 * way, and importing the cut from the bar model placed that whole model — and,
 * through its percent rounding, the chart registry — in the shared chunk, so a
 * flag-off page downloaded code only a catalogue section runs (Wave 2.6 R1-3).
 * `BarChart.model` re-exports everything here for its callers.
 */

/** Longest drawn bar label, before middle truncation. */
export const MAX_BAR_LABEL = 34
/** The one character that marks a middle truncation. */
export const ELLIPSIS = '…'

/**
 * `text` shortened to `max` characters by cutting the MIDDLE out:
 * `tests.integration…retries_once`. Two tests in one class share a long
 * prefix and differ at the end, so an end-truncated label renames them both to
 * the same thing.
 */
export function middleTruncate(text: string, max = MAX_BAR_LABEL): string {
  // CODE POINTS, not UTF-16 units: `slice` on a surrogate pair leaves a lone
  // surrogate behind, which is not a character at all — the axis draws a
  // replacement glyph and the label is no longer the test's name.
  const points = [...text]
  if (points.length <= max) return text
  const head = Math.ceil((max - 1) / 2)
  const tail = max - 1 - head
  return `${points.slice(0, head).join('')}${ELLIPSIS}${tail > 0 ? points.slice(points.length - tail).join('') : ''}`
}

/** The shortest a fitted category label is cut to, in characters: its first letter, "…", its last. */
export const MIN_FITTED_LABEL = 3

/**
 * A horizontal-bar category label that FITS its axis, by measurement (Wave
 * 2.6 fix round 2, B0 finding 6).
 *
 * The axis is a fixed width, and a label was cut to a fixed number of
 * characters (34, or 13 on a compact axis). Characters are not pixels: in
 * DejaVu Sans on the Linux CI runner a 13-character label is wider than the
 * compact axis, so its head ran off the svg's left edge ("aymen…imeout"), and
 * Recharts, handed a label wider than the axis, wrapped it onto two lines that
 * overlapped the next row. So a label is kept as it was (`first`) when its
 * measured width fits `room`, and otherwise cut in the middle of the FULL name,
 * one character at a time, until it does: a label that already fitted is drawn
 * exactly as before.
 */
export function fitCategoryLabel(label: string, first: string, room: number, measure: (text: string) => number): string {
  if (measure(first) <= room) return first
  for (let chars = [...first].length - 1; chars > MIN_FITTED_LABEL; chars--) {
    const cut = middleTruncate(label, chars)
    if (measure(cut) <= room) return cut
  }
  return middleTruncate(label, MIN_FITTED_LABEL)
}
