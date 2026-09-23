/**
 * `SlowestTests` (VIZ-406) — the 20 slowest tests by p95, with their run count.
 *
 * Rendered as its own ranked list of bars rather than through the catalogue's
 * `BarChart`: at the time this shipped that component did not exist yet
 * (VIZ-402 is a sibling story), and a ranked list of at most 20 rows needs no
 * chart engine at all — it is a table with a visual length encoding, so it
 * stays readable at any width, is navigable with a screen reader row by row,
 * and costs nothing to render. If `BarChart` lands and gains a ranked
 * horizontal mode, this body can be swapped for it without touching the model.
 *
 * VIZ-402 has since landed, and the swap is still NOT a drop-in — three
 * reasons, each of which would change what this chart claims:
 *   - `barsFromSeries` SUMS every series at the same x, so the `p95` and `runs`
 *     series of `slowestToChartSeries` would be added together into one bar;
 *   - it then drops every `y: null` row, so the unmeasured test would vanish
 *     rather than sorting last with a "—";
 *   - `RankedModel.valueLabel` is `formatNumber`, so "2,520,000" would replace
 *     "42m", and there is no slot for the run count beside the bar.
 * Swapping would mean a duration-formatted, null-preserving, two-value ranked
 * mode in `BarChart` — a change to that component, not a change here.
 *
 * The run count sits beside every bar because a p95 over two runs is not the
 * same claim as a p95 over two hundred, and ranking without it invites acting
 * on noise.
 *
 * A test whose p95 was NOT measured shows "—" and sorts LAST (`rankSlowestTests`)
 * rather than sorting as zero: it is not the fastest test in the project, it is
 * an unknown one.
 *
 * KEYBOARD AND NARROW WIDTHS. There is no drawing surface here to give a cursor
 * to: every row is real text in a real `<li>`, which a screen reader walks with
 * its own virtual cursor and which no `role="application"` takes away — the
 * reason the VIZ-401/402 charts need `useChartCursor` does not apply. What it
 * did need is room: below `SLOWEST_STACK_WIDTH` the name takes its own line
 * (`useContainerWidth`), because on one line at 320 px it truncated to about
 * nine characters and all twenty rows read identically.
 */
import { formatDuration, formatNumber, NO_VALUE } from '@/utils/formatters'
import { CHART_VARS } from './tokens'
import { useContainerWidth } from './chartLayout'
import type { SlowestTestsModel } from './durationBuckets'

export interface SlowestTestsProps {
  model: SlowestTestsModel
}

export const SLOWEST_EMPTY = 'No test in this window carries a duration'

/**
 * Under this many CSS px the name goes onto its own line.
 *
 * On one line the row spends a fixed 9.5rem on the bar and the value, so at
 * 320 px the name column is about 60 px — nine characters. Every one of the
 * twenty rows then reads "checkout s…", the full name is a `title` only a
 * mouse can reach, and the chart says nothing at all at 400 % zoom (SC 1.4.10,
 * SC 1.4.4). Stacked, the name gets the row's whole width.
 */
export const SLOWEST_STACK_WIDTH = 420

export default function SlowestTests({ model }: SlowestTestsProps) {
  // The longest MEASURED p95 sets the bar scale; an unmeasured row has no bar.
  const longest = model.rows.reduce((most, row) => (row.p95 !== null && row.p95 > most ? row.p95 : most), 0)
  // 0 is "not measured yet", which must read as "not narrow" — never as narrow.
  const [wrapRef, width] = useContainerWidth<HTMLElement>()
  const stacked = width > 0 && width < SLOWEST_STACK_WIDTH

  if (model.rows.length === 0) {
    return (
      <p data-chart="slowest-tests" data-chart-empty="" className="text-sm text-[var(--color-text-secondary)]">
        {SLOWEST_EMPTY}
      </p>
    )
  }

  return (
    <figure ref={wrapRef} data-chart="slowest-tests" className="m-0 flex flex-col gap-1">
      <ol
        data-slowest-list=""
        data-slowest-stacked={stacked ? 'true' : 'false'}
        className={stacked ? 'flex flex-col gap-2' : 'flex flex-col gap-1'}
      >
        {model.rows.map((row) => {
          const width = row.p95 !== null && longest > 0 ? Math.max((row.p95 / longest) * 100, 1) : 0
          const bar = (
            <span className="flex items-center gap-1">
              <span
                data-testid="ranked-bar"
                aria-hidden="true"
                // The value axis starts at zero by construction: the bar's
                // length IS the value, scaled against the longest measured p95.
                style={{ width: `${width}%`, backgroundColor: CHART_VARS.series[4] }}
                className="inline-block h-2 rounded-sm"
              />
            </span>
          )
          const value = (
            <span className={stacked ? 'flex items-baseline gap-2 tabular-nums' : 'flex flex-col items-end tabular-nums'}>
              <span className="text-[var(--color-text)]">{row.p95 === null ? NO_VALUE : formatDuration(row.p95)}</span>
              <span className="text-[10px] text-[var(--color-text-secondary)]">
                {formatNumber(row.runs)} {row.runs === 1 ? 'run' : 'runs'}
              </span>
            </span>
          )
          return stacked ? (
            <li key={row.name} className="flex flex-col gap-1 text-xs">
              {/* The whole name, wrapped — not nine characters and a `title`. */}
              <span data-slowest-name="" className="break-words text-[var(--color-text)]">
                {row.name}
              </span>
              <span className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
                {bar}
                {value}
              </span>
            </li>
          ) : (
            <li key={row.name} className="grid grid-cols-[minmax(0,1fr)_5rem_4.5rem] items-center gap-2 text-xs">
              <span data-slowest-name="" title={row.name} className="truncate text-[var(--color-text)]">
                {row.name}
              </span>
              {bar}
              {value}
            </li>
          )
        })}
      </ol>
      {model.truncated && (
        <figcaption data-chart-truncation="" className="text-xs text-[var(--color-text-secondary)]">
          Showing the slowest {formatNumber(model.shown)} of {formatNumber(model.total)} tests.
        </figcaption>
      )}
    </figure>
  )
}
