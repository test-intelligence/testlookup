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
 */
import { forwardRef, useCallback, useMemo, useState } from 'react'
import type { ChartState } from './chartState'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import { formatPercentPoints, formatPlainValue, type ValueFormatter } from './chartText'
import MultiSeriesChart from './MultiSeriesChart'
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
  extends Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format' | 'changeLabel' | 'tableExtras'> {
  /** Required for `ready` / `truncated`; ignored for every other state, which the frame owns. */
  model: MultiSeriesModel | null
  state: ChartState<unknown>
  /** Series keys hidden when the chart first draws. */
  initialHidden?: readonly string[]
  animate?: boolean
}

export const MULTI_SERIES_CHART_TYPE = 'Multi-series line chart'

/** A rate is percentage points (0..100); a count is a plain number. */
const multiSeriesFormat =(model: Pick<MultiSeriesModel, 'metric'> | null): ValueFormatter =>
  model?.metric.kind === 'rate' ? formatPercentPoints : formatPlainValue

const MultiSeriesChartFrame = forwardRef<HTMLDivElement, MultiSeriesChartFrameProps>(function MultiSeriesChartFrame(
  { model, initialHidden, animate, height = 280, scopeLabel, ...frameProps },
  ref,
) {
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(() => new Set(initialHidden ?? []))
  const [change, setChange] = useState<VisibilityChange | null>(null)
  const [labelsFit, setLabelsFit] = useState(true)

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
    },
    [hidden],
  )
  const onIsolate = useCallback(
    (key: string) => {
      setHiddenKeys(isolateHidden(hidden, key, keys))
      setChange({ kind: 'isolated', key })
    },
    [hidden, keys],
  )
  const onShowAll = useCallback(() => {
    setHiddenKeys(new Set())
    setChange({ kind: 'all-shown' })
  }, [])

  const series = useMemo(() => (model ? multiSeriesToChartSeries(model, hidden) : null), [model, hidden])
  const format = multiSeriesFormat(model)
  const axes = useMemo(() => (model ? { x: model.xTitle, y: model.metric.title } : undefined), [model])
  const changeLabel = model && change ? visibilityAnnouncement(model, hidden, change) : undefined
  const caveat = model ? comparabilityCaveat(model.comparability) : null
  const summaryScope = caveat ? (scopeLabel ? `${scopeLabel}; ${caveat}` : caveat) : scopeLabel

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
    >
      {model ? (
        <MultiSeriesChart
          model={model}
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
      ) : null}
    </ChartFrame>
  )
})

export default MultiSeriesChartFrame
