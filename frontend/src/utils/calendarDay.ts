/**
 * Calendar-day helpers for the day-bucketed analytics endpoints.
 *
 * The backend groups by `DATE_TRUNC('day', tr.created_at)` and serialises the
 * bucket as a bare `YYYY-MM-DD`. Those are **UTC** calendar days, and they are
 * labels, not instants — so nothing here converts one to a local time.
 *
 * The bug this was written for: `new Date('2026-08-16')` parses as UTC
 * *midnight*, so `.toLocaleDateString()` renders "Aug 15" anywhere west of
 * Greenwich. Every date on /trends was a day early — the run-cadence heatmap,
 * the daily-breakdown axis, the gap narrative, and the axis tick labelled
 * "(today)", which named yesterday.
 *
 * The window builders these replace read a *local* calendar date and stamped
 * the cells with `toISOString()`. That mix turns out to produce the same day
 * sequence as staying in UTC (`setDate` on a copy moves whole local days, and
 * the UTC stamp tracks it, DST included — checked against the 2026 US
 * transitions). It is consolidated here for one frame end-to-end, not because
 * it was miscounting.
 */

const MS_PER_DAY = 24 * 60 * 60 * 1000
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DAY_ISO_RE = /^(\d{4})-(\d{2})-(\d{2})/

/** The UTC calendar day an instant falls in, as `YYYY-MM-DD`. */
export function utcDayIso(at: Date = new Date()): string {
  return at.toISOString().slice(0, 10)
}

/** `iso` shifted by whole days, staying in the UTC frame. */
export function shiftDayIso(iso: string, deltaDays: number): string {
  const ms = dayIsoToUtcMs(iso)
  if (ms == null) return iso
  return new Date(ms + deltaDays * MS_PER_DAY).toISOString().slice(0, 10)
}

/** UTC midnight of a `YYYY-MM-DD` day, or null if it isn't one. */
export function dayIsoToUtcMs(iso: string): number | null {
  const m = DAY_ISO_RE.exec(iso)
  if (!m) return null
  const ms = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return Number.isNaN(ms) ? null : ms
}

/**
 * "Aug 16" — formatted from the string's own parts, so the label always names
 * the day the backend bucketed, in every timezone.
 */
export function formatDayIso(iso: string): string {
  const m = DAY_ISO_RE.exec(iso)
  if (!m) return iso
  const month = MONTHS[Number(m[2]) - 1]
  if (!month) return iso
  return `${month} ${Number(m[3])}`
}

/** Whole days between two calendar days, `later - earlier`. */
export function daysBetweenDayIso(laterIso: string, earlierIso: string): number | null {
  const a = dayIsoToUtcMs(laterIso)
  const b = dayIsoToUtcMs(earlierIso)
  if (a == null || b == null) return null
  return Math.round((a - b) / MS_PER_DAY)
}

/**
 * How long ago a calendar day was, at the granularity the input actually has.
 *
 * The old helper ran `Date.now() - new Date(iso)` on these day strings and
 * reported hours, so a run ingested minutes ago read "23h ago" — precision the
 * value never carried, pointing at the wrong day.
 */
export function relativeDayLabel(iso: string | null, now: Date = new Date()): string {
  if (!iso) return '—'
  const days = daysBetweenDayIso(utcDayIso(now), iso)
  if (days == null || days < 0) return '—'
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days}d ago`
}
