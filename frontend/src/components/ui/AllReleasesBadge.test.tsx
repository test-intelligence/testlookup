/**
 * AllReleasesBadge — marks a panel the release filter does not reach.
 *
 * The problem it exists for: the picker sits in the TopBar, so it is present
 * on every route, but only some data is release-scoped. On a page where one
 * card visibly changes when you pick a release, that change teaches the reader
 * the filter works *here* — so they read the numbers beside it as the same
 * release's story. A page where nothing changes at least invites suspicion.
 *
 * The two properties worth guarding are opposites, and both are easy to lose:
 * the badge must be INVISIBLE when no release is selected (or it becomes
 * permanent furniture nobody reads), and it must be PRESENT the moment one is
 * (or the panel it marks silently misrepresents itself).
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocked = vi.hoisted(() => ({
  projectState: { activeProjectId: null as string | null },
}))

vi.mock('@/store/projectStore', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.projectState) => unknown) =>
      selector ? selector(mocked.projectState) : mocked.projectState,
  }
})

import AllReleasesBadge from './AllReleasesBadge'
import { useReleaseStore } from '@/store/releaseStore'
import { ALL_PROJECTS_ID } from '@/store/projectStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-0000-0000-000000000002'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'

describe('AllReleasesBadge', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    mocked.projectState = { activeProjectId: PROJECT_A }
  })

  it('renders nothing when no release is selected', () => {
    const { container } = render(<AllReleasesBadge />)

    // Zero change to the default UI. A badge that is always on stops being
    // read, and would announce a discrepancy that does not exist.
    expect(container).toBeEmptyDOMElement()
  })

  it('appears once a release is selected', () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    render(<AllReleasesBadge />)

    expect(screen.getByText('All releases')).toBeInTheDocument()
  })

  it('carries the supplied reason in its title', () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    render(<AllReleasesBadge reason="Hours saved is a rolling 30-day figure." />)

    // The reason is the difference between "we forgot" and "this cannot be
    // answered per release", which is what a reader needs to know.
    expect(screen.getByTitle(/rolling 30-day figure/)).toBeInTheDocument()
  })

  it('still says something useful without a reason', () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    render(<AllReleasesBadge />)

    expect(screen.getByTitle(/covers all releases/i)).toBeInTheDocument()
  })

  it('stays hidden in All Projects mode, where no release applies', () => {
    // `useReleaseScope` refuses to scope here, so no panel is being filtered
    // and there is no discrepancy to mark. Reading the store directly instead
    // of the scope hook would wrongly badge every panel on the page.
    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { container } = render(<AllReleasesBadge />)

    expect(container).toBeEmptyDOMElement()
  })

  it('stays hidden while the selection belongs to another project', () => {
    // Same reason: between a project switch and reconciliation the filter is
    // not being applied, so nothing is unscoped relative to it.
    mocked.projectState = { activeProjectId: PROJECT_B }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { container } = render(<AllReleasesBadge />)

    expect(container).toBeEmptyDOMElement()
  })
})
