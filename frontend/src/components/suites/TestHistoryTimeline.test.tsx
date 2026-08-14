/**
 * The timeline must show evidence a table of ticks erases.
 *
 * Roadmap Phase 1 (P1-A). Each test pins a property whose regression would
 * quietly remove information a user needs to judge stability.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import TestHistoryTimeline, { MAX_TIMELINE_CELLS } from './TestHistoryTimeline'
import type { CanonicalRunHistoryItem } from '@/types/suites'

function item(overrides: Partial<CanonicalRunHistoryItem> = {}): CanonicalRunHistoryItem {
  return {
    test_case_id: Math.random().toString(36).slice(2),
    test_run_id: 'run-1',
    status: 'PASSED',
    duration_ms: 1200,
    suite_name: 'checkout',
    created_at: '2026-08-01T10:00:00Z',
    build_number: '101',
    branch: 'main',
    environment: 'staging',
    environment_source: 'explicit',
    retry_count: 0,
    is_flaky_run: false,
    ...overrides,
  }
}

describe('TestHistoryTimeline', () => {
  it('renders one cell per run', () => {
    render(<TestHistoryTimeline items={[item(), item(), item()]} />)
    expect(screen.getByRole('list', { name: /outcome per run/i })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('reads oldest to newest, reversing the API newest-first order', () => {
    // A flip pattern is only legible if time runs one way. The API returns
    // newest-first for every other consumer, so the reversal lives here.
    render(
      <TestHistoryTimeline
        items={[
          item({ status: 'FAILED', build_number: 'newest' }),
          item({ status: 'PASSED', build_number: 'oldest' }),
        ]}
      />,
    )
    const cells = screen.getAllByRole('listitem')
    expect(cells[0].textContent ?? '').toBe('')
    expect(cells[0].querySelector('span')?.getAttribute('title')).toContain('oldest')
    expect(cells[1].querySelector('span')?.getAttribute('title')).toContain('newest')
  })

  it('surfaces a retry as "passed on attempt N" rather than a bare tick', () => {
    // A retry is evidence, not a way to make the build green.
    render(<TestHistoryTimeline items={[item({ retry_count: 2 })]} />)
    const cell = screen.getAllByRole('listitem')[0].querySelector('span')
    expect(cell?.getAttribute('title')).toContain('passed on attempt 3')
  })

  it('counts a run flagged flaky without a retry count as retried', () => {
    render(<TestHistoryTimeline items={[item({ retry_count: null, is_flaky_run: true })]} />)
    expect(screen.getByText('Runs with retries').parentElement?.textContent).toContain('1')
  })

  it('shows an unrecorded environment as unknown, never a default group', () => {
    // Folding nulls into one bucket would make a corpus that never recorded
    // environments look perfectly environment-consistent.
    render(
      <TestHistoryTimeline
        items={[item({ environment: null, environment_source: 'unknown' })]}
      />,
    )
    const cell = screen.getAllByRole('listitem')[0].querySelector('span')
    expect(cell?.getAttribute('title')).toContain('environment unknown')
    expect(screen.getByText('none recorded')).toBeInTheDocument()
  })

  it('marks a derived environment as inferred so it can be weighted down', () => {
    render(
      <TestHistoryTimeline
        items={[item({ environment: 'github_actions:mainline', environment_source: 'derived' })]}
      />,
    )
    const cell = screen.getAllByRole('listitem')[0].querySelector('span')
    expect(cell?.getAttribute('title')).toContain('(inferred)')
  })

  it('counts flips over pass/fail only, not skips', () => {
    // A skip is an absence of evidence, not a change of outcome. Counting it
    // as a transition inflates instability for a test that never ran.
    render(
      <TestHistoryTimeline
        items={[
          item({ status: 'PASSED' }),
          item({ status: 'SKIPPED' }),
          item({ status: 'PASSED' }),
        ]}
      />,
    )
    expect(screen.getByText('Flips').parentElement?.textContent).toContain('0')
  })

  it('counts a genuine pass/fail transition as one flip', () => {
    render(
      <TestHistoryTimeline
        items={[item({ status: 'PASSED' }), item({ status: 'FAILED' })]}
      />,
    )
    expect(screen.getByText('Flips').parentElement?.textContent).toContain('1')
  })

  it('caps the strip but says so, rather than truncating silently', () => {
    // A silently-clipped history reads as "this is everything" — the exact
    // quiet under-reporting this view exists to fix.
    render(<TestHistoryTimeline items={Array.from({ length: 200 }, () => item())} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(MAX_TIMELINE_CELLS)
    expect(screen.getByText(/last 120 of 200 runs/i)).toBeInTheDocument()
  })

  it('keeps the NEWEST runs when the cap bites, not the oldest', () => {
    // A stability judgement is made from the recent past.
    const newest = item({ build_number: 'newest-run' })
    const rest = Array.from({ length: 300 }, () => item({ build_number: 'older' }))
    render(<TestHistoryTimeline items={[newest, ...rest]} />)
    const cells = screen.getAllByRole('listitem')
    const titles = cells.map((c) => c.querySelector('span')?.getAttribute('title') ?? '')
    expect(titles.some((t) => t.includes('newest-run'))).toBe(true)
  })

  it('renders nothing when there is no history rather than an empty chrome', () => {
    const { container } = render(<TestHistoryTimeline items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('never implies flakiness trends to zero', () => {
    // Phase 1 framing guard: insertion rate tracks fix rate, so any
    // "remaining"/"burndown" wording is a promise that cannot be kept.
    const { container } = render(<TestHistoryTimeline items={[item(), item()]} />)
    const text = (container.textContent ?? '').toLowerCase()
    for (const forbidden of ['remaining', 'burndown', 'burn-down', 'debt']) {
      expect(text).not.toContain(forbidden)
    }
  })
})
