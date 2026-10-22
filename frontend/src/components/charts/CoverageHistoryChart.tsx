/**
 * W08 TRND-07 — "Coverage history" inside `ChartFrame`: the daily coverage
 * snapshots of one project as two series, with the project's timeline notes
 * (TRND-04) drawn over them.
 *
 *   - bars: "Tests in the catalogue" (right axis, a count);
 *   - line: "Exercised in the last 30 days (%)" (left axis, 0-100).
 *
 * A day with no snapshot is a gap in both (the frame's table says "—" with the
 * reason): never a zero. Note text is user input and reaches the plot only as
 * React text (a `<text>` child), cut to the kit's short label; the tooltip is a
 * React component, never a string formatter.
 *
 * The frame's series is built FROM the drawn model, so the plot, the summary,
 * the table and the CSV cannot disagree. Lives in the chart kit because only
 * the kit may import Recharts (`scripts/chart-guard.mjs`).
 */
import { forwardRef, useMemo } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatNumber } from '@/utils/formatters'
import ChartFrame, { type ChartFrameProps } from './ChartFrame'
import ChartResponsive from './ChartResponsive'
import type { ChartState } from './chartStateCore'
import { useChartAnimation } from './motion'
import { ChartLegend, type LegendEntry } from './patterns'
import { CHART_FONT, CHART_VARS, RECHARTS_AXIS_TICK, RECHARTS_AXIS_TITLE, RECHARTS_GRID, RECHARTS_TOOLTIP_STYLE } from './tokens'
import {
  CATALOGUE_LABEL,
  coverageHistoryToChartSeries,
  EXERCISED_LABEL,
  type CoverageHistoryModel,
} from './coverageHistoryModel'
import { noteDateRange, noteShortLabel, notesCovering, type NoteMarker } from './timeSeriesModel'

/** The note band's fill: the axis colour at 12 % (an existing token through `color-mix`, no new one). */
export const NOTE_BAND_FILL = 'color-mix(in srgb, var(--chart-axis) 12%, transparent)'
const NOTE_DASH = '4 3'

export type CoverageHistoryChartFrameProps = Omit<ChartFrameProps, 'children' | 'series' | 'chartType' | 'axes'> & {
  state: ChartState<unknown>
  model: CoverageHistoryModel | null
  animate?: boolean
}

const shortDay = (day: string) => {
  const at = Date.parse(`${day}T00:00:00Z`)
  if (Number.isNaN(at)) return day
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(at))
}

/** A note's label on the plot: React text inside a `g` that names the note. */
function NoteLabel({ note, viewBox }: { note: NoteMarker; viewBox?: { x?: number; y?: number; width?: number } }) {
  const x = (viewBox?.x ?? 0) + 4
  const y = (viewBox?.y ?? 0) + 12
  return (
    <g data-chart-note={note.id} data-chart-note-kind={note.kind}>
      <text x={x} y={y} fill={CHART_VARS.axis} fontSize={CHART_FONT.annotation}>
        {noteShortLabel(note.label)}
      </text>
    </g>
  )
}

function HistoryTooltip({
  active,
  label,
  model,
}: {
  active?: boolean
  label?: string | number
  model: CoverageHistoryModel
}) {
  if (!active || label === undefined) return null
  const row = model.rows.find((r) => r.x === String(label))
  if (!row) return null
  const covering = notesCovering(model.notes, row.x)
  return (
    <div data-coverage-history-tip="" style={{ ...RECHARTS_TOOLTIP_STYLE, padding: '8px 10px', maxWidth: 280 }}>
      <div className="font-semibold">{shortDay(row.x)} (UTC)</div>
      {row.stored ? (
        <>
          <div>
            {CATALOGUE_LABEL}: {formatNumber(row.catalogue ?? 0)}
          </div>
          <div>
            {EXERCISED_LABEL}: {row.exercisedPct === null ? '—' : `${formatNumber(row.exercisedPct, { maximumFractionDigits: 1 })}%`}
            {row.exercised !== null ? ` (${formatNumber(row.exercised)} tests)` : ''}
          </div>
        </>
      ) : (
        <div>No snapshot for this day</div>
      )}
      {covering.map((note) => (
        <div key={note.id} data-coverage-history-tip-note="" className="mt-1">
          {note.label}
          {note.author ? ` — ${note.author}` : ''}, {noteDateRange(note)}
        </div>
      ))}
    </div>
  )
}

export function CoverageHistoryPlot({ model, height, animate: requested }: { model: CoverageHistoryModel; height: number; animate?: boolean }) {
  const animate = useChartAnimation(requested)
  const legend: LegendEntry[] = [
    // One quantity per day: the one-measure colour (W29b UIX-05; = series-2, no pixel moves).
    { key: 'catalogue', label: CATALOGUE_LABEL, fill: CHART_VARS.measure },
    { key: 'exercised', label: EXERCISED_LABEL, stroke: CHART_VARS.series[0], dot: true },
  ]
  const rows = model.rows.map((row) => ({ x: row.x, catalogue: row.catalogue, exercised: row.exercisedPct }))
  return (
    <figure data-chart="coverage-history" className="m-0 flex flex-col gap-1">
      <ChartResponsive height={height}>
        <ComposedChart data={rows} margin={{ top: 16, right: 12, left: 8, bottom: 8 }} accessibilityLayer={false}>
          <CartesianGrid {...RECHARTS_GRID} vertical={false} />
          <XAxis dataKey="x" axisLine={false} tickLine={false} tick={RECHARTS_AXIS_TICK} tickFormatter={shortDay} minTickGap={16} />
          <YAxis
            yAxisId="pct"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            axisLine={false}
            tickLine={false}
            tick={RECHARTS_AXIS_TICK}
            width={44}
            label={{ value: 'Exercised %', angle: -90, position: 'insideLeft', ...RECHARTS_AXIS_TITLE }}
          />
          <YAxis
            yAxisId="tests"
            orientation="right"
            domain={[0, Math.max(1, model.largest)]}
            allowDecimals={false}
            axisLine={false}
            tickLine={false}
            tick={RECHARTS_AXIS_TICK}
            width={56}
            label={{ value: 'Tests', angle: 90, position: 'insideRight', ...RECHARTS_AXIS_TITLE }}
          />
          <Tooltip content={<HistoryTooltip model={model} />} isAnimationActive={false} />
          <Legend content={() => <ChartLegend entries={legend} />} />
          <Bar yAxisId="tests" dataKey="catalogue" name={CATALOGUE_LABEL} fill={CHART_VARS.measure} barSize={10} isAnimationActive={animate} />
          <Line
            yAxisId="pct"
            dataKey="exercised"
            name={EXERCISED_LABEL}
            type="linear"
            stroke={CHART_VARS.series[0]}
            strokeWidth={2}
            dot={{ r: 2, strokeWidth: 0, fill: CHART_VARS.series[0] }}
            connectNulls={false}
            isAnimationActive={animate}
          />
          {model.notes.map((note) =>
            note.kind === 'band' ? (
              <ReferenceArea
                key={note.id}
                yAxisId="pct"
                x1={note.from}
                x2={note.to}
                fill={NOTE_BAND_FILL}
                fillOpacity={1}
                ifOverflow="extendDomain"
                label={<NoteLabel note={note} />}
              />
            ) : (
              <ReferenceLine
                key={note.id}
                yAxisId="pct"
                x={note.from}
                stroke={CHART_VARS.axis}
                strokeDasharray={NOTE_DASH}
                label={<NoteLabel note={note} />}
              />
            ),
          )}
        </ComposedChart>
      </ChartResponsive>
      <figcaption data-chart-axis-caption="" className="text-xs text-[var(--color-text-secondary)]">
        Days are UTC; a day without a snapshot is a gap.
      </figcaption>
    </figure>
  )
}

const CoverageHistoryChartFrame = forwardRef<HTMLDivElement, CoverageHistoryChartFrameProps>(function CoverageHistoryChartFrame(
  { model, animate, height = 260, ...frameProps },
  ref,
) {
  const series = useMemo(() => (model ? coverageHistoryToChartSeries(model) : null), [model])
  const notesTable =
    model && model.notes.length > 0 ? (
      <table data-coverage-history-notes="" className="mt-2 w-full text-left text-xs">
        <caption className="text-left font-semibold">Notes</caption>
        <tbody>
          {model.notes.map((note) => (
            <tr key={note.id}>
              <td className="pr-2">{noteDateRange(note)}</td>
              <td className="pr-2">{note.label}</td>
              <td>{note.author ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ) : null
  return (
    <ChartFrame
      {...frameProps}
      ref={ref}
      height={height}
      series={series}
      chartType="Line and bar chart"
      axes={{ x: 'Day (UTC)', y: `${EXERCISED_LABEL} / ${CATALOGUE_LABEL}` }}
      tableExtras={notesTable}
    >
      {model ? <CoverageHistoryPlot model={model} height={height} animate={animate} /> : null}
    </ChartFrame>
  )
})

export default CoverageHistoryChartFrame
