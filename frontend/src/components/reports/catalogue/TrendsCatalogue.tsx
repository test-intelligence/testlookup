/**
 * The Trends catalogue sections (VIZ-408, Wave 2.6): what `/trends` adds below
 * its body grid (unconditionally since Phase D, S3).
 *
 *   1. **Pass rate by suite** — `MultiSeriesChartFrame` over
 *      `chart-data?metric=pass_rate&group_by=day&group_by=suite&top_n=7`: the
 *      seven busiest suites plus "Other", the server folding the rest from
 *      counts.
 *   2. **Test duration p50 / p95** — `DurationChartFrame kind="trend"` over two
 *      `chart-data` day series joined by `durationBandPoints`. A day whose p95
 *      the server did not measure is a GAP in the line and "—" in the table,
 *      never 0 ms.
 *   2b. **Compare** (VIZ-605) — the suites and releases chosen in the filter
 *      bar on one line chart: by suite, by release, or by suite and release
 *      (colour by suite, dash by release), see `CompareSection`.
 *   3. **Suite pass rate by day** — the heatmap (VIZ-501, Wave 3;
 *      unconditional since Phase D, S4): the shared `HeatmapSection` over
 *      `/analytics/heatmap?kind=suite_day`. It has its own request since Wave 3:
 *      the server ranks, caps and counts the rows (top 40 by failures, no
 *      "Other" row), where the Wave 2.6 heatmap re-read section 1's top-7
 *      series. The suite series request below therefore feeds section 1 only.
 *
 * `sections` picks which of them render (default all): `/trends` (UX
 * redesign P3) mounts the catalogue once per tab with that tab's sections, so
 * a section in another tab is not rendered and asks for nothing.
 *
 * Every request is built by `useCatalogueParams`: the page's window clamped to
 * `ROW_GRAIN_MAX_WINDOW_DAYS` (these are the per-execution, "row grain"
 * charts), the page's project, release and suite scope, one value as a scalar.
 *
 * Nothing is asked until a section is near the reader (`LazySection`): the
 * whole module is a lazy chunk the page loads when it draws its body, and each
 * section mounts — and asks — when it scrolls within reach. The suite request
 * starts when EITHER of its two views is near (a reader who jumps straight to
 * the heatmap still gets it), and the unfiltered "ever had a run?" probe that
 * tells an empty chart "no data yet" from "nothing matches" starts with the
 * first section.
 *
 * A one-day window has one point per suite and one p50/p95 pair: not a trend.
 * The three frames say so (`not-measured`, with the reason) and ask nothing.
 *
 * Each frame states the grain it was counted in ("Counted per test
 * execution."), read defensively from `meta.definitions.grain` (plan OD-17):
 * the suite series and the durations sit beside run-total KPIs that can differ
 * when a run's per-test rows never landed.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import { SlidersHorizontal } from 'lucide-react'
import ChartCustomisePanel from '@/components/charts/ChartCustomisePanel'
import {
  SERIES_DIMENSIONS,
  SERIES_METRICS,
  seriesParams,
  titleOf,
  type SeriesChartConfig,
} from '@/components/charts/chartCustomiseModel'
import { useSeriesChartConfig } from '@/components/charts/useSeriesChartConfig'
import { useCatalogChartData, type CatalogParams } from '@/components/charts/chartCatalogSources'
import { hasChartData, type ChartResponse, type ChartState } from '@/components/charts/chartState'
import DurationChartFrame from '@/components/charts/DurationChartFrame'
import { durationBandPoints } from '@/components/charts/durationBuckets'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import { buildMultiSeriesModel, multiSeriesInputFromChartData, OTHER_KEY } from '@/components/charts/multiSeriesModel'
import type { ScopeValue } from '@/lib/scopeParams'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import CompareSection, { COMPARE_HEIGHT } from './CompareSection'
import { defaultCompareSuites } from './compareModel'
import LazySection from './LazySection'
import { clampCatalogueDays, ROW_GRAIN_MAX_WINDOW_DAYS, useCatalogueParams } from './catalogueScope'
import { HeatmapSection } from './HeatmapSection'
import type { HeatmapKind } from './sectionContracts'
import { useEverHadRun } from './useEverHadRun'

// ── Requests ─────────────────────────────────────────────────────────────────

/** Section 1's request: pass rate, day x suite, the top 7 + Other. */
export const SUITE_SERIES_PARAMS: CatalogParams = { metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7 }
export const DURATION_P50_PARAMS: CatalogParams = { metric: 'duration_p50', group_by: 'day' }
export const DURATION_P95_PARAMS: CatalogParams = { metric: 'duration_p95', group_by: 'day' }

const ROW_GRAIN = { maxDays: ROW_GRAIN_MAX_WINDOW_DAYS } as const

// ── Words ────────────────────────────────────────────────────────────────────

export const SUITE_SERIES_TITLE = 'Pass rate by suite'
export const DURATION_TITLE = 'Test duration (p50 / p95)'
export const HEATMAP_TITLE = 'Suite pass rate by day'
/** The one heatmap kind Trends offers: its columns are the page's days. */
const TRENDS_HEATMAP_KINDS: readonly HeatmapKind[] = ['suite_day']

export const ONE_DAY_SUITES_REASON =
  'a one-day window has one point per suite, which is not a trend; pick 7 days or more to compare suites over time'
export const ONE_DAY_DURATION_REASON =
  'a one-day window has one p50 and one p95, which is not a trend; pick 7 days or more to see durations over time'

/** What the frame says when the payload is not a day x series response. */
export const SERIES_SHAPE_ERROR = 'This chart needs a day-by-series response, and the server sent a different shape.'

const PASS_RATE_METRIC = { kind: 'rate', title: 'Pass rate %' } as const

/**
 * The grain the numbers were counted in, from `meta.definitions.grain` — a
 * field the C2 type does not declare yet, so it is read as unknown and only
 * the two values the server documents are put into words.
 */
export function grainNote(meta: EnvelopeMeta | null): string | null {
  const definitions = (meta as { definitions?: unknown } | null)?.definitions
  const grain = typeof definitions === 'object' && definitions !== null ? (definitions as { grain?: unknown }).grain : undefined
  if (grain === 'execution_row') return 'Counted per test execution.'
  if (grain === 'run_aggregate') return 'Counted per run, from run totals.'
  return null
}

function GrainNote({ meta }: { meta: EnvelopeMeta | null }) {
  const note = grainNote(meta)
  return note ? <span data-catalogue-grain="">{note}</span> : null
}

// ── Frame heights (the lazy placeholders hold the drawn frame's height) ─────

const SUITE_SERIES_HEIGHT = 280
const DURATION_HEIGHT = 260
/**
 * A frame's header, toolbar, brush and footer around its plot, px. An
 * estimate: the placeholder only has to stop the page from jumping by a
 * whole frame when a section arrives, not match it to the pixel.
 */
const FRAME_CHROME = 150

// ── State helpers ───────────────────────────────────────────────────────────

const oneDay = <T,>(reason: string): ChartState<T> => ({ status: 'not-measured', reason, meta: null })

/** The day x series chart inside a drawn state, else `null`. */
function seriesChartOf(state: ChartState<ChartResponse>): SeriesChart | null {
  return hasChartData(state) && state.data.series.kind === 'series' ? state.data.series : null
}

const metaOf = (state: ChartState<ChartResponse>): EnvelopeMeta | null => (hasChartData(state) ? state.meta : null)

/** A drawn state whose payload is not a series chart cannot be drawn as one: say so, in the frame. */
function withSeriesShape(state: ChartState<ChartResponse>): ChartState<unknown> {
  if (hasChartData(state) && state.data.series.kind !== 'series') {
    return {
      status: 'error',
      error: { kind: 'invalid-payload', message: SERIES_SHAPE_ERROR, requestId: null, status: null },
    }
  }
  return state
}

/**
 * The duration frame's ONE state from its two requests. A failure of either is
 * the frame's error (Retry asks both again — the one that answered is a cache
 * hit); while either is loading the frame is; once either has drawable data
 * the band is drawn, and a percentile the other request lacks is a gap. With
 * no data on either side, the p50's words (never-had-data, filtered-empty,
 * not-measured) speak for both.
 */
export function durationFrameState(p50: ChartState<ChartResponse>, p95: ChartState<ChartResponse>): ChartState<unknown> {
  const failed = [p50, p95].find((state) => state.status === 'error')
  if (failed?.status === 'error') {
    const retries = [p50, p95].map((state) => (state.status === 'error' ? state.retry : undefined))
    const retry = retries.some(Boolean) ? () => retries.forEach((again) => again?.()) : undefined
    return { ...failed, retry }
  }
  const forbidden = [p50, p95].find((state) => state.status === 'forbidden')
  if (forbidden) return forbidden
  if (p50.status === 'loading' || p95.status === 'loading') return { status: 'loading' }
  const drawn = [p50, p95].find(hasChartData)
  if (drawn && hasChartData(drawn)) {
    const revalidating = [p50, p95].some((state) => hasChartData(state) && state.revalidating)
    return { status: 'ready', data: drawn.data, meta: drawn.meta, revalidating }
  }
  return p50
}

// ── Sections ────────────────────────────────────────────────────────────────

/** Runs `onNear` once the section has mounted, i.e. once `LazySection` found it near. */
function useReportNear(onNear: () => void) {
  useEffect(() => {
    onNear()
  }, [onNear])
}

/** VIZ-604: the toolbar button that opens the customise panel. */
export const CUSTOMISE_BUTTON_TEXT = 'Customise'

function SuiteSeriesSection({
  state,
  windowDays,
  onNear,
  config,
  customising,
  onCustomise,
}: {
  state: ChartState<ChartResponse>
  windowDays: number
  onNear: () => void
  /** The applied customisation (VIZ-604); the default draws the chart as shipped. */
  config: SeriesChartConfig
  customising: boolean
  onCustomise: () => void
}) {
  useReportNear(onNear)
  const chart = seriesChartOf(state)
  const meta = metaOf(state)
  const metric = SERIES_METRICS[config.metric]
  const model = useMemo(
    () =>
      chart
        ? buildMultiSeriesModel({
            series: multiSeriesInputFromChartData(chart),
            metric: config.metric === 'pass_rate' ? PASS_RATE_METRIC : { kind: metric.kind, title: metric.title },
            meta,
            seriesNoun: SERIES_DIMENSIONS[config.seriesBy].plural,
            bucket: config.bucket,
          })
        : null,
    [chart, meta, config.metric, config.seriesBy, config.bucket, metric.kind, metric.title],
  )
  return (
    <div data-catalogue-section="trends-multi-series" className="min-w-0">
      <MultiSeriesChartFrame
        title={titleOf(config)}
        headingLevel={3}
        height={SUITE_SERIES_HEIGHT}
        state={withSeriesShape(state)}
        model={model}
        scopeLabel={`last ${windowDays} days`}
        footer={<GrainNote meta={meta} />}
        toolbar={
          <button
            type="button"
            data-chart-customise=""
            aria-pressed={customising}
            onClick={onCustomise}
            className="inline-flex items-center gap-1 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]"
          >
            <SlidersHorizontal aria-hidden="true" className="h-3.5 w-3.5" />
            {CUSTOMISE_BUTTON_TEXT}
          </button>
        }
        zoom
      />
    </div>
  )
}

function DurationSection({
  days,
  suiteFilter,
  singleDay,
  windowDays,
  everHadData,
  onNear,
}: {
  days: number
  suiteFilter: ScopeValue
  singleDay: boolean
  windowDays: number
  everHadData: boolean | null
  onNear: () => void
}) {
  useReportNear(onNear)
  const p50Params = useCatalogueParams(days, suiteFilter, DURATION_P50_PARAMS, ROW_GRAIN)
  const p95Params = useCatalogueParams(days, suiteFilter, DURATION_P95_PARAMS, ROW_GRAIN)
  const p50 = useCatalogChartData('chart-data', { params: singleDay ? null : p50Params, everHadData })
  const p95 = useCatalogChartData('chart-data', { params: singleDay ? null : p95Params, everHadData })

  const p50Chart = seriesChartOf(p50)
  const p95Chart = seriesChartOf(p95)
  // `durationBandPoints` keeps an unmeasured percentile `null`: a gap, never 0 ms.
  const band = useMemo(
    () => (p50Chart || p95Chart ? durationBandPoints({ p50: p50Chart, p95: p95Chart }) : null),
    [p50Chart, p95Chart],
  )
  const state = singleDay ? oneDay(ONE_DAY_DURATION_REASON) : durationFrameState(p50, p95)
  const meta = metaOf(p50) ?? metaOf(p95)
  return (
    <div data-catalogue-section="trends-duration" className="min-w-0">
      <DurationChartFrame
        kind="trend"
        title={DURATION_TITLE}
        headingLevel={3}
        height={DURATION_HEIGHT}
        state={state}
        band={band}
        scopeLabel={`last ${windowDays} days`}
        footer={<GrainNote meta={meta} />}
        zoom
      />
    </div>
  )
}

/** VIZ-605: the comparison, which asks the existence probe like its siblings. */
function CompareNear({ onNear, ...props }: Parameters<typeof CompareSection>[0] & { onNear: () => void }) {
  useReportNear(onNear)
  return <CompareSection {...props} />
}

// ── The catalogue ───────────────────────────────────────────────────────────

/** The catalogue's sections, by their `data-catalogue-section` / `data-lazy-section` ids, in drawing order. */
const TRENDS_SECTION_IDS = ['trends-multi-series', 'trends-compare', 'trends-duration', 'trends-heatmap'] as const
export type TrendsSectionId = (typeof TRENDS_SECTION_IDS)[number]

export interface TrendsCatalogueProps {
  /** The page's window, days, as the page shows it (snapped to its options). Clamped again on the wire. */
  days: number
  /** The page's suite scope (`usePageSuiteFilter().suiteFilter`). */
  suiteFilter: ScopeValue
  /**
   * Which sections to render (a composition choice, UX redesign P3; default
   * all four). A section left out is not rendered at all — no placeholder,
   * no hook, no request; one listed is drawn exactly as with all four, always
   * in the catalogue's own order.
   */
  sections?: readonly TrendsSectionId[]
}

export default function TrendsCatalogue({ days, suiteFilter, sections = TRENDS_SECTION_IDS }: TrendsCatalogueProps) {
  const windowDays = clampCatalogueDays(days, ROW_GRAIN_MAX_WINDOW_DAYS)
  const singleDay = windowDays < 2
  const shows = (id: TrendsSectionId) => sections.includes(id)

  // Latches, set by the sections as they mount: whether the suite request is
  // wanted (its multi-series is near) and whether any section is.
  const [suitesNear, setSuitesNear] = useState(false)
  const [anyNear, setAnyNear] = useState(false)
  const onSuitesNear = useCallback(() => {
    setSuitesNear(true)
    setAnyNear(true)
  }, [])
  const onDurationNear = useCallback(() => setAnyNear(true), [])

  const everHadData = useEverHadRun(anyNear && !singleDay)
  // VIZ-604: the suite series is customisable; the default config asks exactly SUITE_SERIES_PARAMS.
  const custom = useSeriesChartConfig('trends.suite-series')
  const [customising, setCustomising] = useState(false)
  useEffect(() => {
    if (custom.notice) toast(custom.notice)
  }, [custom.notice])
  const suiteParams = useCatalogueParams(days, suiteFilter, seriesParams(custom.applied), ROW_GRAIN)
  const suites = useCatalogChartData('chart-data', {
    params: suitesNear && !singleDay ? suiteParams : null,
    everHadData,
  })
  const suiteState: ChartState<ChartResponse> = singleDay
    ? oneDay<ChartResponse>(ONE_DAY_SUITES_REASON)
    : suites
  // C1: Compare starts from the busiest suites this chart already ranked (no request of its own);
  // only while the chart draws one line per suite.
  const suiteLines = custom.applied.seriesBy === 'suite' ? (seriesChartOf(suiteState)?.series ?? null) : null
  const busiestKey = defaultCompareSuites(suiteLines, OTHER_KEY).join('\n')
  const busiestSuites = useMemo(() => (busiestKey ? busiestKey.split('\n') : []), [busiestKey])

  return (
    <div data-trends-catalogue="" className="mt-3.5 grid grid-cols-1 gap-3.5 min-w-0">
      {shows('trends-multi-series') && (
        <LazySection label="trends-multi-series" minHeight={SUITE_SERIES_HEIGHT + FRAME_CHROME}>
          <SuiteSeriesSection
            state={suiteState}
            windowDays={windowDays}
            onNear={onSuitesNear}
            config={custom.applied}
            customising={customising}
            onCustomise={() => setCustomising((open) => !open)}
          />
        </LazySection>
      )}
      {shows('trends-compare') && (
        <LazySection label="trends-compare" minHeight={COMPARE_HEIGHT + FRAME_CHROME}>
          <CompareNear
            days={days}
            suiteFilter={suiteFilter}
            everHadData={everHadData}
            busiestSuites={busiestSuites}
            onNear={onDurationNear}
          />
        </LazySection>
      )}
      {shows('trends-duration') && (
        <LazySection label="trends-duration" minHeight={DURATION_HEIGHT + FRAME_CHROME}>
          <DurationSection
            days={days}
            suiteFilter={suiteFilter}
            singleDay={singleDay}
            windowDays={windowDays}
            everHadData={everHadData}
            onNear={onDurationNear}
          />
        </LazySection>
      )}
      {/* The suite series' customise panel: it opens only from that section's toolbar. */}
      {customising && shows('trends-multi-series') ? (
        <ChartCustomisePanel
          chartTitle={titleOf(custom.config)}
          config={custom.config}
          onChange={custom.setConfig}
          onReset={custom.reset}
          onClose={() => setCustomising(false)}
        />
      ) : null}
      {/* Its own lazy placeholder and request: nothing is asked until it is near the reader. */}
      {shows('trends-heatmap') && (
        <HeatmapSection
          days={days}
          suiteFilter={suiteFilter}
          kinds={TRENDS_HEATMAP_KINDS}
          title={HEATMAP_TITLE}
          sectionId="trends-heatmap"
        />
      )}
    </div>
  )
}
