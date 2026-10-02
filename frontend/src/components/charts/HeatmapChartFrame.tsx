/**
 * K5 (Wave 2.6): the suite x day heatmap inside `ChartFrame` — the component
 * the Trends catalogue section mounts, behind both the catalogue flag and the
 * advanced-charts flag (`useHeatmapRollout`, the one seam that reads them).
 *
 * It reads the SAME `useCatalogChartData('chart-data', ...)` state as the
 * multi-series chart above it (`metric=pass_rate`, day x suite, top 7): one
 * request, two views. `heatmapFromChartData` turns that response into the
 * matrix, and the matrix is the frame's `series`, so the canvas, the generated
 * summary and "View as table" read the same cells — a null cell is hatched on
 * the canvas and "No data" in the table, never 0%.
 *
 * Truncation is stated by rows, in the frame's own footer: "top 7 of 12
 * suites; the rest are combined in Other". The envelope's generic `truncated`
 * state is shown as `ready` here, because its "Showing top N of M" would count
 * the Other row as one of the N.
 *
 * The heatmap renderer is ECharts on a canvas, lazily loaded; this module holds
 * no engine code, and it is only ever reached from a lazy section chunk.
 */
import { forwardRef, useMemo, type ReactNode } from 'react'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import HeatmapChart from './HeatmapChart'
import { useContainerWidth } from './chartLayout'
import { utcDayLabel } from './chartText'
import { hasChartData, type ChartResponse, type ChartState } from './chartStateCore'
import { heatmapDescription, heatmapFromChartData, heatmapRowsNote } from './heatmapFromChartData'
import type { NumericMatrix } from './engines/echarts/heatmapOption'

/** What the frame says when the payload is not a day x series response (it cannot be drawn as this heatmap). */
export const HEATMAP_SHAPE_ERROR = 'This heatmap needs a day-by-series response, and the server sent a different shape.'

/** The ordering rule, in words: without it a reader takes the row order for alphabetical or by volume. */
export const HEATMAP_ORDER_NOTE = 'Rows: lowest pass rate first.'

type FrameShell = Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format' | 'state'>

export interface HeatmapChartFrameProps extends FrameShell {
  /** The shared `chart-data` state (day x series, `metric=pass_rate`). */
  state: ChartState<ChartResponse>
  /** What a row is, plural, for the footer and the description. Default "suites". */
  rowNoun?: string
  /** The row axis title (summary and table). Default "Suite". */
  rowAxis?: string
  /** The column axis title. Default "Day (UTC)". */
  columnAxis?: string
  animate?: boolean
}

function HeatmapBody({
  matrix,
  description,
  height,
  animate,
  columnLabels,
}: {
  matrix: NumericMatrix
  description: string
  height: number
  animate?: boolean
  columnLabels?: readonly string[]
}) {
  // The canvas sizes each column label to the chart's real width; '100%'
  // would fall back to a narrow assumed width and cut labels it had room for.
  const [measure, width] = useContainerWidth<HTMLDivElement>()
  return (
    <div ref={measure} className="w-full">
      <HeatmapChart
        data={matrix}
        description={description}
        width={width > 0 ? width : '100%'}
        height={height}
        animate={animate}
        // Worst first, READ DOWNWARD: row 0 (the lowest rate) at the top, as the
        // footer and the table say (ECharts' default draws it at the bottom).
        rowsTopDown
        columnLabels={columnLabels}
      />
    </div>
  )
}

const HeatmapChartFrame = forwardRef<HTMLDivElement, HeatmapChartFrameProps>(function HeatmapChartFrame(
  { state, rowNoun = 'suites', rowAxis = 'Suite', columnAxis = 'Day (UTC)', animate, height = 320, footer, ...frameProps },
  ref,
) {
  const source = hasChartData(state) ? state.data.series : null
  const meta = hasChartData(state) ? state.meta : null
  const built = useMemo(() => (source?.kind === 'series' ? heatmapFromChartData(source, meta) : null), [source, meta])
  // A day axis prints the kit's short day ("Sep 5"), as every other day axis
  // does; the tooltip and the table keep the full day from the matrix.
  const columnLabels = useMemo(
    () => (built && source?.kind === 'series' && source.x_type === 'time' ? built.matrix.x_labels.map(utcDayLabel) : undefined),
    [built, source],
  )

  const frameState = useMemo((): ChartState<unknown> => {
    if (source !== null && source.kind !== 'series') {
      return {
        status: 'error',
        error: { kind: 'invalid-payload', message: HEATMAP_SHAPE_ERROR, requestId: null, status: null },
      }
    }
    if (state.status === 'truncated') {
      return { status: 'ready', data: state.data, meta: state.meta, revalidating: state.revalidating }
    }
    return state
  }, [source, state])

  const rowsNote = built ? heatmapRowsNote(built.rows, rowNoun) : ''
  const frameFooter: ReactNode =
    built || footer ? (
      <>
        {built ? (
          <span data-heatmap-rows="">
            {HEATMAP_ORDER_NOTE}
            {rowsNote ? ` ${rowsNote}` : ''}
          </span>
        ) : null}
        {footer}
      </>
    ) : undefined

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      state={frameState}
      height={height}
      series={built?.matrix ?? null}
      chartType="Heatmap"
      axes={{ x: columnAxis, y: rowAxis }}
      footer={frameFooter}
    >
      {built ? (
        <HeatmapBody
          matrix={built.matrix}
          description={heatmapDescription(frameProps.title, built.matrix, built.rows, rowNoun)}
          height={height}
          animate={animate}
          columnLabels={columnLabels}
        />
      ) : null}
    </ChartFrame>
  )
})

export default HeatmapChartFrame
