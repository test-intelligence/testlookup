/**
 * TRND-04 — timeline notes on the time-series chart, both renderers.
 *
 * Real Recharts (only the container is given a size: jsdom lays nothing out):
 * a range is a band and one day a line, each a `[data-chart-note]` group with
 * its short text as a React text node. Hostile text (`<img onerror>`,
 * `__proto__`) is text, never an element. The ECharts option built from the
 * SAME model carries `markArea` / `markLine` entries with `canvasSafeText`
 * names and no `formatter` key.
 */
import { render } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TimeSeriesChart, { dayTipContent } from './TimeSeriesChart'
import { buildTimeSeriesOption } from './engines/echarts/timeSeriesOption'
import { canvasSafeText } from './engines/echarts/canvasText'
import { readChartTokens } from './tokens'
import { buildTimeSeriesModel, noteShortLabel, timeSeriesFromTrends, type NoteInput } from './timeSeriesModel'

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

const DAYS = ['2026-09-10', '2026-09-11', '2026-09-12', '2026-09-13', '2026-09-14', '2026-09-15']
const points = timeSeriesFromTrends(
  DAYS.map((date, i) => ({ date, passed: 8 + (i % 2), failed: 2 - (i % 2), skipped: 0, broken: 0, total: 10, pass_rate: 80 + (i % 2) * 10 })),
)
const HOSTILE = '<img src=x onerror=alert(1)>'
const NOTES: NoteInput[] = [
  { id: 'band-1', from: '2026-09-11', to: '2026-09-13', label: 'release freeze', author: 'Ada' },
  { id: 'line-1', from: '2026-09-14', to: '2026-09-14', label: HOSTILE, author: 'Mallory' },
  { id: '__proto__', from: '2026-09-15', to: '2026-09-15', label: '__proto__', author: null },
]
const model = buildTimeSeriesModel({ points, notes: NOTES })

describe('notes on the SVG (Recharts) renderer', () => {
  it('draws a band and lines, each a [data-chart-note] group labelled with its short text', () => {
    const { container } = render(<TimeSeriesChart model={model} title="Pass rate trend" animate={false} />)
    const band = container.querySelector('[data-chart-note="band-1"]')
    expect(band).not.toBeNull()
    expect(band).toHaveAttribute('data-chart-note-kind', 'band')
    expect(band?.querySelector('rect')).not.toBeNull()
    expect(band?.querySelector('text')?.textContent).toBe('release freeze')
    const line = container.querySelector('[data-chart-note="line-1"]')
    expect(line).toHaveAttribute('data-chart-note-kind', 'line')
    expect(line?.querySelector('line')).not.toBeNull()
    // Hover text: the full note, its author and its dates.
    expect(band?.querySelector('title')?.textContent).toBe('release freeze — Ada, Sep 11 – Sep 13')
  })

  it('renders hostile note text as TEXT: no element is made from it', () => {
    const { container } = render(<TimeSeriesChart model={model} title="Pass rate trend" animate={false} />)
    expect(container.querySelector('img')).toBeNull()
    const line = container.querySelector('[data-chart-note="line-1"]')
    expect(line?.querySelector('text')?.textContent).toBe(noteShortLabel(HOSTILE))
    expect(line?.querySelector('title')?.textContent).toContain(HOSTILE)
    const proto = container.querySelector('[data-chart-note="__proto__"]')
    expect(proto?.querySelector('text')?.textContent).toBe('__proto__')
    expect(Object.prototype).not.toHaveProperty('onerror')
  })

  it('a model without notes draws no note group', () => {
    const { container } = render(<TimeSeriesChart model={buildTimeSeriesModel({ points })} title="Pass rate trend" animate={false} />)
    expect(container.querySelector('[data-chart-note]')).toBeNull()
  })

  it('the day tooltip lists every note covering the day: "{text} — {author}, {dates}"', () => {
    const index = DAYS.indexOf('2026-09-12')
    const content = dayTipContent({ model, index })
    const rows = content.rows.filter((row) => (row.key ?? '').startsWith('timeline-note-'))
    expect(rows.map((row) => row.value)).toEqual(['release freeze — Ada, Sep 11 – Sep 13'])
    expect(dayTipContent({ model, index: 0 }).rows.some((row) => (row.key ?? '').startsWith('timeline-note-'))).toBe(false)
  })
})

describe('notes on the canvas (ECharts) option', () => {
  const tokens = readChartTokens()
  const option = buildTimeSeriesOption({ model, tokens, description: 'd' }) as unknown as {
    series: { id: string; markLine?: { data: Record<string, unknown>[] }; markArea?: { data: Record<string, unknown>[][]; itemStyle: { opacity: number } } }[]
  }
  const rate = option.series.find((s) => s.id === 'pass_rate')

  it('a band is a markArea pair, a day a markLine entry, every label canvas-safe', () => {
    expect(rate?.markArea?.data).toEqual([[{ xAxis: canvasSafeText('2026-09-11'), name: canvasSafeText('release freeze') }, { xAxis: canvasSafeText('2026-09-13') }]])
    expect(rate?.markArea?.itemStyle.opacity).toBe(0.12)
    const lines = rate?.markLine?.data ?? []
    expect(lines.map((entry) => entry.name)).toEqual([canvasSafeText(noteShortLabel(HOSTILE)), canvasSafeText('__proto__')])
    expect(lines.map((entry) => entry.xAxis)).toEqual([canvasSafeText('2026-09-14'), canvasSafeText('2026-09-15')])
  })

  it('carries no formatter on any mark (the CSP / XSS rule: `assertSafeChartOption`)', () => {
    const json = JSON.stringify({ markLine: rate?.markLine, markArea: rate?.markArea })
    expect(json).not.toMatch(/formatter/)
    for (const entry of [...(rate?.markLine?.data ?? []), ...(rate?.markArea?.data.flat() ?? [])]) {
      expect(Object.keys(entry)).not.toContain('formatter')
      expect(typeof entry.label === 'object' ? Object.keys(entry.label as object) : []).not.toContain('formatter')
    }
  })

  it('no notes: no markArea, and no markLine without a release either (the option is what it was)', () => {
    const plain = buildTimeSeriesOption({ model: buildTimeSeriesModel({ points }), tokens, description: 'd' }) as unknown as { series: { id: string; markLine?: unknown; markArea?: unknown }[] }
    const plainRate = plain.series.find((s) => s.id === 'pass_rate')
    expect(plainRate?.markLine).toBeUndefined()
    expect(plainRate?.markArea).toBeUndefined()
  })
})
