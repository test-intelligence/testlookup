import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import DefectDonut from './DefectDonut'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Pie: ({ children }: { children: ReactNode }) => <div>{children}</div>,
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
})
