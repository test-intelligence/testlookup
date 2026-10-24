/**
 * W11 on `/releases` (REL-06, REL-05 / REL-07, REL-09's menu item):
 *
 * - List | Calendar rides the URL (`?view=calendar`, `&month=`), keeps every
 *   other key (`open`), and List writes no key; the calendar replaces the
 *   cards from the list the page holds;
 * - the ⋯ menu exports the schedule (.ics / .csv) for the selected project
 *   (All Projects: every project), and a failure says so; Attribution rules is
 *   offered with one project selected only;
 * - a QA lead's Copy phases from… on a planning card opens the release beside
 *   the list with the Copy phases form open in its Release Phases.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import toast from 'react-hot-toast'
import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'
import { useAuthStore } from '@/store/authStore'

vi.mock('@/hooks/useReleases', () => ({ useReleases: vi.fn(), useRelease: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(() => ({ data: { items: [] } })) }))
vi.mock('@/hooks/useCompliancePacks', () => ({
  useCompliancePacks: () => ({ packs: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))
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
vi.mock('@/hooks/useReleasePortfolioTrend', () => ({
  useReleasePortfolioTrend: () => ({ data: undefined, error: undefined }),
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: vi.fn(() => ({ data: [], isLoading: false })),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const projectStoreState = vi.hoisted(() => ({
  activeProjectId: 'proj-1' as string,
  activeProject: { id: 'proj-1', name: 'Project One' } as { id: string; name: string } | null,
  projects: [{ id: 'proj-1', name: 'Project One' }] as Array<{ id: string; name: string }>,
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof projectStoreState) => unknown) => selector(projectStoreState)),
}))

const phase = (id: string, name: string, start: string, end: string) => ({
  id, release_id: 'release-1', name, phase_type: 'qa_testing', status: 'pending', description: null, order_index: 0,
  planned_start: start, planned_end: end, actual_start: null, actual_end: null, exit_criteria: null, notes: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
})

const RELEASE = {
  id: 'release-1', project_id: 'proj-1', project_name: 'Project One', name: '2026.11', version: null,
  description: null, status: 'in_progress', planned_date: '2026-11-20T00:00:00Z', released_at: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', test_run_count: 0,
  phases: [phase('ph-1', 'Payments soak', '2026-11-05T00:00:00Z', '2026-11-11T00:00:00Z')],
}
const PLANNED = {
  ...RELEASE, id: 'release-2', name: '2026.12', status: 'planning', planned_date: '2026-12-15T00:00:00Z', phases: [],
}
const detailOf = (id: string) => ({
  ...(id === PLANNED.id ? PLANNED : RELEASE),
  linked_runs: [], outcomes: [],
  metrics: { total_runs: 0, total_tests: 0, total_passed: 0, total_failed: 0, avg_pass_rate: null },
})

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/releases" element={<><ReleasesPage /><LocationProbe /></>} />
        <Route path="/releases/attribution-rules" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

const search = () => new URLSearchParams(screen.getByTestId('location').textContent?.split('?')[1] ?? '')
const header = (container: HTMLElement) => container.querySelector('[data-page-header]') as HTMLElement

beforeEach(async () => {
  projectStoreState.activeProjectId = 'proj-1'
  projectStoreState.activeProject = { id: 'proj-1', name: 'Project One' }
  const { useRelease, useReleases } = await import('@/hooks/useReleases')
  vi.mocked(useReleases).mockReturnValue({ data: { items: [RELEASE, PLANNED] }, isLoading: false, mutate: vi.fn() } as never)
  vi.mocked(useRelease).mockImplementation(((id: string | null) => ({
    data: id ? detailOf(id) : undefined, isLoading: false, mutate: vi.fn(),
  })) as never)
})

afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.setState({ user: null })
})

describe('List | Calendar', () => {
  it('Calendar writes ?view=calendar, keeps `open`, and replaces the cards with the month', () => {
    const { container } = renderAt('/releases?open=release-1&month=2026-11')
    expect(screen.getByRole('complementary', { name: '2026.11' })).toBeInTheDocument()
    fireEvent.click(within(header(container)).getByRole('button', { name: 'Calendar' }))

    expect(search().get('view')).toBe('calendar')
    expect(search().get('open')).toBe('release-1')
    const primary = container.querySelector('[data-primary]') as HTMLElement
    expect(within(primary).queryAllByRole('article')).toHaveLength(0)
    const calendar = within(primary).getByRole('region', { name: 'Release calendar, November 2026' })
    expect(within(calendar).getAllByRole('button', { name: '2026.11 · Payments soak' })).toHaveLength(2)
    expect(within(header(container)).getByRole('button', { name: 'Calendar' })).toHaveAttribute('aria-pressed', 'true')
    // The pane stays beside it.
    expect(screen.getByRole('complementary', { name: '2026.11' })).toBeInTheDocument()
  })

  it('List drops the keys again; the month moves with Next', () => {
    const { container } = renderAt('/releases?view=calendar&month=2026-11')
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    expect(search().get('month')).toBe('2026-12')
    expect(screen.getByRole('region', { name: 'Release calendar, December 2026' })).toBeInTheDocument()
    fireEvent.click(within(header(container)).getByRole('button', { name: 'List' }))
    expect(search().has('view')).toBe(false)
    expect(search().has('month')).toBe(false)
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })

  it('a bar opens its release beside the calendar', () => {
    renderAt('/releases?view=calendar&month=2026-11')
    fireEvent.click(screen.getAllByRole('button', { name: '2026.11 · Payments soak' })[0])
    expect(search().get('open')).toBe('release-1')
    expect(screen.getByRole('complementary', { name: '2026.11' })).toBeInTheDocument()
  })

  it('is offered in All Projects too', () => {
    projectStoreState.activeProjectId = '__ALL__'
    projectStoreState.activeProject = null
    const { container } = renderAt('/releases')
    expect(within(header(container)).getByRole('button', { name: 'Calendar' })).toBeInTheDocument()
    expect(within(header(container)).queryByRole('button', { name: /New release/ })).toBeNull()
  })
})

describe('the ⋯ menu', () => {
  async function pick(container: HTMLElement, item: string) {
    fireEvent.click(within(header(container)).getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: item }))
  }

  it.each([['Export schedule (.ics)', 'ics'], ['Export schedule (.csv)', 'csv']] as const)(
    '%s downloads the schedule of the selected project',
    async (item, format) => {
      const exportSchedule = vi.spyOn(releasesService, 'exportSchedule').mockResolvedValue(undefined)
      const { container } = renderAt('/releases')
      await pick(container, item)
      await waitFor(() => expect(exportSchedule).toHaveBeenCalledWith(format, 'proj-1'))
      expect(toast.error).not.toHaveBeenCalled()
    },
  )

  it('in All Projects the export covers every project the reader can see; Attribution rules is not offered', async () => {
    projectStoreState.activeProjectId = '__ALL__'
    projectStoreState.activeProject = null
    const exportSchedule = vi.spyOn(releasesService, 'exportSchedule').mockResolvedValue(undefined)
    const { container } = renderAt('/releases')
    fireEvent.click(within(header(container)).getByRole('button', { name: 'More actions' }))
    expect(screen.queryByRole('menuitem', { name: 'Attribution rules' })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export schedule (.ics)' }))
    await waitFor(() => expect(exportSchedule).toHaveBeenCalledWith('ics', null))
  })

  it('a failed export says so', async () => {
    vi.spyOn(releasesService, 'exportSchedule').mockRejectedValue(new Error('500'))
    const { container } = renderAt('/releases')
    await pick(container, 'Export schedule (.csv)')
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not export the release schedule.'))
  })

  it('Attribution rules goes to its page with one project selected', async () => {
    const { container } = renderAt('/releases')
    await pick(container, 'Attribution rules')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/releases\/attribution-rules$/)
  })
})

describe('a planning card\'s Copy phases from… (QA lead)', () => {
  it('opens the release beside the list with the Copy phases form open', async () => {
    useAuthStore.setState({ user: { id: 'lead', role: 'QA_LEAD' } as never })
    renderAt('/releases')
    const card = screen.getByRole('article', { name: /^2026\.12/ })
    fireEvent.click(within(card).getByRole('button', { name: 'Copy phases from…' }))
    const pane = await screen.findByRole('complementary', { name: '2026.12' })
    const form = await within(pane).findByRole('region', { name: 'Copy phases' })
    expect(within(form).getByLabelText('From release')).toHaveDisplayValue('2026.11 · 1 phase')
    expect(search().get('open')).toBe('release-2')
  })

  it('Draft from a PRD… in the open release\'s Release Phases opens the draft form there', async () => {
    useAuthStore.setState({ user: { id: 'lead', role: 'QA_LEAD' } as never })
    renderAt('/releases?open=release-2')
    const pane = screen.getByRole('complementary', { name: '2026.12' })
    fireEvent.click(within(pane).getByRole('button', { name: 'Draft from a PRD…' }))
    expect(within(pane).getByRole('region', { name: 'Draft phases from a PRD' })).toBeInTheDocument()
  })

  it('below QA lead neither the card nor the detail offers them', () => {
    renderAt('/releases?open=release-2')
    expect(screen.queryByRole('button', { name: 'Copy phases from…' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Draft from a PRD…' })).toBeNull()
  })
})
