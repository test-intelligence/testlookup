/**
 * W12 SRCH-03 (contract §2.2, test 10): the saved searches card — rows, the
 * ⌘ hints on the first three, rename / share / delete in the page, no share on
 * a search saved for all projects, a row another member shared is run-only —
 * and the Save this search form.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import SavedSearchesCard, { SaveSearchForm } from './SavedSearchesCard'
import { shortcutSearches } from './savedSearchesModel'
import type { SavedView } from '@/services/savedViewsService'

const mockPost = vi.fn()
const mockPatch = vi.fn()
const mockDelete = vi.fn()
vi.mock('@/services/http', () => ({
  getData: vi.fn(),
  putData: vi.fn(),
  postData: (...args: unknown[]) => mockPost(...args),
  patchData: (...args: unknown[]) => mockPatch(...args),
  deleteData: (...args: unknown[]) => mockDelete(...args),
}))
vi.mock('@/services/savedViewsService', () => ({ listSavedViews: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

function view(id: string, overrides: Partial<SavedView> = {}): SavedView {
  return {
    id, user_id: 'me', project_id: 'p1', name: `Search ${id}`, description: null, page: 'search',
    filters: { kind: 'search', q: `query ${id}`, scope: 'tests', mode: 'keyword' },
    is_shared: false, is_default: false, created_at: '2026-10-24T10:00:00Z', updated_at: null,
    ...overrides,
  }
}

const VIEWS = [
  view('a', { filters: { kind: 'search', q: 'status:failed checkout', scope: 'tests', mode: 'hybrid' }, is_shared: true }),
  view('b'),
  view('c', { project_id: null }),
  view('d', { user_id: 'someone-else', is_shared: true }),
  view('e'),
]

function renderCard(views: SavedView[] = VIEWS) {
  const onRun = vi.fn()
  const onChanged = vi.fn()
  render(<SavedSearchesCard views={views} userId="me" onRun={onRun} onChanged={onChanged} />)
  return { onRun, onChanged }
}

const row = (id: string) => document.querySelector(`[data-saved-search="${id}"]`) as HTMLElement

beforeEach(() => {
  mockPost.mockReset()
  mockPatch.mockReset()
  mockDelete.mockReset()
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
  mockPatch.mockResolvedValue({})
  mockDelete.mockResolvedValue(undefined)
})

describe('SavedSearchesCard — rows', () => {
  it('name, scope · mode · query, Shared; ⌘1-⌘3 on the first three; no cap', () => {
    const { onRun } = renderCard()
    expect(document.querySelectorAll('[data-saved-search]')).toHaveLength(5)
    expect(row('a')).toHaveTextContent('Search a')
    expect(row('a')).toHaveTextContent('tests · hybrid · status:failed checkout')
    expect(within(row('a')).getByText('Shared')).toBeInTheDocument()
    expect(within(row('a')).getByText('⌘1')).toBeInTheDocument()
    expect(within(row('c')).getByText('⌘3')).toBeInTheDocument()
    expect(within(row('d')).queryByText(/⌘/)).toBeNull()
    fireEvent.click(within(row('a')).getByRole('button', { name: /Search a/ }))
    expect(onRun).toHaveBeenCalledWith({ q: 'status:failed checkout', scope: 'tests', mode: 'hybrid' })
  })

  it('the Shared badge sits beside the name, outside every truncating element; the actions never shrink or wrap', () => {
    renderCard()
    const badge = row('a').querySelector('[data-saved-search-shared]') as HTMLElement
    expect(badge).toHaveTextContent('Shared')
    // Not inside the truncating query line (where it was clipped at 1280), nor inside the truncating name.
    for (let el: HTMLElement | null = badge; el && el !== row('a'); el = el.parentElement) {
      expect(el.classList.contains('truncate'), el.outerHTML.slice(0, 80)).toBe(false)
    }
    expect(badge.className).toContain('whitespace-nowrap')
    expect(badge.parentElement?.className).toContain('flex-none')
    // The query line truncates instead, its full text on hover.
    const meta = Array.from(row('a').querySelectorAll('.truncate')).find((el) => el.textContent?.includes('status:failed checkout')) as HTMLElement
    expect(meta).toHaveAttribute('title', 'tests · hybrid · status:failed checkout')
    expect(meta).not.toHaveTextContent('Shared')
    const stop = within(row('a')).getByRole('button', { name: 'Stop sharing' })
    expect(stop.className).toContain('whitespace-nowrap')
    expect(stop.parentElement?.className).toContain('flex-none')
  })

  it('the query chip carries its own max-w-full truncate (border, padding, ellipsis) as the meta line\'s last flex item', () => {
    renderCard()
    const chip = row('a').querySelector('[data-saved-search-query]') as HTMLElement
    expect(chip.tagName).toBe('CODE')
    expect(chip).toHaveTextContent('status:failed checkout')
    for (const cls of ['max-w-full', 'truncate', 'min-w-0', 'border', 'px-1']) expect(chip.classList.contains(cls), cls).toBe(true)
    // Nothing inside the chip that a truncation could cut through (no nested outlined tokens).
    expect(chip.children).toHaveLength(0)
    // The chip shrinks in a flex line after a non-shrinking "scope · mode · ".
    const line = chip.parentElement as HTMLElement
    expect(line.className).toContain('flex')
    expect(line.className).toContain('min-w-0')
    expect(line.lastElementChild).toBe(chip)
    expect(line.firstElementChild?.className).toContain('flex-none')
    expect(line.firstElementChild?.textContent).toBe('tests · hybrid · ')
  })

  it('the shortcuts are the first three runnable rows, in the server order', () => {
    expect(shortcutSearches([view('x', { filters: { kind: 'search', q: '', scope: 'all', mode: 'keyword' } }), ...VIEWS]).map((s) => s.q))
      .toEqual(['status:failed checkout', 'query b', 'query c'])
  })

  it('the owner\'s row has Rename, Share and Delete; a global row cannot be shared; another member\'s row is run-only', () => {
    renderCard()
    expect(within(row('b')).getByRole('button', { name: 'Rename' })).toHaveClass('btn-sm')
    expect(within(row('b')).getByRole('button', { name: 'Share' })).toBeInTheDocument()
    expect(within(row('a')).getByRole('button', { name: 'Stop sharing' })).toBeInTheDocument()
    expect(within(row('c')).queryByRole('button', { name: /Share/ })).toBeNull()
    expect(within(row('c')).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    for (const name of ['Rename', 'Share', 'Stop sharing', 'Delete']) {
      expect(within(row('d')).queryByRole('button', { name })).toBeNull()
    }
  })

  it('no runnable row, no card', () => {
    const { container } = render(<SavedSearchesCard views={[view('z', { filters: { kind: 'report_view' } })]} userId="me" onRun={vi.fn()} onChanged={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('SavedSearchesCard — in the page', () => {
  it('Rename opens a form under the row; Save PATCHes the name', async () => {
    const { onChanged } = renderCard()
    fireEvent.click(within(row('b')).getByRole('button', { name: 'Rename' }))
    const form = within(row('b')).getByRole('region', { name: 'Rename saved search' })
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Checkout regressions' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith('/api/v1/saved-views/b', { name: 'Checkout regressions' }, { suppressToast: true }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: 'Rename saved search' })).toBeNull()
  })

  it('a refused rename says the server\'s sentence in the form', async () => {
    mockPatch.mockRejectedValue({ response: { status: 422, data: { detail: [{ loc: ['body', 'name'], msg: 'String should have at most 255 characters' }] } } })
    renderCard()
    fireEvent.click(within(row('b')).getByRole('button', { name: 'Rename' }))
    fireEvent.click(within(row('b')).getByRole('button', { name: 'Save' }))
    expect(await within(row('b')).findByRole('alert')).toHaveTextContent('String should have at most 255 characters')
  })

  it('Share / Stop sharing PATCH is_shared', async () => {
    const { onChanged } = renderCard()
    fireEvent.click(within(row('b')).getByRole('button', { name: 'Share' }))
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith('/api/v1/saved-views/b', { is_shared: true }, { suppressToast: true }))
    fireEvent.click(within(row('a')).getByRole('button', { name: 'Stop sharing' }))
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith('/api/v1/saved-views/a', { is_shared: false }, { suppressToast: true }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2))
  })

  it('Delete asks under the row first; only its Delete sends the request; Cancel sends nothing', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const { onChanged } = renderCard()
    fireEvent.click(within(row('e')).getByRole('button', { name: 'Delete' }))
    const strip = row('e').querySelector('[data-inline-confirm]') as HTMLElement
    expect(strip).toHaveTextContent('Delete the saved search “Search e”?')
    fireEvent.click(within(strip).getByRole('button', { name: 'Cancel' }))
    expect(mockDelete).not.toHaveBeenCalled()

    fireEvent.click(within(row('e')).getByRole('button', { name: 'Delete' }))
    fireEvent.click(within(row('e').querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/api/v1/saved-views/e', { suppressToast: true }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('names are text, never markup', () => {
    renderCard([view('x', { name: '<img src=x onerror=alert(1)>' })])
    expect(row('x').querySelector('img')).toBeNull()
    expect(row('x')).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})

describe('SaveSearchForm', () => {
  const SEARCH = { q: 'checkout timeout', scope: 'tests' as const, mode: 'hybrid' as const }

  it('prefills the name, shares with the pinned project, POSTs the saved search', async () => {
    mockPost.mockResolvedValue(view('new'))
    const onSaved = vi.fn()
    render(<SaveSearchForm search={SEARCH} projectId="p1" projectName="Payments" onClose={vi.fn()} onSaved={onSaved} />)
    const form = screen.getByRole('region', { name: 'Save this search' })
    expect(form.querySelector('#save-search-title')).toHaveTextContent('Save this search')
    expect(within(form).getByLabelText('Name')).toHaveValue('checkout timeout')
    fireEvent.click(within(form).getByLabelText('Share with Payments'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/saved-views', {
      project_id: 'p1', name: 'checkout timeout', page: 'search',
      filters: { kind: 'search', q: 'checkout timeout', scope: 'tests', mode: 'hybrid' }, is_shared: true,
    }, { suppressToast: true }))
    expect(toast.success).toHaveBeenCalledWith('Saved “checkout timeout”.')
    expect(onSaved).toHaveBeenCalled()
  })

  it('in All Projects there is no share checkbox; a one-character query prefills "Search: x"', () => {
    render(<SaveSearchForm search={{ ...SEARCH, q: 'x' }} projectId={null} projectName={null} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.queryByLabelText(/Share with/)).toBeNull()
    expect(screen.getByLabelText('Name')).toHaveValue('Search: x')
  })

  it('the server\'s 422 sentence renders in the form', async () => {
    mockPost.mockRejectedValue({ response: { status: 422, data: { detail: [{ loc: ['body'], msg: 'A search saved for all projects cannot be shared: pin a project first.' }] } } })
    render(<SaveSearchForm search={SEARCH} projectId="p1" projectName="Payments" onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A search saved for all projects cannot be shared: pin a project first.')
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('a name under 2 characters cannot be saved', () => {
    render(<SaveSearchForm search={SEARCH} projectId="p1" projectName="Payments" onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'a' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
