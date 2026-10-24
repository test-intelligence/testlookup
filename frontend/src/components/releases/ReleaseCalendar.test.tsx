/**
 * W11 REL-06: the release calendar renders chips and bars as TEXT, opens a
 * release from either, moves by month, and places everything with static
 * classes (no inline style from data: the production CSP).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Release, ReleasePhase } from '@/types/releases'
import ReleaseCalendar, { CALENDAR_LEGEND } from './ReleaseCalendar'

function phase(id: string, name: string, start: string, end: string, status = 'in_progress'): ReleasePhase {
  return {
    id, release_id: 'rel-11', name, phase_type: 'qa_testing', status, description: null, order_index: 0,
    planned_start: start, planned_end: end, actual_start: null, actual_end: null, exit_criteria: null, notes: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  }
}

const RELEASES: Release[] = [
  {
    id: 'rel-11', project_id: 'p', name: '2026.11', version: null, description: null, status: 'in_progress',
    planned_date: '2026-11-20T00:00:00Z', released_at: null, created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    phases: [phase('ph-soak', 'Payments soak', '2026-11-05T00:00:00Z', '2026-11-11T00:00:00Z')],
  },
  {
    id: 'rel-x', project_id: 'p', name: '<img src=x onerror=alert(1)>', version: null, description: null,
    status: 'planning', planned_date: '2026-11-03T00:00:00Z', released_at: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', phases: [],
  },
]

function renderCalendar(month = '2026-11') {
  const onMonth = vi.fn()
  const onOpen = vi.fn()
  render(<ReleaseCalendar releases={RELEASES} month={month} onMonth={onMonth} onOpen={onOpen} />)
  return { onMonth, onOpen, calendar: screen.getByRole('region', { name: 'Release calendar, November 2026' }) }
}

describe('ReleaseCalendar', () => {
  it('draws the release chip on its day and the phase bar across its days, as text', () => {
    const { calendar } = renderCalendar()
    expect(within(calendar).getByRole('heading', { name: 'November 2026' })).toBeInTheDocument()
    const chip = within(calendar).getByRole('button', { name: '2026.11' })
    expect(chip).toHaveAttribute('data-calendar-item', 'release')
    expect(chip).toHaveClass('col-start-5', 'col-span-1')        // Friday 20 November
    // The soak crosses a week boundary: one bar per row it covers.
    const bars = within(calendar).getAllByRole('button', { name: '2026.11 · Payments soak' })
    expect(bars).toHaveLength(2)
    expect(bars[0]).toHaveClass('col-start-4', 'col-span-4', 'row-start-2')
    expect(bars[1]).toHaveClass('col-start-1', 'col-span-3')
    expect(bars[0]).toHaveAttribute('title', '2026.11 · Payments soak: 2026-11-05 to 2026-11-11 (in progress)')
    // A hostile name is text.
    expect(within(calendar).getByRole('button', { name: '<img src=x onerror=alert(1)>' })).toBeInTheDocument()
    expect(calendar.querySelector('img')).toBeNull()
    expect(within(calendar).getByText(CALENDAR_LEGEND)).toBeInTheDocument()
  })

  it('a chip or a bar opens its release', () => {
    const { calendar, onOpen } = renderCalendar()
    fireEvent.click(within(calendar).getAllByRole('button', { name: '2026.11 · Payments soak' })[1])
    expect(onOpen).toHaveBeenLastCalledWith('rel-11')
    fireEvent.click(within(calendar).getByRole('button', { name: '2026.11' }))
    expect(onOpen).toHaveBeenLastCalledWith('rel-11')
  })

  it('Prev / Next / Today change the month', () => {
    const { calendar, onMonth } = renderCalendar()
    fireEvent.click(within(calendar).getByRole('button', { name: /Prev/ }))
    expect(onMonth).toHaveBeenLastCalledWith('2026-10')
    fireEvent.click(within(calendar).getByRole('button', { name: /Next/ }))
    expect(onMonth).toHaveBeenLastCalledWith('2026-12')
    fireEvent.click(within(calendar).getByRole('button', { name: 'Today' }))
    expect(onMonth).toHaveBeenLastCalledWith(new Date().toISOString().slice(0, 7))
  })

  it('is six week rows, and no element carries an inline style built from the data', () => {
    const { calendar } = renderCalendar()
    expect(calendar.querySelectorAll('[data-calendar-week]')).toHaveLength(6)
    expect(calendar.querySelectorAll('[style]')).toHaveLength(0)
  })
})
