import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import PassRateGauge from './PassRateGauge'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RadialBarChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  // Echoes `isAnimationActive` so the `animate` prop is observable.
  RadialBar: ({ isAnimationActive }: { isAnimationActive?: boolean }) => (
    <div data-testid="radial-bar" data-animate={String(isAnimationActive)} />
  ),
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

  it('leaves the Recharts animation default alone unless told otherwise', () => {
    render(<PassRateGauge value={80} />)
    expect(screen.getByTestId('radial-bar')).toHaveAttribute('data-animate', 'undefined')
  })

  it('forwards animate={false} to the radial bar', () => {
    render(<PassRateGauge value={80} animate={false} />)
    expect(screen.getByTestId('radial-bar')).toHaveAttribute('data-animate', 'false')
  })
})
