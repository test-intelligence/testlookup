/**
 * Multi-value filter control (VIZ-109; consumed by VIZ-303/304/305/306).
 *
 * CLOSED it is one button: the dimension name plus a count badge, drawn
 * "filled" while anything is selected, so an active filter is visible at a
 * glance. It announces `aria-haspopup="dialog"`: what opens is not a bare
 * listbox but a small non-modal DIALOG (`role="dialog"`, named "<label>
 * options") holding a filter field, two bulk buttons and the list. Inside it
 * is the WAI-ARIA combobox-with-listbox pattern:
 *
 *   - focus goes to a filter field (`role="combobox"`); the options are a
 *     `role="listbox" aria-multiselectable` and the ACTIVE option is conveyed
 *     by `aria-activedescendant`, so focus never leaves the field and typing
 *     always filters (type-ahead);
 *   - ArrowDown/ArrowUp move the active option; Home/End jump to the ends
 *     once an option is active (before that they move the text caret);
 *   - Space and Enter toggle the active option. Until an option is active,
 *     Space types a space — "release r2" must be typeable. Every toggle is
 *     announced once in the polite status region ("Release 2 selected, 2 of
 *     3");
 *   - Escape closes WITHOUT reaching an enclosing dialog, and focus returns to
 *     the trigger. Shift+Tab before the field closes back to the trigger; Tab
 *     past the last control closes and moves on to the next control AFTER the
 *     trigger, as if the popup had been inline.
 *
 * Each option's accessible name is its FULL label, then its exact count, then
 * why it is disabled — "Release 2, 74", "Release archived, 0, Archived" — set
 * with `aria-label`, because the row's visual text runs them together
 * ("Release 274") and a truncated label would be read in its "…" form.
 *
 * A CAP (`max`): once reached, unselected options are `aria-disabled`, and the
 * status says "Limit reached (N)" — a limit that silently ignores a click
 * reads as a broken control.
 *
 * CONTROLLED EDGE CASES: a selected value that is not among `options` is
 * listed first as "<value> (not available)" — visible, deselectable, never
 * counted against `max` — and the summary says how many there are. Duplicate
 * option values keep the first and warn once in development.
 *
 * Long lists: 10 options, then "Show more". Past 200 the list is WINDOWED —
 * fixed 32 px rows, the unrendered rows stood in for by padding, and every
 * rendered option carries `aria-setsize`/`aria-posinset`, so a screen reader
 * still announces "37 of 500". A mouse scroll pulls the active option into
 * the visible rows, so `aria-activedescendant` never names an unrendered one.
 *
 * The POPOVER is `position: fixed` and portalled — out of any `overflow:
 * hidden` card — but into the FULLSCREEN element when the trigger is inside
 * it (a portal to <body> is invisible in full screen), else into the nearest
 * `aria-modal` dialog (so it stays inside that dialog's focus trap), else
 * <body>. Portalling breaks `ref.contains()` outside-click logic, so dismissal
 * here checks BOTH the trigger and the popover. It never outgrows the
 * viewport (WCAG 1.4.10 at 200 %/400 % zoom): its height is capped, the list
 * shrinks first, the popover itself scrolls after that, and it opens on
 * whichever side of the trigger has room.
 *
 * Labels are text, never markup, middle-truncated to 60 characters with the
 * full label in `title`. Controlled: `value` in, `onChange(next)` out.
 */
import { Check, ChevronDown } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { formatCompactWithExact, truncateMiddle } from '@/utils/formatters'
import { resolvePopoverContainer } from './popoverContainer'

export interface MultiSelectOption {
  value: string
  label: string
  /** Shown right-aligned, compact ("1.2K") with the exact number in `title`. */
  count?: number
  disabled?: boolean
  /** Why it is disabled; shown in the row and part of its accessible name. */
  disabledReason?: string
}

export interface MultiSelectProps {
  /** The dimension ("Release"): trigger text and the listbox's name. */
  label: string
  options: MultiSelectOption[]
  value: string[]
  onChange: (next: string[]) => void
  /** At most this many selected. */
  max?: number
  /** Disable the whole control; say why in `disabledReason` (shown beside it). */
  disabled?: boolean
  disabledReason?: string
  /** Options listed before "Show more". Default 10. */
  initialVisible?: number
  /** Placeholder of the filter field. Default "Filter…". */
  filterPlaceholder?: string
  className?: string
  /** Hook for tests and the gallery. */
  'data-testid'?: string
  /** The trigger button, for a caller that must move focus to it (a dismissed notice). */
  triggerRef?: RefObject<HTMLButtonElement | null>
  /** `data-*` attributes for the trigger button (`data-report-filter="release"`). */
  triggerData?: Record<`data-${string}`, string>
}

/** A listed row: an option, or a selected value that is not among the options. */
interface Row extends MultiSelectOption {
  unavailable?: boolean
}

export const MULTISELECT_ROW_HEIGHT = 32
export const MULTISELECT_VIRTUALIZE_ABOVE = 200
export const MULTISELECT_INITIAL_VISIBLE = 10
export const MULTISELECT_LABEL_MAX_CHARS = 60
/** Ten rows of listbox before it scrolls (less when the viewport is short). */
const LISTBOX_VIEWPORT = MULTISELECT_ROW_HEIGHT * 10
/** The list never shrinks below three rows; the popover scrolls instead. */
const LISTBOX_MIN_ROWS = 3
const OVERSCAN = 5
const POPOVER_MIN_WIDTH = 280
/** Below this much room on either side, the popover overlaps its trigger rather than shrink further. */
const POPOVER_MIN_SIDE_HEIGHT = 200
const GUTTER = 8

const FOCUS_RING =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-ring)]'

/**
 * Small text in the accent colour: accent-INK, the text-safe accent (plain
 * --color-accent is 4.06:1 on the console card at 12 px; ink is >= 5.17:1 on
 * the card in every theme, >= 4.63:1 on the soft-accent hover fill).
 */
const ACCENT_TEXT_BUTTON = `min-h-6 rounded px-2 text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] ${FOCUS_RING}`

const TABBABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** The first tabbable element after `anchor` in `scope`, in document order, outside `exclude`. */
function nextTabbableAfter(anchor: HTMLElement, scope: HTMLElement, exclude: HTMLElement | null): HTMLElement | null {
  const candidates = Array.from(scope.querySelectorAll<HTMLElement>(TABBABLE))
  for (const el of candidates) {
    if (el.tabIndex < 0 || anchor.contains(el) || exclude?.contains(el)) continue
    if (!(anchor.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue
    if (el.closest('[inert], [hidden]')) continue
    const check = (el as HTMLElement & { checkVisibility?: () => boolean }).checkVisibility
    if (typeof check === 'function' && !check.call(el)) continue
    return el
  }
  return null
}

export default function MultiSelect({
  label,
  options,
  value,
  onChange,
  max,
  disabled = false,
  disabledReason,
  initialVisible = MULTISELECT_INITIAL_VISIBLE,
  filterPlaceholder = 'Filter…',
  className = '',
  'data-testid': testId,
  triggerRef: externalTriggerRef,
  triggerData,
}: MultiSelectProps) {
  const baseId = useId()
  const popoverId = `${baseId}-popover`
  const listboxId = `${baseId}-listbox`
  const reasonId = `${baseId}-reason`
  const optionId = (position: number) => `${baseId}-option-${position}`

  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const setTriggerNode = useCallback(
    (node: HTMLButtonElement | null) => {
      triggerRef.current = node
      if (externalTriggerRef) externalTriggerRef.current = node
    },
    [externalTriggerRef],
  )
  const popoverRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(-1)
  const [showAll, setShowAll] = useState(false)
  const [scrollTop, setScrollTop] = useState(0)
  /** Values selected when the popover opened: listed first, and stable while open. */
  const [pinned, setPinned] = useState<ReadonlySet<string>>(() => new Set())
  const [container, setContainer] = useState<HTMLElement | null>(null)
  /** The last toggle, for the polite status region. */
  const [announcement, setAnnouncement] = useState('')

  // ── the option set: deduped, plus selected values it does not contain ─────
  const known = useMemo(() => {
    const byValue = new Map<string, MultiSelectOption>()
    const duplicates: string[] = []
    for (const option of options) {
      if (byValue.has(option.value)) duplicates.push(option.value)
      else byValue.set(option.value, option)
    }
    return { list: Array.from(byValue.values()), byValue, duplicates }
  }, [options])

  const warned = useRef(new Set<string>())
  useEffect(() => {
    if (!import.meta.env.DEV) return
    for (const duplicate of known.duplicates) {
      if (warned.current.has(duplicate)) continue
      warned.current.add(duplicate)
      console.warn(`MultiSelect "${label}": duplicate option value "${duplicate}"; only the first is listed.`)
    }
  }, [known, label])

  const selectedValues = useMemo(() => Array.from(new Set(value)), [value])
  const selected = useMemo(() => new Set(selectedValues), [selectedValues])

  /** Counts as the user sees them: real options against the cap, the rest "not available". */
  const tally = useCallback(
    (values: readonly string[]) => {
      const available = values.filter((v) => known.byValue.has(v)).length
      const unavailable = values.length - available
      return max !== undefined
        ? { n: available, of: max, unavailable }
        : { n: values.length, of: known.list.length + unavailable, unavailable }
    },
    [known, max],
  )
  const current = tally(selectedValues)
  const atCap = max !== undefined && current.n >= max

  const rows = useMemo<Row[]>(() => {
    const missing: Row[] = []
    for (const v of [...selectedValues, ...pinned]) {
      if (known.byValue.has(v) || missing.some((row) => row.value === v)) continue
      missing.push({ value: v, label: `${v} (not available)`, unavailable: true })
    }
    const listed: Row[] = [...missing, ...known.list]
    if (pinned.size === 0) return listed
    return [...listed.filter((o) => pinned.has(o.value)), ...listed.filter((o) => !pinned.has(o.value))]
  }, [known, selectedValues, pinned])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle === '' ? rows : rows.filter((o) => o.label.toLowerCase().includes(needle))
  }, [rows, query])

  const truncated = !showAll && filtered.length > initialVisible
  const visible = truncated ? filtered.slice(0, initialVisible) : filtered
  const windowed = visible.length > MULTISELECT_VIRTUALIZE_ABOVE
  const scrollable = visible.length * MULTISELECT_ROW_HEIGHT > LISTBOX_VIEWPORT

  const windowStart = windowed ? Math.max(0, Math.floor(scrollTop / MULTISELECT_ROW_HEIGHT) - OVERSCAN) : 0
  const windowEnd = windowed
    ? Math.min(visible.length, windowStart + Math.ceil(LISTBOX_VIEWPORT / MULTISELECT_ROW_HEIGHT) + OVERSCAN * 2)
    : visible.length

  const isBlocked = (row: Row) =>
    Boolean(row.disabled) || (!selected.has(row.value) && (atCap || Boolean(row.unavailable)))

  /** The list's real height: less than ten rows when the popover had to shrink. */
  const listViewport = () => scrollerRef.current?.clientHeight || LISTBOX_VIEWPORT

  // ── open / close ──────────────────────────────────────────────────────────
  const openPopover = () => {
    if (disabled) return
    setPinned(new Set(value))
    setQuery('')
    setActiveIndex(-1)
    setShowAll(false)
    setScrollTop(0)
    setAnnouncement('')
    setContainer(resolvePopoverContainer(triggerRef.current))
    setOpen(true)
  }

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }, [])

  // ── positioning (written straight to the node, like HeaderPopover) ────────
  const reposition = useCallback(() => {
    const anchor = triggerRef.current
    const popover = popoverRef.current
    if (!anchor || !popover) return
    const rect = anchor.getBoundingClientRect()
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    const width = Math.min(Math.max(rect.width, POPOVER_MIN_WIDTH), viewportWidth - GUTTER * 2)
    const left = Math.max(GUTTER, Math.min(rect.left, viewportWidth - width - GUTTER))
    popover.style.width = `${width}px`
    popover.style.left = `${left}px`

    // Never taller than the viewport (at 400 % zoom that is ~256 px): the
    // list shrinks first (flex), then the popover itself scrolls.
    popover.style.maxHeight = `${Math.max(0, viewportHeight - GUTTER * 2)}px`
    const height = popover.offsetHeight
    const below = rect.bottom + 4
    const roomBelow = viewportHeight - GUTTER - below
    const roomAbove = rect.top - 4 - GUTTER

    let top: number
    if (height <= roomBelow) {
      top = below
    } else if (height <= roomAbove) {
      top = rect.top - 4 - height
    } else if (Math.max(roomAbove, roomBelow) >= POPOVER_MIN_SIDE_HEIGHT) {
      // Fits neither side whole: the roomier side, capped to it.
      if (roomAbove > roomBelow) {
        popover.style.maxHeight = `${roomAbove}px`
        top = rect.top - 4 - popover.offsetHeight
      } else {
        popover.style.maxHeight = `${roomBelow}px`
        top = below
      }
    } else {
      // Too little room either side: overlap the trigger, inside the viewport.
      top = Math.max(GUTTER, Math.min(below, viewportHeight - GUTTER - height))
    }
    popover.style.top = `${top}px`
  }, [])

  /**
   * Whether the list REALLY scrolls, measured after positioning. The row count
   * alone (`scrollable`) misses the common case: a popover capped by the room
   * below its trigger shrinks the list (flex) under ten rows, and a region that
   * scrolls with nothing focusable inside is unreachable from the keyboard
   * (axe scrollable-region-focusable) — the options are not focusable, the
   * field keeps focus via aria-activedescendant.
   */
  const [overflowing, setOverflowing] = useState(false)
  const measureOverflow = useCallback(() => {
    const node = scrollerRef.current
    setOverflowing(node !== null && node.scrollHeight > node.clientHeight + 1)
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    reposition()
    measureOverflow()
    inputRef.current?.focus()
  }, [open, reposition, measureOverflow])

  // Size changes (show more, filtering) can require a flip.
  useLayoutEffect(() => {
    if (!open) return
    reposition()
    measureOverflow()
  }, [open, visible.length, reposition, measureOverflow])

  useEffect(() => {
    if (!open) return
    const onScroll = (event: Event) => {
      // The popover's own scrolling (list, overflow) never moves the trigger.
      if (event.target instanceof Node && popoverRef.current?.contains(event.target)) return
      reposition()
    }
    window.addEventListener('scroll', onScroll, true)
    const onResize = () => {
      reposition()
      measureOverflow()
    }
    window.addEventListener('resize', onResize)
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target)) return // the trigger toggles itself
      if (popoverRef.current?.contains(target)) return // portalled: not inside the trigger
      close(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open, reposition, close, measureOverflow])

  // Keep the active option in view (non-windowed lists; windowed ones scroll in moveTo).
  useEffect(() => {
    if (!open || windowed || activeIndex < 0) return
    const node = document.getElementById(`${baseId}-option-${activeIndex}`)
    // jsdom has no scrollIntoView.
    node?.scrollIntoView?.({ block: 'nearest' })
  }, [open, windowed, activeIndex, baseId])

  // ── selection ─────────────────────────────────────────────────────────────
  const say = (values: readonly string[], what: string) => {
    const { n, of } = tally(values)
    setAnnouncement(`${what}, ${n} of ${of}`)
  }

  const toggle = (row: Row) => {
    if (row.disabled) return
    if (selected.has(row.value)) {
      const next = selectedValues.filter((v) => v !== row.value)
      onChange(next)
      say(next, `${row.label} deselected`)
      return
    }
    if (atCap || row.unavailable) return
    const next = [...selectedValues, row.value]
    onChange(next)
    say(next, `${row.label} selected`)
  }

  const addable = filtered.filter((o) => !o.disabled && !o.unavailable && !selected.has(o.value))
  const room = max === undefined ? addable.length : Math.max(0, max - current.n)
  // Both bulk buttons can disable themselves on click; a focused button that
  // turns disabled drops focus to <body>, so focus goes back to the field.
  const selectAll = () => {
    inputRef.current?.focus()
    if (room === 0 || addable.length === 0) return
    const added = addable.slice(0, room).map((o) => o.value)
    const next = [...selectedValues, ...added]
    onChange(next)
    say(next, `${added.length} selected`)
  }
  const lockedSelected = selectedValues.filter((v) => known.byValue.get(v)?.disabled)
  const selectNone = () => {
    inputRef.current?.focus()
    onChange(lockedSelected)
    say(lockedSelected, 'Selection cleared')
  }

  // ── keyboard ──────────────────────────────────────────────────────────────
  const moveTo = (index: number) => {
    if (visible.length === 0) return
    const next = Math.max(0, Math.min(visible.length - 1, index))
    setActiveIndex(next)
    if (windowed) {
      const viewport = listViewport()
      const rowTop = next * MULTISELECT_ROW_HEIGHT
      let top = scrollTop
      if (rowTop < top) top = rowTop
      else if (rowTop + MULTISELECT_ROW_HEIGHT > top + viewport) {
        top = rowTop + MULTISELECT_ROW_HEIGHT - viewport
      }
      if (top !== scrollTop) {
        // State first, so the row is rendered in this same update; the DOM
        // scroll follows (its scroll event then reports the same value).
        setScrollTop(top)
        if (scrollerRef.current) scrollerRef.current.scrollTop = top
      }
    }
  }

  /**
   * A wheel or scrollbar scroll of a WINDOWED list: move the window, and pull
   * the active option into the visible rows — else aria-activedescendant
   * names an option that is no longer rendered, and Space toggles a row the
   * user cannot see.
   */
  const onWindowedScroll = (node: HTMLDivElement) => {
    const top = node.scrollTop
    setScrollTop(top)
    const viewport = node.clientHeight || LISTBOX_VIEWPORT
    const firstShown = Math.ceil(top / MULTISELECT_ROW_HEIGHT)
    const lastShown = Math.max(firstShown, Math.floor((top + viewport) / MULTISELECT_ROW_HEIGHT) - 1)
    setActiveIndex((index) =>
      index < 0 ? index : Math.min(Math.max(index, firstShown), Math.min(lastShown, visible.length - 1)),
    )
  }

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        moveTo(activeIndex + 1)
        return
      case 'ArrowUp':
        event.preventDefault()
        moveTo(activeIndex <= 0 ? 0 : activeIndex - 1)
        return
      case 'Home':
      case 'End':
        if (activeIndex < 0) return // caret movement in the field
        event.preventDefault()
        moveTo(event.key === 'Home' ? 0 : visible.length - 1)
        return
      case ' ':
        if (activeIndex < 0) return // types a space
        event.preventDefault()
        if (visible[activeIndex]) toggle(visible[activeIndex])
        return
      case 'Enter':
        // Never submit an enclosing form.
        event.preventDefault()
        if (activeIndex >= 0 && visible[activeIndex]) toggle(visible[activeIndex])
        return
      default:
        return
    }
  }

  const onPopoverKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      // Ours: an enclosing dialog listening on document must not close too.
      event.stopPropagation()
      event.preventDefault()
      close(true)
      return
    }
    if (event.key !== 'Tab') return
    const focusables = Array.from(
      popoverRef.current?.querySelectorAll<HTMLElement>('input, button:not([disabled]), [tabindex="0"]') ?? [],
    )
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      event.stopPropagation()
      close(true)
    } else if (!event.shiftKey && document.activeElement === last) {
      // Onward, as if the popup were inline after the trigger — found BEFORE
      // closing, within the portal's scope (an enclosing modal dialog's trap).
      event.preventDefault()
      event.stopPropagation()
      const anchor = triggerRef.current
      const next = anchor ? nextTabbableAfter(anchor, container ?? document.body, popoverRef.current) : null
      close(next === null)
      next?.focus()
    }
  }

  // ── render ────────────────────────────────────────────────────────────────
  const count = selectedValues.length
  const active = count > 0
  const activeOptionId = activeIndex >= 0 && activeIndex < visible.length ? optionId(activeIndex) : undefined
  const remainingHidden = filtered.length - visible.length
  const hasListbox = visible.length > 0
  const statusText = [announcement, atCap ? `Limit reached (${max})` : '', hasListbox ? '' : 'No matches']
    .filter(Boolean)
    .join('. ')

  const trigger = (
    <button
      ref={setTriggerNode}
      {...triggerData}
      type="button"
      aria-haspopup="dialog"
      // Spelled out: a badge's bare "2" concatenates into "Release2".
      aria-label={active ? `${label}, ${count} selected` : undefined}
      aria-expanded={open}
      aria-controls={open ? popoverId : undefined}
      aria-disabled={disabled || undefined}
      aria-describedby={disabled && disabledReason ? reasonId : undefined}
      data-active={active ? 'true' : 'false'}
      onClick={() => {
        if (disabled) return
        if (open) close(false)
        else openPopover()
      }}
      onKeyDown={(event) => {
        if (event.key === 'ArrowDown' && !open && !disabled) {
          event.preventDefault()
          openPopover()
        }
      }}
      className={`inline-flex min-h-8 max-w-full items-center gap-2 rounded-md border px-3 py-1 text-sm ${FOCUS_RING} ${
        disabled
          ? 'cursor-not-allowed border-[var(--color-border)] text-[var(--color-text-secondary)] opacity-60'
          : active
            ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg-soft)] text-[var(--color-text)]'
            : 'border-[var(--color-border)] bg-[var(--color-bg-input)] text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <span className="truncate">{label}</span>
      {active && (
        <span
          data-testid={testId ? `${testId}-count` : undefined}
          className="inline-flex min-w-5 items-center justify-center rounded-full bg-[var(--color-btn-primary-bg)] px-1.5 text-xs font-semibold text-[var(--color-btn-primary-text)]"
        >
          {count}
        </span>
      )}
      <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0" />
    </button>
  )

  const popover = open && container
    ? createPortal(
        <div
          ref={popoverRef}
          id={popoverId}
          role="dialog"
          aria-label={`${label} options`}
          data-multiselect-popover=""
          onKeyDown={onPopoverKeyDown}
          onMouseDown={(event) => {
            // A press on the popover's own padding would blur the field and
            // strand keyboard focus on <body>.
            if (!(event.target as HTMLElement).closest('input, button')) event.preventDefault()
          }}
          className="fixed z-[80] flex max-w-[calc(100vw-16px)] flex-col gap-2 overflow-y-auto overscroll-contain rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-2 text-sm text-[var(--color-text)] shadow-2xl"
          // Parked off-screen until the layout effect measures, never at 0,0.
          style={{ top: -9999, left: -9999, width: POPOVER_MIN_WIDTH }}
        >
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            // Only while there is a listbox to point at ("No matches" has none).
            aria-controls={hasListbox ? listboxId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={activeOptionId}
            aria-label={`Filter ${label}`}
            placeholder={filterPlaceholder}
            value={query}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setQuery(event.target.value)
              setActiveIndex(-1)
              setScrollTop(0)
              setAnnouncement('')
              if (scrollerRef.current) scrollerRef.current.scrollTop = 0
            }}
            onKeyDown={onInputKeyDown}
            className={`min-h-8 w-full shrink-0 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-secondary)] ${FOCUS_RING}`}
          />

          <div className="flex shrink-0 items-center justify-between gap-2 text-xs">
            <span className="text-[var(--color-text-secondary)]">
              {current.n} of {current.of} selected
              {current.unavailable > 0 && `, ${current.unavailable} not available`}
            </span>
            <span className="flex gap-1">
              <button
                type="button"
                onClick={selectAll}
                disabled={room === 0 || addable.length === 0}
                className={`${ACCENT_TEXT_BUTTON} disabled:cursor-not-allowed disabled:text-[var(--color-text-secondary)] disabled:opacity-60`}
              >
                {query.trim() ? 'Select matching' : 'Select all'}
              </button>
              <button
                type="button"
                onClick={selectNone}
                disabled={selectedValues.length === lockedSelected.length}
                className={`${ACCENT_TEXT_BUTTON} disabled:cursor-not-allowed disabled:text-[var(--color-text-secondary)] disabled:opacity-60`}
              >
                Select none
              </button>
            </span>
          </div>

          {hasListbox && (
            // The SCROLLER is a wrapper, not the listbox: padding on a scroll
            // container itself cannot be taller than the container (the box
            // just grows), so the spacer padding goes on the list inside it.
            <div
              ref={scrollerRef}
              data-multiselect-scroller=""
              // A region that scrolls must be reachable without a mouse (axe
              // scrollable-region-focusable): the options themselves are not
              // focusable — the field keeps focus — so the scroller is a tab stop
              // whenever it has more rows than it shows: by row count, or as
              // MEASURED once the popover is placed (a capped popover shrinks
              // the list). From the field, ArrowUp/Down scroll the active
              // option into view instead (the effect below).
              tabIndex={scrollable || overflowing ? 0 : undefined}
              role={scrollable || overflowing ? 'group' : undefined}
              aria-label={scrollable || overflowing ? `${label} list` : undefined}
              data-scrolls={scrollable || overflowing ? 'true' : undefined}
              onMouseDown={(event) => event.preventDefault() /* keep focus in the field */}
              onScroll={windowed ? (event) => onWindowedScroll(event.currentTarget) : undefined}
              className={`shrink overflow-y-auto overscroll-contain rounded ${FOCUS_RING}`}
              style={{
                ...(windowed
                  ? {
                      height: LISTBOX_VIEWPORT,
                      // Scroll anchoring would "correct" scrollTop each time
                      // the padding and rows swap, fighting the window.
                      overflowAnchor: 'none' as const,
                    }
                  : { maxHeight: LISTBOX_VIEWPORT }),
                // A short viewport shrinks the list (flex), to three rows at least.
                minHeight: Math.min(visible.length, LISTBOX_MIN_ROWS) * MULTISELECT_ROW_HEIGHT,
              }}
            >
            <ul
              id={listboxId}
              role="listbox"
              aria-multiselectable="true"
              aria-label={label}
              style={
                windowed
                  ? {
                      paddingTop: windowStart * MULTISELECT_ROW_HEIGHT,
                      paddingBottom: (visible.length - windowEnd) * MULTISELECT_ROW_HEIGHT,
                    }
                  : undefined
              }
            >
              {visible.slice(windowStart, windowEnd).map((option, offset) => {
                const position = windowStart + offset
                const isSelected = selected.has(option.value)
                const blocked = isBlocked(option)
                const shownLabel = truncateMiddle(option.label, MULTISELECT_LABEL_MAX_CHARS)
                const countText = option.count === undefined ? null : formatCompactWithExact(option.count)
                const reason = option.disabled ? option.disabledReason : undefined
                // Full label, exact count, reason — separated. The row's own
                // text would read "Release 274" or the "…" form.
                const name = [option.label, countText?.title, reason].filter(Boolean).join(', ')
                return (
                  <li
                    key={option.value}
                    id={optionId(position)}
                    role="option"
                    aria-label={name}
                    aria-selected={isSelected}
                    aria-disabled={blocked || undefined}
                    aria-setsize={visible.length}
                    aria-posinset={position + 1}
                    data-value={option.value}
                    data-unavailable={option.unavailable ? 'true' : undefined}
                    onClick={() => {
                      setActiveIndex(position)
                      toggle(option)
                    }}
                    className={`flex items-center gap-2 rounded px-2 ${
                      position === activeIndex ? 'bg-[var(--color-bg-hover)]' : ''
                    } ${blocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                    style={{ height: MULTISELECT_ROW_HEIGHT }}
                  >
                    <span
                      aria-hidden="true"
                      className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                        isSelected
                          ? 'border-[var(--color-btn-primary-bg)] bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]'
                          : 'border-[var(--color-border)]'
                      }`}
                    >
                      {isSelected && <Check className="h-3 w-3" />}
                    </span>
                    <span
                      className={`min-w-0 flex-1 truncate ${option.unavailable ? 'italic' : ''}`}
                      title={shownLabel === option.label ? undefined : option.label}
                    >
                      {shownLabel}
                    </span>
                    {reason && (
                      <span aria-hidden="true" className="shrink-0 truncate text-xs text-[var(--color-text-secondary)]">
                        ({reason})
                      </span>
                    )}
                    {countText && (
                      <span
                        aria-hidden="true"
                        title={countText.title}
                        className="shrink-0 text-xs tabular-nums text-[var(--color-text-secondary)]"
                      >
                        {countText.text}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
            </div>
          )}

          {truncated && (
            <button
              type="button"
              onClick={() => {
                // The button unmounts on click; focus goes to the field, on
                // the first option it revealed.
                setShowAll(true)
                setActiveIndex(visible.length)
                inputRef.current?.focus()
              }}
              className={`${ACCENT_TEXT_BUTTON} shrink-0 self-start text-xs`}
            >
              Show more ({remainingHidden})
            </button>
          )}

          <p role="status" aria-live="polite" className="shrink-0 px-2 text-xs text-[var(--color-text-secondary)]">
            {statusText}
          </p>
        </div>,
        container,
      )
    : null

  return (
    <div className={`inline-flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 ${className}`} data-testid={testId}>
      {trigger}
      {disabled && disabledReason && (
        // Visible, not screen-reader-only: a sighted user needs the reason
        // too. The trigger stays focusable (aria-disabled) and is described by it.
        <span id={reasonId} className="text-xs text-[var(--color-text-secondary)]">
          {disabledReason}
        </span>
      )}
      {popover}
    </div>
  )
}
