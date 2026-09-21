import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import DefectDonut from './DefectDonut'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  // Echoes `isAnimationActive` so the `animate` prop is observable.
  Pie: ({ children, isAnimationActive }: { children: ReactNode; isAnimationActive?: boolean }) => (
    <div data-testid="pie" data-animate={String(isAnimationActive)}>{children}</div>
  ),
  Cell: () => <div data-testid="pie-cell" />,
  Tooltip: () => <div data-testid="tooltip" />,
  Legend: () => <div data-testid="legend" />,
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
})
