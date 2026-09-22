/**
 * Filter chips (VIZ-109; the building block of VIZ-303's FilterChips).
 *
 * `Chip` = dimension label + value + optional warning + a remove button.
 * The remove button's accessible name says WHAT it removes — "Remove filter
 * Release R2" — because a screen-reader user tabbing a row of chips hears
 * only the buttons, and ten "Remove" buttons are indistinguishable. It is a
 * 24×24 px target at minimum (WCAG 2.5.8).
 *
 * `ChipList` lays chips out, collapses the tail into "+N more", and owns the
 * focus problem removal creates: the focused button is destroyed, and focus
 * would fall to `<body>` — the top of the page for a keyboard user. It moves
 * to the next chip instead, the previous one if the last was removed, and to
 * the caller's `fallbackFocusRef` (e.g. the filter control) when none is left.
 *
 * Labels and values render as text, never markup; long values are cut in the
 * middle with the full text in `title`.
 */
import { AlertTriangle, X } from 'lucide-react'
import { useLayoutEffect, useRef, useState, type Ref, type RefObject } from 'react'
import { truncateMiddle } from '@/utils/formatters'

/** Longest value shown in full, in characters; longer values are middle-truncated. */
export const CHIP_VALUE_MAX_CHARS = 40

export interface ChipProps {
  /** The dimension: "Release", "Suite". */
  label: string
  /** The selected value: "R2", "checkout". */
  value: string
  /** A caution about this filter (e.g. "No runs in this window"); shows an icon. */
  warning?: string
  /** Omit for a read-only chip (no remove button). */
  onRemove?: () => void
  /** Accessible name for the remove button. Default `Remove filter {label} {value}`. */
  removeLabel?: string
  /** Ref to the remove button, for focus management. */
  removeRef?: Ref<HTMLButtonElement>
  className?: string
}

const TARGET_24 =
  'inline-flex h-6 min-h-6 w-6 min-w-6 shrink-0 items-center justify-center rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-ring)]'

export function Chip({ label, value, warning, onRemove, removeLabel, removeRef, className = '' }: ChipProps) {
  const shown = truncateMiddle(value, CHIP_VALUE_MAX_CHARS)
  return (
    <span
      data-chip=""
      className={`inline-flex max-w-full items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-card)] py-0.5 pl-2.5 text-xs text-[var(--color-text)] ${onRemove ? 'pr-0.5' : 'pr-2.5'} ${className}`}
    >
      {warning && (
        <span title={warning} className="inline-flex shrink-0">
          <AlertTriangle
            role="img"
            aria-label={`Warning: ${warning}`}
            className="h-3.5 w-3.5 text-[var(--status-broken)]"
          />
        </span>
      )}
      <span className="shrink-0 text-[var(--color-text-secondary)]">{label}:</span>
      <span className="min-w-0 truncate font-medium" title={shown === value ? undefined : value}>
        {shown}
      </span>
      {onRemove && (
        <button
          ref={removeRef}
          type="button"
          aria-label={removeLabel ?? `Remove filter ${label} ${value}`}
          onClick={onRemove}
          className={`${TARGET_24} text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]`}
        >
          <X aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      )}
    </span>
  )
}

export interface ChipItem {
  /** Stable identity; also the key handed to `onRemove`. */
  id: string
  label: string
  value: string
  warning?: string
}

export interface ChipListProps {
  items: ChipItem[]
  onRemove: (id: string) => void
  /** Chips shown before collapsing the rest into "+N more". Default 5. */
  limit?: number
  /** Names the list for assistive tech. Default "Active filters". */
  ariaLabel?: string
  /** Where focus goes when the last remaining chip is removed. */
  fallbackFocusRef?: RefObject<HTMLElement | null>
  className?: string
}

export const DEFAULT_CHIP_LIMIT = 5

export default function ChipList({
  items,
  onRemove,
  limit = DEFAULT_CHIP_LIMIT,
  ariaLabel = 'Active filters',
  fallbackFocusRef,
  className = '',
}: ChipListProps) {
  const [expanded, setExpanded] = useState(false)
  const buttons = useRef(new Map<string, HTMLButtonElement>())
  /**
   * Set by a remove click, consumed only once the parent re-renders WITHOUT
   * that chip. A re-render that still holds it (a new array of the same
   * chips, an optimistic copy, a store round-trip) leaves the request
   * waiting; a parent that refuses the removal keeps focus where it is, and
   * the next remove click replaces the request.
   */
  const pending = useRef<{ removed: string; next: string | null } | null>(null)

  useLayoutEffect(() => {
    const request = pending.current
    if (!request) return
    if (items.some((item) => item.id === request.removed)) return
    pending.current = null
    const target = request.next === null ? null : buttons.current.get(request.next)
    if (target) target.focus()
    else fallbackFocusRef?.current?.focus()
  }, [items, fallbackFocusRef])

  if (items.length === 0) return null

  const collapsible = items.length > limit
  const visible = collapsible && !expanded ? items.slice(0, limit) : items
  const hidden = items.length - visible.length

  const remove = (index: number) => {
    const neighbour = items[index + 1] ?? items[index - 1] ?? null
    pending.current = { removed: items[index].id, next: neighbour?.id ?? null }
    onRemove(items[index].id)
  }

  return (
    <div className={`flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}>
      <ul aria-label={ariaLabel} className="flex min-w-0 flex-wrap items-center gap-1.5">
        {visible.map((item, index) => (
          <li key={item.id} className="min-w-0 max-w-full">
            <Chip
              label={item.label}
              value={item.value}
              warning={item.warning}
              onRemove={() => remove(index)}
              removeRef={(node) => {
                if (node) buttons.current.set(item.id, node)
                else buttons.current.delete(item.id)
              }}
            />
          </li>
        ))}
      </ul>
      {collapsible && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((open) => !open)}
          // accent-INK: the text-safe accent. Plain --color-accent is 4.06:1 on
          // the console card at 12 px; ink is >= 5.17:1 in every theme, and
          // >= 4.63:1 on the soft-accent hover fill (bg-hover drops lab to 4.25).
          className="inline-flex min-h-6 items-center rounded-full px-2 text-xs font-medium text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]"
        >
          {expanded ? 'Show fewer' : `+${hidden} more`}
        </button>
      )}
    </div>
  )
}
