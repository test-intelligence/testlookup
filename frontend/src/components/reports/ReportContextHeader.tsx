/**
 * `ReportContextHeader` (VIZ-301): Project, Release, Test Suite, Window, Basis
 * and Generated, in large type, at the top of every report — so a screenshot
 * of a report says which data it shows.
 *
 * Props-driven and pure: `meta` (C2) in, a `<dl>` out. The content model is
 * `buildContextEntries` (what the SERVER applied, never what was asked).
 * Values are at least `--text-xl`, labels at least `--text-sm` (held by a
 * Playwright geometry check on the computed style, not by class names).
 *
 * More than three suites (or releases) show as "payments, cart, api +4": the
 * "+4" opens a popover listing all of them. The popover is a PORTAL
 * (`HeaderPopover`), so outside-click dismissal checks the trigger AND the
 * panel — `ref.contains()` on the header alone would treat a click inside the
 * list as outside.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { HeaderPopover } from '@/components/ui/HeaderPopover'
import Skeleton from '@/components/ui/Skeleton'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import {
  buildContextEntries,
  CONTEXT_INLINE_MAX,
  CONTEXT_LABELS,
  CONTEXT_ORDER,
  type ContextEntry,
  type ContextValue,
} from './contextModel'

export interface ReportContextHeaderProps {
  /** The validated envelope; `null` while loading or when it is unavailable. */
  meta: EnvelopeMeta | null
  allProjects: boolean
  loading?: boolean
  /** Shown when `meta` is null and not loading. */
  unavailableReason?: string
  /** "(max for summary)": the window shown is not the page's own; see `summaryWindow`. */
  windowNote?: string
  className?: string
}

const LABEL = 'text-[length:var(--text-sm)] font-medium text-[var(--color-text-secondary)]'
const VALUE = 'text-[length:var(--text-xl)] font-semibold leading-tight text-[var(--color-text)]'
const TAG =
  'ml-1.5 inline-flex items-center rounded-full border border-[var(--color-border)] px-2 py-px align-middle text-[length:var(--text-sm)] font-medium text-[var(--color-text-secondary)]'
const FOCUS =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-ring)]'

function Value({ value }: { value: ContextValue }) {
  return (
    <span className="inline">
      <span data-context-value="" className={VALUE}>
        {value.text}
      </span>
      {value.tag && (
        <span data-context-tag="" className={TAG}>
          {value.tag}
        </span>
      )}
    </span>
  )
}

/** "a, b, c +4" with a portal popover listing every value. */
function ValueList({ entry }: { entry: ContextEntry }) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const listId = useId()
  const values = entry.values
  const inline = values.slice(0, CONTEXT_INLINE_MAX)
  const rest = values.length - inline.length
  const noun = entry.key === 'suite' ? 'suites' : entry.key === 'release' ? 'releases' : 'values'

  // Focus moves into the list as it opens, so a keyboard user can read it.
  useEffect(() => {
    if (open) listRef.current?.focus()
  }, [open])

  const close = useCallback(() => {
    // Focus inside the popover goes back to its trigger; an outside click keeps its own target.
    const hadFocus = listRef.current?.contains(document.activeElement) ?? false
    setOpen(false)
    if (hadFocus) triggerRef.current?.focus()
  }, [])

  return (
    <>
      {inline.map((value, index) => (
        <span key={`${value.text}-${index}`}>
          {index > 0 && <span className={VALUE}>, </span>}
          <Value value={value} />
        </span>
      ))}
      {rest > 0 && (
        <>
          <button
            ref={triggerRef}
            type="button"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            // The name STARTS with the visible text (WCAG 2.5.3 label in name).
            aria-label={`+${rest} more: show all ${values.length} ${noun}`}
            data-context-more=""
            onClick={() => setOpen((o) => !o)}
            className={`ml-2 inline-flex min-h-6 min-w-6 items-center rounded-full border border-[var(--color-border)] px-2 align-middle text-[length:var(--text-sm)] font-semibold text-[var(--color-accent-ink)] hover:bg-[var(--color-accent-bg-soft)] ${FOCUS}`}
          >
            +{rest}
          </button>
          <HeaderPopover
            anchorRef={triggerRef}
            open={open}
            onClose={close}
            width={280}
            role="dialog"
            ariaLabel={`All ${noun} (${values.length})`}
          >
            <ul
              ref={listRef}
              id={listId}
              tabIndex={-1}
              data-context-popover=""
              onKeyDown={(event) => {
                if (event.key === 'Tab') {
                  event.preventDefault()
                  close()
                }
              }}
              className={`flex flex-col gap-1 p-3 text-sm text-[var(--color-text)] ${FOCUS}`}
            >
              {values.map((value, index) => (
                <li key={`${value.text}-${index}`} className="break-words">
                  {value.text}
                  {value.tag && <span className="ml-1 text-[var(--color-text-secondary)]">({value.tag})</span>}
                </li>
              ))}
            </ul>
          </HeaderPopover>
        </>
      )}
    </>
  )
}

function EntryValue({ entry }: { entry: ContextEntry }) {
  if (entry.key === 'generated' && entry.dateTime) {
    return (
      <time dateTime={entry.dateTime} title={entry.dateTime} data-context-value="" className={VALUE}>
        {entry.values[0]?.text}
      </time>
    )
  }
  if (entry.values.length === 0) {
    return (
      <span data-context-value="" data-context-all="" className={VALUE}>
        {entry.allText}
      </span>
    )
  }
  return <ValueList entry={entry} />
}

export default function ReportContextHeader({
  meta,
  allProjects,
  loading = false,
  unavailableReason = 'Report context is unavailable for this response.',
  windowNote,
  className = '',
}: ReportContextHeaderProps) {
  if (!meta) {
    return (
      <section
        aria-label="Report context"
        aria-busy={loading || undefined}
        data-report-context-header=""
        data-state={loading ? 'loading' : 'unavailable'}
        className={`rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 ${className}`}
      >
        {loading ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {CONTEXT_ORDER.map((key) => (
              <div key={key}>
                <span className={LABEL}>{CONTEXT_LABELS[key]}</span>
                <Skeleton variant="line" height={20} width="80%" className="mt-1" />
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--color-text-secondary)]">{unavailableReason}</p>
        )}
      </section>
    )
  }

  const entries = buildContextEntries({ meta, allProjects, windowNote })
  return (
    <section
      aria-label="Report context"
      data-report-context-header=""
      data-state="ready"
      className={`rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 ${className}`}
    >
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {entries.map((entry) => (
          <div key={entry.key} data-context-entry={entry.key} className="min-w-0">
            <dt className={LABEL}>{entry.label}</dt>
            <dd className="mt-0.5 min-w-0 break-words">
              <EntryValue entry={entry} />
              {entry.note && (
                <>
                  {' '}
                  <span
                    data-context-window-note=""
                    className="text-[length:var(--text-sm)] font-medium text-[var(--color-text-secondary)]"
                  >
                    {entry.note}
                  </span>
                </>
              )}
              {entry.detail && (
                <span className="block text-[length:var(--text-sm)] text-[var(--color-text-secondary)]">
                  {entry.detail}
                </span>
              )}
              {entry.ignoredReason && (
                <span
                  data-context-ignored=""
                  className="mt-0.5 flex items-start gap-1 text-[length:var(--text-sm)] text-[var(--color-text-secondary)]"
                >
                  <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--status-broken)]" />
                  <span>Filter not applied: {entry.ignoredReason}</span>
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
