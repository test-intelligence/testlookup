import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import RecentSearchesCard, { HighlightedQuery, RECENT_EMPTY, RECENT_LOAD_ERROR } from './RecentSearchesCard'
import type { RecentSearches } from '@/types/search'

const now = new Date().toISOString()
const RECENTS: RecentSearches = {
  items: [
    { q: 'checkout timeout', scope: 'tests', mode: 'hybrid', project_id: 'p1', result_count: 12, counts_exact: false, ts: now },
    { q: 'login', scope: 'all', mode: 'keyword', project_id: 'p2', result_count: 1, counts_exact: true, ts: now },
    { q: 'release:"2026.11"', scope: 'all', mode: 'keyword', project_id: null, result_count: 0, counts_exact: true, ts: now },
  ],
  max: 20,
  retention_days: 30,
}

function renderCard(props: Partial<Parameters<typeof RecentSearchesCard>[0]> = {}) {
  const onPick = vi.fn()
  const onClear = vi.fn().mockResolvedValue(undefined)
  render(
    <RecentSearchesCard
      recents={RECENTS}
      loading={false}
      failed={false}
      projectNames={{ p1: 'Payments', p2: 'Checkout' }}
      pinnedProjectId="p1"
      onPick={onPick}
      onClear={onClear}
      {...props}
    />,
  )
  return { onPick, onClear }
}

describe('RecentSearchesCard (W12 SRCH-03)', () => {
  it('the server\'s rows: results, time, mode, scope, and the project when it is not the pinned one', () => {
    const { onPick } = renderCard()
    const rows = Array.from(document.querySelectorAll('[data-recent-search]')) as HTMLElement[]
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('12+ results · 0s ago · hybrid · tests')
    expect(rows[0]).not.toHaveTextContent('Payments')
    expect(rows[1]).toHaveTextContent('1 result · 0s ago · keyword · all · Checkout')
    expect(rows[2]).toHaveTextContent('All Projects')
    fireEvent.click(within(rows[1]).getByRole('button', { name: /login/ }))
    expect(onPick).toHaveBeenCalledWith(RECENTS.items[1])
  })

  it('Clear history asks first; the strip\'s Clear clears', async () => {
    const { onClear } = renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    expect(onClear).not.toHaveBeenCalled()
    expect(screen.getByText("Clear all 3 recent searches? This can't be undone.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(onClear).toHaveBeenCalledTimes(1))
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
  })

  it('empty, loading and refused states', () => {
    const { unmount } = render(
      <RecentSearchesCard recents={{ ...RECENTS, items: [] }} loading={false} failed={false} projectNames={{}} pinnedProjectId={null} onPick={vi.fn()} onClear={vi.fn()} />,
    )
    expect(screen.getByText(RECENT_EMPTY)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear history' })).toBeNull()
    unmount()
    render(<RecentSearchesCard recents={undefined} loading={false} failed projectNames={{}} pinnedProjectId={null} onPick={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByText(RECENT_LOAD_ERROR)).toBeInTheDocument()
  })

  it('each part truncates inside its own width: a token chip keeps its outline (its own max-w-full truncate)', () => {
    const { container } = render(<HighlightedQuery q={'checkout status:failed suite:"API Regression"'} />)
    const root = container.querySelector('[data-highlighted-query]') as HTMLElement
    expect(root.className).toContain('overflow-hidden')
    const codes = Array.from(root.querySelectorAll('code'))
    expect(codes.map((c) => c.textContent)).toEqual(['status:failed', 'suite:"API Regression"'])
    for (const code of codes) {
      expect(code.className).toContain('max-w-full')
      expect(code.className).toContain('truncate')
      expect(code.className).toContain('flex-none')
      // Every part after the first sits in a shrinking flex box of the width left for it.
      const box = code.parentElement as HTMLElement
      expect(box.className).toContain('min-w-0')
      expect(box.className).toContain('flex-1')
      expect(box.className).toContain('overflow-hidden')
    }
    // The spaces between the words and the tokens are kept.
    const first = root.firstElementChild as HTMLElement
    expect(first.textContent).toBe('checkout ')
    expect(first.className).toContain('whitespace-pre')
    expect(root).toHaveTextContent('checkout status:failed suite:"API Regression"')
  })

  it('queries are text with their filter tokens as code, never markup', () => {
    const { container } = render(<HighlightedQuery q={'status:failed suite:"API Regression" <img src=x onerror=alert(1)>'} />)
    expect(Array.from(container.querySelectorAll('code')).map((c) => c.textContent)).toEqual(['status:failed', 'suite:"API Regression"'])
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})
