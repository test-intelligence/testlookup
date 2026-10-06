/**
 * VIZ-605 — "Compare": chosen suites and releases side by side on one line
 * chart over the page's window. "Compare by" offers suite, release, or suite
 * and release (one line per pair, "payments · R1", colour by suite and dash
 * by release), whichever the selection supports; the metric is pass rate,
 * failures or executions. The model is `compareModel`; the requests and their
 * validation are `compareData`.
 *
 * C1 (Phase D): the card picks its OWN suites and releases (Suites /
 * Releases in its toolbar, kept in the URL). It read the filter bar's before,
 * and the filter bar is single-select, so no selection could reach two lines.
 * Until the reader chooses, it starts from the busiest suites of "Pass rate
 * by suite" and the top-bar release.
 *
 * Fewer than two lines, or more than `COMPARE_MAX_SERIES`, is said in the
 * frame (pick more; narrow) rather than drawn as a one-line chart or an
 * "Other" that hides pairs the reader chose.
 */
import { useContext, useId, useMemo, useState, type ReactElement } from 'react'
import { useSearchParams } from 'react-router-dom'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import { buildMultiSeriesModel } from '@/components/charts/multiSeriesModel'
import type { ChartState } from '@/components/charts/chartState'
import { hasChartData } from '@/components/charts/chartStateCore'
import MultiSelect, { type MultiSelectOption } from '@/components/ui/MultiSelect'
import { PageSuiteTargetContext } from '@/hooks/pageSuiteTarget'
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
  COMPARE_URL_KEYS,
  compareComparability,
  compareOptions,
  compareRequests,
  compareSeries,
  effectiveCompareBy,
  PICK_MORE_REASON,
  readCompareChoice,
  seriesCount,
  stylesForLines,
  tooManyReason,
  writeCompareChoice,
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

/** A URL choice as one comparable string: `null` when the key is absent. */
const choiceParam = (params: URLSearchParams, key: string): string | null =>
  params.has(key) ? params.getAll(key).join('\n') : null

const fromChoiceParam = (key: string, value: string | null): string[] | null =>
  value === null ? null : readCompareChoice(new URLSearchParams(value.split('\n').map((v) => [key, v])), key)

const NO_SUITES: readonly string[] = []

export const COMPARE_PICKER_LABELS = { suite: 'Suites', release: 'Releases' } as const

export const NO_RELEASES_REASON = 'Choose one project to compare releases'

export const BUSIEST_TAKEAWAY = 'The busiest suites in the window; choose others with Suites.'

export interface CompareSectionProps {
  days: number
  /** The page's suite scope: the rest of the page's filter, and a default suite when it names one. */
  suiteFilter: ScopeValue
  everHadData: boolean | null
  /** The busiest suites of "Pass rate by suite", the starting selection (`defaultCompareSuites`). */
  busiestSuites?: readonly string[]
}

export default function CompareSection({ days, suiteFilter, everHadData, busiestSuites = NO_SUITES }: CompareSectionProps): ReactElement {
  const byId = useId()
  const metricId = useId()
  const windowDays = clampCatalogueDays(days, ROW_GRAIN_MAX_WINDOW_DAYS)
  const pageSuites = useMemo(() => normalizeScope(suiteFilter), [suiteFilter])
  const releaseScope = useReleaseScope()
  const pageReleases = useMemo(() => normalizeScope(releaseScope), [releaseScope])
  const { data: releaseData } = useReleases(undefined, { cached: true })
  const suiteTarget = useContext(PageSuiteTargetContext)

  // The reader's choice from the URL; until they choose, the page's suite (when it names one)
  // or the busiest suites, and the top-bar release.
  const [params, setParams] = useSearchParams()
  // Each choice as one string (`null` = never chosen), so a change elsewhere in the URL is not a new choice.
  const suiteParam = choiceParam(params, COMPARE_URL_KEYS.suite)
  const releaseParam = choiceParam(params, COMPARE_URL_KEYS.release)
  const chosenSuites = useMemo(() => fromChoiceParam(COMPARE_URL_KEYS.suite, suiteParam), [suiteParam])
  const chosenReleases = useMemo(() => fromChoiceParam(COMPARE_URL_KEYS.release, releaseParam), [releaseParam])
  const suites = useMemo(
    () => chosenSuites ?? (pageSuites.length > 0 ? pageSuites : [...busiestSuites]),
    [chosenSuites, pageSuites, busiestSuites],
  )
  const releases = useMemo(() => chosenReleases ?? pageReleases, [chosenReleases, pageReleases])
  const choose = (key: string, values: string[]) =>
    setParams((current) => writeCompareChoice(current, key, values), { replace: true })

  const pickable = suiteTarget?.options
  const suiteOptions: MultiSelectOption[] = useMemo(() => {
    const names = new Map<string, string>()
    for (const name of [...(pickable ?? []), ...busiestSuites, ...suites]) {
      const key = name.trim().toLowerCase()
      if (key && !names.has(key)) names.set(key, name.trim())
    }
    return [...names.values()].sort((a, b) => a.localeCompare(b)).map((name) => ({ value: name, label: name }))
  }, [pickable, busiestSuites, suites])
  const releaseItems = releaseData?.items ?? []
  const releaseOptions: MultiSelectOption[] = releaseItems.map((r) => ({ value: r.id, label: r.name }))

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
      <MultiSelect
        label={COMPARE_PICKER_LABELS.suite}
        options={suiteOptions}
        value={suites}
        onChange={(next) => choose(COMPARE_URL_KEYS.suite, next)}
        max={COMPARE_MAX_SERIES}
        triggerData={{ 'data-compare-picker': 'suite' }}
      />
      <MultiSelect
        label={COMPARE_PICKER_LABELS.release}
        options={releaseOptions}
        value={releases}
        onChange={(next) => choose(COMPARE_URL_KEYS.release, next)}
        max={COMPARE_MAX_SERIES}
        disabled={releaseOptions.length === 0 && releases.length === 0}
        disabledReason={NO_RELEASES_REASON}
        triggerData={{ 'data-compare-picker': 'release' }}
      />
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

  const startedFromBusiest = chosenSuites === null && pageSuites.length === 0 && suites.length > 0
  const takeaway =
    by === 'suite_release'
      ? 'Colour is the suite; the dash is the release.'
      : by === 'suite' && startedFromBusiest
        ? BUSIEST_TAKEAWAY
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
