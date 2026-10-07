import { render, screen } from '@testing-library/react'
import { BarChart3 } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import MetricCard from './MetricCard'

describe('MetricCard', () => {
  it('renders title and metric value', () => {
    render(<MetricCard title="Pass Rate" metric={{ value: '98.2%' }} icon={<BarChart3 />} />)

    expect(screen.getByText('Pass Rate')).toBeInTheDocument()
    expect(screen.getByText('98.2%')).toBeInTheDocument()
  })

  it('shows placeholder when metric is missing', () => {
    render(<MetricCard title="Defects" icon={<BarChart3 />} />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders loading skeleton and hides trend while loading', () => {
    render(
      <MetricCard
        title="Stability"
        metric={{ value: 100, trend: 12, trend_direction: 'up' }}
        icon={<BarChart3 />}
        loading
      />,
    )

    expect(screen.queryByText('100')).not.toBeInTheDocument()
    expect(screen.queryByText('12% vs prev period')).not.toBeInTheDocument()
  })

  it('renders absolute trend value regardless of sign', () => {
    render(
      <MetricCard
        title="Flaky"
        metric={{ value: 7, trend: -6, trend_direction: 'down' }}
        icon={<BarChart3 />}
      />,
    )

    expect(screen.getByText('6% vs prev period')).toBeInTheDocument()
  })

  // Narrow the trend row's parent without a non-null assertion (lint ratchet).
  function trendRowClass(text: string): string {
    const parent = screen.getByText(text).parentElement
    if (parent === null) throw new Error(`trend row for "${text}" has no parent element`)
    return parent.className
  }

  it('colors a rising trend green by default (up is good)', () => {
    render(
      <MetricCard
        title="Pass Rate"
        metric={{ value: '98%', trend: 3, trend_direction: 'up' }}
        icon={<BarChart3 />}
      />,
    )

    expect(trendRowClass('3% vs prev period')).toContain('--status-passed')
  })

  it('colors a falling trend green when positiveDirection is down (Failed/Flaky/Duration)', () => {
    // Audit 1.5: "Failed tests trending down" used to render red — the trend
    // color keyed on direction alone, not on whether the direction was good.
    render(
      <MetricCard
        title="Failed"
        metric={{ value: 4, trend: -20, trend_direction: 'down' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )

    expect(trendRowClass('20% vs prev period')).toContain('--status-passed')
  })

  it('colors a rising trend red when positiveDirection is down', () => {
    render(
      <MetricCard
        title="Failed"
        metric={{ value: 9, trend: 25, trend_direction: 'up' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )

    expect(trendRowClass('25% vs prev period')).toContain('--status-failed')
  })

  // Fix round B (a11y M1): direction and good/bad are WORDS, not only the
  // icon's shape and colour (WCAG 1.4.1 use of colour, 1.1.1 non-text).
  it('states the direction and whether it is better or worse in text; the icon is decoration', () => {
    const { container } = render(
      <MetricCard
        title="Failed"
        metric={{ value: 9, trend: 25, trend_direction: 'up' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('Up25% vs prev period(worse)')
    const svg = row.querySelector('svg') as SVGElement
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg.getAttribute('class')).toContain('shrink-0')
  })

  it('takes the caller change text, e.g. percentage points', () => {
    const { container } = render(
      <MetricCard
        title="Pass rate"
        metric={{ value: '91.2%', trend: 2.9, trend_direction: 'down', trend_text: '2.9 pp vs previous period' }}
        icon={<BarChart3 />}
      />,
    )
    expect(container.querySelector('[data-metric-trend]')?.textContent).toBe('Down2.9 pp vs previous period(worse)')
  })

  it('"none": a reason with no number, no direction word, no judgement', () => {
    const { container } = render(
      <MetricCard
        title="Broken"
        metric={{ value: 2, trend: null, trend_direction: 'none', trend_text: 'New: 0 in the previous period' }}
        icon={<BarChart3 />}
      />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('New: 0 in the previous period')
    expect(row.querySelector('svg')).toBeNull()
  })

  it('flat is "No change", never judged', () => {
    const { container } = render(
      <MetricCard title="Runs" metric={{ value: 5, trend: 0, trend_direction: 'flat' }} icon={<BarChart3 />} />,
    )
    expect(container.querySelector('[data-trend-direction]')?.textContent).toBe('No change')
    expect(container.querySelector('[data-trend-judgement]')).toBeNull()
  })

  it('does not announce itself as a live region (audit 1.5: no per-card aria-live)', () => {
    // A dashboard renders 6-8 cards polling via SWR; per-card role="status"
    // re-announced every value to screen readers on every refresh.
    const { container } = render(
      <MetricCard title="Pass Rate" metric={{ value: '98.2%' }} icon={<BarChart3 />} />,
    )

    expect(container.querySelector('[aria-live]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  it('compact: a narrow card truncates the change text, never the judgement', () => {
    // The P0 baseline showed "Up 8% vs previous period (bet…": one truncating
    // span cut the word that says whether the change is good.
    const { container } = render(
      <MetricCard
        compact
        title="Runs"
        icon={null}
        metric={{ value: 412, trend_direction: 'up', trend_text: '8% vs previous period' }}
      />,
    )

    const judgement = container.querySelector('[data-trend-judgement]')
    expect(judgement).toHaveTextContent('(better)')
    expect(judgement).toHaveClass('shrink-0')
    expect(judgement).not.toHaveClass('truncate')
    const change = judgement?.previousElementSibling
    expect(change).toHaveTextContent('Up 8% vs previous period')
    expect(change).toHaveClass('truncate')
    expect(container.querySelector('[data-metric-trend]')).toHaveAttribute('title', 'Up 8% vs previous period (better)')
  })

  it('compact: an unjudged change has no judgement span', () => {
    const { container } = render(
      <MetricCard compact title="Duration" icon={null} metric={{ value: '4m', trend_direction: 'none', trend_text: 'not measured last period' }} />,
    )

    expect(container.querySelector('[data-trend-judgement]')).toBeNull()
    expect(container.querySelector('[data-metric-trend]')).toHaveAttribute('title', 'not measured last period')
  })

  it('compact: the label is one truncated line whose hover text is the hint, or the label itself', () => {
    const { rerender } = render(<MetricCard compact title="Time to resolve" hint="Mean time to resolve, over the defects resolved" icon={null} metric={{ value: '2.3d' }} />)
    const label = screen.getByText('Time to resolve')
    expect(label).toHaveClass('truncate')
    expect(label).toHaveAttribute('title', 'Mean time to resolve, over the defects resolved')
    rerender(<MetricCard compact title="Escape rate" icon={null} metric={{ value: '56%' }} />)
    expect(screen.getByText('Escape rate')).toHaveAttribute('title', 'Escape rate')
  })

  it('compact: the sparkline takes what the value leaves, capped to it, and drops below the value rather than overlap it', () => {
    const { container } = render(
      <MetricCard compact title="Total executions" icon={null} metric={{ value: '4437' }} sparkline={<svg data-testid="spark" />} />,
    )
    const aside = container.querySelector('[data-metric-aside]') as HTMLElement
    expect(aside).toContainElement(screen.getByTestId('spark'))
    // Grows into the room left (flex-1), never under 4rem (min-w-16), and caps its child to that room.
    expect(aside.className.split(/\s+/)).toEqual(expect.arrayContaining(['flex-1', 'min-w-16', '[&>*]:max-w-full']))
    // The row wraps: under 4rem left, the aside takes a line of its own.
    expect((aside.parentElement as HTMLElement).className.split(/\s+/)).toContain('flex-wrap')
  })
})
