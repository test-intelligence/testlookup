import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ActivityPage from './ActivityPage'
import { LIVE_PREFERENCE_KEY } from '@/hooks/useActivityLive'
import { backendUrl } from '@/services/api'
import { useAuthStore } from '@/store/authStore'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { encodeSseFrame, makeActivityEvent, mockStreamResponse, type SseFrame } from '@/test/activityLiveMocks'
import { STREAM_ERROR_SENTENCES } from '@/types/activityLive'
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

// WIN-01: the feed offers 14d like every windowed page; a fortnight picked
// elsewhere is shown and asked for as a fortnight, never snapped to a week.
describe('ActivityPage — the 14d window (WIN-01)', () => {
  const NOW = Date.parse('2026-10-16T12:00:00Z')
  const DAY_MS = 86_400_000

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs'],
      actor_types: ['system'],
      entity_types: ['run'],
      events: {},
    })
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
    useTimeWindowStore.setState({ days: 30 })
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
  })
  afterEach(() => {
    vi.restoreAllMocks()
    useTimeWindowStore.setState({ days: 30 })
  })

  const windowButtons = () =>
    screen.getAllByRole('button').filter(b => /^(\d+d|24h)$/.test(b.textContent ?? ''))
  const sinceOf = (call: unknown[]) => Date.parse((call[1] as { since?: string } | undefined)?.since ?? '')

  it('offers 24h · 7d · 14d · 30d · 90d, in that order (the shared labels)', async () => {
    renderPage()
    await screen.findByRole('feed')
    expect(windowButtons().map(b => b.textContent)).toEqual(['24h', '7d', '14d', '30d', '90d'])
  })

  it('clicking 14d asks for events since now − 14 days', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    renderPage()
    await screen.findByRole('feed')

    fireEvent.click(screen.getByRole('button', { name: '14d' }))

    await waitFor(() => {
      expect(vi.mocked(listActivity).mock.calls.some(call => sinceOf(call) === NOW - 14 * DAY_MS)).toBe(true)
    })
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(screen.getByRole('button', { name: '14d' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('a stored 14d opens as 14d and asks for 14 days (no snap to 7)', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    useTimeWindowStore.setState({ days: 14 })
    renderPage()
    await screen.findByRole('feed')

    const sinces = vi.mocked(listActivity).mock.calls.map(sinceOf)
    expect(sinces).toContain(NOW - 14 * DAY_MS)
    expect(sinces).not.toContain(NOW - 7 * DAY_MS)
    expect(screen.getByRole('button', { name: '14d' })).toHaveAttribute('aria-pressed', 'true')
    expect(useTimeWindowStore.getState().days).toBe(14)
  })
})

describe('ActivityPage — a failed page must not discard the loaded ones', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs'],
      actor_types: ['system'],
      entity_types: ['run'],
      events: {},
    })
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
  })

  it('keeps already-loaded rows when a later page fails', async () => {
    // The reader had 34 rows, pressed "Load older", the cursor request failed,
    // and the whole feed collapsed to "Activity is unavailable". Losing what
    // you already had is worse than the failed page, and it makes a transient
    // blip look like an outage.
    vi.mocked(listActivity)
      .mockResolvedValueOnce(page([event()], { next_cursor: 'abc' }))
      .mockRejectedValue(new Error('page 2 exploded'))

    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter>
          <ActivityPage />
        </MemoryRouter>
      </SWRConfig>,
    )

    await screen.findByRole('feed')
    fireEvent.click(screen.getByRole('button', { name: /load older/i }))

    // Wait for page 2 to have been attempted, so the assertion below is about
    // the post-failure state and not a race against the click.
    await waitFor(() =>
      expect(vi.mocked(listActivity).mock.calls.length).toBeGreaterThan(1),
    )

    // THE discriminator: with the old `error ?` guard the feed element is
    // GONE here. Asserting on the absence of the outage text is not enough -
    // it is absent in both variants, which is what made the first version of
    // this test vacuous.
    await waitFor(() => {
      expect(
        screen.queryByRole('feed'),
        'a failed page discarded the rows that had already loaded',
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Build 4312 completed — 3 failed of 812')).toBeInTheDocument()
  })

  it('still shows the outage state when NOTHING loaded', async () => {
    // The other half: with no rows at all, the full-page outage state is
    // correct and must not be regressed away by the fix above.
    vi.mocked(listActivity).mockRejectedValue(new Error('boom'))

    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter>
          <ActivityPage />
        </MemoryRouter>
      </SWRConfig>,
    )

    expect(await screen.findByText(/Activity is unavailable/i)).toBeInTheDocument()
  })
})

// HON-09: the /releases rail's "View full audit log" (and the old `/audit`
// bookmark) open /activity?category=release. The page read only `?event=`
// from the URL, so the link could not pre-filter it.
describe('ActivityPage — ?category= pre-selects the filter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs', 'release', 'configuration'],
      actor_types: ['user', 'system'],
      entity_types: ['run', 'release'],
      events: {},
    })
    vi.mocked(listActivity).mockResolvedValue(page([event({ category: 'release', summary: 'Release 2026.10 created' })]))
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
  })

  function renderAt(entry: string) {
    return render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={[entry]}>
          <ActivityPage />
        </MemoryRouter>
      </SWRConfig>,
    )
  }

  it('selects the Release category and asks the API for it, never for the unfiltered feed first', async () => {
    renderAt('/activity?category=release')

    await screen.findByRole('feed')
    expect(screen.getByRole('combobox', { name: 'Filter by category' })).toHaveValue('release')
    const calls = vi.mocked(listActivity).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    for (const [, params] of calls) expect(params).toMatchObject({ category: ['release'] })
  })

  it('drops a category the registry does not list (the API would 422), and loads the whole feed', async () => {
    renderAt('/activity?category=nonsense')

    await screen.findByRole('feed')
    expect(screen.getByRole('combobox', { name: 'Filter by category' })).toHaveValue('')
    for (const [, params] of vi.mocked(listActivity).mock.calls) expect(params).not.toHaveProperty('category')
  })

  it('still loads the feed, unfiltered, when the registry itself fails', async () => {
    vi.mocked(listActivityEventTypes).mockRejectedValue(new Error('registry down'))
    renderAt('/activity?category=release')

    await screen.findByRole('feed')
    for (const [, params] of vi.mocked(listActivity).mock.calls) expect(params).not.toHaveProperty('category')
  })
})

// W21d ACT-02: the Live toggle. OFF by default (no stream request at all); ON
// opens ONE fetch stream with the bearer header (no token in the URL), lays a
// streamed row on top of the feed, and pauses polling only while LIVE.
describe('ActivityPage — Live (ACT-02)', () => {
  const STREAM_URL = backendUrl(`/api/v1/projects/${PROJECT_ID}/activity/stream`)
  const READY: SseFrame = {
    event: 'ready',
    id: '1730000000000-0',
    data: { project_id: PROJECT_ID, last_event_id: '1730000000000-0', heartbeat_seconds: 15, max_seconds: 900 },
  }
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs', 'configuration'],
      actor_types: ['user', 'system'],
      entity_types: ['run'],
      events: {},
    })
    vi.mocked(listActivity).mockResolvedValue(page([event()]))
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
    useAuthStore.setState({ token: 'live-token' })
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    window.localStorage.clear()
    useAuthStore.setState({ token: null })
  })

  it('is OFF by default: the toggle is unpressed and no stream is requested', async () => {
    renderPage()
    expect(await screen.findByText('Build 4312 completed — 3 failed of 812')).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Live' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(document.querySelector('[data-activity-live-status]')).toBeNull()
  })

  it('ON opens the project stream with the bearer header and puts a streamed row on top', async () => {
    const streamed = makeActivityEvent({
      project_id: PROJECT_ID,
      category: 'configuration',
      summary: 'Retention policy changed to 90 days',
    })
    // A body held open: the row is sent once the fresh stream's page-0
    // re-read has answered (that answer, like the mock, does not hold it).
    const encoder = new TextEncoder()
    let body: ReadableStreamDefaultController<Uint8Array> | undefined
    fetchMock.mockImplementation(() => Promise.resolve(new Response(
      new ReadableStream<Uint8Array>({ start(c) { body = c } }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))
    renderPage()
    await screen.findByText('Build 4312 completed — 3 failed of 812')
    fireEvent.click(screen.getByRole('button', { name: 'Live' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    body?.enqueue(encoder.encode(encodeSseFrame(READY)))
    await waitFor(() => expect(vi.mocked(listActivity)).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByRole('feed')).toHaveAttribute('aria-busy', 'false'))
    body?.enqueue(encoder.encode(encodeSseFrame({ event: 'activity', id: '1730000000001-0', data: streamed })))

    expect(await screen.findByText('Retention policy changed to 90 days')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(STREAM_URL)
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer live-token')
    await waitFor(() => expect(screen.getAllByTestId('activity-row').map((row) => row.textContent)).toEqual([
      expect.stringContaining('Retention policy changed to 90 days'),
      expect.stringContaining('Build 4312 completed'),
    ]))
    expect(screen.getByRole('button', { name: 'Live' })).toHaveAttribute('aria-pressed', 'true')
    expect(window.localStorage.getItem(LIVE_PREFERENCE_KEY)).toBe('on')
  })

  it('a streamed row the filters exclude is not prepended', async () => {
    const other = makeActivityEvent({ project_id: PROJECT_ID, category: 'configuration', summary: 'Flag turned on' })
    fetchMock.mockImplementation(() => Promise.resolve(mockStreamResponse({
      frames: [READY, { event: 'activity', id: '1730000000001-0', data: other }],
    })))
    renderPage()
    await screen.findByText('Build 4312 completed — 3 failed of 812')
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by category' }), { target: { value: 'runs' } })
    fireEvent.click(screen.getByRole('button', { name: 'Live' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Live' })).toHaveAttribute('data-live-status', 'live'))
    expect(screen.queryByText('Flag turned on')).toBeNull()
  })

  it('pauses the 30 s polling only while LIVE, and polls again once live updates are unavailable', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    window.localStorage.setItem(LIVE_PREFERENCE_KEY, 'on')
    let available = true
    fetchMock.mockImplementation(() => Promise.resolve(available
      ? mockStreamResponse({ frames: [READY] })
      : mockStreamResponse({ status: 503, code: 'live_unavailable' })))
    // One act per second: React commits (and SWR re-arms its timer) between
    // steps, as it would in a browser, instead of once at the end of an act.
    const tick = async (ms: number) => {
      for (let done = 0; done < Math.max(ms, 1); done += 1_000) {
        await act(async () => { await vi.advanceTimersByTimeAsync(Math.min(1_000, ms - done)) })
      }
    }
    renderPage()
    for (let i = 0; i < 10; i += 1) await tick(0)
    expect(screen.getByRole('button', { name: 'Live' })).toHaveAttribute('data-live-status', 'live')
    expect(document.querySelector('[data-activity-live-status]')).toBeNull()

    const settled = vi.mocked(listActivity).mock.calls.length
    await tick(65_000)
    expect(vi.mocked(listActivity).mock.calls.length).toBe(settled)

    available = false
    await tick(65_000)
    expect(screen.getByRole('status')).toHaveTextContent(STREAM_ERROR_SENTENCES.live_unavailable)
    expect(vi.mocked(listActivity).mock.calls.length).toBeGreaterThan(settled)
  })
})
