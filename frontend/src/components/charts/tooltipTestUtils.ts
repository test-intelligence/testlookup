/**
 * Test helpers for VIZ-601: read a DRAWN tooltip back into the content model.
 *
 * "Content equality mouse vs keyboard" is asserted by reading what the pointer
 * tooltip actually rendered (React `ChartTooltip` or the ECharts DOM node —
 * they share their markup) back into a `TooltipContent`, and comparing its
 * `tooltipText` with what the keyboard cursor announced. A tooltip that drew
 * one row the cursor did not say, or said it differently, fails.
 */
import { TIP_CLASS, type TooltipContent, type TooltipRow, type TooltipRowKind } from './tooltip'

const first = (className: string) => `.${className.split(' ')[0]}`

/** The content a drawn tooltip (or readout) shows, read from its DOM. */
export function readTooltip(el: Element): TooltipContent {
  const title = el.querySelector(first(TIP_CLASS.title))?.textContent ?? undefined
  const rows: TooltipRow[] = Array.from(el.querySelectorAll(first(TIP_CLASS.row)), (line) => {
    const kind = (line.getAttribute('data-tip-kind') ?? 'value') as TooltipRowKind
    const rawLabel = line.querySelector(first(TIP_CLASS.label))?.textContent ?? ''
    // A note's label is drawn as "Label: " before its sentence.
    const label = kind === 'note' ? rawLabel.replace(/: $/, '') : rawLabel
    const value = line.querySelector(first(TIP_CLASS.value))?.textContent ?? ''
    const detail = line.querySelector(first(TIP_CLASS.detail))?.textContent?.replace(/^\((.*)\)$/, '$1')
    return detail ? { kind, label, value, detail } : { kind, label, value }
  })
  return title === undefined ? { rows } : { title, rows }
}

/** `[label, value]` pairs of a drawn tooltip, for compact assertions. */
export function tooltipPairs(el: Element): [string, string][] {
  return readTooltip(el).rows.map((row) => [row.label, row.value])
}
