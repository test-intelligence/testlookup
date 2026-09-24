/**
 * `MultiSeriesChart` inside `ChartFrame` (VIZ-404) — the component a page mounts.
 *
 * The frame owns every non-data state, the summary, the table view and the
 * announcer; this owns the one piece of state the comparison adds — WHICH
 * series the reader has hidden — because all three of the frame's outputs have
 * to hear about it:
 *
 *   - the TABLE keeps a hidden series and marks it "(hidden)"
 *     (`multiSeriesToChartSeries`), so hiding a line never silently drops a
 *     column;
 *   - the SUMMARY changes with it, so the frame reports a change — and the
 *     frame's `changeLabel` is the legend action in words ("cart hidden, 2 of 3
 *     series shown"), so the page's ONE announcer says exactly that, once;
 *   - the plot drops the line and its direct label.
 *
 * A not-comparable caveat is part of the SUMMARY too (as a scope sentence):
 * the frame body is described by the summary, so a reader who never reaches
 * the banner still hears that the comparison is qualified.
 *
 * VIZ-407: `zoom` adds the range brush under the plot. A zoom SLICES the model
 * this frame was given (`sliceMultiSeriesModel`) — it never rebuilds one from
 * the zoomed days, because the "top 7 + Other" fold would re-rank on the
 * slice and hand the lines different colours. The hidden series are keyed on
 * the line keys, which a slice keeps, so they survive every zoom and reset.
 * On a release-aligned axis the brush speaks in days since release start, and
 * such a range is never offered as the page window.
 */
import { forwardRef, useCallback, useMemo, useState } from 'react'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import { formatPercentPoints, formatPlainValue, type ValueFormatter } from './chartText'
import MultiSeriesChart from './MultiSeriesChart'
import ChartRangeBrush from './zoom/ChartRangeBrush'
import { useFrameZoom } from './zoom/useFrameZoom'
import {
  CALENDAR_WORDS,
  RELATIVE_DAY_WORDS,
  sliceMultiSeriesModel,
  zoomOptionsOf,
  type ChartZoomOptions,
  type ZoomRange,
} from './zoom/zoomModel'
import {
  comparabilityCaveat,
  isolateHidden,
  multiSeriesToChartSeries,
  toggleHidden,
  visibilityAnnouncement,
  type MultiSeriesModel,
  type VisibilityChange,
} from './multiSeriesModel'

export interface MultiSeriesChartFrameProps
  extends Omit<
    ChartFrameProps,
    'children' | 'series' | 'chartType' | 'axes' | 'format' | 'changeLabel' | 'tableExtras' | 'zoomNote'
  > {
  /** Required for `ready` / `truncated`; ignored for every other state, which the frame owns. */
  model: MultiSeriesModel | null
  state: ChartState<unknown>
  /** Series keys hidden when the chart first draws. */
  initialHidden?: readonly string[]
  animate?: boolean
  /**
   * VIZ-407 local zoom: `true` draws the range brush; the object form can open
   * zoomed and can offer "Apply as time filter" (see `ChartZoomOptions`).
   */
  zoom?: boolean | ChartZoomOptions
}

const NO_DAYS: readonly string[] = []

export const MULTI_SERIES_CHART_TYPE = 'Multi-series line chart'

/** A rate is percentage points (0..100); a count is a plain number. */
const multiSeriesFormat =(model: Pick<MultiSeriesModel, 'metric'> | null): ValueFormatter =>
  model?.metric.kind === 'rate' ? formatPercentPoints : formatPlainValue

const MultiSeriesChartFrame = forwardRef<HTMLDivElement, MultiSeriesChartFrameProps>(function MultiSeriesChartFrame(
  { model, initialHidden, animate, height = 280, scopeLabel, zoom, ...frameProps },
  ref,
) {
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(() => new Set(initialHidden ?? []))
  const [change, setChange] = useState<VisibilityChange | null>(null)
  const [labelsFit, setLabelsFit] = useState(true)
  // Which of the two controls the reader used last: its words are the announcement.
  const [lastControl, setLastControl] = useState<'legend' | 'zoom'>('legend')

  // Only keys this model still has: a series that left the data is not "hidden".
  const hidden = useMemo<ReadonlySet<string>>(
    () => new Set(model ? model.lines.filter((line) => hiddenKeys.has(line.key)).map((line) => line.key) : []),
    [model, hiddenKeys],
  )
  const keys = useMemo(() => model?.lines.map((line) => line.key) ?? [], [model])

  const onToggle = useCallback(
    (key: string) => {
      const next = toggleHidden(hidden, key)
      setHiddenKeys(next)
      setChange({ kind: next.has(key) ? 'hidden' : 'shown', key })
      setLastControl('legend')
    },
    [hidden],
  )
  const onIsolate = useCallback(
    (key: string) => {
      setHiddenKeys(isolateHidden(hidden, key, keys))
      setChange({ kind: 'isolated', key })
      setLastControl('legend')
    },
    [hidden, keys],
  )
  const onShowAll = useCallback(() => {
    setHiddenKeys(new Set())
    setChange({ kind: 'all-shown' })
    setLastControl('legend')
  }, [])

  const relative = model?.alignment === 'release-start'
  const zoomOptions = zoomOptionsOf(zoom)
  const zoomState = useFrameZoom({
    options: zoomOptions,
    xs: model?.xs ?? NO_DAYS,
    model,
    title: frameProps.title,
    words: relative ? RELATIVE_DAY_WORDS : CALENDAR_WORDS,
    relative,
  })
  const onZoom = zoomState.brush?.onRangeChange
  const brush =
    zoomState.brush && onZoom
      ? {
          ...zoomState.brush,
          onRangeChange: (next: ZoomRange | null) => {
            setLastControl('zoom')
            onZoom(next)
          },
        }
      : null
  // What is DRAWN: the model as given, or its zoomed slice — every line kept.
  const view = useMemo(() => (model ? sliceMultiSeriesModel(model, zoomState.range) : null), [model, zoomState.range])

  const series = useMemo(() => (view ? multiSeriesToChartSeries(view, hidden) : null), [view, hidden])
  // The strip's context: every SHOWN line over the whole window, gaps kept.
  const spark = useMemo(() => {
    if (!model) return undefined
    return model.lines
      .filter((line) => !hidden.has(line.key))
      .map((line) => {
        const byDay = new Map(line.points.map((point) => [point.x, point.y]))
        return model.xs.map((x) => byDay.get(x) ?? null)
      })
  }, [model, hidden])
  const format = multiSeriesFormat(model)
  const axes = useMemo(() => (model ? { x: model.xTitle, y: model.metric.title } : undefined), [model])
  const legendLabel = model && change ? visibilityAnnouncement(model, hidden, change) : undefined
  const changeLabel = lastControl === 'zoom' && zoomState.changeLabel ? zoomState.changeLabel : legendLabel
  const caveat = model ? comparabilityCaveat(model.comparability) : null
  const summaryScope = zoomState.scopeLabel(caveat ? (scopeLabel ? `${scopeLabel}; ${caveat}` : caveat) : scopeLabel)

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      scopeLabel={summaryScope}
      series={series}
      chartType={MULTI_SERIES_CHART_TYPE}
      axes={axes}
      format={format}
      changeLabel={changeLabel}
      zoomNote={zoomState.note}
      // The table is the zoomed days too; it says so right under itself.
      tableExtras={
        zoomState.note ? (
          <p data-chart-zoom-table-note="" className="mt-2 px-2 text-xs text-[var(--color-text-secondary)]">
            {zoomState.note}
          </p>
        ) : undefined
      }
    >
      {view ? (
        <>
          <MultiSeriesChart
            model={view}
            title={frameProps.title}
            hidden={hidden}
            onToggle={onToggle}
            onIsolate={onIsolate}
            onShowAll={onShowAll}
            format={format}
            labelsFit={labelsFit}
            onLabelsFit={setLabelsFit}
            height={height}
            animate={animate}
          />
          {brush ? (
            // A POINT scale: a line chart, whose first and last days sit on the
            // plot's edges — and so do the strip's.
            <ChartRangeBrush {...brush} scale="point" spark={spark} />
          ) : null}
        </>
      ) : null}
    </ChartFrame>
  )
})

export default MultiSeriesChartFrame
