/**
 * `FilterChips` (VIZ-304): every active report filter as a removable chip.
 *
 *   "Release: R1"  "Release: R2"  "Suite: payments"  "Window: last 14 days"  Clear all
 *
 * Built on Epic 1's `Chip` (label + value + warning + a ≥ 24×24 remove button
 * named "Remove filter Release R2"). Props-driven: the caller owns the
 * selection and passes callbacks.
 *
 *   - FOCUS after a removal goes to the NEXT chip, then "Clear all", then the
 *     caller's `fallbackFocusRef` (the filter bar) — never to <body>. The
 *     request waits until the parent re-renders WITHOUT the removed chip, so a
 *     store round-trip or a refused removal does not strand focus.
 *   - More than `limit` (8) removable chips collapse into "+N more"; expanding
 *     moves focus to the FIRST revealed chip (the button itself becomes "Show
 *     fewer", and the new chips would otherwise sit behind the focus).
 *   - The window chip is not removable: a window always applies. Off its
 *     default it offers a RESET button instead; at the default it is read-only.
 *   - A dimension listed in `meta.ignored_filters` marks each of its chips
 *     with a warning icon naming the reason (VIZ-307 explains it in full).
 *   - With no release or suite filter: "No filters applied — showing all
 *     releases and suites".
 *   - How filters combine (AND across dimensions, OR within one) is stated in
 *     a disclosure on the bar, reachable by keyboard, not only on hover.
 */
import { Info } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { Chip } from '@/components/ui/Chip'
import type { EnvelopeMeta } from '@/lib/viz/contracts'

export const FILTER_CHIP_LIMIT = 8
export const NO_FILTERS_TEXT = 'No filters applied — showing all releases and suites'
export const COMBINE_RULE_TEXT =
  'Filters combine with AND across dimensions and OR within one: a run matches when its release is any selected release AND its suite is any selected suite.'

export interface ReleaseChip {
  id: string
  /** Display name; the id is never shown when a name is known. */
  label: string
}

export interface FilterChipsProps {
  releases: ReleaseChip[]
  suites: string[]
  windowDays: number
  defaultWindowDays: number
  /** `meta.ignored_filters`: chips of these dimensions carry a warning. */
  ignoredFilters?: EnvelopeMeta['ignored_filters']
  onRemoveRelease: (id: string) => void
  onRemoveSuite: (name: string) => void
  onResetWindow: () => void
  onClearAll: () => void
  /** Where focus goes when no chip and no "Clear all" is left (the filter bar). */
  fallbackFocusRef?: RefObject<HTMLElement | null>
  limit?: number
  className?: string
}

interface Item {
  id: string
  label: string
  value: string
  warning?: string
  remove: () => void
}

/** The pending-focus marker for a window reset (never a chip id: those carry a `release:`/`suite:` prefix). */
const WINDOW_ITEM = 'window'

const windowValue = (days: number) => (days <= 0 ? 'all time' : days === 1 ? 'last 24 hours' : `last ${days} days`)

const FOCUS =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-ring)]'
const TEXT_BUTTON = `inline-flex min-h-6 items-center rounded-full px-2 text-xs font-medium text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] ${FOCUS}`

export default function FilterChips({
  releases,
  suites,
  windowDays,
  defaultWindowDays,
  ignoredFilters = [],
  onRemoveRelease,
  onRemoveSuite,
  onResetWindow,
  onClearAll,
  fallbackFocusRef,
  limit = FILTER_CHIP_LIMIT,
  className = '',
}: FilterChipsProps) {
  const ignored = new Map(ignoredFilters.map((f) => [f.dimension, f.reason]))
  const items: Item[] = [
    ...releases.map((r) => ({
      id: `release:${r.id}`,
      label: 'Release',
      value: r.label,
      warning: ignored.get('release'),
      remove: () => onRemoveRelease(r.id),
    })),
    ...suites.map((s) => ({
      id: `suite:${s}`,
      label: 'Suite',
      value: s,
      warning: ignored.get('suite'),
      remove: () => onRemoveSuite(s),
    })),
  ]

  const [expanded, setExpanded] = useState(false)
  const [showRule, setShowRule] = useState(false)
  const ruleId = useId()
  const buttons = useRef(new Map<string, HTMLButtonElement>())
  const clearAllRef = useRef<HTMLButtonElement>(null)
  /** Set by a removal; honoured once the parent re-renders without that chip. */
  const pending = useRef<{ removed: string | null; next: string | null } | null>(null)
  /** Set by "+N more": the id of the first chip it reveals, focused once rendered. */
  const revealFocus = useRef<string | null>(null)

  const itemIds = JSON.stringify(items.map((i) => i.id))
  const windowOff = windowDays !== defaultWindowDays
  useLayoutEffect(() => {
    const request = pending.current
    if (!request) return
    // Still waiting for the parent to apply the change?
    if (request.removed === WINDOW_ITEM) {
      if (windowOff) return
    } else if (request.removed === null) {
      if (itemIds !== '[]') return
    } else if ((JSON.parse(itemIds) as string[]).includes(request.removed)) {
      return
    }
    pending.current = null
    const target = request.next === null ? null : buttons.current.get(request.next)
    if (target) target.focus()
    else if (clearAllRef.current) clearAllRef.current.focus()
    else fallbackFocusRef?.current?.focus()
  }, [itemIds, windowOff, fallbackFocusRef])

  useLayoutEffect(() => {
    const id = revealFocus.current
    if (!expanded || id === null) return
    revealFocus.current = null
    buttons.current.get(id)?.focus()
  }, [expanded])

  const collapsible = items.length > limit
  const visible = collapsible && !expanded ? items.slice(0, limit) : items
  const hidden = items.length - visible.length

  const removeAt = (index: number) => {
    pending.current = { removed: items[index].id, next: items[index + 1]?.id ?? null }
    items[index].remove()
  }

  const clearAll = () => {
    // "Clear all" itself disappears with the chips (its ref is detached by
    // then), so focus lands on the bar.
    pending.current = { removed: null, next: null }
    onClearAll()
  }

  const windowWarning = ignored.get('window')

  return (
    <div
      data-filter-chips=""
      className={`flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-[var(--color-text)] ${className}`}
    >
      {items.length === 0 ? (
        <p data-no-filters="" className="text-xs text-[var(--color-text-secondary)]">
          {NO_FILTERS_TEXT}
        </p>
      ) : (
        <ul aria-label="Active filters" className="flex min-w-0 flex-wrap items-center gap-1.5">
          {visible.map((item, index) => (
            <li key={item.id} className="min-w-0 max-w-full">
              <Chip
                label={item.label}
                value={item.value}
                warning={item.warning}
                onRemove={() => removeAt(index)}
                removeRef={(node) => {
                  if (node) buttons.current.set(item.id, node)
                  else buttons.current.delete(item.id)
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {collapsible && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => {
            if (!expanded) revealFocus.current = items[limit]?.id ?? null
            setExpanded((o) => !o)
          }}
          className={TEXT_BUTTON}
        >
          {expanded ? 'Show fewer' : `+${hidden} more`}
        </button>
      )}
      <span data-window-chip="" className="min-w-0 max-w-full">
        <Chip
          label="Window"
          value={windowValue(windowDays)}
          warning={windowWarning}
          onRemove={
            windowOff
              ? () => {
                  // The reset button disappears with the reset: focus moves on.
                  pending.current = { removed: WINDOW_ITEM, next: null }
                  onResetWindow()
                }
              : undefined
          }
          removeLabel={`Reset filter Window to ${windowValue(defaultWindowDays)}`}
        />
      </span>
      {(items.length > 0 || windowOff) && (
        <button
          ref={clearAllRef}
          type="button"
          onClick={clearAll}
          className={TEXT_BUTTON}
        >
          Clear all
        </button>
      )}
      <button
        type="button"
        aria-label="How filters combine"
        aria-expanded={showRule}
        aria-controls={ruleId}
        title={COMBINE_RULE_TEXT}
        onClick={() => setShowRule((o) => !o)}
        className={`inline-flex h-6 min-h-6 w-6 min-w-6 items-center justify-center rounded-full text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] ${FOCUS}`}
      >
        <Info aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
      <p id={ruleId} hidden={!showRule} className="basis-full text-xs text-[var(--color-text-secondary)]">
        {COMBINE_RULE_TEXT}
      </p>
    </div>
  )
}
