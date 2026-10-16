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
 *
 * TRND-10: the title, the metric and "Compare by" are Compare's
 * customisation (`compareChartConfig`, `useChartConfig('trends.compare')`),
 * changed in the Customise side panel — the toolbar's two selects moved there;
 * it keeps Suites, Releases and gains Customise. The suites and releases stay
 * in the URL and are mirrored into the saved-view store, so a view reproduces
 * the chart; the catalogue decides which panel is open (`customising`).
 */
import { useCallback, useContext, useEffect, useMemo, type ReactElement } from 'react'
import { useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { SlidersHorizontal } from 'lucide-react'
import ChartCustomisePanel from '@/components/charts/ChartCustomisePanel'
import { useChartConfig } from '@/components/charts/useChartConfig'
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
  COMPARE_MAX_SERIES,
  COMPARE_METRICS,
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
} from './compareModel'
import {
  applyCompareField,
  COMPARE_CHART_KEY,
  COMPARE_SETTINGS_MODEL,
  compareCustomiseSpec,
  compareTitleOf,
  resetCompareSettings,
  withCompareChoice,
  type CompareChartConfig,
} from './compareChartConfig'

const CUSTOMISE_BUTTON =
  'inline-flex items-center gap-1 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]'

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
  /** TRND-10: Compare's Customise panel is open (the catalogue keeps one panel open at a time). */
  customising?: boolean
  /** Toggles the panel (the toolbar's Customise button). Absent: no Customise button. */
  onCustomise?: () => void
  /** Closes the panel. */
  onCloseCustomise?: () => void
}

export default function CompareSection({
  days,
  suiteFilter,
  everHadData,
  busiestSuites = NO_SUITES,
  customising = false,
  onCustomise,
  onCloseCustomise,
}: CompareSectionProps): ReactElement {
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

  // TRND-10: the title, metric and compare-by are this chart's customisation; the
  // store (a saved view) also gets the reader's selection from the URL.
  const mirror = useCallback(
    (config: CompareChartConfig) => withCompareChoice(config, chosenSuites, chosenReleases),
    [chosenSuites, chosenReleases],
  )
  const custom = useChartConfig(COMPARE_CHART_KEY, COMPARE_SETTINGS_MODEL, { mirror })
  useEffect(() => {
    if (custom.notice) toast(custom.notice)
  }, [custom.notice])
  const metric = custom.applied.metric
  const options = compareOptions(suites, releases)
  const by = effectiveCompareBy(custom.applied.by, options)
  const title = compareTitleOf(custom.config)
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
      {onCustomise ? (
        <button
          type="button"
          data-chart-customise="compare"
          aria-pressed={customising}
          onClick={onCustomise}
          className={CUSTOMISE_BUTTON}
        >
          <SlidersHorizontal aria-hidden="true" className="h-3.5 w-3.5" />
          Customise
        </button>
      ) : null}
    </>
  )
  const spec = compareCustomiseSpec(custom.config, { byOptions: options, effectiveBy: effectiveCompareBy(custom.config.by, options) })

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
    <div
      data-catalogue-section="trends-compare"
      data-compare-by-active={by ?? ''}
      data-compare-metric-active={metric}
      className="min-w-0"
    >
      <MultiSeriesChartFrame
        title={title}
        takeaway={takeaway}
        headingLevel={3}
        height={COMPARE_HEIGHT}
        state={state}
        model={model}
        toolbar={toolbar}
        scopeLabel={`last ${windowDays} days`}
      />
      {customising ? (
        <ChartCustomisePanel
          chartTitle={title}
          spec={spec}
          onField={(id, value) => custom.setConfig(applyCompareField(custom.config, id, value))}
          onReset={() => custom.setConfig(resetCompareSettings(custom.config))}
          onClose={() => onCloseCustomise?.()}
        />
      ) : null}
    </div>
  )
}
