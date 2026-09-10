import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ActivityPage from './ActivityPage'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { ActivityEvent, ActivityPage as Page } from '@/services/activityService'

vi.mock('@/services/activityService', () => ({
  listActivity: vi.fn(),
  getActivityEvent: vi.fn(),
  listActivityEventTypes: vi.fn(),
  listEntityActivity: vi.fn(),
  exportActivity: vi.fn(),
}))

import {
  listActivity,
  listActivityEventTypes,
} from '@/services/activityService'

const PROJECT_ID = '11111111-1111-1111-1111-111111111111'

function event(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: 'e1',
    project_id: PROJECT_ID,
    release_id: null,
    occurred_at: new Date().toISOString(),
    category: 'runs',
    event_type: 'run.completed',
    actor: { type: 'system', id: null, name: 'ingestion', ref: 'ingestion' },
    entity: { type: 'run', id: 'r1', label: 'Build 4312', href: '/runs/r1' },
    target: null,
    summary: 'Build 4312 completed — 3 failed of 812',
    context: null,
    has_diff: false,
    source: null,
    group_key: null,
    ...overrides,
  }
}

function page(items: ActivityEvent[], overrides: Partial<Page> = {}): Page {
  return {
    items,
    next_cursor: null,
    ledger_started_at: '2026-09-01T00:00:00Z',
    window: { since: null, until: null },
    ...overrides,
  }
}

function renderPage() {
  // A fresh SWR cache per render. Without it the module-level cache carries
  // results between tests in this file, so the second render is served from
  // cache and the fetcher is never called — every "what did we request?"
  // assertion then reads an empty mock.calls and fails for the wrong reason.
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <ActivityPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('ActivityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs', 'configuration'],
      actor_types: ['user', 'system'],
      entity_types: ['run'],
      events: {},
    })
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
  })

  it('renders the events it is given', async () => {
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    renderPage()

    expect(
      await screen.findByText('Build 4312 completed — 3 failed of 812'),
    ).toBeInTheDocument()
  })

  it('says the ledger is UNAVAILABLE when the fetch fails, not that it is empty', async () => {
    // The distinction the whole state machine exists for. "No activity yet"
    // and "we could not reach the backend" lead a reader to opposite
    // conclusions about whether to worry, and absence is not health.
    vi.mocked(listActivity).mockRejectedValue(new Error('boom'))
    renderPage()

    expect(await screen.findByText(/Activity is unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText(/No activity in this window/i)).not.toBeInTheDocument()
  })

  it('tells the reader when the ledger starts, so an empty window is not read as "nothing happened"', async () => {
    // There is no backfill: the ledger begins at deploy. Without this line an
    // empty 90-day view on a two-year-old project looks like a broken feature.
    vi.mocked(listActivity).mockResolvedValue(
      page([], { ledger_started_at: '2026-09-01T00:00:00Z' }),
    )
    renderPage()

    expect(await screen.findByText(/No activity in this window/i)).toBeInTheDocument()
    expect(screen.getByText(/activity ledger starts on/i)).toBeInTheDocument()
  })

  it('offers a project picker instead of a dead end in All Projects mode', async () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    renderPage()

    // ProjectRequiredEmptyState renders the shared prompt. A bare sentence
    // telling the reader to go and find the top bar is the thing the
    // routeScope ratchet rejects.
    expect(
      await screen.findByText(/Activity is recorded per project/i),
    ).toBeInTheDocument()
    expect(listActivity).not.toHaveBeenCalled()
  })

  it('exposes the feed as a feed landmark', async () => {
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    renderPage()

    expect(await screen.findByRole('feed')).toBeInTheDocument()
  })

  it('only offers "Load older" when another page exists', async () => {
    vi.mocked(listActivity).mockResolvedValue(page([event()], { next_cursor: null }))
    renderPage()
    await screen.findByRole('feed')
    expect(screen.queryByRole('button', { name: /load older/i })).not.toBeInTheDocument()

    vi.mocked(listActivity).mockResolvedValue(
      page([event()], { next_cursor: 'abc' }),
    )
    renderPage()
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /load older/i }).length).toBeGreaterThan(0),
    )
  })

  it('ignores a search shorter than three characters', async () => {
    // Documented behaviour, not a silent no-op: a 1-2 char trigram query
    // matches so much it is neither useful nor indexable.
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    renderPage()
    await screen.findByRole('feed')

    const input = screen.getByLabelText(/search activity/i)
    fireEvent.change(input, { target: { value: 'ab' } })
    fireEvent.blur(input)

    await waitFor(() => {
      const calls = vi.mocked(listActivity).mock.calls
      expect(calls.every(([, params]) => params?.q === undefined)).toBe(true)
    })
  })

  it('sends a search of three characters or more', async () => {
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    renderPage()
    await screen.findByRole('feed')

    const input = screen.getByLabelText(/search activity/i)
    fireEvent.change(input, { target: { value: 'policy' } })
    fireEvent.blur(input)

    await waitFor(() => {
      const calls = vi.mocked(listActivity).mock.calls
      expect(calls.some(([, params]) => params?.q === 'policy')).toBe(true)
    })
  })

  it('resolves the time window at fetch time rather than pinning it to first render', async () => {
    // A `since` frozen during render would slowly exclude the newest events on
    // a page left open — and Date.now() in render is impure besides.
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    renderPage()
    await screen.findByRole('feed')

    const [, params] = vi.mocked(listActivity).mock.calls[0]
    const since = params?.since
    expect(since).toBeTruthy()
    expect(Date.parse(since ?? '')).not.toBeNaN()
    // `days` is a client-side shorthand and must not reach the API.
    expect((params as Record<string, unknown> | undefined)?.days).toBeUndefined()
  })
})
