import { formatDistanceToNow, format, isSameDay } from 'date-fns'

export const formatDate = (d: string | Date) => format(new Date(d), 'MMM dd, yyyy')
export const formatDateTime = (d: string | Date) => format(new Date(d), 'MMM dd, HH:mm')
export const fromNow = (d: string | Date) => formatDistanceToNow(new Date(d), { addSuffix: true })

/**
 * Compact relative age: 'just now' / '2m ago' / '3h ago' / '5d ago'.
 *
 * Distinct from `fromNow` (date-fns prose, e.g. 'about 2 hours ago') — this is
 * the terse form the provenance "last refreshed" lines use, where the value sits
 * inline in a dense metadata row.
 *
 * Future timestamps and unparseable input both collapse to 'just now' rather
 * than rendering a negative age or 'Invalid Date'.
 */
export const shortAgo = (d: string | number | Date): string => {
  const ms = Date.now() - new Date(d).getTime()
  if (!Number.isFinite(ms) || ms < 0) return 'just now'
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/**
 * Compact, null-safe run timestamp for disambiguating runs that share a
 * human-readable number. "Run #1" repeats per (project, suite) and across
 * projects, so the same label can point at many different executions — pairing
 * it with *when the run was generated* makes each row identifiable at a glance.
 *
 * Returns '' for missing/invalid input (callers can `&&`-guard the suffix) so a
 * legacy row with no timestamp never renders "Invalid Date". Formatted in the
 * browser's LOCAL timezone (matching `formatDate`/`formatDateTime`).
 */
export const formatRunWhen = (d?: string | Date | null): string => {
  if (!d) return ''
  const date = new Date(d)
  if (Number.isNaN(date.getTime())) return ''
  return format(date, 'MMM dd, HH:mm')
}

/**
 * Relative label for a DAY-bucketed calendar date (``yyyy-mm-dd``) — e.g. a
 * trend point's day used for "Last run".
 *
 * Parsed at LOCAL midnight (no ``Z``/offset) and compared by whole calendar
 * days, so "today" never resolves to a *future* UTC instant for users east or
 * west of UTC — the bug that made "Last run —" appear near day boundaries when
 * the value was forced to ``…T00:00:00Z`` and the resulting age went negative.
 * Day-granular output matches the day-granular input (no fake "X min ago"
 * precision on a midnight value). Empty/invalid → '—'.
 */
export const dayTimeAgo = (dayOnly?: string | null): string => {
  if (!dayOnly) return '—'
  const then = new Date(`${dayOnly}T00:00:00`) // no 'Z' → LOCAL time
  if (Number.isNaN(then.getTime())) return '—'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const days = Math.round((startOfToday.getTime() - then.getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days} d ago`
}

/**
 * Human duration for a millisecond count. '—' means *no value* (missing, NaN,
 * or an invalid negative from clock skew); a genuine zero renders '0ms', not
 * '—'. A test step that ran in under a millisecond has a KNOWN, instantaneous
 * duration. Collapsing it into the same dash used for "unknown" told the
 * reader the step wasn't timed when it was. Output is tiered so each scale
 * drops the unit below the
 * noise floor: sub-second in ms, sub-minute as one-decimal seconds, sub-hour as
 * whole minutes + seconds, and an hour or more as whole hours + minutes.
 *
 * The hours tier matters because this formats real run durations — a run's
 * total, the "Avg run duration" tile, a slow test case — and a large CI suite
 * routinely runs past an hour. Without it a 2h07m run read as `127m 3s`, which
 * a human has to divide in their head; `2h 7m` is legible at a glance. Seconds
 * are dropped at the hour scale for the same reason ms are dropped past a
 * second — at that magnitude they are noise, not signal. Sub-hour output is
 * unchanged.
 */
export const formatDuration = (ms?: number | null): string => {
  if (ms == null || Number.isNaN(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`
  return `${Math.floor(ms / 3_600_000)}h ${Math.floor((ms % 3_600_000) / 60_000)}m`
}

export const formatPassRate = (rate?: number | null): string =>
  rate != null ? `${rate.toFixed(1)}%` : '—'

export const statusColor = (status: string): string => ({
  PASSED:  'text-[var(--status-passed)]',
  FAILED:  'text-[var(--status-failed)]',
  BROKEN:  'text-[var(--status-broken)]',
  SKIPPED: 'text-[var(--status-skipped)]',
  UNKNOWN: 'text-[var(--color-text-secondary)]',
}[status?.toUpperCase()] ?? 'text-[var(--color-text-secondary)]')

export const categoryColor = (cat: string): string => ({
  PRODUCT_BUG:      'text-[var(--status-failed)]',
  INFRASTRUCTURE:   'text-[var(--status-broken)]',
  TEST_DATA:        'text-[var(--status-broken)]',
  AUTOMATION_DEFECT:'text-[var(--status-flaky)]',
  FLAKY:            'text-[var(--status-flaky)]',
  UNKNOWN:          'text-[var(--color-text-secondary)]',
}[cat?.toUpperCase()] ?? 'text-[var(--color-text-secondary)]')

export const confidenceColor = (score: number): string => {
  if (score >= 80) return 'text-[var(--status-passed)]'
  if (score >= 60) return 'text-[var(--status-broken)]'
  return 'text-[var(--status-failed)]'
}

/**
 * Compact wall-clock stamp for dense table cells: 'Aug 28, 14:32'.
 *
 * Distinct from `formatDateTime` ('MMM dd') — this uses a non-padded day so the
 * cell stays narrow for single-digit dates ('Aug 8', not 'Aug 08'). Full-length
 * `toLocaleString()` values were what pushed the Started/End columns off-screen
 * on /intelligence, /runs and /live; callers pair this with `isoTooltip` so the
 * exact instant is still one hover away.
 *
 * Missing/unparseable input returns the em-dash placeholder rather than
 * 'Invalid Date'.
 */
export const formatCompactDateTime = (d?: string | Date | null): string => {
  if (!d) return '—'
  const date = new Date(d)
  if (Number.isNaN(date.getTime())) return '—'
  return format(date, 'MMM d, HH:mm')
}

/** The two ends of a Timing range, pre-formatted, plus whether it crossed a day. */
export interface TimingRangeParts {
  /** Start stamp, always present: 'Aug 28, 23:50'. */
  head: string
  /**
   * End stamp, or null when there is no parseable end (still running — the
   * caller renders '…'). Time-only ('00:12') when same-day, date-qualified
   * ('Aug 29, 00:12') when the run crossed a calendar day.
   */
  tail: string | null
  /**
   * True when start and end fall on different calendar days. Computed by
   * comparing the two instants directly (`isSameDay`), NOT by sniffing the
   * formatted string — so it cannot be broken by a change to how the stamps
   * read. `TimingCell` uses it to stack the range onto two lines, because a
   * cross-day range on one nowrap line (~230px) overflows its narrow column.
   */
  crossDay: boolean
}

/**
 * Structured start→end range for a Timing cell. `formatTimingRange` collapses
 * this to one string; `TimingCell` reads `crossDay` to choose its layout.
 *
 * Returns null when there is no parseable start — the caller renders the
 * em-dash placeholder rather than 'Invalid Date'.
 */
export const timingRangeParts = (
  start?: string | Date | null,
  end?: string | Date | null,
): TimingRangeParts | null => {
  if (!start) return null
  const from = new Date(start)
  if (Number.isNaN(from.getTime())) return null

  const head = format(from, 'MMM d, HH:mm')
  if (!end) return { head, tail: null, crossDay: false }

  const to = new Date(end)
  if (Number.isNaN(to.getTime())) return { head, tail: null, crossDay: false }

  const crossDay = !isSameDay(from, to)
  return { head, tail: format(to, crossDay ? 'MMM d, HH:mm' : 'HH:mm'), crossDay }
}

/**
 * Collapsed start→end range for a single "Timing" cell.
 *
 * Same calendar day  → 'Aug 28, 14:32 → 14:46' (the date is not repeated).
 * Spanning midnight  → 'Aug 28, 23:50 → Aug 29, 00:12' (it MUST repeat, or an
 *                      overnight run reads as a 23-hour-negative duration).
 * Still running      → 'Aug 28, 14:32 → …'
 * No start at all    → '—'
 *
 * The same-day collapse is what frees the ~240px that the two separate
 * nowrap columns consumed.
 */
export const formatTimingRange = (
  start?: string | Date | null,
  end?: string | Date | null,
): string => {
  const parts = timingRangeParts(start, end)
  if (!parts) return '—'
  return `${parts.head} → ${parts.tail ?? '…'}`
}

/**
 * Full-precision ISO text for a Timing cell's `title` tooltip.
 *
 * The compact cell is deliberately lossy (no seconds, no year, no timezone), so
 * the tooltip carries the unabbreviated instants — otherwise shortening the
 * column would destroy information rather than relocate it.
 */
export const isoTooltip = (
  start?: string | Date | null,
  end?: string | Date | null,
  live = false,
): string => {
  const label = (d?: string | Date | null): string => {
    if (!d) return 'not finished'
    const date = new Date(d)
    return Number.isNaN(date.getTime()) ? 'unknown' : date.toISOString()
  }
  if (!start) return 'No start time recorded'
  // A live session's second instant is the LAST EVENT SEEN, not an end. Calling
  // it "Ended" would assert the run finished at a moment it is still running.
  const tail = live
    ? `Still running · last event ${label(end)}`
    : `Ended ${label(end)}`
  return `Started ${label(start)}
${tail}`
}

/**
 * Human-readable byte size. Binary units (KiB/MiB) because that is what object
 * stores report.
 *
 * Takes a NUMBER, not `number | null`, on purpose: "not measured" is a
 * rendering decision the caller has to make visibly, not something a formatter
 * should quietly turn into a dash. A store that could not be reached and a
 * store holding 0 B must not format alike.
 */
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  const exponent = Math.min(
    Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)),
    units.length - 1,
  )
  const value = bytes / Math.pow(1024, exponent)
  // One decimal above bytes; whole numbers for raw bytes.
  return exponent === 0 ? `${bytes} B` : `${value.toFixed(1)} ${units[exponent]}`
}
