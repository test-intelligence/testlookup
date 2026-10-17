/**
 * `/quarantine` shows project-level state under a release picker.
 *
 * A quarantine request is a standing decision about a TEST — proposed,
 * approved, released — and `/flaky-quarantine` accepts no release at all. But
 * the release picker sits in the TopBar on every route, so with 2.4.0 selected
 * a reader takes the "Awaiting review" tile as 2.4.0's proposals. It is the
 * project's.
 *
 * This is the milder half of the problem `AllReleasesBadge` exists for — a page
 * where nothing changes at least invites suspicion, unlike one where a
 * neighbouring card visibly moves — but it is still a surface ignoring a filter
 * the header is showing, which S4b says has to be declared.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useFlakyQuarantine', () => ({
  useQuarantineList: () => ({
    requests: [],
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  }),
  useQuarantineStats: () => ({
    stats: undefined,
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  }),
}))

// W09 FLK-02: the Quarantined view reads the policy for its cap warning.
vi.mock('@/hooks/useQuarantinePolicy', () => ({
  useQuarantinePolicy: () => ({ policy: null, error: undefined, isLoading: false, mutate: vi.fn() }),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: true, isQaLead: true, canWrite: true }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn(
    (selector: (s: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
      selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } }),
  ),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

import QuarantinePage from './QuarantinePage'
import { useReleaseStore } from '@/store/releaseStore'

const renderPage = () =>
  render(
    <MemoryRouter>
      <QuarantinePage />
    </MemoryRouter>,
  )

beforeEach(() => {
  useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
})

describe('QuarantinePage — saying that the release filter does not apply', () => {
  it('marks the page when a release is selected', async () => {
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderPage()

    expect(await screen.findByText('All releases')).toBeInTheDocument()
  })

  it('explains why, rather than only flagging it', async () => {
    // A bare marker tells a reader something is different without telling them
    // what, which is the same dead end the "select a project" prompts were.
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderPage()

    const badge = await screen.findByText('All releases')
    expect(badge.getAttribute('title')).toMatch(/standing decision about a test/)
  })

  it('stays silent when no release is selected', async () => {
    // The control. With no release chosen there is no discrepancy to explain,
    // and the page looks exactly as it did before the release axis existed — a
    // badge that always rendered would pass both tests above and turn into
    // furniture people stop reading.
    renderPage()

    expect(await screen.findByText('Flaky Quarantine')).toBeInTheDocument()
    expect(screen.queryByText('All releases')).toBeNull()
  })

  it('carries no Experimental badge any more (W09 FLK-01: quarantine graduated)', async () => {
    // The header's `actions` slot held the Experimental badge beside this one;
    // removing it must not drop the release badge with it.
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    renderPage()

    expect(await screen.findByText('All releases')).toBeInTheDocument()
    expect(screen.queryByText(/experimental/i)).toBeNull()
    expect(document.querySelector('[title^="This feature is experimental"]')).toBeNull()
  })
})
