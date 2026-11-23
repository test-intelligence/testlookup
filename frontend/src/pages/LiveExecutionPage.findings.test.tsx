/**
 * 2026-11-21 product findings on the Live page (D-86, D-87, D-88, D-89, D-90, D-94).
 *
 * - D-86: a row links to /runs/<canonical TestRun id>; a row that only knows
 *   the SDK's own id (a slug /runs refuses) shows its label as text.
 * - D-87: one run listed under the SDK's id and under its UUID is one row.
 * - D-88: a running session is named by its build label, not "Run #1".
 * - D-89: the strip names the window its totals come from, and the hero
 *   counts tests still to run, not the declared totals.
 * - D-90: a test-result card names its test; no card shows a raw frame type.
 * - D-94: the empty table does not claim there are no ACTIVE sessions.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LiveSessionState } from '@/types/live-stream'

const live = vi.hoisted(() => ({ useLiveExecution: vi.fn() }))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: [], isLoading: false })) }))
vi.mock('@/hooks/useEffectivePassRateBands', async () => {
  const { readinessBands } = await import('@/utils/passRateBands')
  return { useEffectivePassRateBands: () => readinessBands(null) }
})

import LiveExecutionPage from './LiveExecutionPage'
import { useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const RUN_UUID = 'bbbbbbbb-0000-5000-8000-000000000042'
const NOW = Date.now()
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

function session(p: Partial<LiveSessionState>): LiveSessionState {
  return {
    run_id: 'zz-run', project_id: PROJECT, build_number: 'zz-build', status: 'completed',
    total: 10, passed: 10, failed: 0, skipped: 0, broken: 0, pass_rate: 100,
    started_at: iso(3_600_000), completed_at: iso(3_000_000), last_event_at: iso(3_000_000),
    ...p,
  }
}

function mount(sessions: LiveSessionState[], recentEvents: unknown[] = []) {
  live.useLiveExecution.mockReturnValue({ sessions, recentEvents, wsStatus: 'open', isLoading: false })
  return render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
}

const table = () => screen.getByRole('table')
const feed = () => screen.getByRole('complementary', { name: 'Pipeline events' })

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockReset()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useTimeWindowStore.setState({ days: 7 })
})

describe('D-88: a running session is named by its build label', () => {
  it('a running session numbered "Run #1" reads as its build label, without the number', () => {
    mount([session({
      run_id: 'ci-build-42', test_run_id: RUN_UUID, build_number: 'ci-build-42', status: 'running',
      run_seq: 1, completed_at: undefined, last_event_at: iso(1_000),
    })])

    const link = within(table()).getByRole('link', { name: 'ci-build-42' })
    expect(link).toHaveAttribute('href', `/runs/${RUN_UUID}`)
    expect(within(table()).queryByText(/Run #1/)).toBeNull()
  })

  it('a finished session keeps its run number as the muted ordinal', () => {
    mount([session({ run_id: 'main-7', test_run_id: RUN_UUID, build_number: 'main-7', run_seq: 3 })])

    expect(within(table()).getByRole('link', { name: 'main-7' })).toBeInTheDocument()
    expect(within(table()).getByText(/Run #3/)).toBeInTheDocument()
  })
})

describe('D-86: a row links only to a run page that answers', () => {
  it('a row that only knows the SDK slug shows its label as text, not a dead link', () => {
    mount([session({ run_id: 'ci-build-42', test_run_id: undefined, build_number: 'ci-build-42', status: 'running', completed_at: undefined, last_event_at: iso(1_000) })])

    expect(within(table()).getByText('ci-build-42')).toBeInTheDocument()
    expect(within(table()).queryByRole('link', { name: 'ci-build-42' })).toBeNull()
  })
})

describe('D-87: one run under two ids is one row', () => {
  it('a slug-keyed row and a UUID-keyed row of the same run collapse to one', () => {
    mount([
      session({ run_id: 'ci-42', test_run_id: RUN_UUID, build_number: 'ci-42', last_event_at: iso(2_000_000) }),
      session({ run_id: RUN_UUID, test_run_id: RUN_UUID, build_number: 'ci-42', last_event_at: iso(1_000_000) }),
    ])

    expect(within(table()).getAllByRole('link', { name: 'ci-42' })).toHaveLength(1)
  })
})

describe('D-89: the status strip says what it counts', () => {
  it('one running session (30 declared, 10 reported) and five finished ones', () => {
    const running = session({
      run_id: 'zz-live', test_run_id: RUN_UUID, build_number: 'zz-live', status: 'running',
      total: 30, passed: 8, failed: 1, skipped: 1, broken: 0, completed_at: undefined, last_event_at: iso(1_000),
    })
    const finished = [1, 2, 3, 4, 5].map(i => session({
      run_id: `zz-done-${i}`, test_run_id: `cccccccc-0000-5000-8000-00000000000${i}`, build_number: `zz-done-${i}`,
    }))
    mount([running, ...finished])

    expect(screen.getByText('20 tests still to run')).toBeInTheDocument()
    expect(screen.queryByText(/tests in flight/)).toBeNull()
    const scope = document.querySelector('[data-live-strip-scope]')
    expect(scope?.textContent).toBe('tests across 6 sessionsin the last 7 days')
  })
})

describe('D-90: event cards name the test and use words', () => {
  it('a test result card shows the test name and its status, not the raw frame type', () => {
    mount([], [
      { type: 'live_test_result', run_id: 'ci-42', build_number: 'ci-42', last_test: 'adds to cart', last_status: 'FAILED', timestamp: NOW - 1_000 },
      { type: 'pipeline_progress', run_id: 'ci-42', timestamp: NOW - 2_000 },
      { type: 'some_new_frame', run_id: 'ci-42', timestamp: NOW - 3_000 },
    ])

    expect(within(feed()).getByText('adds to cart')).toBeInTheDocument()
    expect(within(feed()).getByText('Failed · ci-42')).toBeInTheDocument()
    expect(within(feed()).getByText('Analysis in progress')).toBeInTheDocument()
    expect(within(feed()).getByText('Some new frame')).toBeInTheDocument()
    expect(within(feed()).queryByText('live_test_result')).toBeNull()
    expect(within(feed()).queryByText('pipeline_progress')).toBeNull()
    expect(within(feed()).queryByText('Test result')).toBeNull()
  })
})

describe('D-94: the empty table names the window, not "active" sessions', () => {
  it('reads "No sessions in the last 7 days"', () => {
    mount([])

    expect(within(table()).getByText(/^No sessions in the last 7 days\./)).toBeInTheDocument()
    expect(screen.queryByText(/No active execution sessions/)).toBeNull()
  })
})
