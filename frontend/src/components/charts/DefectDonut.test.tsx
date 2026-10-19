import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import DefectDonut from './DefectDonut'

vi.mock('recharts', () => ({
  // The recharts hooks Wave 2.4's tooltip content reads (`ChartTooltip`'s `usePlotArea`, the bars' `useXAxisScale`): listed so a mock that ever renders that content does not throw.
  usePlotArea: () => undefined,
  useXAxisScale: () => undefined,
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  // Echoes `isAnimationActive` so the `animate` prop is observable.
  Pie: ({ children, isAnimationActive }: { children: ReactNode; isAnimationActive?: boolean }) => (
    <div data-testid="pie" data-animate={String(isAnimationActive)}>{children}</div>
  ),
  Cell: () => <div data-testid="pie-cell" />,
  Tooltip: () => <div data-testid="tooltip" />,
  // Renders the legend's content, so its words and status keys can be read.
  Legend: ({ content }: { content?: () => ReactNode }) => (
    <div data-testid="legend">{typeof content === 'function' ? content() : null}</div>
  ),
}))

describe('DefectDonut', () => {
  it('shows empty-state text when all buckets are zero', () => {
    render(<DefectDonut data={[0, 0, 0, 0]} />)
    expect(screen.getByText('No defect data')).toBeInTheDocument()
  })

  it('renders chart when at least one bucket has data', () => {
    render(<DefectDonut data={[2, 0, 1, 0]} />)
    expect(screen.queryByText('No defect data')).not.toBeInTheDocument()
    expect(screen.getByTestId('legend')).toBeInTheDocument()
  })

  it('leaves the Recharts animation default alone unless told otherwise', () => {
    render(<DefectDonut data={[2, 0, 1, 0]} />)
    expect(screen.getByTestId('pie')).toHaveAttribute('data-animate', 'undefined')
  })

  it('forwards animate={false} to the pie', () => {
    render(<DefectDonut data={[2, 0, 1, 0]} animate={false} />)
    expect(screen.getByTestId('pie')).toHaveAttribute('data-animate', 'false')
  })

  // W29a UIX-07: the one severity scale, P0–P3; the data stays positional.
  it('names the four slices P0 Critical … P3 Low; P0 / P1 are drawn through the failed / broken status path', () => {
    const { container } = render(<DefectDonut data={[4, 3, 2, 1]} animate={false} />)
    const items = Array.from(container.querySelectorAll('[data-chart-legend] li'))
    expect(items.map((li) => li.querySelector('span')?.textContent?.replace(/ \d.*$/, ''))).toEqual([
      'P0 Critical', 'P1 High', 'P2 Medium', 'P3 Low',
    ])
    expect(items.map((li) => li.getAttribute('data-legend-status'))).toEqual(['failed', 'broken', null, null])
    const decals = Array.from(container.querySelectorAll('pattern')).map((p) => p.getAttribute('data-chart-pattern'))
    expect(new Set(decals).size).toBe(4)
  })
})
