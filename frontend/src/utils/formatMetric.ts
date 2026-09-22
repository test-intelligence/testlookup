/**
 * One formatter for every headline metric on a report (VIZ-302).
 *
 * Every report's metrics strip goes through `formatMetric`, so "1,234" and
 * "87.3%" read the same on Overview, Trends and the Summary report. The rules:
 *
 *   - counts use the locale's thousands separator; from one million up they
 *     are compact ("1.23M") and the exact figure moves to `exact` (rendered as
 *     `title` and in the accessible name), so precision is relocated, never lost;
 *   - a pass rate has one decimal place and states its basis ("of executions" /
 *     "of unique tests") — the two bases give different, equally correct
 *     numbers (F-067), and a bare percentage invites the reader to call one
 *     screen a liar;
 *   - durations are humanised ("2h 7m") with the exact milliseconds in `exact`;
 *   - a value that was NOT measured is "—" plus the reason, never `0` or
 *     `0%` ("absence is not health"). Negative and non-finite numbers count as
 *     not measured: no metric here can be negative.
 */
import { formatCompact, formatDuration, formatNumber, formatPercent, NO_VALUE } from './formatters'

export type MetricKind = 'count' | 'percent' | 'duration'

/** From this value up a count is shown compact ("1.23M") with the exact value alongside. */
export const COMPACT_COUNT_FROM = 1_000_000

/** The reason shown for a missing value when the caller gives none. */
export const DEFAULT_UNMEASURED_REASON = 'Not measured for this scope'

export interface FormattedMetric {
  /** What is drawn: "1,234", "1.23M", "87.3%", "2m 5s", or "—". */
  text: string
  /** `false` → `text` is "—" and `reason` says why. */
  measured: boolean
  /** The exact value when `text` abbreviates it ("1,234,567", "125,000 ms"). */
  exact?: string
  /** Why there is no value (only when `measured` is false). */
  reason?: string
}

export interface FormatMetricOptions {
  /** Shown on hover and focus when the value is missing. */
  reason?: string
  locale?: string
}

const isValue = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0

export function formatMetric(
  value: number | null | undefined,
  kind: MetricKind,
  { reason, locale }: FormatMetricOptions = {},
): FormattedMetric {
  if (!isValue(value)) {
    const why = reason && reason.trim() !== '' ? reason : DEFAULT_UNMEASURED_REASON
    return { text: NO_VALUE, measured: false, reason: why }
  }
  switch (kind) {
    case 'count':
      if (value >= COMPACT_COUNT_FROM) {
        return { text: formatCompact(value, { locale }), measured: true, exact: formatNumber(value, { locale }) }
      }
      return { text: formatNumber(value, { locale }), measured: true }
    case 'percent':
      return { text: formatPercent(value, { locale }), measured: true }
    case 'duration': {
      const text = formatDuration(value)
      const exact = `${formatNumber(value, { locale })} ms`
      // Under a second the humanised text IS the exact value.
      return text === `${value}ms` ? { text, measured: true } : { text, measured: true, exact }
    }
  }
}

// ── Pass-rate basis ─────────────────────────────────────────────────────────

/**
 * The bases the backend publishes (`PASS_RATE_BASIS_*` in
 * `backend/app/services/metrics_service.py`, and the C2 `pass_rate_basis`
 * enum in `contracts/viz/README.md`). `formatMetric.test.ts` reads the
 * contract row and fails if a published basis has no label here — the
 * frontend mirror of `test_pass_rate_basis_is_published.py`.
 */
export const PASS_RATE_BASIS_LABELS = {
  executions: 'of executions',
  unique_tests: 'of unique tests',
} as const

export type PassRateBasis = keyof typeof PASS_RATE_BASIS_LABELS

/** "of executions" / "of unique tests"; `null` when the basis is not known (never guessed). */
export function passRateBasisLabel(basis: string | null | undefined): string | null {
  if (!basis || !Object.prototype.hasOwnProperty.call(PASS_RATE_BASIS_LABELS, basis)) return null
  return PASS_RATE_BASIS_LABELS[basis as PassRateBasis]
}

// ── Flaky and Unknown ───────────────────────────────────────────────────────

/**
 * Where the Flaky number comes from, stated beside it. It is the backend's
 * flip detector (`_count_flaky_tests`: a 10–90 % failure ratio AND repeated
 * pass↔fail flips over each test's last 10 runs), and it is one of the
 * `DASHBOARD_RELEASE_UNSCOPED` fields — the release filter does not apply.
 */
export const FLAKY_SOURCE = 'pass↔fail flips over each test’s last 10 runs; not filtered by release'

/** Unknown is shown only when it is not zero (and only when it was measured). */
export function shouldShowUnknown(value: number | null | undefined): boolean {
  return isValue(value) && value > 0
}

// ── Delta vs the previous period ────────────────────────────────────────────

/**
 * "▲ 4.2% vs previous period" material, or `null`. A delta is shown only when
 * the previous period is declared `comparable` — a window with a different
 * scope, or no data, makes any percentage change a false signal.
 */
export function formatDelta(
  trend: number | null | undefined,
  comparable: boolean | undefined,
): { direction: 'up' | 'down' | 'flat'; text: string } | null {
  if (comparable !== true || typeof trend !== 'number' || !Number.isFinite(trend)) return null
  const direction = trend > 0 ? 'up' : trend < 0 ? 'down' : 'flat'
  return { direction, text: `${formatPercent(Math.abs(trend))} vs previous period` }
}
