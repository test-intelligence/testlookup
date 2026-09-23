/**
 * `ChartTable` (VIZ-105), fix round A (M1): the scroll box a long table sits
 * in must be reachable by keyboard — axe `scrollable-region-focusable` — or a
 * keyboard user can never scroll to the rows past its 24rem cap.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChartSeries } from '@/lib/viz/contracts'
import ChartTable from './ChartTable'

const SERIES: ChartSeries = {
  kind: 'series',
  dimensions: ['day', 'suite'],
  x_type: 'time',
  series: [{ key: 'a', label: 'a', points: Array.from({ length: 40 }, (_, i) => ({ x: `2026-03-${String(i + 1).padStart(2, '0')}`, y: i, n: 1 })) }],
}

describe('ChartTable — the scroll box', () => {
  it('is a focusable region named after the caption, with a visible focus ring', () => {
    render(<ChartTable caption="Pass rate — data table" series={SERIES} autoFocus={false} />)
    const region = screen.getByRole('region', { name: 'Pass rate — data table' })
    expect(region).toHaveAttribute('tabindex', '0')
    expect(region).toHaveAttribute('data-chart-table')
    expect(region.className).toMatch(/max-h-96/)
    expect(region.className).toMatch(/overflow-auto/)
    expect(region.className).toMatch(/focus-visible:ring-2/)
    // The table is inside it, so its rows are what the keyboard scrolls.
    expect(region.querySelector('table')).not.toBeNull()
  })

  it('still lands the reader on the caption when it opens', () => {
    render(<ChartTable caption="Pass rate — data table" series={SERIES} />)
    expect(document.activeElement?.tagName).toBe('CAPTION')
  })
})
