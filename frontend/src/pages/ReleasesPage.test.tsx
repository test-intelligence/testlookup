import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'

vi.mock('@/hooks/useReleases', () => ({
  useReleases: vi.fn(),
  useRelease: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

const projectStoreState = vi.hoisted(() => ({
  activeProjectId: 'proj-1' as string,
  activeProject: { id: 'proj-1', name: 'Project One' } as { id: string; name: string } | null,
  projects: [{ id: 'proj-1', name: 'Project One' }] as Array<{ id: string; name: string }>,
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof projectStoreState) => unknown) =>
    selector(projectStoreState)),
}))

describe('ReleasesPage', () => {
  beforeEach(() => {
    projectStoreState.activeProjectId = 'proj-1'
    projectStoreState.activeProject = { id: 'proj-1', name: 'Project One' }
    projectStoreState.projects = [{ id: 'proj-1', name: 'Project One' }]
  })

  it('renders the release summary row with the current hook contracts', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'release-1',
        project_id: 'proj-1',
        project_name: 'Project One',
        name: 'v2.4.0',
        version: '2.4.0',
        description: 'Login revamp',
        status: 'in_progress',
        planned_date: '2026-04-03T00:00:00Z',
        released_at: null,
        created_at: '2026-04-03T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
        phases: [],
        test_run_count: 0,
        linked_runs: [],
        metrics: {
          total_runs: 0,
          total_tests: 0,
          total_passed: 0,
          total_failed: 0,
          avg_pass_rate: null,
        },
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            id: 'release-1',
            project_id: 'proj-1',
            project_name: 'Project One',
            name: 'v2.4.0',
            version: '2.4.0',
            description: 'Login revamp',
            status: 'in_progress',
            planned_date: '2026-04-03T00:00:00Z',
            released_at: null,
            created_at: '2026-04-03T00:00:00Z',
            updated_at: '2026-04-03T00:00:00Z',
            phases: [],
            test_run_count: 0,
            linked_runs: [],
            metrics: {
              total_runs: 0,
              total_tests: 0,
              total_passed: 0,
              total_failed: 0,
              avg_pass_rate: null,
            },
          },
        ],
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    render(
      <MemoryRouter initialEntries={['/releases/release-1']}>
        <Routes>
          <Route path="/releases/:releaseId" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: /Releases/i })).toBeInTheDocument()
    // v2.4.0 now appears in multiple places (release card + summary row) —
    // assert presence without binding to a specific surface.
    expect(screen.getAllByText(/v2\.4\.0/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/In Progress/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'Linked Test Runs (0)' })).toBeVisible()
  })

  it('resolves a release and phase deep link outside the persisted project list', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    projectStoreState.activeProjectId = '__ALL__'
    projectStoreState.activeProject = null
    projectStoreState.projects = [
      { id: 'proj-1', name: 'Project One' },
      { id: 'proj-target', name: 'Target Project' },
    ]
    const target = {
      id: 'release-target',
      project_id: 'proj-target',
      name: 'Target Release',
      version: '3.0.0',
      description: 'Requested by deep link',
      status: 'in_progress',
      planned_date: '2026-05-03T00:00:00Z',
      released_at: null,
      created_at: '2026-05-01T00:00:00Z',
      updated_at: '2026-05-01T00:00:00Z',
      phases: [{
        id: 'phase-target',
        release_id: 'release-target',
        name: 'Target phase',
        phase_type: 'qa_testing',
        status: 'failed',
        description: null,
        order_index: 1,
        planned_start: null,
        planned_end: null,
        actual_start: null,
        actual_end: null,
        exit_criteria: null,
        notes: null,
        created_at: '2026-05-01T00:00:00Z',
        updated_at: '2026-05-01T00:00:00Z',
      }],
      test_run_count: 0,
      linked_runs: [],
      outcomes: [],
      metrics: {
        total_runs: 0,
        total_tests: 0,
        total_passed: 0,
        total_failed: 0,
        avg_pass_rate: null,
      },
    }
    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: target,
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [{
          ...target,
          id: 'release-in-persisted-project',
          project_id: 'proj-1',
          project_name: 'Project One',
          name: 'Persisted Project Release',
          phases: [],
        }],
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    render(
      <MemoryRouter initialEntries={['/releases/release-target#phase-phase-target']}>
        <Routes>
          <Route path="/releases/:releaseId" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Target Release' })).toBeVisible()
    expect(screen.getByText(/1 active across Target Project/)).toBeVisible()
    expect(screen.queryByText('Persisted Project Release')).not.toBeInTheDocument()
    expect(document.getElementById('phase-phase-target')).toHaveTextContent('Target phase')
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' }))

    // P2 item 3: the release workflow timeline is a collapsed "Pipeline"
    // disclosure at the bottom of the detail, after Linked Test Runs.
    const pipeline = screen.getByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Release workflow')).toBeNull()
    const linked = screen.getByRole('heading', { name: /Linked Test Runs/ })
    expect(linked.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText('Release workflow')).toBeInTheDocument()
  })

  it('retries the routed release request after a transient detail failure', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const routedRefetch = vi.fn()
    const listRefetch = vi.fn()
    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      error: new TypeError('Failed to fetch'),
      isLoading: false,
      mutate: routedRefetch,
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [] },
      isLoading: false,
      mutate: listRefetch,
    })

    render(
      <MemoryRouter initialEntries={['/releases/release-target']}>
        <Routes>
          <Route path="/releases/:releaseId" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(routedRefetch).toHaveBeenCalledOnce()
    expect(listRefetch).not.toHaveBeenCalled()
  })

  it('records a reasoned incident from the release side panel', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')
    const refetch = vi.fn()
    const release = {
      id: 'release-1',
      project_id: 'proj-1',
      project_name: 'Project One',
      name: 'v2.4.0',
      version: '2.4.0',
      description: 'Login revamp',
      status: 'released',
      planned_date: '2026-04-03T00:00:00Z',
      released_at: '2026-04-03T00:00:00Z',
      created_at: '2026-04-03T00:00:00Z',
      updated_at: '2026-04-03T00:00:00Z',
      phases: [],
      test_run_count: 0,
    }
    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...release,
        linked_runs: [],
        outcomes: [{
          id: 'outcome-1',
          release_id: 'release-1',
          project_id: 'proj-1',
          outcome_kind: 'rollback',
          reason: 'Database saturation after rollout',
          marked_by_user_id: 'user-1',
          marked_at: '2026-04-04T00:00:00Z',
        }],
        metrics: {
          total_runs: 0,
          total_tests: 0,
          total_passed: 0,
          total_failed: 0,
          avg_pass_rate: null,
        },
      },
      isLoading: false,
      mutate: refetch,
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [release] },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    const markOutcome = vi.spyOn(releasesService, 'markOutcome').mockResolvedValue({
      id: 'outcome-2',
      release_id: 'release-1',
      project_id: 'proj-1',
      outcome_kind: 'incident',
      reason: 'Checkout failures increased after deploy',
      marked_by_user_id: 'user-1',
      marked_at: '2026-04-05T00:00:00Z',
    })

    render(
      <MemoryRouter initialEntries={['/releases']}>
        <Routes>
          <Route path="/releases" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByLabelText(/v2\.4\.0 2\.4\.0/))
    const panel = await screen.findByRole('dialog', { name: 'v2.4.0 2.4.0' })
    expect(within(panel).getByRole('heading', { name: 'Production outcome' })).toBeInTheDocument()
    expect(within(panel).getByText('Database saturation after rollout')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Reason'), {
      target: { value: 'Checkout failures increased after deploy' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Mark incident' }))

    await waitFor(() => {
      expect(markOutcome).toHaveBeenCalledWith(
        'release-1',
        'incident',
        'Checkout failures increased after deploy',
      )
    })
    expect(refetch).toHaveBeenCalled()
  })
})

/**
 * UX redesign P5 item 5: on the list a release's detail is a drill-down in a
 * side panel, not an inline expansion under its card; "Open full page" goes
 * to `/releases/:releaseId`, where the detail IS the page. The two "(planned)"
 * header controls are gone.
 */
describe('ReleasesPage — the release detail in a side panel (P5)', () => {
  const RELEASE = {
    id: 'release-1',
    project_id: 'proj-1',
    project_name: 'Project One',
    name: 'Checkout 2.5',
    version: '2.5.0',
    description: 'Payment retries',
    status: 'in_progress',
    planned_date: '2026-04-10T00:00:00Z',
    released_at: null,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
    phases: [],
    test_run_count: 1,
  }
  const OTHER = { ...RELEASE, id: 'release-2', name: 'Checkout 2.6', version: '2.6.0', description: null, status: 'planning' }
  const detailOf = (id: string) => ({
    ...(id === OTHER.id ? OTHER : RELEASE),
    linked_runs: [],
    outcomes: [],
    metrics: { total_runs: 1, total_tests: 10, total_passed: 9, total_failed: 1, avg_pass_rate: 90 },
  })

  beforeEach(async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useRelease as ReturnType<typeof vi.fn>).mockImplementation((id: string | null) => ({
      data: id ? detailOf(id) : undefined,
      isLoading: false,
      mutate: vi.fn(),
    }))
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [RELEASE, OTHER] },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
  })

  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/releases" element={<ReleasesPage />} />
          <Route path="/releases/:releaseId" element={<><ReleasesPage /><p>full page route</p></>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  const card = (name: RegExp) => screen.getByRole('article', { name })

  it('opens a release in a side panel, not under its card', () => {
    const { container } = renderAt('/releases')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Linked Test Runs/ })).not.toBeInTheDocument()

    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))

    const panel = screen.getByRole('dialog', { name: 'Checkout 2.5 2.5.0' })
    expect(within(panel).getByRole('heading', { name: 'Linked Test Runs (0)' })).toBeInTheDocument()
    expect(within(panel).getByText('Payment retries')).toBeInTheDocument()
    // Nothing expanded inline: the list column holds the cards only.
    const list = container.querySelector('[data-primary]') as HTMLElement
    expect(within(list).getAllByRole('article')).toHaveLength(2)
    expect(within(list).queryByRole('heading', { name: /Linked Test Runs/ })).not.toBeInTheDocument()
    expect(list.contains(panel)).toBe(false)
  })

  it('closes, and opens another release in its place', () => {
    renderAt('/releases')
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    fireEvent.click(screen.getByRole('button', { name: 'Close release' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.click(card(/^Checkout 2\.6 2\.6\.0/))
    expect(screen.getByRole('dialog', { name: 'Checkout 2.6 2.6.0' })).toBeInTheDocument()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('links to the full page and offers Edit and Delete in its footer', () => {
    renderAt('/releases')
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    const panel = screen.getByRole('dialog', { name: 'Checkout 2.5 2.5.0' })
    const full = within(panel).getByRole('link', { name: /Open full page/ })
    expect(full).toHaveAttribute('href', '/releases/release-1')
    expect(within(panel).getByRole('button', { name: 'Delete' })).toBeInTheDocument()

    // Edit opens the release dialog ABOVE the panel (inside it, so the
    // panel's backdrop does not cover it).
    fireEvent.click(within(panel).getByRole('button', { name: 'Edit' }))
    const editor = screen.getByRole('dialog', { name: 'Edit Release' })
    expect(panel.contains(editor)).toBe(true)
    expect(screen.getByDisplayValue('Checkout 2.5')).toBeInTheDocument()

    fireEvent.click(full)
    expect(screen.getByText('full page route')).toBeInTheDocument()
  })

  it('renders the detail as the page itself on /releases/:releaseId, with no panel', () => {
    const { container } = renderAt('/releases/release-1')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(within(primary).getByRole('heading', { name: 'Linked Test Runs (0)' })).toBeInTheDocument()
    expect(within(primary).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    // The card does not toggle the detail away on its own page.
    fireEvent.click(card(/^Checkout 2\.5 2\.5\.0/))
    expect(within(primary).getByRole('heading', { name: 'Linked Test Runs (0)' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('has a compact header with its help topic and New release as the one action', () => {
    const { container } = renderAt('/releases')
    const header = container.querySelector('[data-page-header]') as HTMLElement
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(within(header).getByRole('button', { name: 'Help: Releases' })).toHaveAttribute('data-help-topic', 'releases')
    expect(within(header).getByRole('button', { name: /New release/ })).toBeEnabled()
    expect(within(header).queryByRole('button', { name: /Export schedule|Calendar view/ })).not.toBeInTheDocument()
    expect(screen.queryByText('(planned)')).not.toBeInTheDocument()
    expect(within(header).getAllByRole('button')).toHaveLength(2) // help + New release
  })

  it('says a planned release with no phases is scoped from its detail, with no stub controls', () => {
    renderAt('/releases')
    const planned = card(/^Checkout 2\.6 2\.6\.0/)
    expect(within(planned).getByText('No phases scoped yet')).toBeInTheDocument()
    expect(within(planned).getByText("Add Checkout 2.6's phases in its detail.")).toBeInTheDocument()
    expect(within(planned).queryByRole('button')).not.toBeInTheDocument()
  })
})
