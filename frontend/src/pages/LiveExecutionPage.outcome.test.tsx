/**
 * Live's sessions table: a session with no pass rate yet reads "—", not
 * "0.0%", and "Last completed" counts in hours and days, not minutes.
 *
 * The UX redesign's browser E2E pass (2026-10-08) read a "0.0%" outcome for a
 * run at 10 passed, 1 failed and for a session whose 42 tests had not
 * reported a result (the API sent 0 for "no rate"), and "Last completed 2122m
 * ago".
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const live = vi.hoisted(() => ({ useLiveExecution: vi.fn() }))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useEffectivePassRateBands', async () => { const { readinessBands } = await import('@/utils/passRateBands'); return { useEffectivePassRateBands: () => readinessBands(null) } })
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: [], isLoading: false })) }))

import LiveExecutionPage from './LiveExecutionPage'
import { useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

const NO_RESULT = {
  run_id: 'build-live-demo', test_run_id: 'r-demo', project_id: PROJECT, build_number: 'build-live-demo',
  status: 'completed', total: 42, passed: 0, failed: 0, skipped: 0, broken: 0, pass_rate: null,
  started_at: hoursAgo(36), completed_at: hoursAgo(35), last_event_at: hoursAgo(35),
}
const MEASURED = {
  ...NO_RESULT, run_id: 'viz-3043', test_run_id: 'r-43', build_number: 'viz-3043',
  total: 11, passed: 10, failed: 1, pass_rate: 90.91,
}

function renderWith(sessions: Record<string, unknown>[]) {
  live.useLiveExecution.mockReturnValue({
    sessions,
    activeSessions: [],
    completedSessions: sessions,
    recentEvents: [],
    wsStatus: 'open',
    isLoading: false,
  })
  return render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
}

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockReset()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useTimeWindowStore.setState({ days: 7 })
})

describe('LiveExecutionPage — a session outcome', () => {
  it('reads "—" with no pass rate, and the measured rate otherwise', () => {
    renderWith([NO_RESULT, MEASURED])
    const noResult = screen.getByText('build-live-demo', { selector: 'td *' }).closest('tr') as HTMLElement
    const outcome = within(noResult).getByTitle('No pass rate: no test has passed or failed yet')
    expect(outcome).toHaveTextContent('—')
    expect(noResult).not.toHaveTextContent('0.0%')
    const measured = screen.getByText('viz-3043', { selector: 'td *' }).closest('tr') as HTMLElement
    expect(measured).toHaveTextContent('90.9%')
  })

  it('says how long ago the last session completed in days, not thousands of minutes', () => {
    renderWith([NO_RESULT])
    expect(screen.getByText(/Last completed 1d ago/)).toBeInTheDocument()
    expect(screen.queryByText(/\d{3,}m ago/)).toBeNull()
  })
})

describe('LiveExecutionPage — the sessions table header (W40 F-2)', () => {
  const header = (name: string) => screen.getByRole('columnheader', { name })
  const builds = () => screen.getAllByText(/^(build-live-demo|viz-3043)$/, { selector: 'td *' }).map((el) => el.textContent)

  it('is the ONE table: `.th` labels, the number columns numeric, a sort button (aria-sort) only on the five sortable ones', () => {
    renderWith([NO_RESULT, MEASURED])
    const table = header('Build').closest('table') as HTMLElement
    expect(table).toHaveAttribute('data-density', 'dense')
    const names = ['Build', 'Suite', 'Status', 'Tests', 'Pass', 'Failed', 'Outcome', 'Release', 'Timing']
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(names)
    for (const name of names) expect(header(name).classList.contains('th')).toBe(true)
    for (const name of ['Tests', 'Pass', 'Failed']) expect(header(name).classList.contains('th-num')).toBe(true)
    for (const name of ['Build', 'Suite', 'Tests', 'Failed']) expect(header(name)).toHaveAttribute('aria-sort', 'none')
    // The default order: newest first.
    expect(header('Timing')).toHaveAttribute('aria-sort', 'descending')
    for (const name of ['Status', 'Pass', 'Outcome', 'Release']) {
      expect(header(name)).not.toHaveAttribute('aria-sort')
      expect(within(header(name)).queryByRole('button')).toBeNull()
    }
    // The header keeps its paddings (py-2.5; px-5 over the edge cells) and Outcome its width.
    expect(header('Build').className).toMatch(/\bpx-5\b.*\bpy-2\.5\b/)
    expect(header('Timing').className).toMatch(/\bpx-5\b.*\bpy-2\.5\b/)
    expect(header('Suite').className).toMatch(/\bpy-2\.5\b/)
    expect(header('Outcome').style.width).toBe('140px')
  })

  it('sorts as before: a new column starts descending, a second click flips it', () => {
    renderWith([MEASURED, NO_RESULT])
    const sortBy = (name: string) => fireEvent.click(within(header(name)).getByRole('button', { name }))
    sortBy('Tests')
    expect(header('Tests')).toHaveAttribute('aria-sort', 'descending')
    expect(header('Timing')).toHaveAttribute('aria-sort', 'none')
    expect(builds()).toEqual(['build-live-demo', 'viz-3043'])   // 42 tests, then 11
    sortBy('Tests')
    expect(header('Tests')).toHaveAttribute('aria-sort', 'ascending')
    expect(builds()).toEqual(['viz-3043', 'build-live-demo'])
    sortBy('Failed')
    expect(header('Failed')).toHaveAttribute('aria-sort', 'descending')
    expect(builds()).toEqual(['viz-3043', 'build-live-demo'])   // 1 failed, then 0
  })
})
