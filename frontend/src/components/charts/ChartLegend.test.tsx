/**
 * HYG-08 — `ChartLegend`'s two swatch footprints. A fill swatch is as wide as
 * the line swatch (22 px; the 8 px status decals show 2¾ times across) and as
 * tall as before (14 px), so the legend row keeps its 16 px line box; the line
 * swatch is unchanged, byte for byte. The `<ul>` / `<li>` classes (the gaps) are
 * pinned too: the wider swatch is answered by its footprint, not by new gaps
 * (D-W25c-1, D-W25c-2).
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChartLegend, LEGEND_FILL_SWATCH, LEGEND_LINE_SWATCH, type LegendEntry } from './patterns'
import { CHART_VARS } from './tokens'

const FILL: LegendEntry = { key: 'passed', label: 'Passed', status: 'passed', fill: 'url(#cp1-chart-pattern-passed)' }
const LINE: LegendEntry = { key: 'rate', label: 'Pass rate %', stroke: 'var(--chart-line)', dash: '4 3', dot: true }

function renderLegend(entries: readonly LegendEntry[]) {
  const { container } = render(<ChartLegend entries={entries} />)
  const list = container.querySelector('ul[data-chart-legend]')
  if (!list) throw new Error('no legend list')
  return list
}

describe('ChartLegend — the fill swatch (HYG-08)', () => {
  it('is 22 × 14, the line swatch’s width at the old height', () => {
    expect(LEGEND_FILL_SWATCH).toEqual({ width: 22, height: 14 })
    const entry = renderLegend([FILL]).querySelector('li[data-legend-status="passed"]')
    const svg = entry?.querySelector('svg')
    expect(svg?.getAttribute('width')).toBe('22')
    expect(svg?.getAttribute('height')).toBe('14')
    expect(svg?.getAttribute('aria-hidden')).toBe('true')
  })

  it('draws a 21 × 13 rect at (0.5, 0.5), rounded 2, in the entry’s fill with the border stroke', () => {
    const rect = renderLegend([FILL]).querySelector('rect[data-legend-swatch]')
    expect(rect).not.toBeNull()
    expect(rect?.getAttribute('x')).toBe('0.5')
    expect(rect?.getAttribute('y')).toBe('0.5')
    expect(rect?.getAttribute('width')).toBe('21')
    expect(rect?.getAttribute('height')).toBe('13')
    expect(rect?.getAttribute('rx')).toBe('2')
    expect(rect?.getAttribute('fill')).toBe(FILL.fill)
    expect(rect?.getAttribute('stroke')).toBe(CHART_VARS.border)
  })
})

describe('ChartLegend — the line swatch (unchanged)', () => {
  it('is 24 × 10 with the line from x 1 to 23 at y 5, its dash kept, the dot at the centre', () => {
    expect(LEGEND_LINE_SWATCH).toEqual({ width: 24, height: 10 })
    const list = renderLegend([LINE])
    const svg = list.querySelector('li svg')
    expect(svg?.getAttribute('width')).toBe('24')
    expect(svg?.getAttribute('height')).toBe('10')
    const line = list.querySelector('line[data-legend-swatch]')
    expect(line?.getAttribute('x1')).toBe('1')
    expect(line?.getAttribute('x2')).toBe('23')
    expect(line?.getAttribute('y1')).toBe('5')
    expect(line?.getAttribute('y2')).toBe('5')
    expect(line?.getAttribute('stroke')).toBe(LINE.stroke)
    expect(line?.getAttribute('stroke-width')).toBe('2')
    expect(line?.getAttribute('stroke-dasharray')).toBe('4 3')
    const dot = list.querySelector('circle[data-legend-dot]')
    expect(dot?.getAttribute('cx')).toBe('12')
    expect(dot?.getAttribute('cy')).toBe('5')
    expect(dot?.getAttribute('r')).toBe('3')
    expect(dot?.getAttribute('fill')).toBe(LINE.stroke)
  })

  it('draws no dot when the entry asks for none', () => {
    const list = renderLegend([{ ...LINE, dot: undefined }])
    expect(list.querySelector('circle[data-legend-dot]')).toBeNull()
  })
})

describe('ChartLegend — the row (gaps unchanged, D-W25c-2)', () => {
  it('keeps the list and entry classes, and puts each label after its swatch', () => {
    const list = renderLegend([FILL, LINE])
    expect(list.getAttribute('class')).toBe(
      'flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-3 text-xs text-[var(--color-text-secondary)]',
    )
    const items = Array.from(list.querySelectorAll('li'))
    expect(items).toHaveLength(2)
    for (const item of items) {
      expect(item.getAttribute('class')).toBe('flex items-center gap-1.5')
      expect(item.children[0]?.tagName.toLowerCase()).toBe('svg')
      expect(item.children[1]?.tagName.toLowerCase()).toBe('span')
    }
    expect(items.map((item) => item.textContent)).toEqual(['Passed', 'Pass rate %'])
  })
})
