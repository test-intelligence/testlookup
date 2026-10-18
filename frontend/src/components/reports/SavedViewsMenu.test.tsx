/**
 * VIZ-609: the saved-views manager. Save stores the report scope as a named
 * report view; Open applies a view's scope (minus a release the server says
 * this reader may not apply); my default opens once per tab unless the URL
 * already names a scope; only my own views can be changed.
 */
import { useEffect, useState, type ComponentProps, type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
// P1: the widget-layout hook, which reads the same rows through its own fetcher.
const http = vi.hoisted(() => ({ getData: vi.fn(), postData: vi.fn(), patchData: vi.fn() }))
vi.mock('@/services/http', () => http)
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (select: (s: { activeProjectId: string }) => unknown) => select({ activeProjectId: 'p1' }),
}))

import { useAnalyticsView } from '@/hooks/useAnalyticsView'
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

function renderMenu(
  onApply = vi.fn(),
  url = '/trends',
  extra: Partial<Pick<ComponentProps<typeof SavedViewsMenu>, 'extraFilters' | 'linked'>> = {},
) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
    </MemoryRouter>
  )
  render(<SavedViewsMenu page="trends" projectId="p1" current={CURRENT} onApply={onApply} linked={false} {...extra} />, { wrapper })
  return onApply
}

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
  for (const fn of Object.values(http)) fn.mockReset()
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
    // The view comes second, for a page that stores more than the scope (VIZ-505).
    expect(onApply).toHaveBeenCalledWith(
      { releaseIds: ['r1'], suiteNames: ['payments'], windowDays: 14, releaseNote: null },
      expect.objectContaining({ id: 'v1', name: 'Payments release watch' }),
    )
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

  // W27b PAN-07: a view's Delete asks in a strip right under its row, inside the popover; the strip's Delete is
  // the only thing that sends; the native prompt is gone.
  describe('W27b: Delete asks in the popover, under the view', () => {
    const openMenu = async () => {
      service.listSavedViews.mockResolvedValue([
        view({}),
        view({ id: 'v2', name: '<img src=x onerror=alert(1)>' }),
        view({ id: 'theirs', name: 'Their view', user_id: 'them', is_shared: true }),
      ])
      renderMenu()
      fireEvent.click(screen.getByRole('button', { name: /Views/ }))
      await screen.findByText('Payments release watch')
    }
    const row = (id: string) => document.querySelector(`li[data-saved-view="${id}"]`) as HTMLElement
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null

    it('Delete opens the strip right under that row; nothing is sent; Cancel closes it; the popover stays', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      await openMenu()
      const trash = within(row('v1')).getByTitle('Delete this view')
      trash.focus()
      fireEvent.click(trash)
      expect(strip()).not.toBeNull()
      expect(row('v1').nextElementSibling).toBe(strip()?.closest('li'))
      expect(strip()?.closest('li')).toHaveAttribute('data-in-page-row')
      expect(strip()?.closest('li')).not.toHaveAttribute('data-saved-view')
      expect(strip()).toHaveAttribute('role', 'group')
      expect((strip() as HTMLElement).querySelector('p')?.textContent).toBe('Delete the view "Payments release watch"?')
      expect(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' })).toHaveFocus()
      expect(service.deleteSavedView).not.toHaveBeenCalled()
      expect(document.querySelectorAll('li[data-saved-view]')).toHaveLength(3)

      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
      expect(strip()).toBeNull()
      expect(trash).toHaveFocus()
      expect(screen.getByRole('dialog', { name: 'Saved views' })).toBeInTheDocument()
      expect(service.deleteSavedView).not.toHaveBeenCalled()
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('the strip\'s Delete sends deleteSavedView(id) once, re-reads the views, and the popover stays open', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      service.deleteSavedView.mockResolvedValue(undefined)
      await openMenu()
      const reads = service.listSavedViews.mock.calls.length
      fireEvent.click(within(row('v1')).getByTitle('Delete this view'))
      fireEvent.click((strip() as HTMLElement).querySelector('[data-inline-confirm-action]') as HTMLElement)
      expect(strip()).toBeNull()
      await waitFor(() => expect(service.deleteSavedView).toHaveBeenCalledTimes(1))
      expect(service.deleteSavedView).toHaveBeenCalledWith('v1')
      await waitFor(() => expect(service.listSavedViews.mock.calls.length).toBeGreaterThan(reads))
      const toast = (await import('react-hot-toast')).default
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Deleted "Payments release watch"'))
      expect(screen.getByRole('dialog', { name: 'Saved views' })).toBeInTheDocument()
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('Escape in the strip cancels the strip only: the popover stays open; one strip at a time; a name is text', async () => {
      await openMenu()
      fireEvent.click(within(row('v1')).getByTitle('Delete this view'))
      fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
      expect(strip()).toBeNull()
      expect(screen.getByRole('dialog', { name: 'Saved views' })).toBeInTheDocument()

      fireEvent.click(within(row('v1')).getByTitle('Delete this view'))
      fireEvent.click(within(row('v2')).getByTitle('Delete this view'))
      expect(document.querySelectorAll('[data-inline-confirm]')).toHaveLength(1)
      expect(row('v2').nextElementSibling).toBe(strip()?.closest('li'))
      expect((strip() as HTMLElement).querySelector('img')).toBeNull()
      expect((strip() as HTMLElement).querySelector('strong')?.textContent).toBe('<img src=x onerror=alert(1)>')
      expect(service.deleteSavedView).not.toHaveBeenCalled()
    })

    it('closing the popover drops the question: reopened, no strip', async () => {
      await openMenu()
      fireEvent.click(within(row('v1')).getByTitle('Delete this view'))
      fireEvent.click(screen.getByRole('button', { name: /Views/ }))
      expect(screen.queryByRole('dialog', { name: 'Saved views' })).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: /Views/ }))
      await screen.findByText('Payments release watch')
      expect(strip()).toBeNull()
    })
  })

  it('opens my default once per tab, and never over a link that describes the page', async () => {
    service.listSavedViews.mockResolvedValue([view({ is_default: true })])
    const onApply = renderMenu()
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1))
    expect(window.sessionStorage.getItem(defaultAppliedKey('p1', 'trends'))).toBe('1')

    const again = renderMenu(vi.fn())
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(2))
    expect(again).not.toHaveBeenCalled()

    window.sessionStorage.clear()
    // The page (`useReportViewsMenu`) says `?release=` describes it.
    const linked = renderMenu(vi.fn(), '/trends?release=r9', { linked: true })
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(3))
    expect(linked).not.toHaveBeenCalled()
  })

  it('VIZ-505: extraFilters are saved beside the scope, which they never replace', async () => {
    service.listSavedViews.mockResolvedValue([])
    renderMenu(vi.fn(), '/explore', { extraFilters: { explore: { metric: 'pass_rate' }, window: 999 } })
    fireEvent.click(screen.getByRole('button', { name: /Views/ }))
    await screen.findByText(/No saved views for this page yet/)
    fireEvent.change(screen.getByPlaceholderText(/Payments release watch/), { target: { value: 'Pass rate by suite' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    await waitFor(() => expect(service.createSavedView).toHaveBeenCalledTimes(1))
    expect(service.createSavedView.mock.calls[0][0].filters).toEqual({
      ...reportViewFilters('trends', CURRENT),
      explore: { metric: 'pass_rate' },
    })
  })

  it('VIZ-505: my default hands onApply the view, and `linked` alone decides, whatever the URL', async () => {
    const mine = view({ is_default: true, filters: { kind: 'report_view', page: 'trends', window: 14, explore: { metric: 'pass_rate' } } })
    service.listSavedViews.mockResolvedValue([mine])
    const onApply = renderMenu()
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1))
    expect(onApply.mock.calls[0][1]).toMatchObject({ id: 'v1', filters: { explore: { metric: 'pass_rate' } } })

    // The page says its own URL keys describe it: no default over them.
    window.sessionStorage.clear()
    const linked = renderMenu(vi.fn(), '/explore?metric=executions', { linked: true })
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(2))
    expect(linked).not.toHaveBeenCalled()

    // And a release in the URL no longer blocks it when the page says it is not linked.
    window.sessionStorage.clear()
    const unlinked = renderMenu(vi.fn(), '/explore?release=r9', { linked: false })
    await waitFor(() => expect(unlinked).toHaveBeenCalledTimes(1))
  })

  it("P1: shares the widget layout's request — one GET when both read the page's saved views", async () => {
    const layout = view({ id: 'layout', name: 'trends view', filters: { page: 'trends', instances: [] } })
    const rows = [view({}), layout]
    http.getData.mockResolvedValue(rows)
    service.listSavedViews.mockResolvedValue(rows)
    // As on the pages: the layout hook asks first, and the header (with the
    // menu) renders once the page's own data has loaded, here well after the
    // layout's rows arrived (and after SWR's dedupe window, 0 ms below).
    function Page() {
      const { loading } = useAnalyticsView('trends')
      const [header, setHeader] = useState(false)
      useEffect(() => {
        if (loading) return
        const timer = setTimeout(() => setHeader(true), 30)
        return () => clearTimeout(timer)
      }, [loading])
      return header ? <SavedViewsMenu page="trends" projectId="p1" current={CURRENT} onApply={vi.fn()} linked={false} variant="ghost" /> : null
    }
    render(
      <MemoryRouter initialEntries={['/trends']}>
        {/* No dedupe window: only the shared entry keeps the menu from asking again. */}
        <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
          <Page />
        </SWRConfig>
      </MemoryRouter>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /Views/ }))
    await screen.findByText('Payments release watch')
    expect(screen.queryByText('trends view')).toBeNull()
    // SWR revalidates rows it already holds on the next animation frame: let
    // that frame (and a fetch it would start) pass before counting.
    await act(() => new Promise<void>((resolve) => requestAnimationFrame(() => setTimeout(resolve, 20))))
    expect(http.getData).toHaveBeenCalledTimes(1)
    expect(http.getData).toHaveBeenCalledWith('/api/v1/saved-views', { params: { project_id: 'p1', page: 'trends' } })
    expect(service.listSavedViews).not.toHaveBeenCalled()
  })

  it("P1: the ghost variant is the report pages' GhostBtn, class for class", async () => {
    service.listSavedViews.mockResolvedValue([])
    const wrapper = ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={['/trends']}>
        <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
      </MemoryRouter>
    )
    render(<SavedViewsMenu page="trends" projectId="p1" current={CURRENT} onApply={vi.fn()} linked={false} variant="ghost" />, { wrapper })
    const trigger = screen.getByRole('button', { name: /Views/ })
    expect(trigger.className).toBe(
      'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] border rounded-md transition-colors disabled:opacity-50',
    )
    expect(trigger.style.borderColor).toBe('var(--color-border)')
    expect(trigger.querySelector('svg')?.getAttribute('class')).toContain('h-3.5 w-3.5')
  })

  it('the accent variant stays the default (the Explorer)', () => {
    service.listSavedViews.mockResolvedValue([])
    renderMenu()
    expect(screen.getByRole('button', { name: /Views/ }).className).toContain('min-h-8')
  })
})
