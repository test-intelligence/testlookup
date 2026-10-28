/**
 * W21d ACT-02: Inbox › Activity over the REAL useMyActivity hook (SWR over a
 * mocked activityService). All Projects reads every project and names each
 * row's project; a pinned project sends `project_id`; the count is the
 * server's `total`; a failed read says so (no count, no 0); an entity link in
 * another project switches to it first.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/services/activityService', () => ({
  listMyActivity: vi.fn(),
}))

import ActivityTab from './ActivityTab'
import { listMyActivity } from '@/services/activityService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { makeMyActivityItem, makeMyActivityPage } from '@/test/activityLiveMocks'
import type { Project } from '@/types/projects'

const CHECKOUT = '00000000-0000-4000-8000-0000000000c1'
const BILLING = '00000000-0000-4000-8000-0000000000c2'
const project = (id: string, name: string): Project => ({
  id, name, slug: name.toLowerCase(), is_active: true, created_at: '2026-09-01T00:00:00Z',
})
const PROJECTS = [project(CHECKOUT, 'Checkout'), project(BILLING, 'Billing platform')]

function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="location">{`${loc.pathname}${loc.search}`}</output>
}

function renderTab(url = '/my-failures?tab=activity') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/my-failures" element={<ActivityTab daysOptions={[1, 7, 14, 30]} />} />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </SWRConfig>,
  )
}

const paramsOf = (call = 0) => vi.mocked(listMyActivity).mock.calls[call]?.[0] ?? {}

describe('Inbox › Activity', () => {
  beforeEach(() => {
    vi.mocked(listMyActivity).mockReset()
    useTimeWindowStore.setState({ days: 7 })
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null, projects: PROJECTS })
  })

  it('in All Projects reads every project and shows each involvement, its project and the server total', async () => {
    const mine = makeMyActivityItem({ involvement: 'actor', project_id: CHECKOUT, summary: 'You edited Login works' })
    const mention = makeMyActivityItem({ involvement: 'mentioned', project_id: BILLING, summary: 'Ana commented on Pay works' })
    const assigned = makeMyActivityItem({ involvement: 'assigned', project_id: CHECKOUT, summary: 'Case Refund works was updated' })
    const authored = makeMyActivityItem({ involvement: 'authored', project_id: BILLING, summary: 'Case Invoice works was approved' })
    vi.mocked(listMyActivity).mockResolvedValue(makeMyActivityPage({
      items: [mine, mention, assigned, authored], total: 37, projects: 2, next_cursor: 'c-2',
    }))
    renderTab()

    const feed = await screen.findByRole('feed')
    expect(paramsOf()).not.toHaveProperty('project_id')
    expect(paramsOf()).toMatchObject({ view: 'all' })
    expect(screen.getByText('37 events in the last 7 days')).toBeInTheDocument()
    const rows = within(feed).getAllByTestId('activity-row')
    expect(rows.map((row) => row.querySelector('[data-involvement]')?.textContent)).toEqual([
      'You', 'Mentioned you', 'Assigned to you', 'Your test case',
    ])
    expect(rows.map((row) => row.querySelector('[data-activity-project]')?.textContent)).toEqual([
      'Checkout', 'Billing platform', 'Checkout', 'Billing platform',
    ])
    expect(screen.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true')
  })

  it('a pinned project sends project_id and names no project on the rows', async () => {
    useProjectStore.setState({ activeProjectId: CHECKOUT, activeProject: PROJECTS[0] })
    vi.mocked(listMyActivity).mockResolvedValue(makeMyActivityPage({
      items: [makeMyActivityItem({ project_id: CHECKOUT })], total: 1, projects: 1,
    }))
    renderTab()
    await screen.findByRole('feed')
    expect(paramsOf()).toMatchObject({ project_id: CHECKOUT, view: 'all' })
    expect(document.querySelector('[data-activity-project]')).toBeNull()
    expect(screen.getByText('1 event in the last 7 days')).toBeInTheDocument()
  })

  it('a failed read says so: no count, no 0, no empty-list sentence', async () => {
    vi.mocked(listMyActivity).mockRejectedValue(new Error('boom'))
    renderTab()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the activity.')
    expect(document.querySelector('[data-activity-count]')).toBeNull()
    expect(screen.queryByText(/No activity in the last/)).toBeNull()
  })

  it('switching to Mine reads the mine view and says why it is empty', async () => {
    vi.mocked(listMyActivity).mockImplementation(async (query = {}) => (
      query.view === 'mine'
        ? makeMyActivityPage({ items: [], total: 0, view: 'mine', projects: 2 })
        : makeMyActivityPage({ items: [makeMyActivityItem()], total: 1, projects: 2 })
    ))
    renderTab()
    await screen.findByRole('feed')
    fireEvent.click(screen.getByRole('radio', { name: 'Mine' }))
    expect(await screen.findByText('Nothing you did in the last 7 days.')).toBeInTheDocument()
    expect(vi.mocked(listMyActivity).mock.calls.some(([query]) => query?.view === 'mine')).toBe(true)
    expect(screen.getByTestId('location').textContent).toBe('/my-failures?tab=activity&view=mine')
  })

  it('no membership reads as "not a member", not as a quiet week', async () => {
    vi.mocked(listMyActivity).mockResolvedValue(makeMyActivityPage({ items: [], total: 0, projects: 0 }))
    renderTab()
    expect(await screen.findByText('You are not a member of any project yet.')).toBeInTheDocument()
  })

  it('an entity link in another project switches to that project first, then opens the link', async () => {
    useProjectStore.setState({ activeProjectId: CHECKOUT, activeProject: PROJECTS[0] })
    const entityId = '00000000-0000-4000-8000-0000000000e1'
    const row = makeMyActivityItem({
      involvement: 'mentioned',
      project_id: BILLING,
      entity: { type: 'test_case', id: entityId, label: 'Pay works', href: `/test-management?case=${entityId}` },
    })
    vi.mocked(listMyActivity).mockResolvedValue(makeMyActivityPage({ items: [row], total: 1 }))
    renderTab()
    fireEvent.click(await screen.findByRole('link', { name: 'Pay works' }))
    expect(useProjectStore.getState().activeProjectId).toBe(BILLING)
    expect(screen.getByTestId('location').textContent).toBe(`/test-management?case=${entityId}`)
  })

  it('pages by cursor and renders server text as text', async () => {
    const first = makeMyActivityItem({ summary: '<img src=x onerror="alert(1)"> edited' })
    const older = makeMyActivityItem({ summary: 'An older row' })
    vi.mocked(listMyActivity).mockImplementation(async (query = {}) => (
      query.cursor === 'c-2'
        ? makeMyActivityPage({ items: [older], total: 2, next_cursor: null })
        : makeMyActivityPage({ items: [first], total: 2, next_cursor: 'c-2' })
    ))
    const { container } = renderTab()
    await screen.findByRole('feed')
    expect(container.querySelector('img')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Load older' }))
    expect(await screen.findByText('An older row')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull())
  })
})
