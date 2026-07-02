import { formatDistanceToNow, format } from 'date-fns'

export const formatDate = (d: string | Date) => format(new Date(d), 'MMM dd, yyyy')
export const formatDateTime = (d: string | Date) => format(new Date(d), 'MMM dd, HH:mm')
export const fromNow = (d: string | Date) => formatDistanceToNow(new Date(d), { addSuffix: true })

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

export const formatDuration = (ms?: number | null): string => {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`
}

export const formatPassRate = (rate?: number | null): string =>
  rate != null ? `${rate.toFixed(1)}%` : '—'

export const statusColor = (status: string): string => ({
  PASSED:  'text-emerald-400',
  FAILED:  'text-red-400',
  BROKEN:  'text-orange-400',
  SKIPPED: 'text-amber-400',
  UNKNOWN: 'text-neutral-400',
}[status?.toUpperCase()] ?? 'text-neutral-400')

export const categoryColor = (cat: string): string => ({
  PRODUCT_BUG:      'text-red-400',
  INFRASTRUCTURE:   'text-orange-400',
  TEST_DATA:        'text-amber-400',
  AUTOMATION_DEFECT:'text-purple-400',
  FLAKY:            'text-pink-400',
  UNKNOWN:          'text-neutral-400',
}[cat?.toUpperCase()] ?? 'text-neutral-400')

export const confidenceColor = (score: number): string => {
  if (score >= 80) return 'text-emerald-400'
  if (score >= 60) return 'text-amber-400'
  return 'text-red-400'
}
