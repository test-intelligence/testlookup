/**
 * VIZ-505 — one Explorer panel: the metric over time for one facet value
 * (or for the whole scope when there is no facet), one line per series key.
 *
 * Its own request (`chart-data`, filtered to the facet key), its own states
 * (loading, empty, error with Retry: a slow suite never blanks the grid), and
 * the y-axis the page decides: a shared scale is handed in as the largest
 * value any loaded panel reported, and this panel reports its own through
 * `onMeasured`. Every panel says which scale it is on, in the frame's scope
 * slot, so two panels side by side are never read on different scales by
 * mistake.
 */
import { useEffect, useMemo, type ReactElement } from 'react'
import MultiSeriesChartFrame from '@/components/charts/MultiSeriesChartFrame'
import { buildMultiSeriesModel, multiSeriesInputFromChartData } from '@/components/charts/multiSeriesModel'
import { useCatalogChartData, type CatalogParams } from '@/components/charts/chartCatalogSources'
import type { ChartState } from '@/components/charts/chartState'
import { hasChartData } from '@/components/charts/chartStateCore'
import type { SeriesStyle } from '@/components/reports/catalogue/compareModel'
import {
  DIMENSION_LABELS,
  EXPLORE_METRICS,
  ownMaxOf,
  panelStyles,
  panelYAxis,
  yScaleText,
  type ExploreConfig,
} from './exploreModel'

/** The plot's height, px. */
export const PANEL_HEIGHT = 220
/** The frame's header, toolbar and footer around the plot, px: what a not-yet-mounted panel holds. */
export const PANEL_CHROME = 150

/** The key `chart-data` gives the one series of a single-dimension request (`SINGLE_SERIES_KEY`). */
const SINGLE_SERIES_KEY = 'value'

export const PANEL_SHAPE_ERROR = 'This panel needs a time-by-series response, and the server sent a different shape.'

export interface ExplorePanelProps {
  title: string
  /** The facet value this panel is filtered to; `null` for the one panel of an unfaceted view. */
  facetKey: string | null
  /** The request, or `null` while the scope has not resolved. */
  params: CatalogParams | null
  config: ExploreConfig
  everHadData: boolean | null
  /** The shared legend: series key → colour and dash slot. */
  styles: Readonly<Record<string, SeriesStyle>>
  /** The largest value any loaded panel reported (a shared count scale). */
  sharedMax: number | null
  /** How many panels have reported so far, and how many there are. */
  measured: number
  panels: number
  /** This panel's largest value once its data is in (0 when it has none). */
  onMeasured: (facetKey: string, ownMax: number) => void
}

export default function ExplorePanel({
  title,
  facetKey,
  params,
  config,
  everHadData,
  styles,
  sharedMax,
  measured,
  panels,
  onMeasured,
}: ExplorePanelProps): ReactElement {
  const fetched = useCatalogChartData('chart-data', { params, everHadData })
  const info = EXPLORE_METRICS[config.metric]
  const chart = hasChartData(fetched) && fetched.data.series.kind === 'series' ? fetched.data.series : null
  const meta = hasChartData(fetched) ? fetched.meta : null

  const model = useMemo(() => {
    if (!chart) return null
    // The server labels the one series of an unsplit request with the metric KEY.
    const series = multiSeriesInputFromChartData(chart).map((s) =>
      config.series === null && s.key === SINGLE_SERIES_KEY ? { ...s, label: info.label } : s,
    )
    return buildMultiSeriesModel({
      series,
      metric: { kind: info.kind, title: info.title },
      meta,
      styles: panelStyles(styles, series.map((s) => s.key)),
      seriesNoun: config.series ? DIMENSION_LABELS[config.series].plural : 'series',
      bucket: config.x,
    })
  }, [chart, meta, config.series, config.x, info, styles])

  const ownMax = ownMaxOf(model)
  const settled = model !== null || fetched.status === 'filtered-empty' || fetched.status === 'never-had-data'
  useEffect(() => {
    if (settled) onMeasured(facetKey ?? '', ownMax ?? 0)
  }, [settled, ownMax, facetKey, onMeasured])

  const shown = useMemo(() => {
    if (!model) return null
    return { ...model, yAxis: panelYAxis(info.kind, config.yScale, ownMax, sharedMax, model.yAxis) }
  }, [model, info.kind, config.yScale, ownMax, sharedMax])

  const state: ChartState<unknown> =
    hasChartData(fetched) && !chart
      ? { status: 'error', error: { kind: 'invalid-payload', message: PANEL_SHAPE_ERROR, requestId: null, status: null } }
      : fetched

  const scope = shown ? (
    <span data-explore-yscale={config.yScale} className="text-xs text-[var(--color-text-secondary)]">
      {yScaleText(info.kind, config.yScale, shown.yAxis, measured, panels)}
    </span>
  ) : undefined

  return (
    <div data-explore-panel={facetKey ?? ''} className="min-w-0">
      <MultiSeriesChartFrame
        title={title}
        headingLevel={3}
        height={PANEL_HEIGHT}
        state={state}
        model={shown}
        scope={scope}
        scopeLabel={`last ${config.days} days`}
      />
    </div>
  )
}
