/**
 * UX P2 "remove the noise" on /live.
 *
 * The "Live execution workflow" panel drew four invented stages (Stream
 * Connection → Run Monitoring → Event Rollup → Release Readout) with a
 * "100% conf" pill for an open socket, a selected-stage strip and a Cost tile
 * that was always "—". It is gone; the Pipeline events feed it shared a grid
 * with stays, full width, and labels each event by what it is.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const NOW = Date.now()

const live = vi.hoisted(() => ({
  useLiveExecution: vi.fn(),
}))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: [], isLoading: false })) }))

import LiveExecutionPage from './LiveExecutionPage'
import { useProjectStore } from '@/store/projectStore'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'

beforeEach(() => {
  localStorage.clear()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  live.useLiveExecution.mockReturnValue({
    sessions: [],
    activeSessions: [],
    completedSessions: [],
    recentEvents: [
      { type: 'live_run_started', run_id: 'r1', build_number: '41', timestamp: NOW - 20_000 },
      { type: 'live_run_complete', run_id: 'r0', build_number: '40', timestamp: NOW - 90_000 },
    ],
    // An open socket is exactly when the old ribbon showed "100% conf".
    wsStatus: 'open',
    isLoading: false,
  })
})

describe('LiveExecutionPage — no decorative workflow', () => {
  it('renders no workflow panel, stage strip, confidence pill or always-empty Cost tile', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    expect(screen.getByRole('heading', { level: 1, name: 'Live Execution' })).toBeInTheDocument()
    expect(screen.queryByText('Live execution workflow')).toBeNull()
    expect(screen.queryByText(/Connection health → run monitoring/)).toBeNull()
    expect(screen.queryByText(/selected stage/i)).toBeNull()
    expect(screen.queryByText(/% conf/)).toBeNull()
    expect(screen.queryByText(/\d+ evidence/)).toBeNull()
    expect(screen.queryByText('Cost')).toBeNull()
    for (const stage of ['Stream Connection', 'Run Monitoring', 'Event Rollup', 'Release Readout']) {
      expect(screen.queryByText(stage)).toBeNull()
    }
  })

  it('keeps the Pipeline events feed, labelling each event by what it is', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    const feed = screen.getByRole('complementary', { name: 'Pipeline events' })
    expect(within(feed).getByText('Run started')).toBeInTheDocument()
    expect(within(feed).getByText('Run completed')).toBeInTheDocument()
    expect(within(feed).getByRole('button', { name: 'runs' })).toBeInTheDocument()
    expect(within(feed).queryByRole('button', { name: 'stage' })).toBeNull()
  })

  it('says each event\'s age once ("20s ago", "1m ago"), not "… ago ago"', () => {
    render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)

    const feed = screen.getByRole('complementary', { name: 'Pipeline events' })
    expect(within(feed).getByText(/^\d+s ago$/)).toBeInTheDocument()
    expect(within(feed).getByText(/^1m ago$/)).toBeInTheDocument()
    expect(within(feed).queryByText(/ago ago/)).toBeNull()
  })
})
