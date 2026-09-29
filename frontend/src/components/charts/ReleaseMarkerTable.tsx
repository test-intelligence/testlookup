/**
 * The release markers a time-series chart draws, as a real table (VIZ-403).
 *
 * A marker is drawn OVER the plot, so it is not a value in the series and
 * `ChartTable` — which is built from the series — cannot know about it. Without
 * this, "View as table" would be a strictly lesser view of the chart: a reader
 * on the table would lose the release boundaries the chart exists to relate
 * changes to. It goes in through `ChartFrame`'s `tableExtras`, beside the data
 * table, so both open together.
 */
import type { ReleaseMarker } from './timeSeriesModel'
import { releaseMarkerRows } from './timeSeriesModel'

export const RELEASE_TABLE_EMPTY = 'No releases fall inside this window'
/** Beside a release the chart's zoom (VIZ-407) has taken off the plot. */
export const OUTSIDE_ZOOM_MARK = 'outside the zoomed view'

export default function ReleaseMarkerTable({
  markers,
  caption = 'Release markers',
  outsideWindow = 0,
  outsideView,
}: {
  markers: readonly ReleaseMarker[]
  caption?: string
  /**
   * Releases that could not be placed on the axis — dated outside the drawn
   * window, or carrying a date nothing can parse. Stated, never silently
   * dropped.
   */
  outsideWindow?: number
  /**
   * VIZ-407: the days of markers the chart's zoom has taken OFF the plot. They
   * stay in this table — a zoom narrows the picture, it does not un-release
   * anything — and each is marked as outside the view, so the table does not
   * claim the plot shows them.
   */
  outsideView?: readonly string[]
}) {
  const rows = releaseMarkerRows(markers)
  const offPlot = new Set(outsideView ?? [])
  return (
    <div data-chart-release-table="" className="mt-3 rounded border border-[var(--color-border)]">
      <table className="w-full border-collapse text-left text-xs text-[var(--color-text)]">
        <caption className="px-2 py-1 text-left text-sm font-medium text-[var(--color-text)]">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="border-b border-[var(--color-border)] px-2 py-1 font-semibold">
              Day (UTC)
            </th>
            <th scope="col" className="border-b border-[var(--color-border)] px-2 py-1 font-semibold">
              Releases
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={2} className="px-2 py-1 text-[var(--color-text-secondary)]">
                {RELEASE_TABLE_EMPTY}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.day} className="border-b border-[var(--color-border)] last:border-b-0">
                <th scope="row" className="px-2 py-1 font-medium tabular-nums">
                  {row.day}
                </th>
                <td className="px-2 py-1">
                  {row.names}
                  {offPlot.has(row.day) && (
                    <span data-chart-release-outside-zoom="" className="text-[var(--color-text-secondary)]">
                      {' '}
                      ({OUTSIDE_ZOOM_MARK})
                    </span>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      {outsideWindow > 0 && (
        <p className="border-t border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
          {outsideWindow} {outsideWindow === 1 ? 'release is' : 'releases are'} not shown: dated outside this window, or
          carrying a date that could not be read.
        </p>
      )}
    </div>
  )
}
