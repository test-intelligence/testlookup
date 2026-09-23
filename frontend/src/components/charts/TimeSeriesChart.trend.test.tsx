/**
 * VIZ-405 — the trend overlays as the reader meets them: two toggles, drawn in
 * a style of their own (dash AND colour, never colour alone), anomaly markers
 * that carry the rule which flagged them, a disabled state that says why, an
 * explanation behind every statistic on hover AND focus, the keyboard cursor
 * and the table view.
 *
 * Asserted on what reaches Recharts (the mock captures every `Line` and
 * `ReferenceDot`), as the VIZ-403 renderer tests are.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { cloneElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChart, { TimeSeriesTooltip } from './TimeSeriesChart'
import TimeSeriesChartFrame from './TimeSeriesChartFrame'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { CHART_MESSAGES } from './chartMessages'
import { CHART_VARS } from './tokens'
import { TREND_OVERLAY_STYLE } from './TimeSeriesChartOverlayStyle'
import { trendAnalysisFixture, trendAnalysisSparseFixture } from './__fixtures__/wave2Fixtures'
import {
  ANOMALY_RULE,
  INSUFFICIENT_DATA_REASON,
  MOVING_AVERAGE_LABEL,
  TREND_LINE_LABEL,
  analyzeTrend,
  trendRowsForDay,
} from '@/lib/trendStats'

interface Captured {
  chartData: Record<string, unknown>[]
  lines: Record<string, unknown>[]
  dots: Record<string, unknown>[]
  tooltips: Record<string, unknown>[]
}

const captured: Captured = { chartData: [], lines: [], dots: [], tooltips: [] }

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children, data }: { children: ReactNode; data: Record<string, unknown>[] }) => {
    captured.chartData = data
    return <div data-testid="composed-chart">{children}</div>
  },
  CartesianGrid: () => <div />,
  Legend: ({ content }: { content?: () => ReactNode }) => <div>{content ? content() : null}</div>,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: (props: Record<string, unknown>) => {
    captured.tooltips.push(props)
    return <div />
  },
  Line: (props: Record<string, unknown>) => {
    captured.lines.push(props)
    return <div data-testid={`line-${String(props.dataKey)}`} />
  },
  Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Cell: () => <div />,
  ReferenceLine: () => <div />,
  ReferenceDot: (props: Record<string, unknown>) => {
    captured.dots.push(props)
    // Render the marker SHAPE the chart hands Recharts, at a fixed point.
    const shape = props.shape as ((p: { cx: number; cy: number }) => ReactElement) | undefined
    return <svg data-testid="reference-dot">{shape ? shape({ cx: 10, cy: 10 }) : null}</svg>
  },
}))

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

function reset() {
  captured.chartData = []
  captured.lines = []
  captured.dots = []
  captured.tooltips = []
}

/** The lines drawn in the LAST render, by data key. */
const lastLines = () => {
  const byKey = new Map<string, Record<string, unknown>>()
  for (const line of captured.lines) byKey.set(String(line.dataKey), line)
  return byKey
}

/** The value, or a failed test. */
function must<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error('expected a value')
  return value
}

const toggle = (name: RegExp) => screen.getByRole('button', { name })
const ANOMALY_DAY = '2026-03-23'

describe('VIZ-405 · off by default', () => {
  it('draws no overlay, no toggle and no statistic when trend overlays are not requested', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} />)
    expect(document.querySelector('[data-trend-controls]')).toBeNull()
    expect(document.querySelector('[data-trend-stats]')).toBeNull()
    expect([...lastLines().keys()]).toEqual(['rate'])
    expect(captured.dots).toHaveLength(0)
    // The rows handed to Recharts carry nothing new either.
    expect(Object.keys(captured.chartData[0]).sort()).toEqual(['executions', 'partial', 'rate', 'x'])
  })
})

describe('VIZ-405 · the toggles', () => {
  it('offers "7-day moving average" and "Trend line", both off at first', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const group = screen.getByRole('group', { name: /trend overlays/i })
    const ma = within(group).getByRole('button', { name: /7-day moving average/i })
    const trend = within(group).getByRole('button', { name: /trend line/i })
    expect(ma).toHaveAttribute('aria-pressed', 'false')
    expect(trend).toHaveAttribute('aria-pressed', 'false')
    expect(ma).not.toHaveAttribute('aria-disabled')
    expect([...lastLines().keys()]).toEqual(['rate'])
  })

  it('draws the moving average and the trend line in a distinct DASHED style and a token colour', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    fireEvent.click(toggle(/7-day moving average/i))
    fireEvent.click(toggle(/trend line/i))
    expect(toggle(/7-day moving average/i)).toHaveAttribute('aria-pressed', 'true')
    expect(toggle(/trend line/i)).toHaveAttribute('aria-pressed', 'true')
    const lines = lastLines()
    const rate = must(lines.get('rate'))
    const ma = must(lines.get('movingAverage'))
    const trend = must(lines.get('trendLine'))
    // Never colour-only: each overlay has a dash the rate line does not, and
    // the two overlays' dashes differ from each other.
    expect(rate.strokeDasharray).toBeUndefined()
    expect(ma.strokeDasharray).toBeTruthy()
    expect(trend.strokeDasharray).toBeTruthy()
    expect(ma.strokeDasharray).not.toBe(trend.strokeDasharray)
    // …and a colour from the tokens that is not the rate line's.
    for (const overlay of [ma, trend]) {
      expect(CHART_VARS.series).toContain(overlay.stroke)
      expect(overlay.stroke).not.toBe(rate.stroke)
    }
    // A gap stays a gap in the average too.
    expect(ma.connectNulls).toBe(false)
  })

  it('hands Recharts the module’s numbers, not re-derived ones', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }} />)
    const analysis = analyzeTrend(trendAnalysisFixture.points)
    if (!analysis.available) throw new Error('fixture should be analysable')
    const byDay = new Map(captured.chartData.map((row) => [row.x, row]))
    for (const point of analysis.movingAverage) expect(byDay.get(point.x)?.movingAverage).toBe(point.value)
    for (const point of analysis.fit.line) expect(byDay.get(point.x)?.trendLine).toBe(point.value)
    // No average on a day nobody ran anything (the Saturdays).
    expect(byDay.get('2026-03-14')?.movingAverage ?? null).toBeNull()
  })

  it('works controlled: reports the change and draws what it is told', () => {
    reset()
    const onShownChange = vi.fn()
    render(
      <TimeSeriesChart
        model={trendAnalysisFixture}
        trendOverlays={{ shown: { movingAverage: false, trendLine: true }, onShownChange }}
      />,
    )
    expect([...lastLines().keys()].sort()).toEqual(['rate', 'trendLine'])
    fireEvent.click(toggle(/7-day moving average/i))
    expect(onShownChange).toHaveBeenCalledWith({ movingAverage: true, trendLine: true })
  })

  it('adds the overlays to the legend with the same dash they are drawn with', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }} />)
    const legend = document.querySelector('[data-chart-legend]') as HTMLElement
    const entry = (label: string) =>
      Array.from(legend.querySelectorAll('li')).find((li) => li.textContent === label)?.querySelector('[data-legend-swatch]')
    expect(entry(MOVING_AVERAGE_LABEL)?.getAttribute('stroke-dasharray')).toBe(String(lastLines().get('movingAverage')?.strokeDasharray))
    expect(entry(TREND_LINE_LABEL)?.getAttribute('stroke-dasharray')).toBe(String(lastLines().get('trendLine')?.strokeDasharray))
  })
})

describe('VIZ-405 · too little data', () => {
  it('disables both toggles with the visible reason, and draws nothing even if asked', () => {
    reset()
    render(
      <TimeSeriesChart
        model={trendAnalysisSparseFixture}
        trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }}
      />,
    )
    const reason = document.querySelector('[data-trend-disabled-reason]') as HTMLElement
    expect(reason).toHaveTextContent(INSUFFICIENT_DATA_REASON)
    expect(reason).toBeVisible()
    for (const button of [toggle(/7-day moving average/i), toggle(/trend line/i)]) {
      expect(button).toHaveAttribute('aria-disabled', 'true')
      expect(button).toHaveAttribute('aria-pressed', 'false')
      expect(button.getAttribute('aria-describedby')?.split(' ')).toContain(reason.id)
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-pressed', 'false')
    }
    expect([...lastLines().keys()]).toEqual(['rate'])
    expect(captured.dots).toHaveLength(0)
  })

  it('still states the last week against the previous one', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisSparseFixture} trendOverlays={{}} />)
    expect(document.querySelector('[data-trend-stats]')?.textContent).toMatch(
      /Last 7 days 73\.0% vs 94\.8% the previous 7 \(down 21\.8 pts; 600 vs 600 executions\)/,
    )
  })
})

describe('VIZ-405 · anomaly markers', () => {
  it('marks the flagged day with a SHAPE on the rate axis', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    expect(captured.dots).toHaveLength(1)
    expect(captured.dots[0]).toMatchObject({ x: ANOMALY_DAY, y: 79, yAxisId: 'rate' })
    const marker = document.querySelector(`[data-trend-anomaly="${ANOMALY_DAY}"]`)
    // A triangle path, not a recoloured dot.
    expect(marker?.tagName.toLowerCase()).toBe('path')
    expect(marker?.getAttribute('d')).toMatch(/Z$/)
  })

  it('the day’s tooltip states the rule that flagged it', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const tip = captured.tooltips[captured.tooltips.length - 1].content as ReactElement
    render(cloneElement(tip as ReactElement<{ active?: boolean; label?: string }>, { active: true, label: ANOMALY_DAY }))
    const tooltip = document.querySelector('[data-chart-tooltip]') as HTMLElement
    expect(tooltip.textContent).toMatch(/Flagged as unusual/)
    expect(tooltip.textContent).toMatch(/79\.0% is 15\.1 MADs below 94\.1%, the median of the 3 previous Mondays/)
    expect(tooltip.textContent).toContain(ANOMALY_RULE)
  })

  it('lists the flagged days with a matching shape in the statistics', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const stat = document.querySelector('[data-trend-stat="anomalies"]') as HTMLElement
    expect(stat.textContent).toMatch(/1 day flagged as unusual/)
    expect(stat.querySelector('path[data-trend-anomaly-swatch]')).not.toBeNull()
  })
})

describe('VIZ-405 · every statistic is explainable on hover AND focus', () => {
  const explanationFor = (el: HTMLElement) => {
    const id = el.getAttribute('aria-describedby')?.split(' ').find((part) => document.getElementById(part)?.getAttribute('role') === 'tooltip')
    return id ? (document.getElementById(id) as HTMLElement) : null
  }

  it('opens the moving average’s method, window and sample on focus, and closes on Escape', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const ma = toggle(/7-day moving average/i)
    const tip = must(explanationFor(ma))
    expect(tip).not.toBeVisible()
    act(() => ma.focus())
    expect(tip).toBeVisible()
    expect(tip.textContent).toMatch(/Method:/)
    expect(tip.textContent).toMatch(/Window: 7 calendar days/)
    expect(tip.textContent).toMatch(/Sample: 21 days averaged from 27 days with runs \(5,511 executions\)/)
    fireEvent.keyDown(ma, { key: 'Escape' })
    expect(tip).not.toBeVisible()
  })

  it('opens the trend line’s explanation on hover, and closes when the pointer leaves', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const trend = toggle(/trend line/i)
    const tip = must(explanationFor(trend))
    fireEvent.mouseEnter(trend.parentElement as HTMLElement)
    expect(tip).toBeVisible()
    expect(tip.textContent).toMatch(/least-squares/)
    expect(tip.textContent).toMatch(/2026-03-01 to 2026-03-30 \(30 days, 27 with runs, 5,511 executions\)/)
    fireEvent.mouseLeave(trend.parentElement as HTMLElement)
    expect(tip).not.toBeVisible()
  })

  it('explains the period comparison and the anomaly rule on activation, and closes when focus leaves', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    for (const [stat, pattern] of [
      ['period', /2026-03-24 to 2026-03-30: 93\.2%, 1,485 executions on 7 days\. 2026-03-17 to 2026-03-23: 91\.4%, 1,276 executions on 6 days/],
      ['anomalies', /same weekday in the previous 4 weeks.*13 days judged, 1 flagged/],
      ['trend', /least-squares/],
    ] as const) {
      const button = document.querySelector(`[data-trend-stat="${stat}"] button`) as HTMLElement
      const tip = must(explanationFor(button))
      act(() => button.focus())
      // A toggletip: focus alone describes it (aria-describedby); activation shows it.
      expect(tip).not.toBeVisible()
      fireEvent.click(button)
      expect(tip).toBeVisible()
      expect(tip.textContent).toMatch(pattern)
      // Short enough to be heard as a description on every focus.
      expect((tip.textContent ?? '').length).toBeLessThanOrEqual(240)
      act(() => button.blur())
      expect(tip).not.toBeVisible()
    }
  })
})

describe('VIZ-405 · the keyboard cursor reads the overlays', () => {
  it('reads the average, the trend and the anomaly rule for a day', () => {
    reset()
    render(
      <ChartAnnouncerProvider>
        <TimeSeriesChart
          model={trendAnalysisFixture}
          title="Pass rate"
          timeZone="UTC"
          locale="en-US"
          trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }}
        />
      </ChartAnnouncerProvider>,
    )
    const surface = document.querySelector('[data-chart-cursor]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'Home' })
    // 2026-03-23 is the 23rd day: 22 steps right of the first.
    for (let i = 0; i < 22; i++) fireEvent.keyDown(surface, { key: 'ArrowRight' })
    const said = document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''
    expect(said).toMatch(/2026-03-23/)
    expect(said).toMatch(/7-day moving average \d+\.\d%/)
    expect(said).toMatch(/Trend line \d+\.\d%/)
    expect(said).toContain(ANOMALY_RULE)
    expect(document.querySelector('[data-chart-readout]')?.textContent).toContain(ANOMALY_RULE)
  })
})

describe('VIZ-405 · inside the frame', () => {
  const state = { status: 'ready' as const, data: null, meta: null, revalidating: false }

  it('uses the module’s sentence as the takeaway while an overlay is on, with the period line', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={trendAnalysisFixture}
        state={state}
        takeaway="Caller takeaway"
        trendAnalysis={{ initialShown: { trendLine: true } }}
      />,
    )
    expect(document.querySelector('[data-chart-takeaway]')?.textContent).toBe(
      'Pass rate is falling 0.8 pts per week (30 days, 27 days with runs) · Last 7 days 93.2% vs 91.4% the previous 7 (up 1.8 pts; 1,485 vs 1,276 executions)',
    )
    // Turning the overlay off gives the caller's sentence back — the period line stays.
    fireEvent.click(screen.getByRole('button', { name: /trend line/i }))
    expect(document.querySelector('[data-chart-takeaway]')?.textContent).toBe(
      'Caller takeaway · Last 7 days 93.2% vs 91.4% the previous 7 (up 1.8 pts; 1,485 vs 1,276 executions)',
    )
  })

  it('leaves the takeaway and the table exactly as they were when not requested', () => {
    render(<TimeSeriesChartFrame title="Pass rate trend" headingLevel={3} model={trendAnalysisFixture} state={state} takeaway="Kept" />)
    expect(document.querySelector('[data-chart-takeaway]')?.textContent).toBe('Kept')
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.queryByRole('table', { name: /trend analysis/i })).toBeNull()
  })

  it('lists the overlays and the flagged day, with its rule, in the table view', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={trendAnalysisFixture}
        state={state}
        trendAnalysis
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const table = screen.getByRole('table', { name: /trend analysis/i })
    const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Day (UTC)', MOVING_AVERAGE_LABEL, TREND_LINE_LABEL, 'Flagged as unusual'])
    const analysis = analyzeTrend(trendAnalysisFixture.points)
    const anomalyRow = within(table).getByRole('rowheader', { name: ANOMALY_DAY }).parentElement as HTMLElement
    const [anomalyText] = trendRowsForDay(analysis, ANOMALY_DAY, { movingAverage: false, trendLine: false }).map((r) => r.value)
    expect(anomalyRow.textContent).toContain(anomalyText)
    // A day with no average shows "—", never a number it does not have.
    const firstRow = within(table).getByRole('rowheader', { name: '2026-03-01' }).parentElement as HTMLElement
    expect(firstRow.textContent).toContain('—')
    // The statistics themselves, with their explanations, beside the table.
    const summary = document.querySelector('[data-trend-table-summary]') as HTMLElement
    expect(summary.textContent).toMatch(/Pass rate is falling 0\.8 pts per week/)
    expect(summary.textContent).toMatch(/Last 7 days 93\.2% vs 91\.4%/)
    expect(summary.textContent).toMatch(/Method: least-squares/)
  })

  it('states the reason in the table view when the overlays are unavailable', () => {
    render(
      <TimeSeriesChartFrame
        title="Pass rate trend"
        headingLevel={3}
        model={trendAnalysisSparseFixture}
        state={state}
        trendAnalysis
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    const summary = document.querySelector('[data-trend-table-summary]') as HTMLElement
    expect(summary.textContent).toContain(INSUFFICIENT_DATA_REASON)
    expect(summary.textContent).toMatch(/Last 7 days 73\.0% vs 94\.8%/)
    expect(screen.queryByRole('table', { name: /trend analysis/i })).toBeNull()
  })
})

describe('VIZ-405 fix round B · explanations are dismissible and reopenable (SC 1.4.13)', () => {
  const explanationFor = (el: HTMLElement) => {
    const id = el.getAttribute('aria-describedby')?.split(' ').find((part) => document.getElementById(part)?.getAttribute('role') === 'tooltip')
    return id ? (document.getElementById(id) as HTMLElement) : null
  }

  it('closes an explanation opened by HOVER on Escape pressed anywhere, not only on the button', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    const trend = toggle(/trend line/i)
    const tip = must(explanationFor(trend))
    fireEvent.mouseEnter(trend.parentElement as HTMLElement)
    expect(tip).toBeVisible()
    // Focus is elsewhere (the body): the key reaches the document, not the button.
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(tip).not.toBeVisible()
    // Moving the pointer back in asks for it again.
    fireEvent.mouseLeave(trend.parentElement as HTMLElement)
    fireEvent.mouseEnter(trend.parentElement as HTMLElement)
    expect(tip).toBeVisible()
  })

  it('makes each statistic a toggletip: aria-expanded, toggled by activation, reopenable after Escape', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{}} />)
    for (const stat of ['trend', 'period', 'anomalies']) {
      const button = document.querySelector(`[data-trend-stat="${stat}"] button`) as HTMLElement
      const tip = must(explanationFor(button))
      expect(button).toHaveAttribute('aria-expanded', 'false')
      // Enter and Space activate a <button> as a click.
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-expanded', 'true')
      expect(tip).toBeVisible()
      fireEvent.keyDown(button, { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(tip).not.toBeVisible()
      fireEvent.click(button)
      expect(tip).toBeVisible()
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(tip).not.toBeVisible()
    }
  })

  it('describes a disabled toggle by its visible reason ONCE — no second copy in a tooltip', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisSparseFixture} trendOverlays={{}} />)
    const reason = document.querySelector('[data-trend-disabled-reason]') as HTMLElement
    for (const button of [toggle(/7-day moving average/i), toggle(/trend line/i)]) {
      expect(button.getAttribute('aria-describedby')).toBe(reason.id)
      expect(explanationFor(button)).toBeNull()
    }
  })
})

describe('VIZ-405 fix round B · the pressed state is more than a tint', () => {
  it('shows a check mark on a pressed toggle, and a neutral, struck-through swatch on one that is off', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{ initialShown: { movingAverage: true } }} />)
    const on = toggle(/7-day moving average/i)
    const off = toggle(/trend line/i)
    expect(on.querySelector('[data-trend-toggle-check]')).not.toBeNull()
    expect(off.querySelector('[data-trend-toggle-check]')).toBeNull()
    const swatch = (button: HTMLElement) => button.querySelector('[data-trend-toggle-swatch]') as SVGElement
    expect(swatch(on).querySelector('line')?.getAttribute('stroke')).toBe(String(lastLines().get('movingAverage')?.stroke))
    expect(swatch(on).querySelector('[data-trend-swatch-strike]')).toBeNull()
    // Off: the swatch keeps its dash, drawn in the neutral colour AND struck through — not a colour-only difference.
    expect(swatch(off).querySelector('line')?.getAttribute('stroke')).toBe(CHART_VARS.neutral)
    expect(swatch(off).querySelector('line')?.getAttribute('stroke-dasharray')).toBe(TREND_OVERLAY_STYLE.trendLine.dash)
    expect(swatch(off).querySelector('[data-trend-swatch-strike]')).not.toBeNull()
    // The check mark takes the same room whether shown or not, so toggling never shifts the row.
    expect(on.querySelector('[data-trend-toggle-mark]')?.getAttribute('class')).toBe(off.querySelector('[data-trend-toggle-mark]')?.getAttribute('class'))
  })
})

describe('VIZ-405 fix round B · each overlay is drawn on a card-coloured halo', () => {
  it('puts a wider, solid, card-coloured copy of each shown overlay between the bars and the rate line', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{ initialShown: { movingAverage: true, trendLine: true } }} />)
    const order = captured.lines.slice(-5).map((line) => `${String(line.className ?? '')}|${String(line.dataKey)}`)
    expect(order).toEqual([
      'trend-overlay-halo|movingAverage',
      'trend-overlay-halo|trendLine',
      '|rate',
      'trend-overlay-moving-average|movingAverage',
      'trend-overlay-trend-line|trendLine',
    ])
    for (const key of ['movingAverage', 'trendLine']) {
      const halo = must(captured.lines.find((line) => line.className === 'trend-overlay-halo' && line.dataKey === key))
      const overlay = must(lastLines().get(key))
      expect(halo.stroke).toBe(CHART_VARS.card)
      // Its own Recharts layer, between the bars' (300) and the lines' (400): see the style module.
      expect(halo.zIndex).toBe(350)
      expect(overlay.zIndex).toBeUndefined()
      expect(halo.strokeDasharray).toBeUndefined()
      expect(Number(halo.strokeWidth)).toBeGreaterThanOrEqual(Number(overlay.strokeWidth) + 4)
      expect(halo.type).toBe(overlay.type)
      // Out of the legend and the tooltip: it is a drawing aid, not a series.
      expect(halo.legendType).toBe('none')
      expect(halo.tooltipType).toBe('none')
    }
  })

  it('draws no halo for an overlay that is off, and none at all by default', () => {
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} trendOverlays={{ initialShown: { trendLine: true } }} />)
    expect(captured.lines.filter((line) => line.className === 'trend-overlay-halo').map((line) => line.dataKey)).toEqual(['trendLine'])
    reset()
    render(<TimeSeriesChart model={trendAnalysisFixture} />)
    expect(captured.lines.some((line) => line.className === 'trend-overlay-halo')).toBe(false)
  })
})

describe('VIZ-405 fix round B · the gallery fixture under the new rules', () => {
  it('still flags 2026-03-23 — against the three Mondays before it', () => {
    const analysis = analyzeTrend(trendAnalysisFixture.points)
    if (!analysis.available) throw new Error('fixture should be analysable')
    expect(analysis.anomalies.map((a) => a.x)).toEqual([ANOMALY_DAY])
    const [flagged] = analysis.anomalies
    // Mondays 2026-03-02 94.4, 03-09 94.1, 03-16 93.4 (python recompute): median 94.1, MAD 0.3 → 1.
    expect(flagged.median).toBeCloseTo(94.1, 9)
    expect(flagged.baselineDays).toBe(3)
    expect(flagged.deviations).toBeCloseTo(15.1, 9)
    expect(analysis.judgedDays).toBe(13)
    // The 3-execution day (2026-03-10) is under the minimum: never judged.
    expect(analysis.fit.direction).toBe('falling')
  })

  it('keeps every explanation under 240 characters (they were 280-344, read in full on every focus)', () => {
    const analysis = analyzeTrend(trendAnalysisFixture.points)
    if (!analysis.available) throw new Error('fixture should be analysable')
    const lengths = Object.fromEntries(Object.entries(analysis.explain).map(([key, text]) => [key, text.length]))
    for (const [key, length] of Object.entries(lengths)) expect(length, key).toBeLessThanOrEqual(240)
  })
})

describe('TimeSeriesTooltip without overlay rows', () => {
  it('renders exactly the VIZ-403 rows', () => {
    render(<TimeSeriesTooltip active label={ANOMALY_DAY} model={trendAnalysisFixture} timeZone="UTC" locale="en-US" />)
    expect(document.querySelector('[data-chart-tooltip]')?.textContent).not.toMatch(/Flagged/)
  })
})
