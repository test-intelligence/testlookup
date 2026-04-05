import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import PassRateGauge from './PassRateGauge'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RadialBarChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RadialBar: () => <div data-testid="radial-bar" />,
}))

describe('PassRateGauge', () => {
  it('renders formatted pass rate label', () => {
    render(<PassRateGauge value={94.456} />)
    expect(screen.getByText('94.5%')).toBeInTheDocument()
    expect(screen.getByText('Pass Rate')).toBeInTheDocument()
  })

  it('respects custom size', () => {
    const { container } = render(<PassRateGauge value={80} size={160} />)
    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper).toHaveStyle({ width: '160px', height: '160px' })
  })
})
