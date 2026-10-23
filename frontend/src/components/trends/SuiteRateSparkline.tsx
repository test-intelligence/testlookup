/**
 * TRND-06 (a): one Suite pass rates row's sparkline — the suite's daily pass
 * rate over the page's window, the SAME line "Pass rate by suite" draws, from
 * the SAME request (`useSuiteSparklines`: one `top_n=7` read serves every row
 * and the chart; SWR dedupes the identical key).
 *
 * A LAZY module (`TrendsPage` imports it with `import()`): the request
 * pipeline it needs (`chartCatalogSources`, the chart request queue) is
 * section-only code that must stay out of the page's static closure
 * (`sectionOnlyModules.test.ts`). It mounts only once the card is near (the
 * card's `LazySection`), so nothing is asked before.
 *
 * A day without a measured rate is a break in the line, never 0 %. A suite not
 * among the chart's 7 busiest draws "—" with the reason; so does a one-day
 * window (one point is not a trend) and an unavailable series.
 */
import Sparkline, { type SparklineTone } from '@/components/charts/Sparkline'
import { NOT_AMONG_BUSIEST, ONE_DAY_SUITES_REASON, SUITE_SERIES_TITLE } from '@/components/reports/catalogue/trendsCatalogueParams'
import { useSuiteSparklines } from '@/hooks/useSuiteSparklines'
import type { ScopeValue } from '@/lib/scopeParams'

export const SPARKLINE_UNAVAILABLE = `The daily pass rates of "${SUITE_SERIES_TITLE}" are not available`

const PERCENT_DOMAIN = [0, 100] as const
const formatPercent = (v: number) => `${v.toFixed(1)}%`

export interface SuiteRateSparklineProps {
  days: number
  suiteFilter: ScopeValue
  suiteName: string
  tone: SparklineTone
  /** The slot's height, px (the row reserves it, so nothing moves when the line arrives). */
  height: number
}

export default function SuiteRateSparkline({ days, suiteFilter, suiteName, tone, height }: SuiteRateSparklineProps) {
  const sparks = useSuiteSparklines(days, suiteFilter, true)
  const line = sparks.lineOf(suiteName)
  const oneDay = days < 2
  if (!oneDay && sparks.state === 'loading') return null
  if (line !== undefined && line.some((value) => value !== null)) {
    return (
      <div data-sparkline="">
        <Sparkline
          series={line}
          label={`${suiteName} pass rate per day`}
          domain={PERCENT_DOMAIN}
          format={formatPercent}
          tone={tone}
          height={height}
        />
      </div>
    )
  }
  const reason = oneDay ? ONE_DAY_SUITES_REASON : sparks.state === 'unavailable' ? SPARKLINE_UNAVAILABLE : NOT_AMONG_BUSIEST
  return (
    <span data-sparkline-missing="" title={reason} className="text-2xs text-[var(--color-text-muted)]" style={{ lineHeight: `${height}px` }}>
      —
    </span>
  )
}
