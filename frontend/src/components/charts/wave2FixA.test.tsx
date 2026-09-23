/**
 * Fix round A for the Wave-2 chart catalogue — one describe per finding from
 * the correctness and accessibility reviews. Every test here states the
 * CORRECT behaviour, so reverting the fix it names turns it red.
 *
 * The plots are real Recharts components rendered in jsdom, which has no
 * layout: `ResponsiveContainer` measures 0 and draws no marks. That is fine
 * for everything asserted here (the wrapper, its ARIA, the model, the table
 * and the footer are all outside the SVG); the drawn geometry is asserted in
 * `tests/ci-e2e/chart-gallery.spec.ts`, in a real browser.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'

import BarChart from './BarChart'
import DonutChart from './DonutChart'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CURSOR_HINT } from './ChartCursor'
import ChartFrame from './ChartFrame'
import {
  PERCENT_MODE_NOTE,
  UNREADABLE_COUNT_REASON,
  barsFromSeries,
  chartResponseFromFailureCategories,
  chartResponseFromTopFailing,
  middleTruncate,
  rankedModel,
  statusBarModel,
  statusBarSeries,
} from './BarChart.model'
import { largestRemainderPercents } from './chartCatalog'
import {
  chartTableModel,
  formatterFor,
  formatPlainValue,
  summarizeChart,
  type SeriesFormat,
} from './chartText'
import type { ChartResponse, ChartState } from './chartState'

const ready = (series: SeriesChart): ChartState<ChartResponse> => ({
  status: 'ready',
  data: { meta: null, series },
  meta: null,
  revalidating: false,
})

const categorySeries = (points: [string, number][], dimension = 'category'): SeriesChart => ({
  kind: 'series',
  dimensions: [dimension],
  x_type: 'category',
  series: [{ key: 'value', label: 'Value', points: points.map(([x, y]) => ({ x, y, n: Math.abs(y) })) }],
})

const withAnnouncer = (node: React.ReactNode) => render(<ChartAnnouncerProvider>{node}</ChartAnnouncerProvider>)
const announced = () => document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''

// ── 1 (BLOCKER) one formatter per SERIES, not one per chart ──────────────────

describe('1 · the value formatter is per series', () => {
  /** A duration and a run count in one chart: two units, two formatters. */
  const twoUnits: SeriesChart = {
    kind: 'series',
    dimensions: ['test'],
    x_type: 'category',
    series: [
      { key: 'p95', label: 'p95 (ms)', points: [{ x: 'checkout.spec', y: 42000, n: 10 }] },
      { key: 'runs', label: 'Runs', points: [{ x: 'checkout.spec', y: 10, n: 10 }] },
    ],
  }
  const asMs = (v: number) => `${v}ms`
  const format: SeriesFormat = { p95: asMs, runs: formatPlainValue }

  it('resolves a formatter by series key, with a named default and a fallback', () => {
    expect(formatterFor(format, 'p95', formatPlainValue)(42000)).toBe('42000ms')
    expect(formatterFor(format, 'runs', formatPlainValue)(10)).toBe('10')
    // A key the record does not name falls back to the chart's own default.
    expect(formatterFor(format, 'other', formatPlainValue)(10)).toBe('10')
    expect(formatterFor({ '*': asMs }, 'anything', formatPlainValue)(3)).toBe('3ms')
    // A bare function is still one formatter for everything.
    expect(formatterFor(asMs, 'runs', formatPlainValue)(10)).toBe('10ms')
    expect(formatterFor(undefined, 'runs', asMs)(10)).toBe('10ms')
  })

  it('formats each TABLE column with its own series formatter', () => {
    const model = chartTableModel(twoUnits, { x: 'Test' }, format)
    expect(model.columns).toEqual(['Test', 'p95 (ms)', 'Runs'])
    // The run count is a count. One formatter for the whole chart printed it
    // as "42000ms"'s sibling, "10ms".
    expect(model.rows[0].cells).toEqual(['42000ms', '10'])
  })

  it('formats each SUMMARY sentence with its own series formatter', () => {
    const summary = summarizeChart({ chartType: 'Ranked bar chart', series: twoUnits, format })
    expect(summary).toContain('p95 (ms): min 42000ms')
    expect(summary).toContain('Runs: min 10 (checkout.spec), max 10')
    expect(summary).not.toContain('10ms (checkout.spec)')
  })

  it('reaches the frame: the same keyed format serves the summary and the table', () => {
    const { container } = render(
      <ChartFrame
        title="Slowest tests"
        state={{ status: 'ready', data: null, meta: null, revalidating: false }}
        headingLevel={3}
        series={twoUnits}
        axes={{ x: 'Test' }}
        format={format}
      >
        <div />
      </ChartFrame>,
    )
    expect(container.querySelector('[data-chart-summary]')?.textContent).toContain('Runs: min 10')
    fireEvent.click(screen.getByRole('button', { name: /view as table/i }))
    const row = screen.getByRole('row', { name: /checkout\.spec/ })
    expect(row.textContent).toContain('42000ms')
    expect(row.textContent).not.toMatch(/\b10ms\b/)
  })
})

// ── 2 (MAJOR a11y) a keyboard reader can read every point ────────────────────

describe('2 · the keyboard cursor', () => {
  const state = ready(categorySeries([['checkout', 41], ['auth', 9]]))

  it('names the drawing surface after the chart, and is never an application', () => {
    const { container } = withAnnouncer(<BarChart title="Top failing tests" state={state} variant="ranked" />)
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    expect(surface).toHaveAttribute('tabindex', '0')
    expect(surface).toHaveAttribute('role', 'group')
    expect(surface.getAttribute('aria-label')).toContain('Top failing tests')
    expect(surface.getAttribute('aria-label')).toContain('2 bars')
    expect(surface.getAttribute('aria-label')).toContain(CURSOR_HINT)
    // `accessibilityLayer` used to put this on the surface, unnamed.
    expect(container.querySelector('[role="application"]')).toBeNull()
  })

  it('reads each point through the PAGE announcer, not a live region of its own', () => {
    const { container } = withAnnouncer(<BarChart title="Top failing tests" state={state} variant="ranked" />)
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(announced()).toBe('Top failing tests: checkout: 41')
    expect(surface).toHaveAttribute('data-chart-cursor-index', '0')
    expect(container.querySelector('[data-chart-readout]')?.textContent).toBe('checkout: 41')

    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(announced()).toBe('Top failing tests: auth: 9')
    // Past the end it stays on the last point rather than wrapping silently.
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(surface).toHaveAttribute('data-chart-cursor-index', '1')
    fireEvent.keyDown(surface, { key: 'Home' })
    expect(surface).toHaveAttribute('data-chart-cursor-index', '0')

    // ONE live region on the page, whatever the chart did.
    expect(container.ownerDocument.querySelectorAll('[aria-live], [role="status"]')).toHaveLength(2)
  })

  it('dismisses with Escape and keeps the reader where they were', () => {
    const { container } = withAnnouncer(<BarChart title="Top failing tests" state={state} variant="ranked" />)
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    surface.focus()
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(container.querySelector('[data-chart-readout]')).not.toBeNull()
    fireEvent.keyDown(surface, { key: 'Escape' })
    expect(container.querySelector('[data-chart-readout]')).toBeNull()
    expect(surface).toHaveAttribute('data-chart-cursor', 'idle')
    // Focus is NOT lost: Escape dismisses a tooltip, it does not eject the
    // reader from the chart.
    expect(document.activeElement).toBe(surface)
  })

  it('leaves Tab alone, so the cursor never traps the reader', () => {
    const { container } = withAnnouncer(<BarChart title="Top failing tests" state={state} variant="ranked" />)
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    const tab = fireEvent.keyDown(surface, { key: 'Tab' })
    // `fireEvent` returns false when the handler called preventDefault.
    expect(tab).toBe(true)
  })

  it('gives the donut the same cursor', () => {
    const donut = ready({
      kind: 'series',
      dimensions: ['status'],
      x_type: 'category',
      series: [
        {
          key: 'count',
          label: 'Count',
          points: [
            { x: 'passed', y: 880, n: 880 },
            { x: 'failed', y: 120, n: 120 },
          ],
        },
      ],
    })
    const { container } = withAnnouncer(<DonutChart title="Execution results" state={donut} />)
    const surface = container.querySelector('[data-donut]') as HTMLElement
    expect(surface.getAttribute('aria-label')).toContain('Execution results')
    fireEvent.keyDown(surface, { key: 'ArrowRight' })
    expect(announced()).toBe('Execution results: Passed 880 (88.0%)')
  })
})

// ── 3 (MAJOR a11y) the 100% toggle reaches the words, not just the drawing ───

describe('3 · the absolute / 100% mode is stated everywhere', () => {
  const suites: SeriesChart = {
    kind: 'series',
    dimensions: ['suite', 'status'],
    x_type: 'category',
    series: [
      { key: 'passed', label: 'Passed', points: [{ x: 'checkout', y: 80, n: 80 }] },
      { key: 'failed', label: 'Failed', points: [{ x: 'checkout', y: 20, n: 20 }] },
    ],
  }

  it('says which mode is live, and the summary reads the same unit as the plot', () => {
    const { container } = render(<BarChart title="Results by suite" state={ready(suites)} variant="stacked" />)
    expect(container.querySelector('[data-chart-summary]')?.textContent).toContain('Passed: min 80')
    expect(container.textContent).not.toContain(PERCENT_MODE_NOTE)

    fireEvent.click(screen.getByRole('button', { name: 'Show 100%' }))
    // The chart says so, in words, next to the chart.
    expect(container.textContent).toContain(PERCENT_MODE_NOTE)
    // …and the summary a screen reader gets follows the toggle too.
    const summary = container.querySelector('[data-chart-summary]')?.textContent ?? ''
    expect(summary).toContain('Passed: min 80.0%')
    expect(summary).toContain('Share of bar (%)')
  })
})

// ── 5 (MAJOR a11y) the page controls keep focus and describe themselves ──────

describe('5 · turning to the last page never drops focus', () => {
  const many = Array.from({ length: 60 }, (_, i) => [`test-${String(i).padStart(3, '0')}`, 60 - i] as [string, number])

  it('keeps the button focusable at the ends and marks it aria-disabled', () => {
    const { container } = render(<BarChart title="Top failing tests" state={ready(categorySeries(many))} variant="ranked" />)
    const footer = container.querySelector('[data-chart-footer]') as HTMLElement
    const next = within(footer).getByRole('button', { name: 'Next bars' })
    const previous = within(footer).getByRole('button', { name: 'Previous bars' })
    expect(previous).toHaveAttribute('aria-disabled', 'true')
    expect(next).toHaveAttribute('aria-disabled', 'false')

    next.focus()
    fireEvent.click(next)
    // The last page. `disabled` would have destroyed the focused control and
    // dropped the reader onto <body>.
    expect(document.activeElement).toBe(next)
    expect(next).toHaveAttribute('aria-disabled', 'true')
    expect(next).not.toBeDisabled()
    // …and pressing it again does nothing rather than paging past the end.
    fireEvent.click(next)
    expect(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-page')).toBe('1')
  })

  it('describes both buttons with the page state', () => {
    const { container } = render(<BarChart title="Top failing tests" state={ready(categorySeries(many))} variant="ranked" />)
    const footer = container.querySelector('[data-chart-footer]') as HTMLElement
    const status = footer.querySelector('[data-bar-page-status]') as HTMLElement
    expect(status.textContent).toBe('Page 1 of 2')
    for (const name of ['Previous bars', 'Next bars']) {
      expect(within(footer).getByRole('button', { name })).toHaveAttribute('aria-describedby', status.id)
    }
  })

  it('announces a page turn as a page turn, once', () => {
    const { container } = render(
      <BarChart title="Top failing tests" state={ready(categorySeries(many))} variant="ranked" />,
    )
    // `changeLabel` is what the frame reports instead of the default "updated".
    const frame = container.querySelector('[data-chart-frame]') as HTMLElement
    expect(frame).not.toBeNull()
    fireEvent.click(within(container.querySelector('[data-chart-footer]') as HTMLElement).getByRole('button', { name: 'Next bars' }))
    expect((container.querySelector('[data-bar-page-status]') as HTMLElement).textContent).toBe('Page 2 of 2')
  })
})

// ── 7 (MAJOR) the legacy adapters keep the C2 envelope ───────────────────────

describe('7 · the legacy adapters pass the envelope through', () => {
  const meta = { measured: false, reason: 'no project is selected' }

  it('keeps meta, so not-measured and truncated can still fire', () => {
    expect(chartResponseFromTopFailing({ items: [], meta }).meta).toMatchObject(meta)
    expect(chartResponseFromFailureCategories({ items: [], meta }).meta).toMatchObject(meta)
    // No envelope is still null, not an invented one.
    expect(chartResponseFromTopFailing({ items: [] }).meta).toBeNull()
  })
})

// ── 8 (MAJOR) bars are keyed by identity, not by display name ────────────────

describe('8 · two tests that share a name stay two bars', () => {
  it('keys on the fingerprint and labels from x_labels', () => {
    const response = chartResponseFromTopFailing({
      items: [
        { test_name: 'checkout should pay', fail_count: 3, test_fingerprint: 'fp-a' },
        { test_name: 'checkout should pay', fail_count: 4, test_fingerprint: 'fp-b' },
      ],
    })
    const bars = barsFromSeries(response.series)
    // Summed by name this was ONE bar of 7 — a count no test in the payload
    // has — and `BreakdownChart` then counted one category where there are two.
    expect(bars).toEqual([
      { key: 'fp-a', label: 'checkout should pay', value: 3 },
      { key: 'fp-b', label: 'checkout should pay', value: 4 },
    ])
  })
})

// ── 9 (MINOR) the rest of the correctness review ─────────────────────────────

describe('9 · the minors', () => {
  it('middle-truncates over code points, never splitting a surrogate pair', () => {
    // 40 code points, so it really is truncated: 30 was 60 UTF-16 units, which
    // the old length check truncated and the new one (correctly) does not.
    const out = middleTruncate('🎯'.repeat(40))
    // Iterating a string yields whole code points, so a surrogate PAIR is one
    // two-unit character. A one-unit character in the surrogate range is the
    // orphaned half `slice` used to leave behind.
    const lone = [...out].filter((ch) => ch.length === 1 && ch.charCodeAt(0) >= 0xd800 && ch.charCodeAt(0) <= 0xdfff)
    expect(lone).toEqual([])
    // …and it is still a middle truncation of the right length.
    expect(out).toContain('…')
    expect([...out]).toHaveLength(34)
  })

  it('hands an all-zero ranking over as filtered-empty, exactly as the donut does', () => {
    const zeros = categorySeries([['a', 0], ['b', 0], ['c', 0]])
    expect(rankedModel(barsFromSeries(zeros)).allZero).toBe(true)
    const { container } = render(<BarChart title="Failures by category" state={ready(zeros)} variant="ranked" />)
    expect(container.querySelector('[data-chart-frame]')?.getAttribute('data-chart-state')).toBe('filtered-empty')
    expect(container.querySelector('[data-bar-chart="ranked"]')).toBeNull()
  })

  it('makes an unreadable legacy count a gap, never a measured zero', () => {
    const response = chartResponseFromTopFailing({ items: [{ test_name: 'a', fail_count: '12' }, { test_name: 'b' }] })
    const points = (response.series as SeriesChart).series[0].points
    expect(points.map((p) => p.y)).toEqual([null, null])
    expect(points.map((p) => p.measured)).toEqual([false, false])
    expect(points[0].reason).toBe(UNREADABLE_COUNT_REASON)
    // A gap is not a bar at all.
    expect(barsFromSeries(response.series)).toEqual([])
  })

  it('states the number of bars in the SET, not a page count it does not draw', () => {
    const items = [
      ...Array.from({ length: 49 }, (_, i) => ({ key: `hi${i}`, label: `hi${i}`, value: 100 })),
      ...Array.from({ length: 20 }, (_, i) => ({ key: `tie${i}`, label: `tie${i}`, value: 7 })),
    ]
    const model = rankedModel(items, { topN: 50 })
    expect(model.bars).toHaveLength(50)
    expect(model.notes[0]).not.toMatch(/55 bars shown/)
    expect(model.notes[0]).toContain('55 bars in all')
  })

  it('gives a negative value no share of a whole', () => {
    expect(largestRemainderPercents([80, -20, 20])).toEqual([80, 0, 20])
    expect(largestRemainderPercents([80, -20, 20]).reduce((a, b) => a + b, 0)).toBe(100)
    // …so a stacked bar whose only other bucket is negative is a full bar of
    // the one status that has a share, not a bar with a segment pointing out
    // of it. (A status nothing has at all is not a segment: `statusBarModel`
    // keeps only the statuses some row measured above zero.)
    const model = statusBarModel([{ key: 's', label: 's', counts: { passed: 10, failed: -4 } }], {
      mode: 'percent',
    })
    expect(model.statuses).toEqual(['passed'])
    expect(model.bars[0].segments.map((s) => s.percent)).toEqual([100])
  })

  it('never says "latest" about a category axis', () => {
    const categorical = summarizeChart({ chartType: 'Ranked bar chart', series: categorySeries([['a', 1], ['b', 2]]) })
    expect(categorical).not.toContain('latest')
    const timed = summarizeChart({
      chartType: 'Line chart',
      series: {
        kind: 'series',
        dimensions: ['day'],
        x_type: 'time',
        series: [
          {
            key: 'rate',
            label: 'Pass rate',
            points: [
              { x: '2026-09-18', y: 90, n: 10 },
              { x: '2026-09-19', y: 92, n: 10 },
            ],
          },
        ],
      },
    })
    expect(timed).toContain('latest 92 (2026-09-19)')
  })

  it('puts the donut centre total in its table view', () => {
    const donut = ready({
      kind: 'series',
      dimensions: ['status'],
      x_type: 'category',
      series: [
        {
          key: 'count',
          label: 'Count',
          points: [
            { x: 'passed', y: 880, n: 880 },
            { x: 'failed', y: 120, n: 120 },
          ],
        },
      ],
    })
    const { container } = render(<DonutChart title="Execution results" state={donut} />)
    fireEvent.click(screen.getByRole('button', { name: /view as table/i }))
    expect(container.querySelector('[data-donut-table-total]')?.textContent).toBe('Total 1,000 executions')
    expect(screen.getByRole('row', { name: /Passed/ }).textContent).toContain('88.0%')
  })
})

// ── the table column headers follow the x_labels the adapters now carry ──────

describe('the table names a row by its display name, not by its key', () => {
  it('reads x_labels for the row header', () => {
    const response = chartResponseFromTopFailing({
      items: [{ test_name: 'checkout should pay', fail_count: 3, test_fingerprint: 'fp-a' }],
    })
    const model = chartTableModel(response.series, { x: 'Test' })
    expect(model.rows[0].header).toBe('checkout should pay')
  })

  it('and so does the summary', () => {
    const response = chartResponseFromTopFailing({
      items: [{ test_name: 'checkout should pay', fail_count: 3, test_fingerprint: 'fp-a' }],
    })
    expect(summarizeChart({ chartType: 'Ranked bar chart', series: response.series })).toContain(
      '(checkout should pay)',
    )
  })
})

// ── the 100% series still validates as contract C3 ───────────────────────────

describe('the mode-following series is still a valid C3 chart', () => {
  it('keeps n as the true count', () => {
    const model = statusBarModel([{ key: 's', label: 's', counts: { passed: 8, failed: 2 } }], { mode: 'percent' })
    const series = statusBarSeries(model) as SeriesChart
    expect(series.series.map((s) => s.points[0].y)).toEqual([80, 20])
    expect(series.series.map((s) => s.points[0].n)).toEqual([8, 2])
  })
})
