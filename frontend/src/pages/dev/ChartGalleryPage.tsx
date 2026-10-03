/**
 * `/__charts` — the chart gallery. DEVELOPMENT BUILDS ONLY.
 *
 * A deterministic page that renders every chart component in the kit, at a
 * fixed size, from fixed data, with animation off. It exists to be looked at
 * by machines: `tests/ci-e2e/chart-gallery.spec.ts` asserts the charts draw
 * (geometry, not text) and `tests/visual/chart-gallery.visual.spec.ts`
 * screenshots each item per theme. Nothing here fetches, so it runs with no
 * backend and no login.
 *
 * `App.tsx` guards both the import and the route with `import.meta.env.DEV`,
 * so a production build carries none of this — `npm run check:bundle` and a
 * grep of `dist/` for `data-gallery-item` are the proof.
 *
 * `?theme=<id>` renders the gallery in one of the app's real themes. The id is
 * checked against the theme registry; anything else leaves the active theme
 * alone. The attribute is set on `<html>` — where `themeStore` puts it, so the
 * derived tokens resolve exactly as they do in the app — but the STORE is not
 * touched: a screenshot run must not rewrite the developer's saved theme.
 */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import TrendChart from '@/components/charts/TrendChart'
import DefectDonut from '@/components/charts/DefectDonut'
import PassRateGauge from '@/components/charts/PassRateGauge'
import HeatmapChart from '@/components/charts/HeatmapChart'
import DonutChart from '@/components/charts/DonutChart'
import BarChart, { BreakdownChart } from '@/components/charts/BarChart'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import DurationChartFrame from '@/components/charts/DurationChartFrame'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import Sparkline from '@/components/charts/Sparkline'
import GaugeBar from '@/components/charts/GaugeBar'
import RingGauge from '@/components/charts/RingGauge'
import DayStrip, { type DayStripProps } from '@/components/charts/DayStrip'
import HeatmapChartFrame from '@/components/charts/HeatmapChartFrame'
import {
  heatmapFrameDense,
  heatmapFrameEdges,
  heatmapFrameHostile,
  heatmapFrameMeta,
  heatmapFrameStatus,
  heatmapFrameWorstFirst,
} from '@/components/charts/__fixtures__/heatmapFrame'
import ChartFrame from '@/components/charts/ChartFrame'
import CoverageTreemap from '@/components/charts/CoverageTreemap'
import TestScatter from '@/components/charts/TestScatter'
import FailureGroupsFrame from '@/components/charts/FailureGroupsFrame'
import {
  COLOR_BY_OPTIONS,
  COVERAGE_MAP_CAPTION,
  COVERAGE_MAP_EMPTY_HEIGHT,
  coverageDescription,
  coverageEmptyText,
  isColorBy,
  levelView,
  otherNote,
  type CoverageColorBy,
  type CoverageLevelView,
} from '@/components/charts/coverageMap.model'
import {
  coverageEmpty,
  coverageHostile,
  coverageOneTest,
  coveragePaymentsClasses,
  coverageSuites,
  type CoverageMapFixture,
} from '@/components/charts/__fixtures__/coverageMap'
import {
  scatterAllExcluded,
  scatterDefault,
  scatterDense,
  scatterHostile,
} from '@/components/charts/__fixtures__/testScatter'
import { ScatterFooter } from '@/components/reports/catalogue/ScatterSection'
import {
  nothingPlacedSentence,
  scatterDescription,
  scatterTakeaway,
} from '@/components/charts/testScatter.model'
import {
  clustersBody,
  failureGroupsResponse,
  type FailureGroupsFixtureOptions,
} from '@/components/charts/failureGroups/failureGroups.fixtures'
import SystemicClusters from '@/components/charts/failureGroups/SystemicClusters'
import { validateClustersResponse, type SystemicClustersResponse } from '@/components/charts/failureGroups/systemicClusters.model'
import { SUITE_RULE_NOTE } from '@/components/reports/catalogue/CoverageMapSection'
import {
  NO_TESTS_MESSAGE,
  SCATTER_CHART_TYPE,
  SCATTER_MIN_EXECUTIONS,
  scatterFrameHeight,
} from '@/components/reports/catalogue/ScatterSection.model'
import { bandsTone, formatGaugeNumber } from '@/components/charts/gaugeBar.model'
import type { StackedColumnModel } from '@/components/charts/stackedColumnModel'
import {
  hostileLabelsFixture,
  longWindowFixture,
  manyCategoriesFixture,
  seriesMonthlyFixture,
  singleBucketFixture,
  statusDailyFixture,
} from '@/components/charts/__fixtures__/stackedColumn'
import { DAY_STRIP_FIXTURES } from '@/components/charts/__fixtures__/dayStrip'
import { buildMultiSeriesModel, readComparability, type MultiSeriesModel } from '@/components/charts/multiSeriesModel'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { ChartResponse, ChartState } from '@/components/charts/chartState'
import { buildTimeSeriesModel, type TimeSeriesModel } from '@/components/charts/timeSeriesModel'
import type { DurationHistogramModel } from '@/components/charts/durationBuckets'
import {
  durationBandFixture,
  durationHistogramEmptyFixture,
  durationHistogramFixture,
  inProgressRunsFixture,
  slowestTestsFixture,
  trendAnalysisFixture,
  trendAnalysisSparseFixture,
  trendSinglePointFixture,
  trendWithReleasesFixture,
  trendWithReleasesMeta,
  trendWithReleasesReleases,
  trendZoomedAxisFixture,
  trendZoomReleasesFixture,
  trendZoomReleasesMeta,
} from '@/components/charts/__fixtures__/wave2Fixtures'
import type { ChartSeries, GraphChart, PointsChart, TreeChart } from '@/lib/viz/contracts'
import { formatPercent } from '@/utils/formatters'
import { THEMES, type ThemeId } from '@/store/themeStore'
import {
  GALLERY_CANVAS,
  GALLERY_FRAME_PLOT_HEIGHT,
  GALLERY_ITEMS,
  GALLERY_LOCALE,
  GALLERY_NOW,
  GALLERY_TIME_ZONE,
  galleryCanvasSize,
  galleryChartHeight,
  galleryEngine,
  galleryFramed,
  galleryStatusSeries,
  GALLERY_FLUID_CANVAS_PARAM,
  GALLERY_FRAME_HEADING_LEVEL,
  HOSTILE_LABEL,
  type GalleryCategorySeries,
  type GalleryComparison,
  type GalleryCoverageMapFixture,
  type GalleryDayStripFixture,
  type GalleryFormat,
  type GalleryClustersFixture,
  type GalleryHeatmapFrameFixture,
  type GalleryScatterFixture,
  type GalleryHistogramFixture,
  type GalleryItem,
  type GalleryStackedFixture,
  type GalleryTimeSeriesFixture,
} from './chartGalleryFixtures'
import { STATES_VIEW_PARAM } from './chartStatesFixtures'
import ChartStatesGallery from './ChartStatesGallery'

const THEME_QUERY_PARAM = 'theme'
/** `?view=states` renders every ChartFrame state (VIZ-107) instead of the chart items. */
const VIEW_QUERY_PARAM = 'view'
/**
 * `?canvas=fluid` releases the fixed 640 px canvas and lets every item take
 * the width it is actually given.
 *
 * The pinned canvas is what makes the visual baselines comparable, and it is
 * also what hides SC 1.4.10 entirely: at a 320 px viewport the canvas stays
 * 640 px and scrolls INSIDE its box, so nothing ever reflows and a chart that
 * loses its whole value axis at 320 px looks perfect in the gallery. This
 * parameter is how a spec asks the same charts to be narrow for real.
 */
const CANVAS_QUERY_PARAM = 'canvas'

/** A registry id, or `null` for anything that is not one. */
function resolveTheme(requested: string | null): ThemeId | null {
  const match = THEMES.find((theme) => theme.id === requested)
  return match ? match.id : null
}

/**
 * A settled `ChartState` over a fixture, so a Wave-2 item draws the REAL
 * `ChartFrame` (its header, notes, table view and empty states) with no
 * network at all. `meta: null` keeps the footer free of a "N of M" line the
 * fixtures cannot honestly fill in.
 */
function readyState(series: GalleryCategorySeries): ChartState<ChartResponse> {
  return { status: 'ready', data: { meta: null, series: series as ChartSeries }, meta: null, revalidating: false }
}

/**
 * A settled state for the VIZ-403 / VIZ-406 frames, which build their own
 * `series` FROM the model they draw rather than taking one. `data` is only the
 * frame's "there is something to draw" signal here.
 */
const DRAWN_STATE: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }

/**
 * The one scoped time-series item's state: its fixture's envelope `meta`, so
 * the frame footer and the exports carry a project, a window and totals.
 * Keyed by fixture — only a fixture with a meta of its own can be scoped.
 */
function timeSeriesState(key: GalleryTimeSeriesFixture, scoped: boolean | undefined): ChartState<unknown> {
  if (!scoped) return DRAWN_STATE
  if (key !== 'trend-zoom-releases') throw new Error(`gallery fixture ${key} has no envelope meta to scope it with`)
  return SCOPED_ZOOM_STATE
}
const SCOPED_ZOOM_STATE: ChartState<unknown> = { status: 'ready', data: null, meta: trendZoomReleasesMeta, revalidating: false }

/**
 * VIZ-601 scenario 3 on a time series: the headline trend — its days, its
 * envelope meta and its releases — with the 03-03 release renamed to markup.
 * Built here rather than in `wave2Fixtures` because `HOSTILE_LABEL` lives in
 * the gallery's fixtures — the one string every spec compares against.
 * Module-level: built once.
 *
 * The META is passed too: the model marks the still-filling day from it, and
 * without it 03-10 was drawn as a finished bar of 96 executions — a drop the
 * product would never draw for these data (baseline review B).
 */
const HOSTILE_RELEASE_ID = 'r1' // 1.4.0, dated 03-03: inside the window, so it is drawn
const trendHostileReleaseFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: trendWithReleasesFixture.points,
  meta: trendWithReleasesMeta,
  releases: trendWithReleasesReleases.map((release) =>
    release.id === HOSTILE_RELEASE_ID ? { ...release, name: HOSTILE_LABEL } : release,
  ),
})

/**
 * The fixture behind each time-series item. An exhaustive switch on purpose:
 * `chartGalleryFixtures` names fixtures by key (it has to stay importable from
 * plain Node), so THIS is where a renamed or deleted fixture has to become a
 * type error rather than an empty chart.
 */
function timeSeriesFixture(key: GalleryTimeSeriesFixture): TimeSeriesModel {
  switch (key) {
    case 'trend-with-releases':
      return trendWithReleasesFixture
    case 'trend-single-point':
      return trendSinglePointFixture
    case 'trend-zoomed-axis':
      return trendZoomedAxisFixture
    case 'trend-analysis':
      return trendAnalysisFixture
    case 'trend-analysis-sparse':
      return trendAnalysisSparseFixture
    case 'trend-zoom-releases':
      return trendZoomReleasesFixture
    case 'trend-hostile-release':
      return trendHostileReleaseFixture
  }
}

/**
 * VIZ-405: what a `trendOverlays` item hands the frame — both overlays start
 * ON, so the baseline shows them drawn. One object for every render: the frame
 * reads it once, as its initial state.
 */
const GALLERY_TREND_ANALYSIS = { initialShown: { movingAverage: true, trendLine: true } } as const

function histogramFixture(key: GalleryHistogramFixture): DurationHistogramModel {
  switch (key) {
    case 'duration-histogram':
      return durationHistogramFixture
    case 'duration-histogram-empty':
      return durationHistogramEmptyFixture
  }
}

/**
 * The VIZ-404 model for a comparison fixture, built ONCE per item: the model
 * is what the plot, the tooltip, the cursor and the table read, and a new one
 * on every render would restart the keyboard cursor under the reader.
 */
const comparisonModels = new Map<GalleryComparison, MultiSeriesModel>()
function comparisonModel(comparison: GalleryComparison): MultiSeriesModel {
  let model = comparisonModels.get(comparison)
  if (!model) {
    model = buildMultiSeriesModel({
      series: comparison.series,
      metric: comparison.metric,
      alignment: comparison.alignment,
      // The API's wire shape, read by the SAME reader as a real envelope's.
      comparability: comparison.comparability ? readComparability(comparison.comparability) : null,
      seriesNoun: comparison.seriesNoun,
    })
    comparisonModels.set(comparison, model)
  }
  return model
}

/** Wave 2.5 (K1): the model behind each stacked-column item. Exhaustive, as `timeSeriesFixture` is. */
function stackedFixture(key: GalleryStackedFixture): StackedColumnModel {
  switch (key) {
    case 'stacked-status-daily':
      return statusDailyFixture
    case 'stacked-series-monthly':
      return seriesMonthlyFixture
    case 'stacked-hostile-labels':
      return hostileLabelsFixture
    case 'stacked-single-bucket':
      return singleBucketFixture
    case 'stacked-long-window':
      return longWindowFixture
    case 'stacked-many-categories':
      return manyCategoriesFixture
  }
}

/**
 * Wave 2.5 (K5): each DayStrip fixture's props, built ONCE. A new `cells`
 * array on every render would rebuild the strip's model and restart its
 * keyboard cursor under the reader. A key with no fixture throws here, at
 * import, rather than drawing an empty box.
 */
const DAY_STRIP_PROPS = new Map<string, DayStripProps>(DAY_STRIP_FIXTURES.map((fixture) => [fixture.id, fixture.props()]))
function dayStripProps(key: GalleryDayStripFixture): DayStripProps {
  const props = DAY_STRIP_PROPS.get(key)
  if (!props) throw new Error(`no DayStrip fixture named ${key}`)
  return props
}

/**
 * Wave 2.6 (K5): the settled `/analytics/heatmap` state behind each
 * heatmap-frame item, built ONCE (a new state object per render would rebuild
 * the matrix). The 14-day items arrive `truncated`, as the Trends request does
 * (top 7 of 12 suites by failures); the frame states that by rows, in its own
 * footer.
 */
const TRUNCATED_FORTNIGHT: ChartState<ChartResponse> = {
  status: 'truncated',
  data: heatmapFrameWorstFirst,
  meta: heatmapFrameMeta,
  shown: 7,
  total: 12,
  revalidating: false,
}
const HEATMAP_FRAME_STATES: Record<GalleryHeatmapFrameFixture, ChartState<ChartResponse>> = {
  'heatmap-frame': TRUNCATED_FORTNIGHT,
  'heatmap-frame-90d': { status: 'ready', data: heatmapFrameDense, meta: heatmapFrameDense.meta, revalidating: false },
  'heatmap-frame-hostile': {
    status: 'ready',
    data: heatmapFrameHostile,
    meta: heatmapFrameHostile.meta,
    revalidating: false,
  },
  // Wave 3 (FK1).
  'heatmap-frame-status': { status: 'ready', data: heatmapFrameStatus, meta: heatmapFrameStatus.meta, revalidating: false },
  'heatmap-frame-edges': { status: 'ready', data: heatmapFrameEdges, meta: heatmapFrameEdges.meta, revalidating: false },
  'heatmap-frame-fit': TRUNCATED_FORTNIGHT,
}

// ── Wave 3 (PR-B) ───────────────────────────────────────────────────────────
//
// Each item draws its frame as its catalogue section does, from a settled
// state built ONCE, with none of the section's data hooks, flags, drill URL or
// rows panel: the gallery fetches nothing.

/** A settled state over a fixture response, with its envelope `meta`. */
function settled<T extends { meta: ChartResponse['meta'] }>(response: T): ChartState<T> {
  return { status: 'ready', data: response, meta: response.meta, revalidating: false }
}

/** The coverage-map level behind each item. Exhaustive, as `timeSeriesFixture` is. */
function coverageFixture(key: GalleryCoverageMapFixture): CoverageMapFixture {
  switch (key) {
    case 'coverage-suites':
      return coverageSuites
    case 'coverage-payments-classes':
      return coveragePaymentsClasses
    case 'coverage-hostile':
      return coverageHostile
    case 'coverage-one-test':
      return coverageOneTest
    case 'coverage-empty':
      return coverageEmpty
  }
}

/** The scatter body behind each item. */
function scatterFixture(key: GalleryScatterFixture): ChartResponse<PointsChart> {
  switch (key) {
    case 'scatter-default':
      return scatterDefault
    case 'scatter-dense':
      return scatterDense
    case 'scatter-hostile':
      return scatterHostile
    case 'scatter-all-excluded':
      return scatterAllExcluded
  }
}

/**
 * What `CoverageMapSection` hands its frame: the level's tree, or, for a
 * level with no node at all, the frame's own empty state (the section's
 * `useCatalogChartData` reads `nodes: []` as `filtered-empty`).
 */
function coverageState({ response }: CoverageMapFixture): ChartState<ChartResponse<TreeChart>> {
  return response.series.nodes.length > 0 ? settled(response) : { status: 'filtered-empty', meta: response.meta }
}
/**
 * Each coverage item's state and level view, built ONCE per fixture: a new
 * `children` array on every render would rebuild the treemap's option and
 * restart its keyboard cursor under the reader.
 */
interface CoverageItemData {
  fixture: CoverageMapFixture
  state: ChartState<ChartResponse<TreeChart>>
  view: CoverageLevelView
}
const COVERAGE_ITEMS = new Map<GalleryCoverageMapFixture, CoverageItemData>()
function coverageItemOf(key: GalleryCoverageMapFixture): CoverageItemData {
  let data = COVERAGE_ITEMS.get(key)
  if (!data) {
    const fixture = coverageFixture(key)
    const state = coverageState(fixture)
    const view = levelView(state.status === 'ready' ? state.data.series : null, fixture.level)
    data = { fixture, state, view }
    COVERAGE_ITEMS.set(key, data)
  }
  return data
}

/** `CoverageMapSection`'s grain line, from `meta.definitions.grain` (read defensively, as there). */
function coverageGrain(meta: ChartResponse['meta']): string | null {
  const definitions = (meta as { definitions?: unknown } | null)?.definitions
  const grain = typeof definitions === 'object' && definitions !== null ? (definitions as { grain?: unknown }).grain : undefined
  return grain === 'execution_row' ? 'Counted per test execution.' : null
}

/** `CoverageMapSection`'s "Colour by" select: the toolbar of the map, so the gallery can switch the measure too. */
function CoverageColorBySelect({ value, onChange }: { value: CoverageColorBy; onChange: (next: CoverageColorBy) => void }) {
  return (
    <label className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
      Colour by
      <select
        value={value}
        data-coverage-color-by=""
        className="min-h-6 rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-1.5 py-0.5 text-xs text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        onChange={(event) => {
          if (isColorBy(event.target.value)) onChange(event.target.value)
        }}
      >
        {COLOR_BY_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * One coverage-map level, composed as `CoverageMapSection` composes it: the
 * frame (title, the honest-labelling caption, the colour select, the footer's
 * fold, suite rule and grain) around `CoverageTreemap`. Two things are left
 * out on purpose: the breadcrumb (six `<nav aria-label="Breadcrumb">`
 * landmarks on one page fail axe's landmark-unique rule; the production
 * region baseline has it) and the drill handlers (the gallery is static).
 */
function CoverageMapItem({ item }: { item: Extract<GalleryItem, { chart: 'coverage-map' }> }) {
  const { fixture, state, view } = coverageItemOf(item.fixture)
  const [colorBy, setColorBy] = useState<CoverageColorBy>(item.colorBy)
  const tree = state.status === 'ready' ? state.data.series : null
  const meta = state.status === 'ready' ? state.meta : null
  const note = otherNote(view, meta?.truncated_total ?? null, fixture.level.depth)
  const grain = coverageGrain(meta)
  return (
    <ChartFrame
      title={item.title}
      takeaway={COVERAGE_MAP_CAPTION}
      headingLevel={GALLERY_FRAME_HEADING_LEVEL}
      state={state}
      // As the section: an empty level is one sentence in a short body (X2 / F-14), not a 360 px band.
      height={state.status === 'filtered-empty' ? COVERAGE_MAP_EMPTY_HEIGHT : galleryChartHeight(item)}
      emptyMessage={coverageEmptyText(fixture.level)}
      series={tree}
      chartType="Treemap"
      scopeLabel="last 30 days"
      toolbar={<CoverageColorBySelect value={colorBy} onChange={setColorBy} />}
      footer={
        <>
          {note ? <span data-coverage-other-note="">{note}</span> : null}
          <span data-coverage-suite-rule="">{SUITE_RULE_NOTE}</span>
          {grain ? <span data-catalogue-grain="">{grain}</span> : null}
        </>
      }
    >
      {view.children.length > 0 ? (
        <CoverageTreemap
          items={view.children}
          level={fixture.level}
          colorBy={colorBy}
          description={coverageDescription(view, fixture.level, colorBy)}
          height={galleryChartHeight(item)}
          animate={false}
        />
      ) : null}
    </ChartFrame>
  )
}

const SCATTER_STATES = new Map<GalleryScatterFixture, ChartState<ChartResponse<PointsChart>>>()
function scatterStateOf(key: GalleryScatterFixture) {
  let state = SCATTER_STATES.get(key)
  if (!state) {
    // Ready even with no point: every test LEFT OUT is an answer (the section's own accessors).
    state = settled(scatterFixture(key))
    SCATTER_STATES.set(key, state)
  }
  return state
}

/**
 * The test scatter, composed as `ScatterSection` composes it: the frame
 * (takeaway, axes, table view, the footer's exclusions) around `TestScatter`,
 * or, when every test was left out, the sentence that says why. No selection
 * list and no rows panel: they follow a reader's action, and the gallery is
 * static.
 */
function ScatterItem({ item }: { item: Extract<GalleryItem, { chart: 'scatter' }> }) {
  const state = scatterStateOf(item.fixture)
  const chart = state.status === 'ready' ? state.data.series : null
  return (
    <ChartFrame
      title={item.title}
      takeaway={chart ? scatterTakeaway(chart) : undefined}
      headingLevel={GALLERY_FRAME_HEADING_LEVEL}
      // As the section (X4 / F-14): every test left out is one sentence in a short body, not a plot-sized band.
      height={scatterFrameHeight(chart)}
      state={state}
      series={chart && chart.points.length > 0 ? chart : null}
      chartType={SCATTER_CHART_TYPE}
      axes={chart ? { x: chart.x.label, y: chart.y.label } : undefined}
      scopeLabel="last 30 days"
      emptyMessage={NO_TESTS_MESSAGE}
      footer={chart ? <ScatterFooter chart={chart} /> : undefined}
    >
      {chart === null ? null : chart.points.length === 0 ? (
        <p data-scatter-nothing="" className="py-8 text-center text-sm text-[var(--color-text-secondary)]">
          {nothingPlacedSentence(chart.excluded, SCATTER_MIN_EXECUTIONS)}
        </p>
      ) : (
        <TestScatter
          data={chart}
          description={scatterDescription(item.title, chart)}
          height={galleryChartHeight(item)}
          animate={false}
        />
      )}
    </ChartFrame>
  )
}

/** Each failure-groups item's state, built ONCE per item (its options object is the key). */
const FAILURE_GROUP_STATES = new Map<FailureGroupsFixtureOptions, ChartState<ChartResponse<GraphChart>>>()
function failureGroupsStateOf(options: FailureGroupsFixtureOptions) {
  let state = FAILURE_GROUP_STATES.get(options)
  if (!state) {
    state = settled(failureGroupsResponse(options))
    FAILURE_GROUP_STATES.set(options, state)
  }
  return state
}

/** Nothing to open: the gallery has no group panel. */
const OPEN_NO_GROUP = () => {}

/**
 * R6: each clusters item's settled state, from FK3's `clustersBody` through
 * the tab's own validator (a body that does not validate fails here, at
 * import, not as an error frame in a screenshot). Built once.
 */
function clustersState(body: unknown): ChartState<SystemicClustersResponse> {
  const checked = validateClustersResponse(body)
  if (!checked.ok) throw new Error(`gallery clusters fixture: ${checked.errors.join('; ')}`)
  return { status: 'ready', data: checked.value, meta: checked.value.meta, revalidating: false }
}
const CLUSTERS_STATES: Record<GalleryClustersFixture, ChartState<SystemicClustersResponse>> = {
  clusters: clustersState(clustersBody()),
  'clusters-empty': clustersState(clustersBody({ items: [], total: 0 })),
}

/** The formatter a Wave 2.5 item names by key (`chartGalleryFixtures` cannot import one). */
function galleryFormat(format: GalleryFormat | undefined): ((v: number) => string) | undefined {
  switch (format) {
    case 'percent':
      return (v) => formatPercent(v)
    case 'number':
      return formatGaugeNumber
    case undefined:
      return undefined
  }
}

function renderChart(item: GalleryItem) {
  switch (item.chart) {
    case 'status-donut':
      return (
        <DonutChart
          title={item.title}
          state={readyState(galleryStatusSeries(item.counts))}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          centreCaption={item.caption}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
        />
      )
    case 'bars':
      return (
        <BarChart
          title={item.title}
          state={readyState(item.data)}
          variant={item.variant}
          topN={item.topN}
          initialMode={item.initialMode}
          dimension={item.dimension}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
        />
      )
    case 'breakdown':
      return (
        <BreakdownChart
          title={item.title}
          state={readyState(item.data)}
          preferred={item.preferred}
          dimension={item.dimension}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
        />
      )
    case 'trend':
      return (
        <TrendChart
          data={item.data}
          type={item.variant}
          height={GALLERY_CANVAS.height}
          animate={false}
        />
      )
    case 'donut':
      return <DefectDonut data={item.data} animate={false} />
    case 'gauge':
      // The gauge is square; fill the canvas height so its arc is a real size.
      return <PassRateGauge value={item.value} size={GALLERY_CANVAS.height} animate={false} />
    case 'heatmap':
      // ECharts, canvas, lazy: the engine chunk is fetched when this mounts.
      return (
        <HeatmapChart
          data={item.data}
          description={item.description}
          width={GALLERY_CANVAS.width}
          height={galleryChartHeight(item)}
          animate={false}
        />
      )
    case 'time-series':
      return (
        <TimeSeriesChartFrame
          title={item.title}
          state={timeSeriesState(item.fixture, item.scoped)}
          model={timeSeriesFixture(item.fixture)}
          inProgressRuns={item.inProgress ? inProgressRunsFixture : undefined}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
          // Determinism, and the reason this chart could not simply be dropped
          // into the gallery: left to the machine, `now` is the real clock and
          // the zone is the runner's. The "today in UTC" note would then appear
          // or vanish with the DATE the baseline was taken, and the tooltip's
          // local-equivalent row would read differently on every machine —
          // neither of which is a regression, and both of which flap a diff.
          now={GALLERY_NOW}
          timeZone={GALLERY_TIME_ZONE}
          locale={GALLERY_LOCALE}
          // Undefined for every VIZ-403 item, which then renders exactly as before.
          trendAnalysis={item.trendOverlays ? GALLERY_TREND_ANALYSIS : undefined}
          // VIZ-407: undefined for every earlier item, which then draws no brush.
          zoom={item.zoom}
          // VIZ-104 K2: undefined for every earlier item, which then draws no target.
          rateTarget={item.rateTarget}
        />
      )
    case 'duration-histogram':
      return (
        <DurationChartFrame
          kind="histogram"
          title={item.title}
          state={DRAWN_STATE}
          histogram={histogramFixture(item.fixture)}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
        />
      )
    case 'duration-band':
      return (
        <DurationChartFrame
          kind="trend"
          title={item.title}
          state={DRAWN_STATE}
          band={durationBandFixture}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
          zoom={item.zoom}
        />
      )
    case 'slowest-tests':
      return (
        <DurationChartFrame
          kind="slowest"
          title={item.title}
          state={DRAWN_STATE}
          slowest={slowestTestsFixture}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
        />
      )
    case 'multi-series':
      // No `now` / `timeZone` to pin: the comparison reads neither — its days
      // are the fixture's literal UTC days, drawn and tabled as given.
      return (
        <MultiSeriesChartFrame
          title={item.title}
          state={DRAWN_STATE}
          model={comparisonModel(item.comparison)}
          initialHidden={item.comparison.initialHidden}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
          zoom={item.zoom}
        />
      )
    case 'stacked-column':
      return (
        <StackedColumnChartFrame
          title={item.title}
          state={DRAWN_STATE}
          model={stackedFixture(item.fixture)}
          bucketNoun={item.bucketNoun}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={GALLERY_FRAME_PLOT_HEIGHT}
          animate={false}
        />
      )
    case 'sparkline': {
      // One cell per width, as a KPI cell holds it; a series too short to
      // draw leaves the caller's caption (Overview's dashed line) in its place.
      const { widths, format, ...props } = item.sparkline
      return (
        <div className="flex w-full items-end justify-center gap-8">
          {widths.map((width) => (
            <div key={width} data-sparkline-cell={width} style={{ width }}>
              <Sparkline {...props} format={galleryFormat(format)} />
              {item.empty && (
                <p className="border-t border-dashed border-[var(--color-border)] pt-1 text-[11px] text-[var(--color-text-secondary)]">
                  No trend line: fewer than 2 measured days
                </p>
              )}
            </div>
          ))}
        </div>
      )
    }
    case 'gauge-bar': {
      const { box, format, ...props } = item.gauge
      return (
        <div style={{ width: box }}>
          <GaugeBar {...props} format={galleryFormat(format)} />
        </div>
      )
    }
    case 'ring-gauge':
      // The gauge is square; fill the canvas height, as `PassRateGauge`'s item does.
      return (
        <RingGauge
          value={item.ring.value}
          caption={item.ring.caption}
          format={galleryFormat(item.ring.format)}
          tone={bandsTone(item.ring.bands)}
          size={GALLERY_CANVAS.height}
          animate={false}
        />
      )
    case 'day-strip':
      // The strip is as wide as its box: a flex child would otherwise shrink to nothing.
      return (
        <div className="w-full">
          <DayStrip {...dayStripProps(item.fixture)} />
        </div>
      )
    case 'heatmap-frame':
      // ECharts, canvas, lazy, as `heatmap`; the frame measures its own width,
      // so the canvas is as wide as the frame body inside the 640 px box.
      return (
        <HeatmapChartFrame
          title={item.title}
          state={HEATMAP_FRAME_STATES[item.fixture]}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={galleryChartHeight(item)}
          animate={false}
          // Wave 3: undefined for every earlier item, which then draws exactly as before.
          fit={item.fit}
          nouns={item.nouns}
          rowAxis={item.rowAxis}
          columnAxis={item.columnAxis}
        />
      )
    case 'coverage-map':
      return <CoverageMapItem item={item} />
    case 'scatter':
      return <ScatterItem item={item} />
    case 'failure-groups':
      // React SVG laid out by d3: the plot measures its own width inside the frame.
      return (
        <FailureGroupsFrame
          title={item.title}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          state={failureGroupsStateOf(item.groups)}
          scopeLabel="last 7 days"
          height={galleryChartHeight(item)}
          initialView={item.view}
          // Six frames on one page: each table's scrolling region needs its own name (axe landmark-unique).
          tableCaption={`${item.title}: groups, largest first`}
          onOpenGroup={OPEN_NO_GROUP}
        />
      )
    case 'systemic-clusters':
      // The tab's own frame, drawn from a settled state: the gallery fetches nothing.
      return (
        <SystemicClusters
          params={null}
          allProjects={false}
          headingLevel={GALLERY_FRAME_HEADING_LEVEL}
          height={galleryChartHeight(item)}
          state={CLUSTERS_STATES[item.fixture]}
          title={item.title}
        />
      )
  }
}

export default function ChartGalleryPage() {
  const [params] = useSearchParams()
  const theme = resolveTheme(params.get(THEME_QUERY_PARAM))
  const statesView = params.get(VIEW_QUERY_PARAM) === STATES_VIEW_PARAM
  const fluid = params.get(CANVAS_QUERY_PARAM) === GALLERY_FLUID_CANVAS_PARAM

  useEffect(() => {
    if (theme === null) return
    const root = document.documentElement
    const previous = root.getAttribute('data-theme')
    root.setAttribute('data-theme', theme)
    return () => {
      if (previous === null) root.removeAttribute('data-theme')
      else root.setAttribute('data-theme', previous)
    }
  }, [theme])

  return (
    <main
      data-testid="chart-gallery"
      data-gallery-theme={theme ?? 'default'}
      data-gallery-view={statesView ? 'states' : 'charts'}
      data-gallery-canvas-mode={fluid ? 'fluid' : 'pinned'}
      className="min-h-screen bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)]"
    >
      <header className="mb-6">
        <h1 className="text-xl font-semibold">Chart gallery (dev only)</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Every chart component from fixed data, {GALLERY_CANVAS.width}×{GALLERY_CANVAS.height}{' '}
          px, animation off. Theme: <code>{theme ?? 'active theme'}</code>
          {theme === null && (
            <>
              {' '}
              — add <code>?theme=</code> one of {THEMES.map((t) => t.id).join(', ')}.
            </>
          )}{' '}
          {statesView ? (
            <>Showing every chart frame state.</>
          ) : (
            <>
              Add <code>?view=states</code> for every chart frame state.
            </>
          )}
        </p>
      </header>

      {statesView ? (
        // One live region for every frame on the page (ChartAnnouncer).
        <ChartAnnouncerProvider>
          <ChartStatesGallery />
        </ChartAnnouncerProvider>
      ) : (
      // The Wave-2 items are real chart frames, so this page needs the one
      // page-level announcer too - never one live region per frame.
      <ChartAnnouncerProvider>
      <div className="flex flex-col gap-6">
        {GALLERY_ITEMS.map((item) => {
          const headingId = `gallery-${item.id}-title`
          // A FRAMED item already has a heading: the frame's own, at level 2.
          // A second <h2> above it named the same chart twice, so every
          // framed item read as "Stacked bars, Stacked bars".
          const framed = galleryFramed(item)
          return (
            <section
              key={item.id}
              data-gallery-item={item.id}
              data-gallery-empty={item.empty ? 'true' : 'false'}
              data-gallery-engine={galleryEngine(item)}
              {...(framed ? { 'aria-label': item.title } : { 'aria-labelledby': headingId })}
              className="w-fit max-w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
            >
              {!framed && (
                <h2 id={headingId} className="mb-3 text-sm font-medium">
                  {item.title}
                </h2>
              )}
              {/*
                The canvas is a fixed 640px, wider than a phone. The page must
                not scroll sideways, so the canvas scrolls inside this box —
                and a scrollable box has to be reachable from the keyboard.
                It carries NO role and no name of its own: a framed chart's
                frame is already a named `role="group"`, and wrapping it in a
                second one made every chart a group inside a group saying the
                same thing.
              */}
              <div
                data-gallery-scroller={item.id}
                tabIndex={0}
                // Fluid or pinned, anything that still cannot shrink (an
                // ECharts canvas is a fixed pixel size) scrolls HERE rather
                // than making the whole page scroll sideways.
                className="max-w-full overflow-x-auto"
              >
                <div
                  data-gallery-canvas={item.id}
                  style={fluid ? { width: '100%' } : galleryCanvasSize(item)}
                  className={framed ? 'w-full' : 'flex items-center justify-center'}
                >
                  {renderChart(item)}
                </div>
              </div>
            </section>
          )
        })}
      </div>
      </ChartAnnouncerProvider>
      )}
    </main>
  )
}
