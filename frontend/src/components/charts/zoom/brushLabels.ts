/**
 * The words under the range brush's strip (Wave 2.6 R2-20), and whether they
 * fit its row. Kept out of the component module so it exports components only.
 */
import { measureTextWidth } from '../textMeasure'
import type { AxisWords, ZoomRange } from './zoomModel'

/** The gap between the labels under the strip (`gap-2`), px. */
const LABEL_GAP_PX = 8

/**
 * The label under the middle of the strip, in two lengths. The long one names
 * the range; the short one — for a row too narrow for the long one beside the
 * two end labels (a phone: "Showing all 14 / days" wrapped) — keeps the count,
 * which the handles' own positions and `aria-valuetext` do not say.
 */
export function brushSelectionText({
  xs,
  range,
  zoomed,
  pending,
  words,
}: {
  xs: readonly string[]
  /** The range shown (a drag's preview, or the committed one). */
  range: ZoomRange
  /** Zoomed, or a drag is being previewed. */
  zoomed: boolean
  /** The day a first click picked, waiting for its other end. */
  pending: number | null
  words: AxisWords
}): { long: string; short: string } {
  const count = xs.length.toLocaleString('en-US')
  if (pending !== null) {
    return { long: `From ${words.full(xs[pending])}: click the day the range ends`, short: `From ${words.short(xs[pending])}: click the end` }
  }
  if (zoomed) {
    const days = `${(range.end - range.start + 1).toLocaleString('en-US')} of ${count} days`
    return { long: `Showing ${words.range(xs[range.start], xs[range.end])} (${days})`, short: days }
  }
  return { long: `Showing all ${count} days`, short: `All ${count} days` }
}

/**
 * Whether the three labels under the strip fit its row on one line each, in
 * the font they are drawn in (`measureTextWidth`, so DejaVu on a Linux runner
 * is measured as DejaVu). `null` — no layout to measure — keeps the long label.
 */
export function brushLabelsFit(row: HTMLElement, texts: readonly string[], inset: { left: number; right: number }): boolean | null {
  const room = row.getBoundingClientRect().width - inset.left - inset.right
  if (!(room > 0)) return null
  const style = getComputedStyle(row)
  const font = `${style.fontWeight || '400'} ${style.fontSize || '12px'} ${style.fontFamily || 'sans-serif'}`
  let total = LABEL_GAP_PX * (texts.length - 1)
  for (const text of texts) {
    const width = measureTextWidth(text, font)
    if (width === null) return null
    total += width
  }
  return total <= room
}
