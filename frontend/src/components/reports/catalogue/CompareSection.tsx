/**
 * VIZ-605 — "Compare": the suites and releases chosen in the filter bar,
 * side by side on one line chart over the page's window. "Compare by" offers
 * suite, release, or suite and release (one line per pair, "payments · R1",
 * colour by suite and dash by release), whichever the selection supports; the
 * metric is pass rate, failures or executions. The model is `compareModel`;
 * the requests and their validation are `compareData`.
 *
 * Fewer than two lines, or more than `COMPARE_MAX_SERIES`, is said in the
 * frame (pick more; narrow) rather than drawn as a one-line chart or an
 * "Other" that hides pairs the reader chose.
 */
import { useId, useMemo, useState, type ReactElement } from 'react'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import { buildMultiSeriesModel } from '@/components/charts/multiSeriesModel'
import type { ChartState } from '@/components/charts/chartState'
import { hasChartData } from '@/components/charts/chartStateCore'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { useReleases } from '@/hooks/useReleases'
import { normalizeScope, type ScopeValue } from '@/lib/scopeParams'
import { UNATTRIBUTED_RELEASE } from '@/lib/viz/contracts'
import { clampCatalogueDays, ROW_GRAIN_MAX_WINDOW_DAYS, useCatalogueParams } from './catalogueScope'
import { useCompareData, type CompareData } from './compareData'
import {
  COMPARE_BY_LABELS,
  COMPARE_MAX_SERIES,
  COMPARE_METRICS,
  COMPARE_TITLE,
  compareComparability,
  compareOptions,
  compareRequests,
  compareSeries,
  effectiveCompareBy,
  PICK_MORE_REASON,
  seriesCount,
  stylesForLines,
  tooManyReason,
  type CompareBy,
  type CompareMetric,
} from './compareModel'

const SELECT =
  'rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

export const COMPARE_HEIGHT = 280

export const ONE_DAY_COMPARE_REASON =
  'a one-day window has one point per line, which is not a trend; pick 7 days or more to compare over time'

const ROW_GRAIN = { maxDays: ROW_GRAIN_MAX_WINDOW_DAYS } as const

const notMeasured = (reason: string): ChartState<CompareData> => ({ status: 'not-measured', reason, meta: null })

export interface CompareSectionProps {
  days: number
  /** The page's suite scope: the suites to compare. */
  suiteFilter: ScopeValue
  everHadData: boolean | null
}

export default function CompareSection({ days, suiteFilter, everHadData }: CompareSectionProps): ReactElement {
  const byId = useId()
  const metricId = useId()
  const windowDays = clampCatalogueDays(days, ROW_GRAIN_MAX_WINDOW_DAYS)
  const suites = useMemo(() => normalizeScope(suiteFilter), [suiteFilter])
  const releaseScope = useReleaseScope()
  const releases = useMemo(() => normalizeScope(releaseScope), [releaseScope])
  const { data: releaseData } = useReleases(undefined, { cached: true })

  const [choice, setChoice] = useState<CompareBy | null>(null)
  const [metric, setMetric] = useState<CompareMetric>('pass_rate')
  const options = compareOptions(suites, releases)
  const by = effectiveCompareBy(choice, options)
  const count = by ? seriesCount(by, suites, releases) : 0
  const tooMany = count > COMPARE_MAX_SERIES
  const singleDay = windowDays < 2

  const base = useCatalogueParams(days, suiteFilter, undefined, ROW_GRAIN)
  // `useCompareData` keys on the requests' JSON, so a fresh array per render asks nothing again.
  const requests = by && base && !tooMany && !singleDay ? compareRequests(by, base, metric, suites, releases) : null
  const fetched = useCompareData(requests, everHadData)

  const names = new Map((releaseData?.items ?? []).map((r) => [r.id, r.name]))
  const releaseName = (id: string) => (id === UNATTRIBUTED_RELEASE ? 'Unattributed runs' : (names.get(id) ?? id.slice(0, 8)))

  const state: ChartState<unknown> = !by
    ? notMeasured(PICK_MORE_REASON)
    : tooMany
      ? notMeasured(tooManyReason(count))
      : singleDay
        ? notMeasured(ONE_DAY_COMPARE_REASON)
        : fetched

  let model = null
  if (by && hasChartData(fetched)) {
    const { slices, meta } = fetched.data
    const series = compareSeries(by, slices, releaseName)
    model = buildMultiSeriesModel({
      series,
      metric: { kind: COMPARE_METRICS[metric].kind, title: COMPARE_METRICS[metric].title },
      meta,
      comparability: compareComparability(slices),
      styles: stylesForLines(by, series, suites, releases),
      seriesNoun: 'lines',
    })
  }

  const toolbar = (
    <>
      {options.length > 1 && by ? (
        <label htmlFor={byId} className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
          Compare by
          <select
            id={byId}
            data-compare-by=""
            value={by}
            onChange={(event) => setChoice(event.target.value as CompareBy)}
            className={SELECT}
          >
            {options.map((option) => (
              <option key={option} value={option}>
                {COMPARE_BY_LABELS[option]}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {by ? (
        <label htmlFor={metricId} className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
          Metric
          <select
            id={metricId}
            data-compare-metric=""
            value={metric}
            onChange={(event) => setMetric(event.target.value as CompareMetric)}
            className={SELECT}
          >
            {(Object.keys(COMPARE_METRICS) as CompareMetric[]).map((key) => (
              <option key={key} value={key}>
                {COMPARE_METRICS[key].label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </>
  )

  const takeaway =
    by === 'suite_release'
      ? 'Colour is the suite; the dash is the release.'
      : by
        ? `One line per ${by === 'suite' ? 'suite' : 'release'} you chose.`
        : undefined

  return (
    <div data-catalogue-section="trends-compare" data-compare-by-active={by ?? ''} className="min-w-0">
      <MultiSeriesChartFrame
        title={COMPARE_TITLE}
        takeaway={takeaway}
        headingLevel={3}
        height={COMPARE_HEIGHT}
        state={state}
        model={model}
        toolbar={toolbar}
        scopeLabel={`last ${windowDays} days`}
      />
    </div>
  )
}
