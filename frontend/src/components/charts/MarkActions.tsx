/**
 * The focused mark's intents as real, Tab-reachable buttons (Wave 3, WCAG
 * 2.1.1: a drill, the rows or a filter is never shortcut-only or hover-only).
 *
 * Rendered by the keyboard cursor's readout (`useChartCursor`) for an SVG
 * chart, and by a canvas chart (heatmap, treemap, scatter) under its own plot
 * for its keyboard-highlighted mark. The label is React text, middle-truncated
 * on the button; the whole label is in the readout and the announcement.
 */
import { truncateMiddle } from '@/utils/formatters'
import { intentLabel, type ChartMark, type MarkActivateHandler, type MarkIntent } from './marks'

/** Longest mark label a button shows. */
export const MARK_BUTTON_LABEL_MAX = 48

export interface MarkActionsProps {
  mark: ChartMark
  /** The intents on offer, in order (`availableIntents`). Nothing is rendered for none. */
  intents: readonly MarkIntent[]
  onActivate: MarkActivateHandler
  /**
   * The element to focus BEFORE the action runs (the chart), given the
   * pressed button. A panel the action opens hands focus back, on close, to
   * whatever had it when it opened, and these buttons go as soon as the chart
   * loses its focused mark: without this, focus falls to <body>.
   */
  returnFocus?: (button: HTMLButtonElement) => HTMLElement | null | undefined
}

export function MarkActions({ mark, intents, onActivate, returnFocus }: MarkActionsProps) {
  if (intents.length === 0) return null
  const shown = { ...mark, label: truncateMiddle(mark.label, MARK_BUTTON_LABEL_MAX) }
  return (
    <div data-mark-actions="" role="group" aria-label="Actions for the focused value" className="mt-1 flex flex-wrap gap-2">
      {intents.map((intent) => (
        <button
          key={intent}
          type="button"
          data-mark-intent={intent}
          onClick={(event) => {
            returnFocus?.(event.currentTarget)?.focus()
            onActivate(mark, intent)
          }}
          className="min-h-6 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-0.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        >
          {intentLabel(intent, shown)}
        </button>
      ))}
    </div>
  )
}

export default MarkActions
