/**
 * Wave 2.6 fix round (R2-14) — the footer's count agrees with the fold notice.
 *
 * `chart-data` caps the series axis at 8 — seven suites and its merged
 * "Other" — and says `truncated_total: 11`. The frame's footer counted the
 * eight SERIES it got ("Showing top 8 of 11") right beside the fold notice's
 * "11 suites: the 7 with the most executions are drawn, and the other 4 are
 * folded into Other": eight counted Other as a suite. The top-N count is the
 * suites drawn on their own, and the sentence names the merged remainder:
 * "Showing top 7 of 11 + Other".
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import MultiSeriesChartFrame from './MultiSeriesChartFrame'
import { addUtcDays } from './seriesAlignment'
import { OTHER_KEY, buildMultiSeriesModel, type MultiSeriesInputSeries, type MultiSeriesModel } from './multiSeriesModel'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <svg>{children}</svg>,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  ReferenceLine: () => null,
  Tooltip: () => null,
  Line: () => null,
  usePlotArea: () => ({ x: 60, y: 16, width: 400, height: 200 }),
  useChartWidth: () => 600,
  useXAxisScale: () => () => 460,
  useYAxisScale: () => (value: unknown) => 216 - (value as number) * 2,
}))

const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const suite = (key: string, n: number): MultiSeriesInputSeries => ({
  key,
  label: key === OTHER_KEY ? 'Other' : key,
  points: [0, 1, 2].map((i) => ({ x: addUtcDays('2026-09-05', i), y: 80 + i, n })),
})

function truncated(shown: number, total: number): ChartState<unknown> {
  const meta = { truncated: true, truncated_total: total, truncated_axes: { series: { shown, total } } } as unknown as EnvelopeMeta
  return { status: 'truncated', data: null, meta, shown, total, revalidating: false }
}

function footerOf(model: MultiSeriesModel, state: ChartState<unknown>): string {
  const { container } = render(
    <ChartAnnouncerProvider>
      <MultiSeriesChartFrame title="Pass rate by suite" state={state} model={model} headingLevel={3} animate={false} />
    </ChartAnnouncerProvider>,
  )
  return container.querySelector('[data-chart-truncation]')?.textContent ?? ''
}

describe('R2-14: "top N of M" counts the suites drawn, not "Other"', () => {
  it('server fold: 7 suites + its Other, 11 in all -> top 7 of 11', () => {
    const series = [...Array.from({ length: 7 }, (_, i) => suite(`s${i}`, 100 - i)), suite(OTHER_KEY, 50)]
    const meta = { truncated_axes: { series: { shown: 8, total: 11 } } } as unknown as EnvelopeMeta
    const model = buildMultiSeriesModel({ series, metric: RATE, meta, seriesNoun: 'suites' })
    expect(model.foldNotice).toMatch(/^11 suites: the 7 with the most executions are drawn, and the other 4 are folded into "Other"\./)
    const footer = footerOf(model, truncated(8, 11))
    expect(footer).toMatch(/Showing top 7 of 11 \+ Other/)
    expect(footer).not.toMatch(/top 8/)
    // The suffix is the footer sentence's, not the table button's.
    expect(footer).toMatch(/View the top 7 as a table/)
    expect(footer).not.toMatch(/table \+ Other/)
  })

  it('a truncated chart with no "Other" drawn keeps the count it was given', () => {
    const series = Array.from({ length: 5 }, (_, i) => suite(`s${i}`, 100 - i))
    const model = buildMultiSeriesModel({ series, metric: RATE, seriesNoun: 'suites' })
    const footer = footerOf(model, truncated(5, 9))
    expect(footer).toMatch(/Showing top 5 of 9/)
    expect(footer).not.toMatch(/Other/)
  })
})
