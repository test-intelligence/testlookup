/**
 * The release KPI cards' trend, as the cards draw it (W11 REL-03). Pure: the
 * sparkline series and their per-point titles, and the two captions, all
 * from the server's `ReleasePortfolioTrend` (`release_portfolio_service`).
 *
 * The definitions are the server's (each card's own rule at Monday week ends
 * and now). Three series rest on TODAY's status, there being no status
 * history yet: every past point of those says so in its title. A `null`
 * point is not measured (before the scope's first release): it stays `null`
 * (the line breaks, no hover target), never 0.
 */
import type { ReleasePortfolioTotals, ReleasePortfolioTrend } from '@/types/releases'

export type PortfolioSeriesKey = 'in_progress' | 'ready_to_ship' | 'blocked' | 'released_30d'

export const PORTFOLIO_SERIES: readonly PortfolioSeriesKey[] = ['in_progress', 'ready_to_ship', 'blocked', 'released_30d']

/** What each count is, in a point's title ("2 in progress"). */
const NOUN: Record<PortfolioSeriesKey, string> = {
  in_progress: 'in progress',
  ready_to_ship: 'ready to ship',
  blocked: 'blocked',
  released_30d: 'released in 30 days',
}

/** The card a series belongs to, in the sparkline's accessible name. */
const CARD: Record<PortfolioSeriesKey, string> = {
  in_progress: 'In progress',
  ready_to_ship: 'Ready to ship',
  blocked: 'Blocked',
  released_30d: 'Released · 30d',
}

export const APPROXIMATION_NOTE = "approximation: today's status"
export const TREND_LOAD_ERROR = 'Could not load the 12-week trend.'

const SHORT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** `13 Oct` from an instant, as its UTC day (the points are Mondays 00:00 UTC). */
export function utcDayLabel(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return `${at.getUTCDate()} ${SHORT_MONTHS[at.getUTCMonth()]}`
}

export interface PointTitleExtra {
  /** The point is the final one (now). */
  isNow?: boolean
  /** The series rests on today's status. */
  approximated?: boolean
  /** Ready to ship: the releases evaluated in the 7 days before the point, and those whose latest call was GO. */
  evaluatedInWeek?: number
  goInWeek?: number
  /** The card's own client count differs from the trend's now point. */
  stale?: boolean
}

/**
 * One point's hover text: `Week to 13 Oct: 2 in progress (approximation:
 * today's status)` / `Week to 13 Oct: 1 ready to ship · GO 1 of 3 evaluated
 * that week (…)` / `Now: 2 in progress` / `Now (as of the trend read): 2 in
 * progress`. `null` for a point that was not measured.
 */
export function pointTitle(key: PortfolioSeriesKey, at: string, value: number | null, extra: PointTitleExtra = {}): string | null {
  if (value == null) return null
  const when = extra.isNow ? (extra.stale ? 'Now (as of the trend read)' : 'Now') : `Week to ${utcDayLabel(at)}`
  let text = `${when}: ${value} ${NOUN[key]}`
  if (key === 'ready_to_ship' && extra.evaluatedInWeek != null && !extra.isNow) {
    text += ` · GO ${extra.goInWeek ?? 0} of ${extra.evaluatedInWeek} evaluated that week`
  }
  if (extra.approximated && !extra.isNow) text += ` (${APPROXIMATION_NOTE})`
  return text
}

export interface PortfolioSparkline {
  series: (number | null)[]
  pointTitles: (string | null)[]
  /** The accessible name's start. */
  label: string
  /** At least two measured points: the line can be drawn (the Sparkline draws nothing below two). */
  drawable: boolean
}

/**
 * The series of one card, its point titles and its label. `clientNow` is the
 * card's own count: when the list was refreshed after the trend and they
 * differ, the card's number stands and the last title says the trend's point
 * is "as of the trend read".
 */
export function sparklineFor(trend: ReleasePortfolioTrend, key: PortfolioSeriesKey, clientNow?: number): PortfolioSparkline {
  const series = (trend[key] ?? []).map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null))
  const approximated = (trend.approximated ?? []).includes(key)
  const last = series.length - 1
  const pointTitles = series.map((value, i) =>
    pointTitle(key, trend.points_at[i] ?? '', value, {
      isNow: i === last,
      approximated,
      evaluatedInWeek: trend.evaluated_in_week?.[i],
      goInWeek: trend.go_in_week?.[i],
      stale: i === last && clientNow != null && value != null && value !== clientNow,
    }),
  )
  const measured = series.filter((v) => v != null).length
  return {
    series,
    pointTitles,
    label: `${CARD[key]}, ${trend.weeks} weeks`,
    drawable: measured >= 2,
  }
}

/** Ready to ship's caption: a count over its stated denominator, never a bare percentage. */
export function readyCaption(totals: ReleasePortfolioTotals, weeks: number): string {
  return totals.evaluated > 0
    ? `GO on ${totals.go} of ${totals.evaluated} evaluated (${weeks} w)`
    : `No releases evaluated in ${weeks} weeks`
}

/** Released · 30d's caption: the released releases that still hold a pack (a retired pack is not coverage). */
export function packCaption(totals: ReleasePortfolioTotals, weeks: number): string {
  return totals.released > 0
    ? `${totals.released_with_pack} of ${totals.released} released with a pack (${weeks} w)`
    : `No releases released in ${weeks} weeks`
}
