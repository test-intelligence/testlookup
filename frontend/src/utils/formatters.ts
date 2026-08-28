import { formatDistanceToNow, format } from 'date-fns'

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
 * duration — collapsing it into the same dash used for "unknown" told the
 * reader the step wasn't timed when it was (the old `if (!ms)` guard treated
 * `0` as falsy).
 */
export const formatDuration = (ms?: number | null): string => {
  if (ms == null || Number.isNaN(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`
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
