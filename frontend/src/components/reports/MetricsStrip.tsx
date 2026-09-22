/**
 * The metrics strip (VIZ-302): the same headline metrics, formatted the same
 * way, on every report. Reuses `MetricCard` for the tile (value ≥
 * `--text-stat-lg`, status icon, trend) and adds what a bare card cannot say:
 *
 *   - the pass-rate BASIS and the Flaky SOURCE, printed under the value;
 *   - for an abbreviated or unmeasured value, the exact figure or the reason,
 *     as VISIBLE text under the value (no hover-only `title`, no tab stop
 *     needed to reveal it);
 *   - DOM order = visual order;
 *   - columns from the strip's own width (auto-fill, min 13rem), so a tile is
 *     never squeezed beside the sidebar.
 *
 * A metric a page does not use is omitted with `include`, never shown as "—".
 * A delta is drawn only when the previous period is comparable — pass rate in
 * percentage points, counts and durations in relative percent — and a
 * comparable metric with no number says why ("New: 0 in the previous
 * period"). When the server says why the periods are not comparable, that
 * reason is printed under the strip.
 */
import {
  CircleAlert,
  CircleCheck,
  CircleHelp,
  CircleSlash,
  CircleX,
  Clock,
  ListChecks,
  Percent,
  Play,
  Shuffle,
  Timer,
  type LucideIcon,
} from 'lucide-react'
import MetricCard from '@/components/ui/MetricCard'
import Skeleton from '@/components/ui/Skeleton'
import { buildStripMetrics, METRIC_ORDER, type MetricId, type ReportMetricsInput, type StripMetric } from './metricsModel'

const ICONS: Record<MetricId, LucideIcon> = {
  runs: Play,
  total_tests: ListChecks,
  passed: CircleCheck,
  failed: CircleX,
  broken: CircleAlert,
  skipped: CircleSlash,
  unknown: CircleHelp,
  flaky: Shuffle,
  pass_rate: Percent,
  total_duration: Clock,
  avg_duration: Timer,
}

const ACCENT: Record<StripMetric['tone'], string> = {
  passed: 'green',
  failed: 'red',
  broken: 'amber',
  flaky: 'purple',
  skipped: 'default',
  unknown: 'default',
  neutral: 'default',
}

function Tile({ metric, loading }: { metric: StripMetric; loading: boolean }) {
  const Icon = ICONS[metric.id]
  const { formatted } = metric
  const hint = loading
    ? undefined
    : formatted.measured
    ? formatted.exact
      ? `Exact value: ${formatted.exact}`
      : undefined
    : `Not measured: ${formatted.reason}`
  // While loading nothing is known yet: no basis/source note either ("basis
  // not stated" would be a claim about a response that has not arrived).
  const note = loading ? undefined : metric.note
  return (
    <div
      role="group"
      aria-label={metric.title}
      data-metric={metric.id}
      data-measured={loading ? undefined : formatted.measured ? 'true' : 'false'}
      className="flex min-w-0 flex-col gap-1 rounded-xl"
    >
      {loading ? (
        // Not MetricCard's own `loading`: its placeholder is a role-less div
        // with aria-label (axe aria-prohibited-attr, serious). Epic 1's
        // Skeleton is aria-hidden; the strip owns the busy state.
        <div className="card flex flex-col gap-2" data-metric-loading="">
          <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">{metric.title}</p>
          <Skeleton variant="block" height={36} width={96} />
        </div>
      ) : (
        <MetricCard
          title={metric.title}
          icon={<Icon aria-hidden="true" className="h-5 w-5" />}
          accentColor={ACCENT[metric.tone]}
          positiveDirection={metric.positive}
          metric={{
            value: formatted.text,
            trend: metric.trend,
            trend_direction: metric.delta?.direction,
            trend_text: metric.delta?.text,
          }}
        />
      )}
      {/* Visible text, not a `title` tooltip: the reason for a "—" and the
          exact figure behind "1.23M" are readable without a mouse (WCAG
          1.4.13 / 2.1.1), and exposed once — as the group's own content. */}
      {(note || hint) && (
        <div className="flex flex-col px-1 text-xs text-[var(--color-text-secondary)]">
          {note && <span data-metric-note="">{note}</span>}
          {hint && <span data-metric-hint="">{hint}</span>}
        </div>
      )}
    </div>
  )
}

/** The narrowest a tile gets (the strip's container decides how many fit). */
export const STRIP_TILE_MIN = '13rem'
/** `min(100%, …)`: a strip narrower than one tile (320 px reflow) is one full-width column, never overflow. */
const STRIP_GRID_COLUMNS = 'grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))]'

export interface MetricsStripProps {
  input: ReportMetricsInput
  /** Metrics this page shows; default all. */
  include?: readonly MetricId[]
  /** While the response is in flight: placeholders, never "—" (that would claim "not measured"). */
  loading?: boolean
  className?: string
}

export default function MetricsStrip({ input, include = METRIC_ORDER, loading = false, className = '' }: MetricsStripProps) {
  const metrics = buildStripMetrics(input, include)
  // Why no delta is drawn, when the server said (C6 `previous.reason`). Only
  // a real reason: an input that never compared says nothing.
  const comparison = !loading && input.comparable !== true ? input.comparisonReason?.trim() : undefined
  return (
    <section
      aria-label="Report metrics"
      aria-busy={loading || undefined}
      data-metrics-strip=""
      className={className}
    >
      {loading && <p className="sr-only">Loading report metrics</p>}
      {/* Columns from the strip's OWN width, not the viewport's: beside the
          sidebar at 1280 px a viewport breakpoint gave six 153 px tiles whose
          values wrapped. A tile is never narrower than STRIP_TILE_MIN. */}
      <div data-metrics-grid="" className={`grid gap-3 ${STRIP_GRID_COLUMNS}`}>
        {metrics.map((metric) => (
          <Tile key={metric.id} metric={metric} loading={loading} />
        ))}
      </div>
      {comparison && (
        <p data-comparison-reason="" className="mt-2 px-1 text-xs text-[var(--color-text-secondary)]">
          No change vs the previous period is shown: {comparison}
        </p>
      )}
    </section>
  )
}
