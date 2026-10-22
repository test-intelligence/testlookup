/**
 * An item's detail, docked beside its list (W27 PAN-01): the pane of
 * `MasterDetail`. Its markup is /test-management's case panel, which was the
 * first in-page detail: a sticky card that keeps the item in view while the
 * list scrolls under it, a header (title, a subtitle row, Prev / "3 of 25" /
 * Next, Expand, Close), a scrolling body and an optional pinned footer.
 *
 * An `<aside>` named by its heading, so it keeps the `complementary` role the
 * non-modal side panels and the case panel had, and a test that found the
 * old panel by name finds the pane by the same name.
 *
 * Keyboard: focus moves into the pane when it opens and back to the opener
 * when it closes (`manageFocus`); Escape while focus is inside closes it
 * (`escape: 'inside'`), a form inside it closing first (the innermost owns
 * the key). `escape: 'none'` leaves Escape to the host (/test-management has
 * its own document-level handler).
 *
 * With a `slot`, Close, Escape, Prev and Next run through `slot.guard`, so a
 * form typed into inside the pane asks before it goes.
 */
import { ChevronLeft, ChevronRight, Maximize2, Minimize2, X } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import type { InlineSlot } from './useInlineSlot'
import { useInPageFocus } from './useInPageFocus'

export interface DetailPaneProps {
  /** React text only. */
  title: ReactNode
  /** Default useId(); TM passes 'tm-case-detail-title'. */
  titleId?: string
  /** The row under the title (TM's pills). */
  subtitle?: ReactNode
  onClose: () => void
  /** 'Close case' (TM) | 'Close release' | 'Close panel' (the run test panes). */
  closeLabel: string
  /** TM: 'Close (Esc)'. */
  closeTitle?: string
  expanded?: boolean
  /** Absent: no Expand button. */
  onToggleExpand?: () => void
  expandLabels?: { expand: string; collapse: string; expandTitle?: string; collapseTitle?: string }
  /** "3 of 25", shown when total > 1. */
  position?: { index: number; total: number }
  onPrev?: () => void
  onNext?: () => void
  /** TM: 'Previous case'. */
  prevLabel?: string
  /** TM: 'Next case'. */
  nextLabel?: string
  /** Extra header controls, left of Prev / Expand / Close. */
  actions?: ReactNode
  /** Pinned under the scrolling body. */
  footer?: ReactNode
  children: ReactNode
  /** 'inside' (default): Escape inside the pane closes it (useInPageFocus). 'none': the host keeps its own
   *  handler (TM's document-level Escape). */
  escape?: 'inside' | 'none'
  /** Close / switch-away run through this slot's guard (a dirty form inside the pane asks first). */
  slot?: InlineSlot
  /** TM: 'tm-case-panel-actions', 'tm-case-position'. */
  testIds?: { actions?: string; position?: string }
  /** TM: data-case-panel="", data-expanded. */
  dataAttributes?: { [data: `data-${string}`]: string }
  /**
   * 'scroll' (default): the children sit in a scrolling body (`min-h-0 flex-1 overflow-y-auto`, plus
   * `bodyClassName` for the host's padding). 'host': the children are placed straight in the pane's column and
   * lay out their own scrolling (TM: its tab bar, then its own scrolling tab content).
   */
  bodyLayout?: 'scroll' | 'host'
  /** The scrolling body's padding ('px-4 py-3' for a former side panel); 'scroll' only. */
  bodyClassName?: string
  /**
   * Default true: focus moves into the pane when it opens and returns to the opener when it closes.
   * false: the pane never moves focus (TM's case panel never did).
   */
  manageFocus?: boolean
}

const PANEL_ICON_BUTTON =
  'inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-muted)] ' +
  'hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] transition-colors ' +
  'disabled:opacity-40 disabled:pointer-events-none'

const DEFAULT_EXPAND_LABELS = { expand: 'Expand to full width', collapse: 'Show the list beside it' }

export function DetailPane({
  title,
  titleId,
  subtitle,
  onClose,
  closeLabel,
  closeTitle,
  expanded = false,
  onToggleExpand,
  expandLabels = DEFAULT_EXPAND_LABELS,
  position,
  onPrev,
  onNext,
  prevLabel = 'Previous',
  nextLabel = 'Next',
  actions,
  footer,
  children,
  escape = 'inside',
  slot,
  testIds,
  dataAttributes,
  bodyLayout = 'scroll',
  bodyClassName,
  manageFocus = true,
}: DetailPaneProps) {
  const generatedId = useId()
  const headingId = titleId ?? generatedId
  // Leaving the item (closing the pane, stepping to the next one) asks first
  // while a form inside it has typed input.
  const away = (action: () => void) => (slot ? slot.guard(action) : action())
  const close = () => away(onClose)
  const ref = useInPageFocus<HTMLElement>({
    onEscape: escape === 'inside' ? close : undefined,
    autoFocus: manageFocus,
    restoreFocus: manageFocus,
  })

  const stepping = position ? position.total > 1 : Boolean(onPrev || onNext)

  return (
    <aside
      ref={ref}
      aria-labelledby={headingId}
      data-in-page="detail"
      {...dataAttributes}
      className="sticky top-3 flex flex-col rounded-xl overflow-hidden"
      style={{
        background: 'var(--color-bg-card)',
        border: '1px solid var(--color-border)',
        // The list scrolls under it; the pane keeps the item in view and
        // scrolls its own body.
        maxHeight: 'calc(100vh - 96px)',
      }}
    >
      <div className="flex flex-col min-h-0 flex-1">
        <div className="px-5 py-3.5 border-b border-[var(--color-border)] flex-shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <h2 id={headingId} className="text-base font-semibold text-[var(--color-text)] leading-snug">{title}</h2>
              {subtitle && <div className="flex items-center gap-2 mt-1.5 flex-wrap">{subtitle}</div>}
            </div>
            <div className="flex items-center gap-0.5 flex-shrink-0" data-testid={testIds?.actions}>
              {actions}
              {stepping && (
                <>
                  <button
                    type="button"
                    aria-label={prevLabel}
                    title={prevLabel}
                    onClick={onPrev ? () => away(onPrev) : undefined}
                    disabled={!onPrev}
                    className={PANEL_ICON_BUTTON}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  {position && (
                    <span
                      data-testid={testIds?.position}
                      className="px-1 text-2xs tabular-nums text-[var(--color-text-muted)] whitespace-nowrap"
                    >
                      {position.index} of {position.total}
                    </span>
                  )}
                  <button
                    type="button"
                    aria-label={nextLabel}
                    title={nextLabel}
                    onClick={onNext ? () => away(onNext) : undefined}
                    disabled={!onNext}
                    className={PANEL_ICON_BUTTON}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                  <span aria-hidden="true" className="mx-1 h-4 w-px bg-[var(--color-border)]" />
                </>
              )}
              {onToggleExpand && (
                <button
                  type="button"
                  aria-label={expanded ? expandLabels.collapse : expandLabels.expand}
                  title={expanded ? (expandLabels.collapseTitle ?? expandLabels.collapse) : (expandLabels.expandTitle ?? expandLabels.expand)}
                  onClick={onToggleExpand}
                  className={PANEL_ICON_BUTTON}
                >
                  {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                </button>
              )}
              <button type="button" aria-label={closeLabel} title={closeTitle} onClick={close} className={PANEL_ICON_BUTTON}>
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {bodyLayout === 'host' ? (
          children
        ) : (
          <div className={`min-h-0 flex-1 overflow-y-auto${bodyClassName ? ` ${bodyClassName}` : ''}`}>{children}</div>
        )}

        {footer && <div className="border-t border-[var(--color-border)] px-4 py-3 flex-shrink-0">{footer}</div>}
      </div>
    </aside>
  )
}

export default DetailPane
