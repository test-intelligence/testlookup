/**
 * The matrix heatmap inside `ChartFrame` (K5, Wave 2.6; VIZ-501, Wave 3): the
 * frame every heatmap section mounts — Trends' suite x day, Suite detail's
 * test x run, Coverage's suite x environment and suite x release.
 *
 * It reads ONE `/analytics/heatmap` response (C3 `matrix` + C2 `meta`), and
 * `heatmapFromMatrix` turns it into the drawable matrix (rate in 0..1, rows in
 * the chosen order, keys permuted with them). That matrix is the frame's
 * `series`, so the canvas, the generated summary and "View as table" read the
 * same cells — a null cell is hatched on the canvas and "No data" (or
 * "Nothing evaluated") in the table, never 0%.
 *
 * The footer states, in words, everything the picture cannot: the row order,
 * the server's row cut ("Top 40 of 200 suites by failures."), a column cut,
 * the partial day, and a colour scale fitted to the data. The envelope's
 * generic `truncated` state is shown as `ready` here, because its "Showing top
 * N of M" says nothing about WHICH axis was cut.
 *
 * The heatmap renderer is ECharts on a canvas, lazily loaded; this module holds
 * no engine code, and it is only ever reached from a lazy section chunk.
 */
import { forwardRef, useMemo, type ReactNode } from 'react'
import type { AnyChartSeries } from '@/lib/viz/contracts'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import HeatmapChart from './HeatmapChart'
import { useContainerWidth } from './chartLayout'
import { utcDayLabel } from './chartText'
import { hasChartData, type ChartResponse, type ChartState } from './chartStateCore'
import {
  columnsAreDays,
  fittedDomain,
  fittedDomainNote,
  heatmapCellMark,
  heatmapColumnsNote,
  heatmapDescription,
  heatmapFromMatrix,
  heatmapOrderNote,
  heatmapRowsNote,
  printedRowLabels,
  printedRunLabels,
  RUN_AXIS_TITLE,
  SUITE_DAY_NOUNS,
  type HeatmapMarkDimensions,
  type HeatmapNouns,
  type HeatmapRowSort,
} from './heatmapFromMatrix'
import type { ChartMark, MarkActivationProps } from './marks'
import type { HeatmapMatrix } from './engines/echarts/heatmapOption'

/** What the frame says when the payload is not a matrix (it cannot be drawn as a heatmap). */
export const HEATMAP_SHAPE_ERROR = 'This heatmap needs a matrix response, and the server sent a different shape.'

/** The default order rule, in words (a rate matrix, worst first: `heatmapOrderNote('rate', 'worst')`). */
export const HEATMAP_ORDER_NOTE = 'Rows: lowest pass rate first.'

type FrameShell = Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes' | 'format' | 'state'>

export interface HeatmapChartFrameProps extends FrameShell {
  /** One `/analytics/heatmap` state (any C3 kind is accepted and refused in words unless it is a matrix). */
  state: ChartState<ChartResponse<AnyChartSeries>>
  /** What a row and a column are, for the footer and the description. Default suites x days. */
  nouns?: HeatmapNouns
  /** The row axis title (summary and table). Default "Suite". */
  rowAxis?: string
  /** The column axis title. Default "Day (UTC)". */
  columnAxis?: string
  /** Row order. Default `worst`. */
  sort?: HeatmapRowSort
  /** Fit the colour scale to the measured range (default off: 0..100%). */
  fit?: boolean
  animate?: boolean
  /**
   * Mark activation (Wave 3): what a cell's row and column are, as C5
   * dimensions. With `onMarkActivate`, a cell can be acted on (click, Enter,
   * the buttons under the plot); without both, the heatmap is as before.
   */
  markDimensions?: HeatmapMarkDimensions
  onMarkActivate?: MarkActivationProps['onMarkActivate']
  markIntents?: MarkActivationProps['markIntents']
}

function HeatmapBody({
  matrix,
  description,
  height,
  animate,
  columnLabels,
  rowLabels,
  domain,
  markOf,
  onMarkActivate,
  markIntents,
  runColumns,
}: {
  matrix: HeatmapMatrix
  description: string
  height: number
  animate?: boolean
  columnLabels?: readonly string[]
  rowLabels: readonly string[]
  domain?: readonly [number, number]
  markOf?: (index: number) => ChartMark | null
  runColumns: boolean
} & MarkActivationProps) {
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
        // Ranked, READ DOWNWARD: row 0 at the top, as the footer and the table
        // say (ECharts' default draws it at the bottom).
        rowsTopDown
        columnLabels={columnLabels}
        rowLabels={rowLabels}
        domain={domain}
        markOf={markOf}
        onMarkActivate={onMarkActivate}
        markIntents={markIntents}
        // A run axis: titled (its labels are bare build numbers), and a long build cut from the start (F-04, F-19).
        columnAxisName={runColumns ? RUN_AXIS_TITLE : undefined}
        columnLabelCut={runColumns ? 'start' : undefined}
      />
    </div>
  )
}

const HeatmapChartFrame = forwardRef<HTMLDivElement, HeatmapChartFrameProps>(function HeatmapChartFrame(
  {
    state,
    nouns = SUITE_DAY_NOUNS,
    rowAxis = 'Suite',
    columnAxis = 'Day (UTC)',
    sort = 'worst',
    fit = false,
    animate,
    height = 320,
    footer,
    markDimensions,
    onMarkActivate,
    markIntents,
    ...frameProps
  },
  ref,
) {
  const source = hasChartData(state) ? state.data.series : null
  const meta = hasChartData(state) ? state.meta : null
  // The columns are runs (test x run): each one labelled by its build number.
  const runColumns = nouns.columns[0] === 'run'
  const model = useMemo(
    () => (source?.kind === 'matrix' ? heatmapFromMatrix(source, meta, sort, { runColumns }) : null),
    [source, meta, sort, runColumns],
  )
  // A day axis prints the kit's short day ("Sep 5"), as every other day axis
  // does; a run axis the build alone ("228 (2)", its title says "Build"). The
  // tooltip and the table keep the full label from the matrix.
  const columnLabels = useMemo(() => {
    if (model && runColumns) return printedRunLabels(model.matrix.x_labels)
    const keys = model ? (model.matrix.x_keys ?? model.matrix.x_labels) : []
    return columnsAreDays(keys) ? keys.map(utcDayLabel) : undefined
  }, [model, runColumns])
  const rowLabels = useMemo(() => (model ? printedRowLabels(model.matrix.y_labels) : []), [model])
  const domain = useMemo(() => (fit && model ? fittedDomain(model.matrix) : null), [fit, model])
  // A cell's mark, from the DRAWN (sorted) matrix: the index the canvas and the keyboard use.
  const markOf = useMemo(
    () =>
      model && markDimensions && onMarkActivate
        ? (index: number) => heatmapCellMark(model.matrix, index, markDimensions)
        : undefined,
    [model, markDimensions, onMarkActivate],
  )

  const frameState = useMemo((): ChartState<unknown> => {
    if (source !== null && source.kind !== 'matrix') {
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

  const notes = model
    ? [
        heatmapOrderNote(model.matrix.value_type, sort),
        heatmapRowsNote(model.rows, nouns),
        heatmapColumnsNote(model.columns),
        model.partialColumn !== null ? 'Today’s column is partial: runs are still arriving.' : '',
        domain ? fittedDomainNote(model.matrix, domain) : '',
      ].filter((note) => note !== '')
    : []
  const frameFooter: ReactNode =
    model || footer ? (
      <>
        {model ? <span data-heatmap-rows="">{notes.join(' ')}</span> : null}
        {footer}
      </>
    ) : undefined

  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      state={frameState}
      height={height}
      series={model?.matrix ?? null}
      chartType="Heatmap"
      axes={{ x: columnAxis, y: rowAxis }}
      footer={frameFooter}
    >
      {model ? (
        <HeatmapBody
          matrix={model.matrix}
          description={heatmapDescription(frameProps.title, model.matrix, nouns, sort)}
          height={height}
          animate={animate}
          columnLabels={columnLabels}
          rowLabels={rowLabels}
          domain={domain ?? undefined}
          markOf={markOf}
          onMarkActivate={markOf ? onMarkActivate : undefined}
          markIntents={markIntents}
          runColumns={runColumns}
        />
      ) : null}
    </ChartFrame>
  )
})

export default HeatmapChartFrame
