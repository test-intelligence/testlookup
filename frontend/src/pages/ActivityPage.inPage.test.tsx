/**
 * W27b PAN-05b: on /activity an event's detail opens IN THE PAGE, right under
 * the feed row it belongs to (the page sits in the 732 px settings column: a
 * pane beside the feed would leave it ~276 px), or right above the feed for a
 * deep link to an event the feed has not loaded. Driven through the REAL feed
 * and event hooks (SWR over a mocked activityService) and a real memory
 * router; every URL read waits for the COMMITTED location in the same waitFor:
 *
 *  - a row PUSHES `?event=<id>` (Back closes it), another row REPLACES, the X
 *    pops our entry; `category` survives every write;
 *  - the detail is the `<li data-in-page-row>` right after its row, never a
 *    dialog; focus moves to its X and returns to the row of the event that was
 *    open (also after a switch: G-E phase-1 finding 4);
 *  - a malformed `?event=` (`../../x`) says so and reads NOTHING;
 *  - a 403 says "this project's activity" with an apostrophe (it rendered the
 *    literal `&apos;`).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ActivityPage from './ActivityPage'
import type { ActivityEvent, ActivityEventDetail, ActivityPage as FeedPage } from '@/services/activityService'
import { useProjectStore } from '@/store/projectStore'

// The detail sits behind a SectionErrorBoundary: the real reporter batches for
// 5 s and would post inside a later test (W08 gate).
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
vi.mock('@/services/activityService', () => ({
  listActivity: vi.fn(),
  getActivityEvent: vi.fn(),
  listActivityEventTypes: vi.fn(),
  listEntityActivity: vi.fn(),
  exportActivity: vi.fn(),
}))

import { getActivityEvent, listActivity, listActivityEventTypes } from '@/services/activityService'

const PROJECT_ID = '11111111-1111-4111-8111-111111111111'

function event(id: string, summary: string, overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id,
    project_id: PROJECT_ID,
    release_id: null,
    occurred_at: '2026-10-16T10:00:00Z',
    category: 'configuration',
    event_type: 'feature_flag.updated',
    actor: { type: 'user', id: 'u1', name: 'Priya Nair', ref: null },
    entity: { type: 'project', id: PROJECT_ID, label: 'Checkout', href: null },
    target: null,
    summary,
    context: null,
    has_diff: false,
    source: null,
    group_key: null,
    ...overrides,
  }
}

const FLAG = event('ev-flag-1', 'Feature flag defect_commander turned on')
const RETENTION = event('ev-retention-2', 'Retention policy changed to 90 days', { event_type: 'retention.updated' })
const OLDER = event('ev-older-9', 'Release 2026.08 was activated', { category: 'release', event_type: 'release.activated' })
const FEED = [FLAG, RETENTION]

const detailOf = (e: ActivityEvent): ActivityEventDetail => ({ ...e, diff: null })
const status = (code: number) => Object.assign(new Error(`Request failed with status code ${code}`), {
  response: { status: code, data: { detail: 'nope' } },
})

function feedPage(items: ActivityEvent[]): FeedPage {
  return { items, next_cursor: null, ledger_started_at: '2026-09-01T00:00:00Z', window: { since: null, until: null } }
}

function setup(entries: string[]) {
  const router = createMemoryRouter(
    [
      { path: '/start', element: <p>START</p> },
      { path: '/activity', element: <ActivityPage /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  const view = render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  )
  return { router, ...view }
}

async function expectAt(router: ReturnType<typeof createMemoryRouter>, url: string, action?: string) {
  await waitFor(() => {
    expect(`${router.state.location.pathname}${router.state.location.search}`).toBe(url)
    if (action) expect(router.state.historyAction).toBe(action)
  })
}

const feed = () => screen.findByRole('feed')
const rowButton = (summary: string) => within(screen.getByRole('feed')).getByRole('button', { name: summary })
const rowOf = (summary: string) => rowButton(summary).closest('li') as HTMLElement
const detailNamed = (name: string) => screen.findByRole('complementary', { name })
/** A browser focuses a button it clicks; jsdom's click does not. */
const press = (button: HTMLElement) => {
  button.focus()
  fireEvent.click(button)
}

describe('ActivityPage — the event detail in the page (W27b)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listActivityEventTypes).mockResolvedValue({
      categories: ['runs', 'release', 'configuration'],
      actor_types: ['user'],
      entity_types: ['project'],
      events: {},
    })
    vi.mocked(listActivity).mockResolvedValue(feedPage(FEED))
    vi.mocked(getActivityEvent).mockImplementation(async (_project: string, id: string) => {
      const found = [...FEED, OLDER].find((e) => e.id === id)
      if (!found) throw status(404)
      return detailOf(found)
    })
    useProjectStore.setState({ activeProjectId: PROJECT_ID })
  })

  it('a row PUSHES ?event=<id> (category kept) and renders the detail in the <li> right after that row, focus on its X', async () => {
    const { router } = setup(['/start', '/activity?category=configuration'])
    await feed()
    expect(getActivityEvent).not.toHaveBeenCalled()

    press(rowButton(RETENTION.summary))
    await expectAt(router, `/activity?category=configuration&event=${RETENTION.id}`, 'PUSH')
    const detail = await detailNamed(RETENTION.summary)

    const holder = detail.closest('li') as HTMLElement
    expect(holder).toHaveAttribute('data-in-page-row')
    expect(holder).not.toHaveAttribute('data-testid', 'activity-row')
    expect(rowOf(RETENTION.summary).nextElementSibling).toBe(holder)
    expect(holder.parentElement).toBe(screen.getByRole('feed'))
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(detail).getByRole('button', { name: 'Close activity details' })).toHaveFocus()
    expect(getActivityEvent).toHaveBeenCalledTimes(1)
    expect(getActivityEvent).toHaveBeenCalledWith(PROJECT_ID, RETENTION.id)
  })

  it('another row REPLACES and moves the detail under it; Back closes it and keeps the category', async () => {
    const { router } = setup(['/start', '/activity?category=configuration'])
    await feed()
    press(rowButton(FLAG.summary))
    await expectAt(router, `/activity?category=configuration&event=${FLAG.id}`, 'PUSH')
    await detailNamed(FLAG.summary)

    press(rowButton(RETENTION.summary))
    await expectAt(router, `/activity?category=configuration&event=${RETENTION.id}`, 'REPLACE')
    const detail = await detailNamed(RETENTION.summary)
    expect(rowOf(RETENTION.summary).nextElementSibling).toBe(detail.closest('li'))
    expect(screen.queryByRole('complementary', { name: FLAG.summary })).toBeNull()
    expect(rowOf(FLAG.summary).nextElementSibling).toBe(rowOf(RETENTION.summary))
    expect(document.querySelectorAll('[data-in-page-row]')).toHaveLength(1)

    act(() => { void router.navigate(-1) })
    await expectAt(router, '/activity?category=configuration', 'POP')
    await waitFor(() => expect(screen.queryByRole('complementary', { name: RETENTION.summary })).toBeNull())
    expect(screen.getByRole('combobox', { name: 'Filter by category' })).toHaveValue('configuration')
  })

  it('the X pops our entry; after a switch, focus returns to the row of the event that was OPEN (not the first)', async () => {
    const { router } = setup(['/start', '/activity?category=configuration'])
    await feed()
    press(rowButton(FLAG.summary))
    await detailNamed(FLAG.summary)
    press(rowButton(RETENTION.summary))
    const detail = await detailNamed(RETENTION.summary)
    expect(within(detail).getByRole('button', { name: 'Close activity details' })).toHaveFocus()

    press(within(detail).getByRole('button', { name: 'Close activity details' }))
    await expectAt(router, '/activity?category=configuration', 'POP')
    await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull())
    expect(rowButton(RETENTION.summary)).toHaveFocus()

    // Escape with focus inside closes it too, and focus goes back to the row.
    press(rowButton(FLAG.summary))
    await detailNamed(FLAG.summary)
    press(rowButton(RETENTION.summary))
    const again = await detailNamed(RETENTION.summary)
    fireEvent.keyDown(within(again).getByRole('button', { name: 'Close activity details' }), { key: 'Escape' })
    await expectAt(router, '/activity?category=configuration', 'POP')
    await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull())
    expect(rowButton(RETENTION.summary)).toHaveFocus()
  })

  it('a deep link to an event the feed has not loaded renders the detail right ABOVE the feed, read once', async () => {
    const { router } = setup([`/activity?category=configuration&event=${OLDER.id}`])
    const detail = await detailNamed(OLDER.summary)
    const list = await feed()
    expect(list.contains(detail)).toBe(false)
    expect(detail.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(getActivityEvent).toHaveBeenCalledTimes(1)
    expect(getActivityEvent).toHaveBeenCalledWith(PROJECT_ID, OLDER.id)

    // Not pushed by us: Close REPLACES the key away (never pops a foreign entry).
    press(within(detail).getByRole('button', { name: 'Close activity details' }))
    await expectAt(router, '/activity?category=configuration', 'REPLACE')
    await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull())
  })

  it('a deep link to an event the feed holds opens under its row once the feed is there', async () => {
    setup([`/activity?event=${FLAG.id}`])
    await feed()
    const detail = await detailNamed(FLAG.summary)
    await waitFor(() => expect(rowOf(FLAG.summary).nextElementSibling).toBe(detail.closest('li')))
    expect(vi.mocked(getActivityEvent).mock.calls.every(([, id]) => id === FLAG.id)).toBe(true)
  })

  it('?event=../../x says it is not a valid link and reads NOTHING; Close drops the key, the category stays', async () => {
    const { router } = setup(['/activity?category=configuration&event=..%2F..%2Fx'])
    const detail = await detailNamed('Activity event')
    expect(within(detail).getByText('That is not a valid event link.')).toBeInTheDocument()
    await feed()
    expect(getActivityEvent).not.toHaveBeenCalled()

    press(within(detail).getByRole('button', { name: 'Close activity details' }))
    await expectAt(router, '/activity?category=configuration', 'REPLACE')
    await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull())
    expect(getActivityEvent).not.toHaveBeenCalled()
  })

  it.each([
    ['a/b'],
    ['a?b'],
    ['%2E%2E'],
  ])('rejects %s before any request', async (raw) => {
    setup([`/activity?event=${encodeURIComponent(raw)}`])
    await detailNamed('Activity event')
    expect(screen.getByText('That is not a valid event link.')).toBeInTheDocument()
    await feed()
    expect(getActivityEvent).not.toHaveBeenCalled()
  })

  it('a 403 reads "this project\'s activity" with an apostrophe, not the literal &apos;', async () => {
    vi.mocked(getActivityEvent).mockRejectedValue(status(403))
    setup(['/activity'])
    await feed()
    press(rowButton(FLAG.summary))
    const detail = await detailNamed('Activity event')
    expect(await within(detail).findByText("You do not have access to this project's activity.")).toBeInTheDocument()
    expect(detail.textContent).not.toContain('&apos;')
  })

  it('a 404 shows the retention line and the feed keeps working around it', async () => {
    vi.mocked(getActivityEvent).mockRejectedValue(status(404))
    setup(['/activity'])
    await feed()
    press(rowButton(FLAG.summary))
    const detail = await detailNamed('Activity event')
    expect(
      await within(detail).findByText('This event no longer exists. It may have been removed by the retention purge.'),
    ).toBeInTheDocument()
    expect(rowButton(RETENTION.summary)).toBeInTheDocument()
  })
})
