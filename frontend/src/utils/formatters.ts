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
 * legacy row with no timestamp never renders "Invalid Date".
 */
export const formatRunWhen = (d?: string | Date | null): string => {
  if (!d) return ''
  const date = new Date(d)
  if (Number.isNaN(date.getTime())) return ''
  return format(date, 'MMM dd, HH:mm')
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
