import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import { CHART_MESSAGES } from '@/components/charts/chartMessages'
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import { GALLERY_CASES } from '@/pages/dev/reportContextFixtures'
import FilteredSummary from './FilteredSummary'
import { compactFilteredSummary, summarizeFilteredDataset } from './summarizeFilteredDataset'

const fixture = (id: string): EnvelopeMeta => {
  const found = GALLERY_CASES.find((c) => c.id === id)
  if (!found?.meta) throw new Error(`no fixture ${id}`)
  return found.meta
}

describe('summarizeFilteredDataset', () => {
  it('filtered: the story sentence, with locale thousands', () => {
    expect(summarizeFilteredDataset(fixture('filtered'))).toEqual({
      kind: 'text',
      filtered: true,
      text: 'Showing 18 of 143 runs · 412 of 3,960 executions · 2 releases · 2 suites · last 14 days',
    })
  })

  it('singular release and suite', () => {
    const base = fixture('filtered')
    const meta = {
      ...base,
      scope: { ...base.scope, releases: base.scope.releases.slice(0, 1), suites: ['payments'], window: { ...base.scope.window, days: 30 } },
    }
    expect(summarizeFilteredDataset(meta)).toMatchObject({
      text: 'Showing 18 of 143 runs · 412 of 3,960 executions · 1 release · 1 suite · last 30 days',
    })
  })

  it('unfiltered: "Showing all …"', () => {
    expect(summarizeFilteredDataset(fixture('unfiltered'))).toEqual({
      kind: 'text',
      filtered: false,
      text: 'Showing all 143 runs · 3,960 executions · last 30 days',
    })
  })

  it('matched 0 hands over to the filtered-empty state', () => {
    expect(summarizeFilteredDataset(fixture('unmeasured'))).toEqual({ kind: 'empty', filtered: true })
  })

  it('totals missing → no line at all, never a guess', () => {
    expect(summarizeFilteredDataset(fixture('totals-missing'))).toBeNull()
    expect(summarizeFilteredDataset(null)).toBeNull()
    const base = fixture('filtered')
    // A partial totals object is not totals.
    const partial = { ...base, totals: { matched_runs: 18, total_runs: 143 } } as unknown as EnvelopeMeta
    expect(summarizeFilteredDataset(partial)).toBeNull()
    expect(compactFilteredSummary(fixture('totals-missing'))).toBeNull()
  })

  it('huge counts stay exact in the sentence', () => {
    expect(summarizeFilteredDataset(fixture('huge-counts'))).toMatchObject({
      text: 'Showing all 1,234,567 runs · 987,654,321 executions · last 30 days',
    })
  })

  it('compact form for a chart footer', () => {
    expect(compactFilteredSummary(fixture('filtered'))).toBe('18/143 runs · 412/3,960 executions')
  })
})

describe('FilteredSummary', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('renders the line, and nothing when totals are missing', () => {
    const { rerender, container } = render(<FilteredSummary meta={fixture('unfiltered')} />)
    expect(screen.getByText('Showing all 143 runs · 3,960 executions · last 30 days')).toBeInTheDocument()
    rerender(<FilteredSummary meta={fixture('totals-missing')} />)
    expect(container.querySelector('[data-filtered-summary]')).toBeNull()
  })

  it('matched 0 shows the filtered-empty copy with Clear filters', () => {
    const clear = vi.fn()
    render(<FilteredSummary meta={fixture('unmeasured')} onClearFilters={clear} />)
    expect(screen.getByText(CHART_MESSAGES.filteredEmpty)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.clearFilters }))
    expect(clear).toHaveBeenCalledOnce()
  })

  it('announces a change ONCE through the page announcer, never the first render, never a second region', () => {
    const { rerender } = render(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={fixture('unfiltered')} />
      </ChartAnnouncerProvider>,
    )
    const polite = () => document.querySelector('[data-chart-announcer="polite"]')?.textContent
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    expect(polite()).toBe('')
    // No second live region of its own under a provider.
    expect(document.querySelector('[data-filtered-summary-live]')).toBeNull()

    rerender(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={fixture('filtered')} />
      </ChartAnnouncerProvider>,
    )
    // The same text re-rendered is not a change.
    rerender(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={{ ...fixture('filtered') }} />
      </ChartAnnouncerProvider>,
    )
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS))
    // Announced as the line itself -- it is not a chart ("Filtered data chart: ..." before).
    expect(polite()).toBe(
      'Showing 18 of 143 runs · 412 of 3,960 executions · 2 releases · 2 suites · last 14 days',
    )
  })

  // Fix round B (a11y M6): the chrome mounts BEFORE its response arrives
  // (meta null, no line), so the first line to appear is the page's initial
  // load. It used to be announced as a "change" on every page load.
  it('the first line after a null start (page load) is NOT announced; the next change is', () => {
    const polite = () => document.querySelector('[data-chart-announcer="polite"]')?.textContent
    const { rerender } = render(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={null} />
      </ChartAnnouncerProvider>,
    )
    rerender(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={fixture('unfiltered')} />
      </ChartAnnouncerProvider>,
    )
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    expect(polite()).toBe('')

    rerender(
      <ChartAnnouncerProvider>
        <FilteredSummary meta={fixture('filtered')} />
      </ChartAnnouncerProvider>,
    )
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS))
    expect(polite()).toMatch(/Showing 18 of 143 runs/)
  })

  it('without a provider: the first line after a null start is silent too', () => {
    const { rerender } = render(<FilteredSummary meta={null} />)
    rerender(<FilteredSummary meta={fixture('unfiltered')} />)
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS * 2))
    expect(document.querySelector('[data-filtered-summary-live]')?.textContent).toBe('')
  })

  it('a line that goes away (refetch) and comes back different is still a change', () => {
    const { rerender } = render(<FilteredSummary meta={fixture('unfiltered')} />)
    rerender(<FilteredSummary meta={fixture('totals-missing')} />)
    rerender(<FilteredSummary meta={fixture('filtered')} />)
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS))
    expect(document.querySelector('[data-filtered-summary-live]')?.textContent).toMatch(/^Showing 18 of 143 runs/)
  })

  it('without a provider, one debounced polite region of its own announces the change', () => {
    const { rerender } = render(<FilteredSummary meta={fixture('unfiltered')} />)
    const live = () => document.querySelector('[data-filtered-summary-live]')
    expect(live()).toHaveAttribute('role', 'status')
    expect(live()?.textContent).toBe('')
    rerender(<FilteredSummary meta={fixture('filtered')} />)
    expect(live()?.textContent).toBe('')
    act(() => vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS))
    expect(live()?.textContent).toMatch(/^Showing 18 of 143 runs/)
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1)
  })
})
