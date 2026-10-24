/**
 * W11 REL-03: the four release cards with their measured trend. A sparkline
 * only for a series with two measured points or more (an all-`null` series
 * draws nothing: absence is not a flat line); Ready to ship and Released ·
 * 30d carry the trend's captions; a failed read keeps the cards and says so;
 * while loading the cards show without sparklines.
 */
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Release, ReleasePortfolioTrend } from '@/types/releases'
import { useReleasePortfolioTrend } from '@/hooks/useReleasePortfolioTrend'
import PortfolioKpis from './PortfolioKpis'
import { deriveRelease } from './mapping'

vi.mock('@/hooks/useReleasePortfolioTrend', () => ({ useReleasePortfolioTrend: vi.fn() }))

const hook = vi.mocked(useReleasePortfolioTrend)

function rel(id: string, status: string): Release {
  return {
    id, project_id: 'p', name: id, version: null, description: null, status, planned_date: null,
    released_at: status === 'released' ? new Date(Date.now() - 3 * 86_400_000).toISOString() : null,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', phases: [],
  }
}

const RELEASES = [rel('a', 'in_progress'), rel('b', 'in_progress'), rel('c', 'released')].map(deriveRelease)

const TREND: ReleasePortfolioTrend = {
  weeks: 12,
  points_at: ['2026-10-05T00:00:00Z', '2026-10-12T00:00:00Z', '2026-10-19T00:00:00Z', '2026-10-23T12:00:00Z'],
  in_progress: [1, 2, 2, 2],
  ready_to_ship: [0, 0, 1, 0],
  blocked: [null, null, null, 0],          // one measured point: no line
  released_30d: [null, null, null, null],  // nothing measured: no line
  evaluated_in_week: [0, 1, 2, 0],
  go_in_week: [0, 0, 1, 0],
  totals: { evaluated: 4, go: 3, conditional_go: 1, no_go: 0, not_evaluated: 0, released: 2, released_with_pack: 1 },
  approximated: ['in_progress', 'ready_to_ship', 'blocked'],
}

function card(label: string): HTMLElement {
  return screen.getByText(label, { selector: 'p.eyebrow' }).closest('[data-metric-card]') as HTMLElement
}

beforeEach(() => hook.mockReset())

describe('PortfolioKpis', () => {
  it('draws a sparkline only where two points or more were measured, with accessible names and point titles', () => {
    hook.mockReturnValue({ data: TREND, error: undefined } as never)
    render(<PortfolioKpis releases={RELEASES} />)
    const lines = screen.getAllByTestId('sparkline')
    expect(lines.map(l => l.getAttribute('aria-label')?.split(':')[0])).toEqual(['In progress, 12 weeks', 'Ready to ship, 12 weeks'])
    expect(within(card('Blocked')).queryByTestId('sparkline')).toBeNull()
    expect(within(card('Released · 30d')).queryByTestId('sparkline')).toBeNull()
    // No empty aside either: the value keeps the tile's width.
    expect(card('Blocked').querySelector('[data-metric-aside]')).toBeNull()
    expect(card('Released · 30d').querySelector('[data-metric-aside]')).toBeNull()
    const titles = Array.from(card('In progress').querySelectorAll('circle title')).map(t => t.textContent)
    expect(titles[0]).toBe("Week to 5 Oct: 1 in progress (approximation: today's status)")
    expect(titles[titles.length - 1]).toBe('Now: 2 in progress')
  })

  it('Ready to ship and Released · 30d carry the trend\'s captions; In progress and Blocked keep theirs', () => {
    hook.mockReturnValue({ data: TREND, error: undefined } as never)
    render(<PortfolioKpis releases={RELEASES} />)
    expect(within(card('Ready to ship')).getByText('GO on 3 of 4 evaluated (12 w)')).toBeInTheDocument()
    expect(within(card('Released · 30d')).getByText('1 of 2 released with a pack (12 w)')).toBeInTheDocument()
    expect(within(card('Blocked')).getByText('no blockers')).toBeInTheDocument()
    // The card's own number stands (the list's count, not the trend's).
    expect(within(card('In progress')).getByText('2')).toBeInTheDocument()
  })

  it('with nothing evaluated or released the captions say so', () => {
    hook.mockReturnValue({
      data: { ...TREND, totals: { ...TREND.totals, evaluated: 0, go: 0, released: 0, released_with_pack: 0 } },
      error: undefined,
    } as never)
    render(<PortfolioKpis releases={RELEASES} />)
    expect(within(card('Ready to ship')).getByText('No releases evaluated in 12 weeks')).toBeInTheDocument()
    expect(within(card('Released · 30d')).getByText('No releases released in 12 weeks')).toBeInTheDocument()
  })

  it('a failed read keeps the cards and says so in one line', () => {
    hook.mockReturnValue({ data: undefined, error: new Error('503') } as never)
    render(<PortfolioKpis releases={RELEASES} />)
    expect(screen.getByText('Could not load the 12-week trend.')).toBeInTheDocument()
    expect(screen.getAllByText(/In progress|Ready to ship|Blocked|Released · 30d/, { selector: 'p.eyebrow' })).toHaveLength(4)
    expect(screen.queryByTestId('sparkline')).toBeNull()
  })

  it('while loading: the cards, no sparkline, no error', () => {
    hook.mockReturnValue({ data: undefined, error: undefined } as never)
    render(<PortfolioKpis releases={RELEASES} />)
    expect(screen.queryByTestId('sparkline')).toBeNull()
    expect(screen.queryByText('Could not load the 12-week trend.')).toBeNull()
    expect(screen.getAllByText(/In progress|Ready to ship|Blocked|Released · 30d/, { selector: 'p.eyebrow' })).toHaveLength(4)
  })
})
