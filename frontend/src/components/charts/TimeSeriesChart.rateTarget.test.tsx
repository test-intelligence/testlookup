/**
 * VIZ-104 K2 — a target on the rate axis. With `rateTarget` absent the chart
 * and its frame render EXACTLY what they rendered before it existed (the
 * committed `TimeSeriesChart.defaultRender.test.tsx` snapshot is the pin; this
 * file adds that absent and `undefined` are one render). With it: a dashed
 * line on the rate axis, a legend entry drawn with the same dash, and ONE
 * sentence in the summary and under the table view — or, where the line
 * cannot be drawn (off a zoomed axis, or on the canvas renderer), a sentence
 * saying so and no legend entry for a line that is not there.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChart, { RATE_TARGET_DASH, rateTargetDrawn, rateTargetSentence } from './TimeSeriesChart'
import TimeSeriesChartFrame from './TimeSeriesChartFrame'
import { CHART_MESSAGES } from './chartMessages'
import { readyState } from './chartState'
import { SVG_POINT_LIMIT, buildTimeSeriesModel, timeSeriesFromTrends, type TimeSeriesModel } from './timeSeriesModel'
import { trendWithReleasesFixture, trendZoomedAxisFixture } from './__fixtures__/wave2Fixtures'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 260 })
        : null,
  }
})

vi.mock('./engines/useEChart', () => ({
  useEChart: () => ({ containerRef: { current: null }, instanceRef: { current: null }, status: 'ready', retry: () => {} }),
}))

const NOW = new Date('2026-03-10T09:00:00Z')
const TARGET = { value: 90, label: 'Target 90%' }

/** A full 0-100 axis (no zoom): every target in range is drawn. */
const fullAxis: TimeSeriesModel = trendWithReleasesFixture

function frame(model: TimeSeriesModel, rateTarget?: typeof TARGET) {
  return render(
    <TimeSeriesChartFrame
      title="Pass rate trend"
      state={readyState(null)}
      model={model}
      headingLevel={3}
      height={260}
      animate={false}
      now={NOW}
      timeZone="UTC"
      locale="en-US"
      rateTarget={rateTarget}
    />,
  )
}

/**
 * The DOM with its generated ids renumbered in order of appearance. React's
 * `useId` counter and Recharts' clip-path counter are global, so a second
 * render of the very same tree gets different numbers; the SHAPE of the ids
 * (which element references which) is what has to be the same.
 */
function canonical(html: string): string {
  const seen = new Map<string, string>()
  return html.replace(/_r_[0-9a-z]+_|recharts\d+-clip/g, (id) => {
    if (!seen.has(id)) seen.set(id, `id${seen.size}`)
    return seen.get(id) as string
  })
}

const summaryOf = (container: HTMLElement) => container.querySelector('[data-chart-summary]')?.textContent ?? ''
const legendLabels = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('[data-chart-legend] li'), (li) => li.textContent)

describe('rateTarget absent: nothing changes', () => {
  it('renders the same DOM whether the prop is left out or passed undefined', () => {
    const omitted = canonical(frame(fullAxis).container.innerHTML)
    const passedUndefined = canonical(frame(fullAxis, undefined).container.innerHTML)
    expect(passedUndefined).toBe(omitted)
    // …and a target DOES change it, so the comparison can fail at all.
    expect(canonical(frame(fullAxis, TARGET).container.innerHTML)).not.toBe(omitted)
  })

  it('draws no target line, no legend entry, no note, and says nothing about a target', () => {
    const { container } = frame(fullAxis)
    expect(container.querySelector('.rate-target-line')).toBeNull()
    expect(legendLabels(container)).not.toContain(TARGET.label)
    expect(container.querySelector('[data-chart-target-note]')).toBeNull()
    expect(summaryOf(container)).not.toMatch(/target/i)
    expect(summaryOf(container)).toMatch(/Y axis: Pass rate % \/ Executions\./)
  })
})

describe('rateTarget present, on the axis', () => {
  it('draws a dashed line on the rate axis and a legend entry with the same dash', () => {
    const { container } = frame(fullAxis, TARGET)
    const line = container.querySelector('.rate-target-line line, line.rate-target-line')
    expect(line, 'the target line is drawn').not.toBeNull()
    expect(line?.getAttribute('stroke-dasharray')).toBe(RATE_TARGET_DASH)
    const entry = Array.from(container.querySelectorAll('[data-chart-legend] li')).find((li) => li.textContent === TARGET.label)
    expect(entry, 'a legend entry names the target').toBeDefined()
    expect(entry?.querySelector('[data-legend-swatch]')?.getAttribute('stroke-dasharray')).toBe(RATE_TARGET_DASH)
    // Drawn, so no "not drawn" note under the plot.
    expect(container.querySelector('[data-chart-target-note]')).toBeNull()
  })

  it('says where the target is in ONE sentence of the summary, and under the table', () => {
    const { container } = frame(fullAxis, TARGET)
    const sentence = rateTargetSentence(fullAxis, TARGET)
    expect(sentence).toBe('Target 90%: a dashed line at 90% on the rate axis.')
    expect(summaryOf(container)).toContain(`Y axis: Pass rate % / Executions. ${sentence}`)
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(container.querySelector('[data-chart-target-table-note]')?.textContent).toBe(sentence)
  })
})

describe('rateTarget present, but not drawable', () => {
  it('draws nothing off a zoomed axis, and says so instead of naming a line in the legend', () => {
    const [low] = trendZoomedAxisFixture.rateAxis.domain
    expect(low).toBeGreaterThan(50)
    const below = { value: low - 10, label: 'Target' }
    expect(rateTargetDrawn(trendZoomedAxisFixture, below)).toBe(false)
    const { container } = frame(trendZoomedAxisFixture, below)
    expect(container.querySelector('.rate-target-line')).toBeNull()
    expect(legendLabels(container)).not.toContain('Target')
    const note = container.querySelector('[data-chart-target-note]')?.textContent ?? ''
    expect(note).toMatch(/is outside the rate axis .* and is not drawn\.$/)
    expect(summaryOf(container)).toContain(note.replace(/\.$/, ''))
  })

  it('draws no target on the canvas renderer, and says so', () => {
    const days = Array.from({ length: SVG_POINT_LIMIT + 4 }, (_, i) => {
      const date = new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10)
      return { date, passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }
    })
    const long = buildTimeSeriesModel({ points: timeSeriesFromTrends(days) })
    expect(long.renderer).toBe('echarts')
    const { container } = render(<TimeSeriesChart model={long} rateTarget={TARGET} now={NOW} timeZone="UTC" />)
    expect(container.querySelector('[data-chart-target-note]')?.textContent).toBe(
      'Target 90% (90%) is not drawn on a window this long.',
    )
  })
})
