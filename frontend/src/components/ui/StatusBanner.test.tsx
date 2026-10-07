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

  it('takes its own pill words when the state word would misname the verdict; the hue stays that of the state', () => {
    const { container } = render(
      <MemoryRouter>
        <StatusBanner state="fail" pillLabel="AT RISK" title="Library health 41/100" facts={[{ label: 'Cases', value: 3 }]} />
      </MemoryRouter>,
    )
    const pill = container.querySelector('[data-banner-pill]') as HTMLElement
    expect(pill).toHaveTextContent(/^AT RISK$/)
    expect(container.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'fail')
    expect(pill.style.color).toBe('var(--status-failed)')
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
    // Fits the width (one row of five on a desktop page, fewer columns when
    // presentation-sized tiles cannot fit), never a fixed count that overflows.
    expect(strip.style.gridTemplateColumns).toBe('repeat(auto-fit, minmax(10rem, 1fr))')
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
    // The presentation-aware stat token (24 px at the desk, 40 px in the room),
    // never a fixed `text-2xl` that presentation mode cannot raise.
    expect(screen.getByText('91.2%').className).toContain('text-[length:var(--text-stat-lg)]')
    expect(screen.getByText('91.2%').className).not.toContain('text-2xl')
    // Two spans, so a narrow tile truncates the change text and never "(worse)".
    expect(screen.getByText('Down 2.1 pp vs previous period')).toBeInTheDocument()
    expect(screen.getByText('(worse)')).toBeInTheDocument()
    expect(screen.getByTitle('Down 2.1 pp vs previous period (worse)')).toHaveAttribute('data-metric-trend', 'down')
  })
})
