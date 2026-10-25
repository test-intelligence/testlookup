/**
 * W13 TCL-01: the Views row — the chips; Save view → `createSavedView` with
 * `page: 'test_management'` and the filters; the 422 sentence inline; Rename /
 * Share / Delete (asked in the row); a view another member shared has no actions.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SavedView } from '@/services/savedViewsService'

const createSavedView = vi.fn()
const updateSavedView = vi.fn()
const deleteSavedView = vi.fn()
const mutate = vi.fn(() => Promise.resolve(undefined))
let views: SavedView[] = []
const useCaseViews = vi.fn((..._args: unknown[]) => ({ views, error: undefined, isLoading: false, mutate }))

vi.mock('@/hooks/useTestLibrary', () => ({ useCaseViews: (...args: unknown[]) => useCaseViews(...args) }))
vi.mock('@/services/savedViewsService', () => ({
  createSavedView: (...args: unknown[]) => createSavedView(...args),
  updateSavedView: (...args: unknown[]) => updateSavedView(...args),
  deleteSavedView: (...args: unknown[]) => deleteSavedView(...args),
}))
vi.mock('@/store/authStore', () => ({
  useAuthStore: (select: (s: unknown) => unknown) => select({ user: { id: 'u-me' } }),
}))
const PAYMENTS_STORE = { activeProject: { id: 'p-1', name: 'Payments' }, projects: [] }
let projectState: Record<string, unknown> = PAYMENTS_STORE
vi.mock('@/store/projectStore', () => ({
  useProjectStore: (select: (s: unknown) => unknown) => select(projectState),
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

import CaseViewsBar from './CaseViewsBar'
import { CASE_PRESET_IDS, CASE_PRESET_LABELS, EMPTY_CASE_FILTERS, type CaseFilterState } from './caseFilterState'

const PRESETS = CASE_PRESET_IDS.map((id) => ({ id, label: CASE_PRESET_LABELS[id] }))

function view(id: string, name: string, filters: Record<string, unknown>, over: Partial<SavedView> = {}): SavedView {
  return {
    id, user_id: 'u-me', project_id: 'p-1', name, description: null, page: 'test_management',
    filters: { kind: 'test_case_view', ...filters }, is_shared: false, is_default: false,
    created_at: '2026-10-20T10:00:00Z', updated_at: null, ...over,
  }
}

const MINE_B = view('v-b', 'Zeta P1s', { priorities: ['high'] })
const MINE_A = view('v-a', 'Alpha drafts', { statuses: ['draft'] }, { is_shared: true })
const THEIRS = view('v-t', 'Beta smoke', { types: ['smoke'] }, { user_id: 'u-priya', is_shared: true })

const onApply = vi.fn()

function renderBar(over: Partial<{ projectId: string | null; current: CaseFilterState; activePresetId: string | null; canSave: boolean }> = {}) {
  return render(
    <CaseViewsBar
      projectId={over.projectId === undefined ? 'p-1' : over.projectId}
      current={over.current ?? EMPTY_CASE_FILTERS}
      activePresetId={over.activePresetId ?? null}
      presets={PRESETS}
      onApply={onApply}
      canSave={over.canSave ?? true}
    />,
  )
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } })
}

beforeEach(() => {
  createSavedView.mockReset()
  updateSavedView.mockReset()
  deleteSavedView.mockReset()
  mutate.mockClear()
  onApply.mockReset()
  useCaseViews.mockClear()
  views = [MINE_B, THEIRS, MINE_A]
  projectState = PAYMENTS_STORE
})

describe('CaseViewsBar', () => {
  it('lists the presets, then my views, then the shared ones, each by name', () => {
    renderBar({ activePresetId: 'my_drafts' })
    expect(useCaseViews).toHaveBeenCalledWith('p-1')
    const row = document.querySelector('[data-case-views]') as HTMLElement
    const chips = within(row).getAllByRole('button').map((b) => b.textContent)
    expect(chips).toEqual(['My drafts', 'My P0/P1', 'Unautomated', 'AI drafts', 'Alpha draftsShared', 'Zeta P1s', 'Beta smokeShared', 'Save view', 'Manage'])
    expect(screen.getByRole('button', { name: 'My drafts' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'My P0/P1' })).toHaveAttribute('aria-pressed', 'false')
    expect(row.querySelectorAll('[data-shared-dot]')).toHaveLength(2)
  })

  it('applies a preset by REPLACING every filter key', () => {
    renderBar({ current: { ...EMPTY_CASE_FILTERS, q: 'login', types: ['e2e'] } })
    fireEvent.click(screen.getByRole('button', { name: 'My P0/P1' }))
    expect(onApply).toHaveBeenCalledWith({ ...EMPTY_CASE_FILTERS, owner: 'me', priorities: ['critical', 'high'] })
    fireEvent.click(screen.getByRole('button', { name: 'Unautomated' }))
    expect(onApply).toHaveBeenLastCalledWith({ ...EMPTY_CASE_FILTERS, automation: 'not_automated' })
  })

  it('marks a saved view active when the state equals it, and applies its filters', () => {
    renderBar({ current: { ...EMPTY_CASE_FILTERS, priorities: ['high'] } })
    expect(screen.getByRole('button', { name: 'Zeta P1s' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^Beta smoke/ })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: /^Beta smoke/ }))
    expect(onApply).toHaveBeenCalledWith({ ...EMPTY_CASE_FILTERS, types: ['smoke'] })
  })

  it('saves the current filters as a test_management view, shared when asked', async () => {
    createSavedView.mockResolvedValueOnce(view('v-new', 'Flaky P1s', {}))
    const current = { ...EMPTY_CASE_FILTERS, priorities: ['critical', 'high'], owner: 'me' }
    renderBar({ current })
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    const form = screen.getByRole('region', { name: 'Save this view' })
    expect(form.querySelector('#case-view-save-title')).not.toBeNull()
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: '  Flaky P1s ' } })
    fireEvent.click(within(form).getByLabelText('Share with Payments'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(createSavedView).toHaveBeenCalledWith({
      project_id: 'p-1', name: 'Flaky P1s', page: 'test_management',
      filters: { kind: 'test_case_view', priorities: ['critical', 'high'], owner: 'me' }, is_shared: true,
    }, { suppressToast: true }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Save this view' })).toBeNull())
    expect(mutate).toHaveBeenCalled()
  })

  it('renders the server 422 sentence in the Save form', async () => {
    createSavedView.mockRejectedValueOnce(httpError(422, [{ loc: ['body'], msg: 'Pick a project to share this view with its members.', type: 'saved_case_view' }]))
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    const form = screen.getByRole('region', { name: 'Save this view' })
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Mine' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('Pick a project to share this view with its members.')
    // Said once, in the form: the shared client's error toast is suppressed.
    expect(createSavedView.mock.calls[0][1]).toEqual({ suppressToast: true })
  })

  it('refuses a one-character name before sending', () => {
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    const form = screen.getByRole('region', { name: 'Save this view' })
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'x' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('A view name is 2 to 255 characters.')
    expect(createSavedView).not.toHaveBeenCalled()
  })

  it('manages only my views: Rename, Share / Stop sharing, and Delete asked in the row', async () => {
    updateSavedView.mockResolvedValue({})
    deleteSavedView.mockResolvedValue(undefined)
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: 'Manage' }))
    const panel = screen.getByRole('region', { name: 'Manage your views' })
    const rows = panel.querySelectorAll('[data-case-view-manage]')
    expect(Array.from(rows).map((r) => r.getAttribute('data-case-view-manage'))).toEqual(['v-a', 'v-b'])
    // Another member's shared view is apply-only.
    expect(within(panel).queryByText('Beta smoke')).toBeNull()

    fireEvent.click(within(panel).getByRole('button', { name: 'Share Zeta P1s' }))
    await waitFor(() => expect(updateSavedView).toHaveBeenCalledWith('v-b', { is_shared: true }))
    fireEvent.click(within(panel).getByRole('button', { name: 'Stop sharing Alpha drafts' }))
    await waitFor(() => expect(updateSavedView).toHaveBeenLastCalledWith('v-a', { is_shared: false }))

    fireEvent.click(within(panel).getByRole('button', { name: 'Rename Zeta P1s' }))
    const rename = within(panel).getByRole('region', { name: 'Rename the view' })
    fireEvent.change(within(rename).getByLabelText('Name'), { target: { value: 'Zeta highs' } })
    fireEvent.click(within(rename).getByRole('button', { name: 'Rename' }))
    await waitFor(() => expect(updateSavedView).toHaveBeenLastCalledWith('v-b', { name: 'Zeta highs' }, { suppressToast: true }))

    fireEvent.click(await within(panel).findByRole('button', { name: 'Delete Alpha drafts' }))
    expect(deleteSavedView).not.toHaveBeenCalled()
    const strip = within(panel).getByRole('group', { name: 'Delete the view “Alpha drafts”?' })
    fireEvent.click(within(strip).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteSavedView).toHaveBeenCalledWith('v-a'))
    expect(mutate).toHaveBeenCalled()
  })

  it('offers no Manage without a view of mine, and nothing to write without a project or the right', () => {
    views = [THEIRS]
    const { rerender } = renderBar()
    expect(screen.getByRole('button', { name: 'Save view' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Manage' })).toBeNull()
    views = [MINE_A]
    rerender(<CaseViewsBar projectId="p-1" current={EMPTY_CASE_FILTERS} activePresetId={null} presets={PRESETS} onApply={onApply} canSave={false} />)
    expect(screen.queryByRole('button', { name: 'Save view' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Manage' })).toBeNull()
    rerender(<CaseViewsBar projectId={null} current={EMPTY_CASE_FILTERS} activePresetId={null} presets={PRESETS} onApply={onApply} canSave />)
    expect(screen.queryByRole('button', { name: 'Save view' })).toBeNull()
    // The presets work in every scope (owner=me is the reader).
    expect(screen.getByRole('button', { name: 'My drafts' })).toBeInTheDocument()
  })

  it('renders in All Projects with a project store that has no projects yet (no throw)', () => {
    projectState = { activeProject: null }
    views = []
    renderBar({ projectId: null })
    expect(screen.getByRole('button', { name: 'My drafts' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save view' })).toBeNull()
  })

  it('names the share box "this project" when the store has no projects for the selected one', () => {
    projectState = { activeProject: null }
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    expect(within(screen.getByRole('region', { name: 'Save this view' })).getByLabelText('Share with this project')).toBeInTheDocument()
  })

  it('shows a view name as text', () => {
    views = [view('v-x', '<img src=x onerror=alert(1)>', {})]
    const { container } = renderBar()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })
})
