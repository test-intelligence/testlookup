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
import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import TrendChart from '@/components/charts/TrendChart'
import DefectDonut from '@/components/charts/DefectDonut'
import PassRateGauge from '@/components/charts/PassRateGauge'
import HeatmapChart from '@/components/charts/HeatmapChart'
import DonutChart from '@/components/charts/DonutChart'
import BarChart, { BreakdownChart } from '@/components/charts/BarChart'
import TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import DurationChartFrame from '@/components/charts/DurationChartFrame'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { ChartResponse, ChartState } from '@/components/charts/chartState'
import type { TimeSeriesModel } from '@/components/charts/timeSeriesModel'
import type { DurationHistogramModel } from '@/components/charts/durationBuckets'
import {
  durationBandFixture,
  durationHistogramEmptyFixture,
  durationHistogramFixture,
  inProgressRunsFixture,
  slowestTestsFixture,
  trendSinglePointFixture,
  trendWithReleasesFixture,
  trendZoomedAxisFixture,
} from '@/components/charts/__fixtures__/wave2Fixtures'
import type { ChartSeries } from '@/lib/viz/contracts'
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
  type GalleryCategorySeries,
  type GalleryHistogramFixture,
  type GalleryItem,
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
  }
}

function histogramFixture(key: GalleryHistogramFixture): DurationHistogramModel {
  switch (key) {
    case 'duration-histogram':
      return durationHistogramFixture
    case 'duration-histogram-empty':
      return durationHistogramEmptyFixture
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
          state={DRAWN_STATE}
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
