import { clsx } from 'clsx'
import { formatDuration, isoTooltip, shortAgo, timingRangeParts } from '@/utils/formatters'

/**
 * One table cell carrying a run's whole timing story: when it started, when it
 * ended, how long it took, and how long ago that was.
 *
 * Replaces the pair of `Started` / `End` columns that /intelligence, /runs and
 * /live each rendered as full `toLocaleString()` values under
 * `whitespace-nowrap`. Two ~200px nowrap columns inside an `overflow-x-auto`
 * wrapper pushed the trailing columns off-screen with no scrollbar cue; the
 * same information collapses into ~158px here.
 *
 * Deliberately NOT breakpoint-hidden. The previous `hidden md:table-cell` made
 * the column vanish below 768px — on /intelligence it is the SORT KEY, so the
 * table silently dropped the very field it was ordered by. Nothing here is
 * hidden at any width; the compact form is what makes that affordable.
 *
 * Precision is relocated, not discarded: the `title` tooltip carries the full
 * ISO instants for both ends.
 */
export function TimingCell({
  started,
  end,
  durationMs,
  live = false,
  className,
}: {
  /** Run start. Missing/unparseable renders an em-dash rather than 'Invalid Date'. */
  started?: string | Date | null
  /** Run end. Null means still running — rendered as '→ …', never as a fake end. */
  end?: string | Date | null
  /** Elapsed milliseconds. `null` renders no duration rather than a bogus '0ms'. */
  durationMs?: number | null
  /**
   * The session has NOT finished and `end` is its last-seen event, not an end
   * time. /live needs this: closing a still-running session with a hard end
   * time would report a finish that never happened.
   */
  live?: boolean
  className?: string
}) {
  const range = timingRangeParts(started, end)
  const hasStart = range != null
  const duration = durationMs != null ? formatDuration(durationMs) : null
  // Only claim an age when there is a real start instant to measure from.
  const age = hasStart ? shortAgo(started as string | Date) : null
  const sub = live
    ? ['live', end ? `last event ${shortAgo(end)}` : null].filter(Boolean).join(' · ')
    : [duration, age].filter(Boolean).join(' · ')

  return (
    <td
      // overflow-hidden clamps the range instead of letting an over-wide
      // nowrap line bleed LEFT over the neighbouring PASS RATE column — the
      // cross-day overlap this cell's stacking (below) is built to prevent.
      className={clsx('px-3.5 py-2 text-right align-middle overflow-hidden', className)}
      title={isoTooltip(started, end, live)}
    >
      <div className="flex flex-col items-end gap-px leading-tight">
        {range == null ? (
          <span className="tabular-nums text-[var(--color-text)]">—</span>
        ) : range.crossDay ? (
          // A cross-day range on one nowrap line is ~230px and overflows the
          // 158px column, so stack it onto two right-aligned lines instead.
          // Widening the column re-crowds the table at laptop widths, which is
          // the problem this compact cell was built to solve.
          <span className="tabular-nums text-[var(--color-text)] whitespace-nowrap text-right">
            <span className="block">{range.head} →</span>
            <span className="block">{range.tail}</span>
          </span>
        ) : (
          <span className="tabular-nums text-[var(--color-text)] whitespace-nowrap">
            {`${range.head} → ${range.tail ?? '…'}`}
          </span>
        )}
        {sub && (
          <span
            className={clsx(
              'text-[11px] whitespace-nowrap',
              live ? 'text-[var(--status-flaky)]' : 'text-[var(--color-text-muted)]',
            )}
          >
            {sub}
          </span>
        )}
      </div>
    </td>
  )
}
