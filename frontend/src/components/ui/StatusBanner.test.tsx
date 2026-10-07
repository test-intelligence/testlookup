import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import KpiStrip from './KpiStrip'
import MetricCard from './MetricCard'
import StatusBanner from './StatusBanner'

describe('StatusBanner', () => {
  it('states the verdict in words, its facts, and one link', () => {
    render(
      <MemoryRouter>
        <StatusBanner
          state="no_go"
          title="Release 2.4"
          facts={[
            { label: 'pass', value: '83.4%' },
            { label: 'new failures', value: 12 },
          ]}
          action={{ label: 'Open gate', href: '/release-gate' }}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('NO-GO')).toBeInTheDocument()
    expect(screen.getByText('83.4%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open gate/ })).toHaveAttribute('href', '/release-gate')
  })

  it('shows at most four facts', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const view = render(
      <StatusBanner state="ok" facts={[1, 2, 3, 4, 5].map((n) => ({ label: `f${n}`, value: n }))} />,
    )
    expect(view.container.querySelectorAll('[data-banner-fact]')).toHaveLength(4)
    warn.mockRestore()
  })
})

describe('KpiStrip + compact MetricCard', () => {
  it('lays out at most five compact tiles in one row', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const view = render(
      <KpiStrip>
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <MetricCard key={n} compact title={`KPI ${n}`} icon={null} metric={{ value: n }} />
        ))}
      </KpiStrip>,
    )
    const strip = view.container.querySelector('[data-kpi-strip]') as HTMLElement
    expect(strip.querySelectorAll('[data-metric-card="compact"]')).toHaveLength(5)
    expect(strip.style.gridTemplateColumns).toBe('repeat(5, minmax(0, 1fr))')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('a compact card keeps the change words and judgement, on one line', () => {
    render(
      <MetricCard
        compact
        title="Pass rate"
        icon={null}
        metric={{ value: '91.2%', trend_direction: 'down', trend_text: '2.1 pp vs previous period' }}
      />,
    )
    expect(screen.getByText('91.2%').className).toContain('text-2xl')
    // Two spans, so a narrow tile truncates the change text and never "(worse)".
    expect(screen.getByText('Down 2.1 pp vs previous period')).toBeInTheDocument()
    expect(screen.getByText('(worse)')).toBeInTheDocument()
    expect(screen.getByTitle('Down 2.1 pp vs previous period (worse)')).toHaveAttribute('data-metric-trend', 'down')
  })
})
