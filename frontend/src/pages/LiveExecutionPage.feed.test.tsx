/**
 * W25e HYG-06 E-3 (T-C3): the /live event feed under paced, batched frames.
 *
 * When the server folded more results into one frame than it listed, the
 * feed shows an explicit "More results" row ("N more results not listed",
 * info tone — so the Errors tab hides it); a warning folded N times reads
 * "<message> (N times)"; and the footer states the real pacing interval
 * instead of the placeholder "Streaming · 100ms batch" that nothing backed.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LIVE_RESULTS_OMITTED, omittedResultsSentence } from '@/hooks/liveFrameModel'

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
const RUN = 'zz-run-feed'
const NOW = Date.now()
const WARNING = 'High failure rate: 60% of tests failing'

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockReset()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useTimeWindowStore.setState({ days: 7 })
  live.useLiveExecution.mockReturnValue({
    sessions: [],
    activeSessions: [],
    completedSessions: [],
    // Newest first, as the hook keeps them.
    recentEvents: [
      { type: LIVE_RESULTS_OMITTED, run_id: RUN, omitted: 2, message: omittedResultsSentence(2), timestamp: NOW - 1_000 },
      { type: 'live_test_result', run_id: RUN, last_test: 'test_b', last_status: 'FAILED', timestamp: NOW - 1_000 },
      { type: 'live_test_result', run_id: RUN, last_test: 'test_a', last_status: 'PASSED', timestamp: NOW - 1_000 },
      { type: 'live_warning', run_id: RUN, message: WARNING, coalesced: 3, timestamp: NOW - 1_000 },
      { type: 'live_warning', run_id: RUN, message: 'Single warning', coalesced: 1, timestamp: NOW - 2_000 },
      { type: 'live_run_started', run_id: RUN, build_number: '41', timestamp: NOW - 5_000 },
    ],
    wsStatus: 'open',
    isLoading: false,
  })
})

const feed = () => screen.getByRole('complementary', { name: 'Pipeline events' })

describe('LiveExecutionPage — the feed under batched frames (W25e E-3)', () => {
  it('renders the omitted row as "More results" with "2 more results not listed"', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    expect(omittedResultsSentence(2)).toBe('2 more results not listed')
    expect(within(feed()).getByText('More results')).toBeInTheDocument()
    expect(within(feed()).getByText('· 2 more results not listed')).toBeInTheDocument()
    // Each listed result is still its own row.
    expect(within(feed()).getAllByText('Test result')).toHaveLength(2)
  })

  it('a warning folded 3 times says so once; a single warning reads as before', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    expect(within(feed()).getByText(`· ${WARNING} (3 times)`)).toBeInTheDocument()
    expect(within(feed()).getByText('· Single warning')).toBeInTheDocument()
    expect(within(feed()).queryByText(/Single warning \(1 times\)/)).toBeNull()
  })

  it('the Errors tab hides the omitted row (info), keeping the failed result and the warnings', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    fireEvent.click(within(feed()).getByRole('button', { name: 'errors' }))

    expect(within(feed()).queryByText('More results')).toBeNull()
    expect(within(feed()).queryByText(/more results not listed/)).toBeNull()
    expect(within(feed()).getAllByText('Test result')).toHaveLength(1) // the FAILED one
    expect(within(feed()).getAllByText('Warning')).toHaveLength(2)
    expect(within(feed()).queryByText('Run started')).toBeNull()

    fireEvent.click(within(feed()).getByRole('button', { name: 'runs' }))
    expect(within(feed()).queryByText('More results')).toBeNull()
    expect(within(feed()).getByText('Run started')).toBeInTheDocument()
  })

  it('the footer states the real pacing interval', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    expect(within(feed()).getByText('Streaming · 200 ms batches')).toBeInTheDocument()
    expect(within(feed()).queryByText(/100ms batch/)).toBeNull()
  })
})
