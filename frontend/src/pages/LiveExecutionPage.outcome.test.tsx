/**
 * Live's sessions table: a session with no pass rate yet reads "—", not
 * "0.0%", and "Last completed" counts in hours and days, not minutes.
 *
 * The UX redesign's browser E2E pass (2026-10-08) read a "0.0%" outcome for a
 * run at 10 passed, 1 failed and for a session whose 42 tests had not
 * reported a result (the API sent 0 for "no rate"), and "Last completed 2122m
 * ago".
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const live = vi.hoisted(() => ({ useLiveExecution: vi.fn() }))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
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
