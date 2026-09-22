import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import PassRateGauge from './PassRateGauge'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RadialBarChart: ({ children, data }: { children: ReactNode; data: unknown }) => (
    <div data-testid="radial-chart" data-rows={JSON.stringify(data)}>
      {children}
    </div>
  ),
  PolarAngleAxis: ({ domain }: { domain: unknown }) => (
    <div data-testid="angle-axis" data-domain={JSON.stringify(domain)} />
  ),
  // Echoes `isAnimationActive` so the `animate` prop is observable.
  RadialBar: ({ isAnimationActive, background }: { isAnimationActive?: boolean; background?: unknown }) => (
    <div
      data-testid="radial-bar"
      data-animate={String(isAnimationActive)}
      data-background={JSON.stringify(background)}
    />
  ),
}))

const rows = () => JSON.parse(screen.getByTestId('radial-chart').getAttribute('data-rows') ?? '[]')

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

  // Two rows (a 100 "track" + the value) made Recharts draw two concentric
  // rings, so the value arc sat outside the grey track (Linux baseline
  // pass-rate-gauge--lab, 2026-09-22). One row over its own background track,
  // on a fixed 0-100 axis, is a gauge.
  it('draws ONE ring: the value over its own background track, on a 0-100 axis', () => {
    render(<PassRateGauge value={96.4} />)
    expect(rows()).toHaveLength(1)
    expect(rows()[0].value).toBe(96.4)
    expect(JSON.parse(screen.getByTestId('radial-bar').getAttribute('data-background') ?? 'null')).toEqual({
      fill: 'var(--chart-grid)',
    })
    expect(screen.getByTestId('angle-axis')).toHaveAttribute('data-domain', '[0,100]')
  })

  it('clamps the arc to 0-100 but keeps the true value in the label', () => {
    render(<PassRateGauge value={104} />)
    expect(rows()[0].value).toBe(100)
    expect(screen.getByText('104.0%')).toBeInTheDocument()
  })
})
