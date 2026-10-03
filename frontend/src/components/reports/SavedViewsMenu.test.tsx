/**
 * VIZ-609: the saved-views manager. Save stores the report scope as a named
 * report view; Open applies a view's scope (minus a release the server says
 * this reader may not apply); my default opens once per tab unless the URL
 * already names a scope; only my own views can be changed.
 */
import type { ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SavedView } from '@/services/savedViewsService'

const service = vi.hoisted(() => ({
  listSavedViews: vi.fn(),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => service)
vi.mock('@/store/authStore', () => ({
  useAuthStore: (select: (s: { user: { id: string } }) => unknown) => select({ user: { id: 'me' } }),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import SavedViewsMenu from './SavedViewsMenu'
import {
  defaultAppliedKey,
  nameTaken,
  orderViews,
  readReportView,
  reportViewFilters,
} from './savedViewsModel'

const view = (over: Partial<SavedView>): SavedView => ({
  id: 'v1',
  user_id: 'me',
  project_id: 'p1',
  name: 'Payments release watch',
  description: null,
  page: 'trends',
  filters: { kind: 'report_view', page: 'trends', release_ids: ['r1'], release_id: 'r1', suites: ['payments'], window: 14 },
  is_shared: false,
  is_default: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
  ...over,
})

const CURRENT = { releaseIds: ['r2'], suiteNames: ['cart', 'payments'], windowDays: 30 }

function renderMenu(onApply = vi.fn(), url = '/trends') {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
    </MemoryRouter>
  )
  render(<SavedViewsMenu page="trends" projectId="p1" current={CURRENT} onApply={onApply} />, { wrapper })
  return onApply
}

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
  service.createSavedView.mockResolvedValue(view({ id: 'new' }))
  service.updateSavedView.mockResolvedValue(view({}))
  window.sessionStorage.clear()
})

describe('savedViewsModel', () => {
  it('saves the report scope as a report view, the one release also under release_id', () => {
    expect(reportViewFilters('trends', CURRENT)).toEqual({
      kind: 'report_view', page: 'trends', window: 30, release_ids: ['r2'], release_id: 'r2', suites: ['cart', 'payments'],
    })
    expect(reportViewFilters('trends', { releaseIds: [], suiteNames: [], windowDays: 7 })).toEqual({
      kind: 'report_view', page: 'trends', window: 7,
    })
  })

  it('reads a view back, dropping a release the server will not apply for this reader', () => {
    expect(readReportView(view({}), 30)).toEqual({ releaseIds: ['r1'], suiteNames: ['payments'], windowDays: 14, releaseNote: null })
    const stale = view({ release: { release_id: 'r1', applied: false, reason: 'Release 2026.08 was archived.' } })
    expect(readReportView(stale, 30)).toEqual({
      releaseIds: [], suiteNames: ['payments'], windowDays: 14, releaseNote: 'Release 2026.08 was archived.',
    })
    // Malformed values are dropped, never applied.
    expect(readReportView(view({ filters: { kind: 'report_view', window: 9999, suites: 'x' } }), 30)).toEqual({
      releaseIds: [], suiteNames: [], windowDays: 30, releaseNote: null,
    })
  })

  it('warns about a duplicate name and lists my default first, then mine, then shared', () => {
    expect(nameTaken([view({})], '  payments RELEASE watch ')).toBe(true)
    expect(nameTaken([view({})], 'Other')).toBe(false)
    const ordered = orderViews(
      [view({ id: 's', name: 'A shared', user_id: 'them' }), view({ id: 'm', name: 'B mine' }), view({ id: 'd', name: 'C default', is_default: true })],
      'me',
    )
    expect(ordered.map((v) => v.id)).toEqual(['d', 'm', 's'])
  })
})

describe('SavedViewsMenu', () => {
  it('saves the current scope as a named, shared, default view', async () => {
    service.listSavedViews.mockResolvedValue([])
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: /Views/ }))
    await screen.findByText(/No saved views for this page yet/)
    fireEvent.change(screen.getByPlaceholderText(/Payments release watch/), { target: { value: 'Cart watch' } })
    fireEvent.click(screen.getByLabelText('Share with the project'))
    fireEvent.click(screen.getByLabelText('My default for this page'))
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    await waitFor(() => expect(service.createSavedView).toHaveBeenCalledTimes(1))
    expect(service.createSavedView).toHaveBeenCalledWith({
      project_id: 'p1',
      name: 'Cart watch',
      page: 'trends',
      filters: reportViewFilters('trends', CURRENT),
      is_shared: true,
      is_default: true,
    })
  })

  it('opens a view by applying its scope; layout rows are not listed', async () => {
    service.listSavedViews.mockResolvedValue([view({}), view({ id: 'layout', name: 'trends view', filters: { page: 'trends', instances: [] } })])
    const onApply = renderMenu()
    fireEvent.click(screen.getByRole('button', { name: /Views/ }))
    await screen.findByText('Payments release watch')
    expect(screen.queryByText('trends view')).toBeNull()
    fireEvent.click(screen.getByText('Payments release watch'))
    expect(onApply).toHaveBeenCalledWith({ releaseIds: ['r1'], suiteNames: ['payments'], windowDays: 14, releaseNote: null })
  })

  it('only my views can be made default, shared or deleted', async () => {
    service.listSavedViews.mockResolvedValue([view({}), view({ id: 'theirs', name: 'Their view', user_id: 'them', is_shared: true })])
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: /Views/ }))
    const mine = (await screen.findByText('Payments release watch')).closest('li') as HTMLElement
    const theirs = screen.getByText('Their view').closest('li') as HTMLElement
    expect(within(theirs).queryByTitle(/Delete/)).toBeNull()
    fireEvent.click(within(mine).getByTitle(/Share with everyone/))
    await waitFor(() => expect(service.updateSavedView).toHaveBeenCalledWith('v1', { is_shared: true }))
    fireEvent.click(within(mine).getByTitle(/Open this view by default/))
    await waitFor(() => expect(service.updateSavedView).toHaveBeenCalledWith('v1', { is_default: true }))
  })

  it('opens my default once per tab, and never over a scope the URL names', async () => {
    service.listSavedViews.mockResolvedValue([view({ is_default: true })])
    const onApply = renderMenu()
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1))
    expect(window.sessionStorage.getItem(defaultAppliedKey('p1', 'trends'))).toBe('1')

    const again = renderMenu(vi.fn())
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(2))
    expect(again).not.toHaveBeenCalled()

    window.sessionStorage.clear()
    const linked = renderMenu(vi.fn(), '/trends?release=r9')
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(3))
    expect(linked).not.toHaveBeenCalled()
  })
})
