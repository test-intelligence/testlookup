/**
 * W27 PAN-02: on /releases a release opens IN THE PAGE, beside the cards, and
 * every form and question opens where its action is. Driven through the REAL
 * release hooks (`useReleases`, `useRelease` on SWR) and a real memory router,
 * so the reads counted are the reads the page makes and every URL read waits
 * for the COMMITTED location in the same `waitFor`:
 *
 *  - a card pushes `?open=<id>` and the pane takes the rail's place (the
 *    rail folds); the release is read once (SWR dedupe), and once too when the
 *    list does not hold it; another card REPLACES; Back closes; a fresh load
 *    of the URL reopens it;
 *  - the key is NOT `?release=`, the top bar's global release filter: that
 *    param neither opens the pane nor is touched by it;
 *  - a malformed id is never read (no request), an unreadable one is the
 *    not-found card, and the cards keep working around it;
 *  - Delete, Remove phase and Set as active release ask inline; only the
 *    strip's button sends the request;
 *  - a typed edit asks before another card (or Close) takes it away;
 *  - New release opens between the health banner and the list; Sync replaces it;
 *  - deleting on a release's own page lands on /releases with the list re-read.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'
import { useAuthStore } from '@/store/authStore'

// A pane behind a SectionErrorBoundary: the real reporter batches for 5 s and
// would post inside a later test (W08 gate).
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
// W19 INT-05: the sync dialog asks whether the project has a GitLab integration (none here).
vi.mock('@/services/gitlabIntegrationService', () => ({
  gitlabIntegrationService: { get: vi.fn(() => Promise.reject(Object.assign(new Error('404'), { response: { status: 404 } }))) },
}))
vi.mock('@/services/releasesService', () => ({
  releasesService: {
    list: vi.fn(), get: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(),
    addPhase: vi.fn(), updatePhase: vi.fn(), deletePhase: vi.fn(), linkRun: vi.fn(), unlinkRun: vi.fn(),
    markOutcome: vi.fn(), getGate: vi.fn(), getGateBaseline: vi.fn(), getPhaseGates: vi.fn(),
    evaluateGate: vi.fn(), evaluatePhaseGate: vi.fn(), activate: vi.fn(), sync: vi.fn(),
  },
}))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate: vi.fn(() => Promise.resolve(undefined)) }))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(() => ({ data: { items: [] } })) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: object) => unknown) => selector({
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }),
}))
vi.mock('@/components/compliance/CompliancePackPanel', () => ({ default: () => null }))
vi.mock('@/hooks/useReleaseGate', () => ({
  useReleaseGate: vi.fn(() => ({ data: null, isLoading: false, mutate: vi.fn() })),
  useReleaseBaseline: vi.fn(() => ({ data: { comparable: false, reason: 'this release has no baseline' } })),
  usePhaseGates: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useProjectCompliancePacks', () => ({
  PROJECT_COMPLIANCE_PACKS_KEY: 'project-compliance-packs',
  useProjectCompliancePacks: () => ({ packs: [], total: 0, isLoading: false, isError: false, refresh: vi.fn() }),
  refreshProjectCompliancePacks: vi.fn(),
}))
vi.mock('@/hooks/useActivityFeed', () => ({
  useActivityFeed: () => ({ events: [], isLoading: false, error: undefined }),
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: vi.fn(() => ({ data: [], isLoading: false })),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const svc = vi.mocked(releasesService)

const A = 'aaaaaaaa-0000-4000-8000-00000000000a'
const B = 'bbbbbbbb-0000-4000-8000-00000000000b'
const FOREIGN = 'cccccccc-0000-4000-8000-00000000000c'
const UNKNOWN = 'dddddddd-0000-4000-8000-00000000000d'

const phase = (id: string, releaseId: string, name: string, status: string) => ({
  id, release_id: releaseId, name, phase_type: 'qa_testing', status, description: null, order_index: 1,
  planned_start: null, planned_end: null, actual_start: null, actual_end: null, exit_criteria: null,
  notes: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
})
const row = (id: string, name: string, version: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, project_id: 'proj-1', project_name: 'Project One', name, version, description: null, status,
  planned_date: null, released_at: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  test_run_count: 0, phases: [] as ReturnType<typeof phase>[], owner: null, gate: null, ...extra,
})
const REL_A = row(A, 'Checkout 2.5', '2.5.0', 'in_progress', {
  description: 'Payment retries',
  phases: [phase('ph-1', A, 'Regression', 'in_progress')],
})
const REL_B = row(B, 'Billing 3.0', '3.0.0', 'planning')
const REL_FOREIGN = row(FOREIGN, 'Foreign 9.0', '9.0.0', 'in_progress', { project_id: 'proj-9', project_name: 'Elsewhere' })
const detailOf = (release: ReturnType<typeof row>) => ({
  ...release,
  linked_runs: [],
  outcomes: [],
  metrics: { total_runs: 0, total_tests: 0, total_passed: 0, total_failed: 0, avg_pass_rate: null },
})
const notFound = () => Object.assign(new Error('Request failed with status code 404'), {
  response: { status: 404, data: { detail: 'Release not found' } },
})

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

function setup(entries: string[]) {
  const router = createMemoryRouter(
    [
      { path: '/start', element: <p>START</p> },
      { path: '/releases', element: <ReleasesPage /> },
      { path: '/releases/:releaseId', element: <ReleasesPage /> },
    ],
    { initialEntries: entries, initialIndex: entries.length - 1 },
  )
  const view = render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  )
  return { router, ...view }
}

async function expectAt(router: ReturnType<typeof createMemoryRouter>, pathname: string, search: string, action?: string) {
  await waitFor(() => {
    expect(router.state.location.pathname).toBe(pathname)
    expect(router.state.location.search).toBe(search)
    if (action) expect(router.state.historyAction).toBe(action)
  })
}

const card = (name: RegExp) => screen.findByRole('article', { name })
const pane = (name: string) => screen.findByRole('complementary', { name })
const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
/** A browser focuses a button it clicks; jsdom's click does not. */
const press = (button: HTMLElement) => {
  button.focus()
  fireEvent.click(button)
}

beforeEach(() => {
  vi.clearAllMocks()
  for (const fn of Object.values(svc)) vi.mocked(fn).mockReset()
  svc.list.mockResolvedValue({ items: [REL_A, REL_B], total: 2 } as never)
  svc.get.mockImplementation((async (id: string) => {
    const hit = [REL_A, REL_B, REL_FOREIGN].find(r => r.id === id)
    if (!hit) throw notFound()
    return detailOf(hit)
  }) as never)
  signInAs('QA_LEAD')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('/releases: a release opens beside the list', () => {
  it('a card pushes ?open=, the pane takes the rail\'s place, and the release is read once', async () => {
    const { router } = setup(['/start', '/releases'])
    const row = () => document.querySelector('[data-master-detail]') as HTMLElement
    await card(/^Checkout 2\.5 2\.5\.0/)
    // Closed: the cards beside the 340 px rail, exactly the old body grid.
    expect(row()).toHaveAttribute('data-master-detail', 'list')
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) 340px')
    expect(screen.getByRole('heading', { name: 'Shipping this week' })).toBeInTheDocument()
    expect(svc.get).not.toHaveBeenCalled()

    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    const opened = await pane('Checkout 2.5 2.5.0')
    await expectAt(router, '/releases', `?open=${A}`, 'PUSH')
    // In the page: no dialog, no modal; beside the cards, in the rail's place.
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(row()).toHaveAttribute('data-master-detail', 'split')
    expect(row().style.gridTemplateColumns).toBe('minmax(0, 1fr) minmax(440px, 46%)')
    expect(row().contains(opened)).toBe(true)
    expect((document.querySelector('[data-primary]') as HTMLElement).contains(opened)).toBe(false)
    expect(screen.queryByRole('heading', { name: 'Shipping this week' })).toBeNull()
    expect(screen.getAllByRole('article')).toHaveLength(2)
    expect(await within(opened).findByRole('heading', { name: 'Linked Test Runs (0)' })).toBeInTheDocument()
    expect(within(opened).getByText('Payment retries')).toBeInTheDocument()
    expect(svc.get.mock.calls).toEqual([[A]])

    // Focus moved into the pane (its Close button); Escape there closes it and pops the entry.
    expect(within(opened).getByRole('button', { name: 'Close release' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    await expectAt(router, '/releases', '', 'POP')
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Shipping this week' })).toBeInTheDocument()
  })

  it('another card switches the pane (replace); Back closes it; the URL alone reopens it', async () => {
    const first = setup(['/start', '/releases'])
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    await pane('Checkout 2.5 2.5.0')
    // Its own card again: nothing new.
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    await expectAt(first.router, '/releases', `?open=${A}`, 'PUSH')

    fireEvent.click(await card(/^Billing 3\.0 3\.0\.0/))
    await pane('Billing 3.0 3.0.0')
    await expectAt(first.router, '/releases', `?open=${B}`, 'REPLACE')
    expect(screen.queryByRole('complementary', { name: 'Checkout 2.5 2.5.0' })).toBeNull()

    // Back lands on the closed list, not on the first release: the switch replaced.
    await act(async () => { await first.router.navigate(-1) })
    await expectAt(first.router, '/releases', '', 'POP')
    expect(screen.queryByRole('complementary')).toBeNull()
    first.unmount()

    // A refresh (or a shared link): the URL alone opens it.
    setup([`/releases?open=${B}`])
    expect(await pane('Billing 3.0 3.0.0')).toBeInTheDocument()
  })

  it("the top bar's release filter (?release=) neither opens the pane nor is touched by it", async () => {
    // `?release=` is the global release filter (ReleasePicker's RELEASE_PARAM) on every page.
    const { router } = setup(['/start', `/releases?release=${B}`])
    await card(/^Checkout 2\.5 2\.5\.0/)
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(svc.get).not.toHaveBeenCalled()

    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    await pane('Checkout 2.5 2.5.0')
    await expectAt(router, '/releases', `?release=${B}&open=${A}`, 'PUSH')
    fireEvent.click(screen.getByRole('button', { name: 'Close release' }))
    await expectAt(router, '/releases', `?release=${B}`, 'POP')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it.each([
    ['../../admin', '?open=../../admin'],
    ['a/b', '?open=a%2Fb'],
    ['an empty value', '?open='],
  ])('a malformed id (%s) is never read: the not-found card, the cards around it', async (_label, search) => {
    const { router } = setup([`/releases${search}`])
    const missing = await pane('Release')
    expect(within(missing).getByText('This release could not be found, or you cannot open it.')).toBeInTheDocument()
    expect(svc.get).not.toHaveBeenCalled()
    expect(screen.getAllByRole('article')).toHaveLength(2)
    // The cards still work: one of them replaces the card with its release.
    fireEvent.click(await card(/^Billing 3\.0 3\.0\.0/))
    await pane('Billing 3.0 3.0.0')
    await expectAt(router, '/releases', `?open=${B}`)
    expect(svc.get.mock.calls).toEqual([[B]])
  })

  it('an id the list does not hold is read once: a readable one opens, a 404 is the not-found card', async () => {
    const readable = setup([`/releases?open=${FOREIGN}`])
    const foreign = await pane('Foreign 9.0 9.0.0')
    expect(await within(foreign).findByRole('heading', { name: 'Linked Test Runs (0)' })).toBeInTheDocument()
    expect(svc.get.mock.calls).toEqual([[FOREIGN]])
    readable.unmount()

    svc.get.mockClear()
    const { router } = setup([`/releases?open=${UNKNOWN}`])
    await waitFor(() => expect(screen.getByText('This release could not be found, or you cannot open it.')).toBeInTheDocument())
    const missing = screen.getByRole('complementary', { name: 'Release' })
    expect(svc.get.mock.calls).toEqual([[UNKNOWN]])
    expect(screen.getAllByRole('article')).toHaveLength(2)
    fireEvent.click(within(missing).getByRole('button', { name: 'Close' }))
    await expectAt(router, '/releases', '')
    expect(screen.queryByRole('complementary')).toBeNull()
  })
})

describe('/releases: the questions are inline, and only the strip sends', () => {
  it('Delete (admin) asks in the pane footer; Cancel sends nothing; Delete deletes, closes the pane and re-reads the list', async () => {
    signInAs('ADMIN')
    const confirm = vi.spyOn(window, 'confirm')
    svc.delete.mockResolvedValue(undefined as never)
    const { router } = setup(['/start', '/releases'])
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    const opened = await pane('Checkout 2.5 2.5.0')
    const actions = opened.querySelector('[data-release-panel-actions]') as HTMLElement
    const del = within(actions).getByRole('button', { name: 'Delete' })

    press(del)
    const QUESTION = 'Delete this release and all its phases? This cannot be undone.'
    let ask = within(opened).getByRole('group', { name: QUESTION })
    expect(follows(ask, actions)).toBe(true)
    expect(within(ask).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(svc.delete).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    expect(within(opened).queryByRole('group', { name: QUESTION })).toBeNull()
    expect(del).toHaveFocus()
    expect(svc.delete).not.toHaveBeenCalled()

    const lists = svc.list.mock.calls.length
    svc.list.mockResolvedValue({ items: [REL_B], total: 1 } as never)
    press(del)
    ask = within(opened).getByRole('group', { name: QUESTION })
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }))
    expect(svc.delete.mock.calls).toEqual([[A]])
    await expectAt(router, '/releases', '', 'POP')
    await waitFor(() => expect(screen.queryByRole('article', { name: /^Checkout 2\.5/ })).toBeNull())
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(svc.list.mock.calls.length).toBeGreaterThan(lists)
    // The deleted release is not read again.
    expect(svc.get.mock.calls).toEqual([[A]])
    expect(confirm).not.toHaveBeenCalled()
  })

  it('Remove phase (admin) asks right under its row; only the strip\'s Remove sends', async () => {
    signInAs('ADMIN')
    svc.deletePhase.mockResolvedValue(undefined as never)
    setup(['/releases'])
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    const opened = await pane('Checkout 2.5 2.5.0')
    fireEvent.click(await within(opened).findByRole('button', { name: 'Remove phase Regression' }))
    const ask = within(opened).getByRole('group', { name: 'Remove phase "Regression"?' })
    expect(document.getElementById('phase-ph-1')?.nextElementSibling).toBe(ask)
    expect(svc.deletePhase).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Remove' }))
    expect(svc.deletePhase.mock.calls).toEqual([[A, 'ph-1']])
    expect(within(opened).queryByRole('group', { name: 'Remove phase "Regression"?' })).toBeNull()
  })

  it('Set as active release asks under its card; only Make active sends', async () => {
    svc.activate.mockResolvedValue({ release_id: B, is_active: true, deactivated: A, changed: true } as never)
    setup(['/releases'])
    const planned = await card(/^Billing 3\.0 3\.0\.0/)
    fireEvent.click(within(planned).getByRole('button', { name: 'Release actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set as active release' }))
    const ask = screen.getByRole('group', { name: 'Make "Billing 3.0" the active release? Runs that no other rule matches will land here.' })
    expect(planned.nextElementSibling).toBe(ask)
    expect(svc.activate).not.toHaveBeenCalled()
    // The menu did not open the card.
    expect(screen.queryByRole('complementary')).toBeNull()
    fireEvent.click(within(ask).getByRole('button', { name: 'Make active' }))
    expect(svc.activate.mock.calls).toEqual([[B]])
  })

  it('a release name renders as text in a question, never as markup', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    svc.list.mockResolvedValue({ items: [REL_A, { ...REL_B, name: hostile }], total: 2 } as never)
    setup(['/releases'])
    const planned = await screen.findByRole('article', { name: new RegExp(`^${hostile.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
    fireEvent.click(within(planned).getByRole('button', { name: 'Release actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set as active release' }))
    const ask = planned.nextElementSibling as HTMLElement
    expect(ask).toHaveTextContent(`Make "${hostile}" the active release?`)
    expect(ask.querySelector('img')).toBeNull()
  })
})

describe('/releases: forms open in the page, one at a time', () => {
  it('Edit opens inside the pane; a typed edit asks before another card takes it; Keep editing keeps the text', async () => {
    const { router } = setup(['/start', '/releases'])
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    const opened = await pane('Checkout 2.5 2.5.0')
    fireEvent.click(within(opened).getByRole('button', { name: 'Edit' }))
    const editor = within(opened).getByRole('region', { name: 'Edit Release' })
    expect(within(editor).getByLabelText('Release Name *')).toHaveFocus()
    fireEvent.change(within(editor).getByLabelText('Description'), { target: { value: 'Payment retries, typed' } })

    fireEvent.click(await card(/^Billing 3\.0 3\.0\.0/))
    const question = within(editor).getByRole('group', { name: 'Discard your changes to "Edit Release"?' })
    await expectAt(router, '/releases', `?open=${A}`)
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }))
    expect(within(editor).getByLabelText('Description')).toHaveValue('Payment retries, typed')
    expect(screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })).toBeInTheDocument()

    // Closing the pane around it asks too.
    fireEvent.click(within(opened).getByRole('button', { name: 'Close release' }))
    fireEvent.click(within(within(editor).getByRole('group', { name: /^Discard your changes/ })).getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })).toBeInTheDocument()

    fireEvent.click(await card(/^Billing 3\.0 3\.0\.0/))
    fireEvent.click(within(within(editor).getByRole('group', { name: /^Discard your changes/ })).getByRole('button', { name: 'Discard changes' }))
    await pane('Billing 3.0 3.0.0')
    await expectAt(router, '/releases', `?open=${B}`, 'REPLACE')
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(svc.update).not.toHaveBeenCalled()
  })

  it('Escape in the editor closes the editor only; Escape in the pane then closes the pane', async () => {
    const { router } = setup(['/start', '/releases'])
    fireEvent.click(await card(/^Checkout 2\.5 2\.5\.0/))
    const opened = await pane('Checkout 2.5 2.5.0')
    press(within(opened).getByRole('button', { name: 'Edit' }))
    fireEvent.keyDown(within(opened).getByLabelText('Release Name *'), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Edit Release' })).toBeNull()
    expect(screen.getByRole('complementary', { name: 'Checkout 2.5 2.5.0' })).toBeInTheDocument()
    // Focus went back to Edit, inside the pane.
    expect(within(opened).getByRole('button', { name: 'Edit' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    await expectAt(router, '/releases', '', 'POP')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('New release opens between the health banner and the list; Sync releases replaces it', async () => {
    const { container } = setup(['/releases'])
    await card(/^Checkout 2\.5 2\.5\.0/)
    const header = container.querySelector('[data-page-header]') as HTMLElement
    fireEvent.click(within(header).getByRole('button', { name: /New release/ }))
    const form = screen.getByRole('region', { name: 'New Release' })
    expect(within(form).getByLabelText('Release Name *')).toHaveFocus()
    const banner = container.querySelector('[data-status-banner]') as HTMLElement
    const body = container.querySelector('[data-master-detail]') as HTMLElement
    expect(follows(banner, form)).toBe(true)
    expect(follows(form, body)).toBe(true)
    expect(body.contains(form)).toBe(false)

    fireEvent.click(within(header).getByRole('button', { name: /Sync releases/ }))
    expect(screen.queryByRole('region', { name: 'New Release' })).toBeNull()
    const sync = screen.getByRole('region', { name: 'Sync releases' })
    expect(follows(banner, sync) && follows(sync, body)).toBe(true)
    expect(document.querySelector('[aria-modal]')).toBeNull()
  })
})

describe("a release's own page", () => {
  it('Delete asks beside its button, then lands on /releases with the list re-read (the deleted card gone)', async () => {
    signInAs('ADMIN')
    svc.delete.mockResolvedValue(undefined as never)
    const { router } = setup([`/releases/${A}`])
    const own = await waitFor(() => {
      const el = document.querySelector('[data-release-page]')
      if (!el) throw new Error('not rendered yet')
      return el as HTMLElement
    })
    await within(own).findByRole('heading', { name: 'Linked Test Runs (0)' })
    expect(screen.queryByRole('complementary')).toBeNull()
    await waitFor(() => expect(svc.list).toHaveBeenCalled())
    const lists = svc.list.mock.calls.length
    svc.list.mockResolvedValue({ items: [REL_B], total: 1 } as never)

    fireEvent.click(within(own).getByRole('button', { name: 'Delete' }))
    const ask = within(own).getByRole('group', { name: 'Delete this release and all its phases? This cannot be undone.' })
    expect(svc.delete).not.toHaveBeenCalled()
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }))
    expect(svc.delete.mock.calls).toEqual([[A]])
    await expectAt(router, '/releases', '')
    await waitFor(() => expect(svc.list.mock.calls.length).toBeGreaterThan(lists))
    await card(/^Billing 3\.0 3\.0\.0/)
    await waitFor(() => expect(screen.queryByRole('article', { name: /^Checkout 2\.5/ })).toBeNull())
  })
})
