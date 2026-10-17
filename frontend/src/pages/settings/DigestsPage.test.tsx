/**
 * The schedule selector must offer WEEKLY_RETRO — and only when it will work.
 *
 * `WEEKLY_RETRO` existed end-to-end on the backend (ORM enum, beat schedule,
 * retro content renderer) with no way to reach it: the selector offered five
 * schedules and this was not one of them. A feature audit initially filed this
 * as "backend built, UI missing" for five features and was wrong about four —
 * this was the only real gap.
 *
 * The gating half matters as much as the option: `dispatch_scheduled_digests`
 * *silently skips* WEEKLY_RETRO subscriptions when the flag is off, so an
 * ungated option lets a user create a subscription that never delivers and
 * never explains why — the silent-failure class this codebase keeps producing.
 *
 * W09 NTF-03 / NTF-04 (below): Per Release names a release of the top bar
 * project; a row says what it covers and fixes an undeliverable scope in
 * place; Saved Views lists and edits report views in the page; a Daily or
 * Weekly digest and the preview may name one. Nothing new is asked on load.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import DigestsPage from './DigestsPage'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const mockFlags = vi.hoisted(() => ({ byKey: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => mockFlags.byKey[key] ?? false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))

const digestData = vi.hoisted(() => ({
  subs: [] as unknown[],
  views: [] as unknown[],
  viewsCalls: [] as Array<[string | undefined, boolean]>,
  mutateSubs: vi.fn(),
  mutateViews: vi.fn(),
}))
vi.mock('@/hooks/useDigestData', () => ({
  useDigestSubscriptions: () => ({
    subscriptions: digestData.subs, isLoading: false, isError: false, mutate: digestData.mutateSubs,
  }),
  useDigestSavedViews: (projectId: string | undefined, enabled: boolean) => {
    digestData.viewsCalls.push([projectId, enabled])
    return { views: enabled ? digestData.views : [], isLoading: false, isError: false, mutate: digestData.mutateViews }
  },
}))

// NTF-03: the top bar's cached release list.
const releasesHook = vi.hoisted(() => ({ data: { items: [] as unknown[], total: 0 } as unknown, calls: [] as unknown[][] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: (...args: unknown[]) => {
    releasesHook.calls.push(args)
    return { data: releasesHook.data, isLoading: false, isValidating: false }
  },
}))

const createSubscription = vi.hoisted(() => vi.fn().mockResolvedValue({}))
const updateSubscription = vi.hoisted(() => vi.fn().mockResolvedValue({}))
const previewDigest = vi.hoisted(() => vi.fn())
vi.mock('../../services/digestService', async (importOriginal) => ({
  // The pure `parseDigestScope` stays real.
  ...(await importOriginal<typeof import('../../services/digestService')>()),
  createSubscription,
  updateSubscription,
  deleteSubscription: vi.fn(),
  pauseSubscription: vi.fn(),
  resumeSubscription: vi.fn(),
  previewDigest,
}))
const views = vi.hoisted(() => ({
  createSavedView: vi.fn().mockResolvedValue({}),
  updateSavedView: vi.fn().mockResolvedValue({}),
  deleteSavedView: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../../services/savedViewsService', () => ({
  createSavedView: views.createSavedView, updateSavedView: views.updateSavedView, deleteSavedView: views.deleteSavedView,
}))

/** PER_RELEASE is present in every flag state. */
const scheduleSelect = () => screen.getByLabelText('Schedule') as HTMLSelectElement

beforeEach(() => {
  digestData.subs = []
  digestData.views = []
  digestData.viewsCalls = []
  digestData.mutateSubs.mockClear()
  digestData.mutateViews.mockClear()
  releasesHook.data = { items: [], total: 0 }
  releasesHook.calls = []
  createSubscription.mockReset().mockResolvedValue({})
  updateSubscription.mockReset().mockResolvedValue({})
  previewDigest.mockReset()
  views.createSavedView.mockReset().mockResolvedValue({})
  views.updateSavedView.mockReset().mockResolvedValue({})
  useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null })
})

describe('DigestsPage schedule selector', () => {
  beforeEach(() => {
    mockFlags.byKey = {}
  })

  it('offers Weekly Retro when the flag is on', () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).toContain('WEEKLY_RETRO')
  })

  it('hides it when the flag is off, because dispatch would skip it silently', () => {
    mockFlags.byKey = { weekly_retro_digest: false }
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).not.toContain('WEEKLY_RETRO')
  })

  it('sends WEEKLY_RETRO to the API when chosen', async () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />, { wrapper: MemoryRouter })

    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), {
      target: { value: 'Team retro' },
    })
    fireEvent.change(scheduleSelect(), { target: { value: 'WEEKLY_RETRO' } })
    fireEvent.click(screen.getByRole('button', { name: /subscribe/i }))

    await waitFor(() =>
      expect(createSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Team retro', schedule: 'WEEKLY_RETRO' }),
      ),
    )
  })

  it('explains how a retro differs from a weekly digest', () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />, { wrapper: MemoryRouter })

    expect(screen.queryByText(/week in review/i)).not.toBeInTheDocument()
    fireEvent.change(scheduleSelect(), { target: { value: 'WEEKLY_RETRO' } })
    expect(screen.getByText(/week in review/i)).toBeInTheDocument()
  })
})

// P2 item 3: the digest workflow timeline is a collapsed "Pipeline"
// disclosure at the bottom of the page; it renders nothing until opened.
describe('DigestsPage workflow timeline', () => {
  it('starts closed below the tabs and opens on click', () => {
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const pipeline = screen.getByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Digest workflow')).toBeNull()
    const tab = screen.getByRole('tab', { name: 'Preview Digest' })
    expect(tab.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText('Digest workflow')).toBeInTheDocument()
  })
})

// UX redesign P5 (the page template): the tabs are the Tabs primitive bound to
// `?tab=`, under a compact PageHeader with a help topic.
describe('DigestsPage tabs in ?tab=', () => {
  function LocationProbe() {
    const { search } = useLocation()
    return <output data-testid="search">{search}</output>
  }
  function renderAt(url: string) {
    return render(
      <MemoryRouter initialEntries={[url]}>
        <DigestsPage />
        <LocationProbe />
      </MemoryRouter>,
    )
  }

  it('renders the compact template header with a help topic', () => {
    renderAt('/settings/digests')
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Digests & Saved Views' })).toHaveAttribute('data-help-topic', 'administration')
  })

  it('opens Digest Subscriptions by default, in the Tabs primitive', () => {
    renderAt('/settings/digests')
    const tablist = screen.getByRole('tablist', { name: 'Digest sections' })
    expect(tablist).toHaveAttribute('data-tabs')
    expect(screen.getByRole('tab', { name: 'Digest Subscriptions' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByPlaceholderText(/weekly qa summary/i)).toBeInTheDocument()
  })

  it('opens Saved Views from ?tab=saved-views', () => {
    useProjectStore.setState({ activeProjectId: 'p1', activeProject: { id: 'p1', name: 'Checkout' } as never })
    renderAt('/settings/digests?tab=saved-views')
    expect(screen.getByRole('tab', { name: 'Saved Views' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'New view' })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/weekly qa summary/i)).toBeNull()
  })

  it('opens Preview Digest from ?tab=preview', () => {
    renderAt('/settings/digests?tab=preview')
    expect(screen.getByRole('tab', { name: 'Preview Digest' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Generate Preview' })).toBeInTheDocument()
  })

  it('writes the chosen tab to the URL and drops it for the default', () => {
    renderAt('/settings/digests')
    fireEvent.click(screen.getByRole('tab', { name: 'Preview Digest' }))
    expect(screen.getByTestId('search').textContent).toBe('?tab=preview')
    fireEvent.click(screen.getByRole('tab', { name: 'Digest Subscriptions' }))
    expect(screen.getByTestId('search').textContent).toBe('')
  })
})

// ── W09 ───────────────────────────────────────────────────────

const P = '00000000-0000-4000-8000-000000000001'
const R_OLD = '11111111-1111-4111-8111-111111111111'
const R_ACTIVE = '22222222-2222-4222-8222-222222222222'
const R_PROG = '33333333-3333-4333-8333-333333333333'

function release(id: string, name: string, over: Record<string, unknown> = {}) {
  return { id, project_id: P, name, status: 'released', is_active: false, ...over }
}
const RELEASES = [
  release(R_OLD, '2026.08'),
  release(R_PROG, '2026.11', { status: 'in_progress' }),
  release(R_ACTIVE, '2026.10', { status: 'in_progress', is_active: true }),
]

function sub(over: Record<string, unknown> = {}) {
  return {
    id: 'sub-1', user_id: 'u1', project_id: P, saved_view_id: null, name: 'Release digest',
    schedule: 'PER_RELEASE', channel: 'email', is_active: true, is_paused: false,
    scope_type: 'release', scope_value: R_ACTIVE, trigger_filter: null, send_when_unchanged: true,
    report_attachment: false, last_delivered_at: null, next_delivery_at: null, delivery_count: 0,
    created_at: '2026-10-01T00:00:00Z', updated_at: null,
    ...over,
  }
}

function view(id: string, name: string, filters: Record<string, unknown>, over: Record<string, unknown> = {}) {
  return {
    id, user_id: 'u1', project_id: P, name, description: null, page: 'trends', filters,
    is_shared: false, is_default: false, created_at: '2026-10-01T00:00:00Z', updated_at: null, ...over,
  }
}
const REPORT_VIEW = view('v-report', 'Checkout regressions', {
  kind: 'report_view', page: 'trends', window: 7, release_ids: [R_ACTIVE], release_id: R_ACTIVE, suites: ['checkout'],
})
const LEGACY_VIEW = view('v-legacy', 'Old empty view', {}, { page: null })
const LAYOUT_VIEW = view('v-layout', 'Trends layout', { page: 'trends', instances: [{ id: 'w1' }], version: 2 })

const inCheckout = () => useProjectStore.setState({ activeProjectId: P, activeProject: { id: P, name: 'Checkout' } as never })
const row = (id: string) => document.querySelector(`[data-subscription-row="${id}"]`) as HTMLElement

describe('DigestsPage NTF-03: Per Release names a release', () => {
  it('asks nothing new on load: the release list is the top bar\'s cached entry, the views are not read', () => {
    inCheckout()
    render(<DigestsPage />, { wrapper: MemoryRouter })
    expect(releasesHook.calls.length).toBeGreaterThan(0)
    for (const args of releasesHook.calls) expect(args).toEqual([undefined, { cached: true }])
    expect(digestData.viewsCalls.every(([, enabled]) => enabled === false)).toBe(true)
  })

  it('Per Release: the select holds the releases in the model order, Subscribe waits for one, then sends it', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    render(<DigestsPage />, { wrapper: MemoryRouter })
    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), { target: { value: 'Release 2026.10' } })
    fireEvent.change(scheduleSelect(), { target: { value: 'PER_RELEASE' } })

    const select = screen.getByLabelText('Release') as HTMLSelectElement
    expect([...select.options].map(o => o.value)).toEqual(['', R_ACTIVE, R_PROG, R_OLD])
    expect([...select.options].map(o => o.textContent)).toEqual(['Choose a release…', '2026.10', '2026.11', '2026.08'])
    const subscribe = screen.getByRole('button', { name: /subscribe/i })
    expect(subscribe).toBeDisabled()
    fireEvent.click(subscribe)
    expect(createSubscription).not.toHaveBeenCalled()

    fireEvent.change(select, { target: { value: R_ACTIVE } })
    expect(subscribe).toBeEnabled()
    fireEvent.click(subscribe)
    await waitFor(() => expect(createSubscription).toHaveBeenCalledTimes(1))
    expect(createSubscription.mock.calls[0][0]).toEqual({
      project_id: P, name: 'Release 2026.10', schedule: 'PER_RELEASE', channel: 'email',
      scope_type: 'release', scope_value: R_ACTIVE,
    })
  })

  it('All Projects: the select is a sentence and Per Release cannot be subscribed', () => {
    releasesHook.data = { items: RELEASES, total: 3 }
    render(<DigestsPage />, { wrapper: MemoryRouter })
    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), { target: { value: 'x' } })
    fireEvent.change(scheduleSelect(), { target: { value: 'PER_RELEASE' } })
    expect(screen.getByText('Pick a project in the top bar to choose a release.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Release')).toBeNull()
    expect(screen.getByRole('button', { name: /subscribe/i })).toBeDisabled()
  })

  it('a refused create shows the server\'s 422 text in the form', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    createSubscription.mockRejectedValue({ response: { status: 422, data: { detail: 'Choose a release of this project.' } } })
    render(<DigestsPage />, { wrapper: MemoryRouter })
    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), { target: { value: 'x' } })
    fireEvent.change(scheduleSelect(), { target: { value: 'PER_RELEASE' } })
    fireEvent.change(screen.getByLabelText('Release'), { target: { value: R_OLD } })
    fireEvent.click(screen.getByRole('button', { name: /subscribe/i }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Choose a release of this project.')
    expect(alert).toHaveAttribute('data-subscription-error')
  })

  it('a row says what it covers: Per Release · 2026.10 via email', () => {
    inCheckout()
    digestData.subs = [sub({ scope_label: '2026.10' })]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const summary = row('sub-1').querySelector('[data-subscription-summary]') as HTMLElement
    expect(summary.textContent).toMatch(/^Per Release · 2026\.10 via email · Delivered: 0 times$/)
    expect(row('sub-1').querySelector('[data-scope-problem]')).toBeNull()
    // Never the raw release id.
    expect(row('sub-1')).not.toHaveTextContent(R_ACTIVE)
  })

  it('an unscoped Per Release row: the badge, and its inline Save sends the PATCH', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    digestData.subs = [sub({ scope_type: null, scope_value: null, scope_problem: 'unscoped' })]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const r = within(row('sub-1'))
    expect(r.getByText('Not delivering — choose a release')).toHaveAttribute('data-scope-problem', 'unscoped')
    fireEvent.change(r.getByRole('combobox'), { target: { value: R_PROG } })
    fireEvent.click(r.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateSubscription).toHaveBeenCalledTimes(1))
    expect(updateSubscription).toHaveBeenCalledWith('sub-1', { scope_type: 'release', scope_value: R_PROG })
    await waitFor(() => expect(digestData.mutateSubs).toHaveBeenCalled())
  })

  it('a refused fix shows the 422 detail as text in the row', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    updateSubscription.mockRejectedValue({ response: { status: 422, data: { detail: 'Choose a release of this project.' } } })
    digestData.subs = [sub({ scope_type: null, scope_value: null, scope_problem: 'release_missing' })]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const r = within(row('sub-1'))
    expect(r.getByText('Not delivering — its release no longer exists')).toBeInTheDocument()
    fireEvent.change(r.getByRole('combobox'), { target: { value: R_ACTIVE } })
    fireEvent.click(r.getByRole('button', { name: 'Save' }))
    expect(await r.findByRole('alert')).toHaveTextContent('Choose a release of this project.')
  })

  it('an unscoped Per Suite row fixes with a suite name', async () => {
    inCheckout()
    digestData.subs = [sub({ schedule: 'PER_SUITE', scope_type: null, scope_value: null, scope_problem: 'unscoped' })]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const r = within(row('sub-1'))
    expect(r.getByText('Not delivering — choose a suite')).toBeInTheDocument()
    fireEvent.change(r.getByLabelText('Suite name'), { target: { value: '  checkout ' } })
    fireEvent.click(r.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateSubscription).toHaveBeenCalledWith('sub-1', { scope_type: 'suite', scope_value: 'checkout' }))
  })

  it('garbage enrichment fields leave the row standing, with no badge', () => {
    inCheckout()
    digestData.subs = [sub({ scope_label: 7, scope_problem: { x: 1 }, saved_view_name: ['v'] })]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    expect(row('sub-1')).toHaveTextContent('Release digest')
    expect(row('sub-1').querySelector('[data-subscription-summary]')?.textContent).toMatch(/^Per Release via email/)
    expect(row('sub-1').querySelector('[data-scope-problem]')).toBeNull()
  })
})

describe('DigestsPage NTF-04: saved report views', () => {
  it('the tab lists report views and legacy rows, and hides a widget-layout row', () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    digestData.views = [REPORT_VIEW, LEGACY_VIEW, LAYOUT_VIEW]
    render(<MemoryRouter initialEntries={['/settings/digests?tab=saved-views']}><DigestsPage /></MemoryRouter>)
    const report = document.querySelector('[data-digest-view-row="v-report"]') as HTMLElement
    expect(report).toHaveTextContent('Checkout regressions')
    expect(report).toHaveTextContent('Saved on Trends · Releases: 2026.10 · Suites: checkout · Last 7 days')
    expect(document.querySelector('[data-digest-view-row="v-legacy"]')).toHaveTextContent('No filters — edit to add some')
    expect(document.querySelector('[data-digest-view-row="v-layout"]')).toBeNull()
    expect(screen.queryByText('Trends layout')).toBeNull()
  })

  it('New view opens the in-page editor; Save posts the report-view shape', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    render(<MemoryRouter initialEntries={['/settings/digests?tab=saved-views']}><DigestsPage /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'New view' }))
    // An in-page section of the tab (owner rule 2026-10-16), not an overlay.
    const editor = within(screen.getByRole('region', { name: 'New view' }))
    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'Checkout' } })
    fireEvent.click(editor.getByRole('checkbox', { name: '2026.10' }))
    fireEvent.change(editor.getByLabelText('Suites (comma-separated)'), { target: { value: ' checkout ' } })
    fireEvent.click(editor.getByRole('checkbox', { name: 'Shared with the project' }))
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.createSavedView).toHaveBeenCalledTimes(1))
    expect(views.createSavedView).toHaveBeenCalledWith({
      project_id: P,
      name: 'Checkout',
      page: 'trends',
      is_shared: true,
      filters: { kind: 'report_view', page: 'trends', window: 7, release_ids: [R_ACTIVE], release_id: R_ACTIVE, suites: ['checkout'] },
    })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'New view' })).toBeNull())
    expect(digestData.mutateViews).toHaveBeenCalled()
  })

  it('a 1-character name or 51 suites blocks Save with the reason as text', () => {
    inCheckout()
    render(<MemoryRouter initialEntries={['/settings/digests?tab=saved-views']}><DigestsPage /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'New view' }))
    const editor = within(screen.getByRole('region', { name: 'New view' }))
    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'C' } })
    expect(editor.getByText('A name needs 2 to 255 characters.')).toBeInTheDocument()
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    expect(views.createSavedView).not.toHaveBeenCalled()

    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'Checkout' } })
    const suites = Array.from({ length: 51 }, (_, i) => `s${i}`).join(',')
    fireEvent.change(editor.getByLabelText('Suites (comma-separated)'), { target: { value: suites } })
    expect(editor.getByText('At most 50 suites.')).toBeInTheDocument()
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    expect(views.createSavedView).not.toHaveBeenCalled()
  })

  it('Edit turns the row into the editor in place and PATCHes the report-view shape', async () => {
    inCheckout()
    releasesHook.data = { items: RELEASES, total: 3 }
    digestData.views = [LEGACY_VIEW]
    render(<MemoryRouter initialEntries={['/settings/digests?tab=saved-views']}><DigestsPage /></MemoryRouter>)
    fireEvent.click(within(document.querySelector('[data-digest-view-row="v-legacy"]') as HTMLElement).getByRole('button', { name: 'Edit' }))
    const editor = within(screen.getByRole('region', { name: 'Edit view Old empty view' }))
    expect((editor.getByLabelText('Name') as HTMLInputElement).value).toBe('Old empty view')
    fireEvent.change(editor.getByLabelText('Window'), { target: { value: '30' } })
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.updateSavedView).toHaveBeenCalledTimes(1))
    expect(views.updateSavedView).toHaveBeenCalledWith('v-legacy', {
      name: 'Old empty view', page: 'trends', is_shared: false, filters: { kind: 'report_view', page: 'trends', window: 30 },
    })
  })

  it('the subscription picker is for Daily and Weekly only, asks for the views on its click, and sends saved_view_id', async () => {
    inCheckout()
    // The list for a project also carries the reader's own project-less views: never offered.
    const projectless = view('v-mine', 'Mine without a project', { kind: 'report_view', page: 'trends', window: 7 }, { project_id: null })
    digestData.views = [REPORT_VIEW, LEGACY_VIEW, projectless]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    // Weekly (the default) offers it, as a button: nothing read yet.
    expect(screen.getByRole('button', { name: 'Choose a saved view' })).toBeInTheDocument()
    expect(digestData.viewsCalls.every(([, enabled]) => !enabled)).toBe(true)
    fireEvent.change(scheduleSelect(), { target: { value: 'PER_RUN' } })
    expect(screen.queryByRole('button', { name: 'Choose a saved view' })).toBeNull()
    expect(screen.queryByLabelText('Saved view (optional)')).toBeNull()
    fireEvent.change(scheduleSelect(), { target: { value: 'DAILY' } })

    fireEvent.click(screen.getByRole('button', { name: 'Choose a saved view' }))
    expect(digestData.viewsCalls[digestData.viewsCalls.length - 1]).toEqual([P, true])
    const picker = screen.getByLabelText('Saved view (optional)') as HTMLSelectElement
    // Report views only (a legacy row cannot filter a digest), "No view" first.
    expect([...picker.options].map(o => o.textContent)).toEqual(['No view (whole project)', 'Checkout regressions'])
    fireEvent.change(picker, { target: { value: 'v-report' } })
    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), { target: { value: 'Daily checkout' } })
    fireEvent.click(screen.getByRole('button', { name: /subscribe/i }))
    await waitFor(() => expect(createSubscription).toHaveBeenCalledTimes(1))
    expect(createSubscription.mock.calls[0][0]).toMatchObject({ schedule: 'DAILY', saved_view_id: 'v-report', project_id: P })
  })

  it('a row with a view says so; a deleted view says the digest covers the whole project, and Change view clears or sets it', async () => {
    inCheckout()
    digestData.views = [REPORT_VIEW]
    digestData.subs = [
      sub({ id: 'sub-v', schedule: 'WEEKLY', scope_type: 'project', scope_value: null, saved_view_id: 'v-report', saved_view_name: 'Checkout regressions' }),
      sub({ id: 'sub-d', schedule: 'DAILY', scope_type: 'project', scope_value: null, saved_view_id: null, saved_view_name: 'Gone view', scope_problem: 'saved_view_deleted' }),
    ]
    render(<DigestsPage />, { wrapper: MemoryRouter })
    expect(row('sub-v').querySelector('[data-subscription-summary]')?.textContent).toMatch(/^Weekly via email · View: Checkout regressions · /)
    const deleted = within(row('sub-d'))
    expect(deleted.getByText('View deleted — this digest covers the whole project')).toBeInTheDocument()
    expect(row('sub-d').querySelector('[data-subscription-summary]')).not.toHaveTextContent('View:')

    fireEvent.click(deleted.getByRole('button', { name: 'Change view' }))
    const picker = row('sub-d').querySelector('[data-digest-view-select]') as HTMLSelectElement
    fireEvent.change(picker, { target: { value: 'v-report' } })
    fireEvent.click(deleted.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateSubscription).toHaveBeenCalledWith('sub-d', { saved_view_id: 'v-report' }))

    fireEvent.click(within(row('sub-v')).getByRole('button', { name: 'Change view' }))
    const second = row('sub-v').querySelector('[data-digest-view-select]') as HTMLSelectElement
    fireEvent.change(second, { target: { value: '' } })
    fireEvent.click(within(row('sub-v')).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateSubscription).toHaveBeenCalledWith('sub-v', { saved_view_id: null }))
  })

  it('the preview sends saved_view_id and renders what the filtered digest covers', async () => {
    inCheckout()
    digestData.views = [REPORT_VIEW]
    previewDigest.mockResolvedValue({
      project_name: 'Checkout', period: 'weekly', generated_at: '2026-10-16T00:00:00Z', total_runs: 3,
      avg_pass_rate: 90, pass_rate_trend: null, new_regressions: 1, top_blockers: [], top_clusters: [],
      flaky_test_count: 0, release_decisions: [], action_items: [],
      scope: {
        source: 'saved_view', view_id: 'v-report', view_name: 'Checkout regressions', release_ids: [R_ACTIVE],
        release_names: ['2026.10'], suites: ['checkout'], window_days: 7, dropped_release_count: 2,
      },
    })
    render(<MemoryRouter initialEntries={['/settings/digests?tab=preview']}><DigestsPage /></MemoryRouter>)
    fireEvent.change(screen.getByLabelText('Saved view'), { target: { value: 'v-report' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Preview' }))
    await waitFor(() => expect(previewDigest).toHaveBeenCalledWith(P, 'weekly', 'v-report'))
    expect(await screen.findByText('Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 7 days')).toBeInTheDocument()
    expect(screen.getByText('2 releases in this view no longer exist in this project and were left out.')).toBeInTheDocument()
    expect(screen.getByText('Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest.')).toBeInTheDocument()
  })

  it('without a view the preview asks as before; a garbage scope renders the preview without the block', async () => {
    inCheckout()
    previewDigest.mockResolvedValue({
      project_name: 'Checkout', period: 'weekly', generated_at: '2026-10-16T00:00:00Z', total_runs: 3,
      avg_pass_rate: 90, pass_rate_trend: null, new_regressions: 1, top_blockers: [], top_clusters: [],
      flaky_test_count: 0, release_decisions: [], action_items: [],
      scope: { source: 42, view_name: { x: 1 } },
    })
    render(<MemoryRouter initialEntries={['/settings/digests?tab=preview']}><DigestsPage /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Generate Preview' }))
    await waitFor(() => expect(previewDigest).toHaveBeenCalledWith(P, 'weekly'))
    expect(await screen.findByText(/Weekly Digest — Checkout/)).toBeInTheDocument()
    expect(document.querySelector('[data-digest-scope]')).toBeNull()
    expect(screen.queryByText(/Filtered by view/)).toBeNull()
  })
})
