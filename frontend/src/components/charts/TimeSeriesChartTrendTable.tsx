/**
 * The VIZ-405 trend analysis as the TABLE reader gets it — through
 * `ChartFrame`'s `tableExtras`, beside the data table and the release markers.
 *
 * The overlays are drawn over the plot rather than being values in the
 * series, so `ChartTable` cannot know about them; without this block "View as
 * table" would lose the moving average, the trend line and — worst — which
 * days were flagged and by what rule. Every computed value is listed whether
 * or not its overlay is currently drawn: the table is the complete view.
 *
 * The statistics and their explanations (method, window, sample size) are
 * stated in full above the table, as text, since a table cell cannot be
 * hovered for them.
 */
import {
  ANOMALY_LABEL,
  MOVING_AVERAGE_LABEL,
  TREND_LINE_LABEL,
  anomalyRuleText,
  formatTrendFit,
  formatTrendPercent,
  periodTakeaway,
  trendTakeaway,
  type TrendAnalysis,
} from '@/lib/trendStats'
import { NO_VALUE } from './chartText'

const CELL = 'px-2 py-1'
const HEAD = `border-b border-[var(--color-border)] ${CELL} font-semibold`

export default function TimeSeriesChartTrendTable({
  analysis,
  days,
  zoomNote,
}: {
  analysis: TrendAnalysis
  /** Every day the chart draws, in order — the rows. */
  days: readonly string[]
  /**
   * VIZ-407: set while the chart is zoomed. The rows are then the zoomed days,
   * but every statistic above them still describes the WHOLE window (they are
   * computed on it, never on the slice) — and the table must say which is which.
   */
  zoomNote?: string
}) {
  const period = periodTakeaway(analysis.period)
  const summary = (
    <div data-trend-table-summary="" className="space-y-1 px-2 py-1 text-xs text-[var(--color-text)]">
      <p className="font-medium">Trend analysis</p>
      {zoomNote && <p data-trend-table-zoom="">{zoomNote}</p>}
      {analysis.available ? (
        <>
          <p>{trendTakeaway(analysis)}.</p>
          <p className="text-[var(--color-text-secondary)]">{analysis.explain.trendLine}</p>
          <p className="text-[var(--color-text-secondary)]">
            {MOVING_AVERAGE_LABEL}: {analysis.explain.movingAverage}
          </p>
          <p className="text-[var(--color-text-secondary)]">
            {ANOMALY_LABEL}: {analysis.explain.anomalies}
          </p>
        </>
      ) : (
        <p>Overlays unavailable: {analysis.reason}.</p>
      )}
      {period ? <p>{period}.</p> : <p>Last 7 days vs previous 7: not measurable.</p>}
      <p className="text-[var(--color-text-secondary)]">{analysis.period.explain}</p>
    </div>
  )

  if (!analysis.available) {
    return (
      <div data-chart-trend-table="" className="mt-3 rounded border border-[var(--color-border)]">
        {summary}
      </div>
    )
  }

  const ma = new Map(analysis.movingAverage.map((point) => [point.x, point]))
  const fitted = new Map(analysis.fit.line.map((point) => [point.x, point.value]))
  const flagged = new Map(analysis.anomalies.map((anomaly) => [anomaly.x, anomaly]))

  return (
    <div data-chart-trend-table="" className="mt-3 rounded border border-[var(--color-border)]">
      {summary}
      <table className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
        <caption className="px-2 py-1 text-left text-sm font-medium text-[var(--color-text)]">Trend analysis by day</caption>
        <thead>
          <tr>
            <th scope="col" className={HEAD}>
              Day (UTC)
            </th>
            <th scope="col" className={HEAD}>
              {MOVING_AVERAGE_LABEL}
            </th>
            <th scope="col" className={HEAD}>
              {TREND_LINE_LABEL}
            </th>
            <th scope="col" className={HEAD}>
              {ANOMALY_LABEL}
            </th>
          </tr>
        </thead>
        <tbody>
          {days.map((day) => {
            const average = ma.get(day)
            const line = fitted.get(day)
            const anomaly = flagged.get(day)
            return (
              <tr key={day} className="border-b border-[var(--color-border)] last:border-b-0">
                <th scope="row" className={`${CELL} font-medium tabular-nums`}>
                  {day}
                </th>
                <td className={`${CELL} tabular-nums`}>{average ? formatTrendPercent(average.value) : NO_VALUE}</td>
                <td className={`${CELL} tabular-nums`}>{line === undefined ? NO_VALUE : formatTrendFit(line)}</td>
                <td className={CELL}>{anomaly ? anomalyRuleText(anomaly) : NO_VALUE}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
