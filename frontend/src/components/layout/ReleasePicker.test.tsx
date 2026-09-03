/**
 * ReleasePicker — the filter must never lie about what it is filtering.
 *
 * Two failure modes drive these tests, and both render as "the product is
 * broken" rather than as an error:
 *
 *  1. A filter is APPLIED but the control shows "All releases" (or blank).
 *     The user sees fewer runs than they expect and nothing explains it.
 *  2. A filter is DROPPED but the control still shows a release name. Every
 *     page renders empty because the id matches no run in this project.
 *
 * Both come from the same root cause: a release id outliving the project it
 * belongs to — across a project switch, a reload, or a shared link. So most of
 * what follows is about clearing, and about *saying so* when we clear.
 */
import { StrictMode } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReleasePicker } from './ReleasePicker'
import { useReleaseStore } from '@/store/releaseStore'
import { ALL_PROJECTS_ID } from '@/store/projectStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-0000-0000-000000000002'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'
const REL_2 = 'rrrrrrrr-0000-0000-0000-000000000002'

const mocked = vi.hoisted(() => ({
  projectState: { activeProjectId: null as string | null },
  releasesResult: { data: undefined as unknown, isLoading: true, isValidating: true },
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}))

vi.mock('@/store/projectStore', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.projectState) => unknown) =>
      selector ? selector(mocked.projectState) : mocked.projectState,
  }
})

vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => mocked.releasesResult,
}))

vi.mock('react-hot-toast', () => ({ default: mocked.toast }))

/** Surfaces the live query string so URL reflection is asserted on the router's
 *  own state rather than on a spy that could pass while the URL never changed. */
function UrlProbe() {
  const { search, pathname } = useLocation()
  return (
    <>
      <output data-testid="url">{search}</output>
      <output data-testid="path">{pathname}</output>
    </>
  )
}

/** Drives real back-navigation, so history behaviour is asserted through the
 *  router rather than by inspecting the arguments we passed it. */
function BackButton() {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>back</button>
}

function renderPicker(initialUrl = '/') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <ReleasePicker />
      <UrlProbe />
    </MemoryRouter>,
  )
}

/** Releases as the list endpoint returns them, already loaded. */
function loaded(items: Array<{ id: string; name: string }>) {
  mocked.releasesResult = {
    data: { items, total: items.length },
    isLoading: false,
    isValidating: false,
  }
}

function stillLoading() {
  mocked.releasesResult = { data: undefined, isLoading: true, isValidating: true }
}

/** Cached data being re-fetched: SWR reports the PREVIOUS list with
 *  `isLoading` already false. Believing it is the current project's list is how
 *  a valid selection gets dropped. */
function revalidatingWithStale(items: Array<{ id: string; name: string }>) {
  mocked.releasesResult = {
    data: { items, total: items.length },
    isLoading: false,
    isValidating: true,
  }
}

/** Navigates within the app, the way a `<Link>` in a page would. */
function LinkTo({ to }: { to: string }) {
  const navigate = useNavigate()
  return <button onClick={() => navigate(to)}>go</button>
}

const picker = () => screen.getByRole('combobox', { name: /filter by release/i })

describe('ReleasePicker', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    mocked.projectState = { activeProjectId: PROJECT_A }
    mocked.toast.mockClear()
    mocked.toast.error.mockClear()
    loaded([
      { id: REL_1, name: '2026.09 Hardening' },
      { id: REL_2, name: '2026.10 Feature Drop' },
    ])
  })

  /**
   * The picker sits in TopBar, under the `/*` route — so it mounts once and
   * NEVER unmounts. Any "do this on mount" logic is therefore "do this once per
   * browser session", which is the wrong lifetime for reading a URL the user
   * keeps changing. These are the cases that broke when hydration was a
   * one-shot.
   */
  describe('the URL keeps working after the first render', () => {
    it('applies a deep link that arrived before a project had resolved', async () => {
      // The common case for non-admins: projectStore starts on the ALL_PROJECTS
      // sentinel and TopBar resolves a real project asynchronously. A link
      // opened in that window must not be thrown away.
      mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
      const { rerender } = renderPicker(`/?release=${REL_1}`)

      // Nothing to do yet — but crucially, the param must survive.
      await waitFor(() => expect(picker()).toBeDisabled())
      expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_1}`)

      mocked.projectState = { activeProjectId: PROJECT_A }
      rerender(
        <MemoryRouter initialEntries={[`/?release=${REL_1}`]}>
          <ReleasePicker />
          <UrlProbe />
        </MemoryRouter>,
      )

      await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1))
    })

    it('adopts a release from in-app navigation, not just the first load', async () => {
      // Every `<Link>` in the app that carries `?release=` depends on this. If
      // only the first render is read, those links are decorative.
      render(
        <MemoryRouter initialEntries={['/overview']}>
          <ReleasePicker />
          <UrlProbe />
          <LinkTo to={`/runs?release=${REL_2}`} />
        </MemoryRouter>,
      )
      await waitFor(() => expect(picker()).toBeEnabled())

      fireEvent.click(screen.getByRole('button', { name: 'go' }))

      await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBe(REL_2))
      expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_2}`)
    })

    it('does not strip a param it is not yet able to act on', async () => {
      // Deleting the param while no project is pinned destroys the link for
      // good: a reload can no longer recover it.
      mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
      renderPicker(`/?release=${REL_1}`)

      await waitFor(() => expect(picker()).toBeDisabled())
      expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_1}`)
    })

    it('clears the filter when the user navigates back past it', async () => {
      // Back/forward is the third way the URL moves without the picker acting,
      // after deep links and in-app navigation. Landing on a URL with no
      // release must leave no release applied, or the filter outlives the page
      // that carried it.
      render(
        <MemoryRouter initialEntries={['/overview']}>
          <ReleasePicker />
          <UrlProbe />
          <LinkTo to={`/runs?release=${REL_1}`} />
          <BackButton />
        </MemoryRouter>,
      )
      await waitFor(() => expect(picker()).toBeEnabled())

      fireEvent.click(screen.getByRole('button', { name: 'go' }))
      await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1))

      fireEvent.click(screen.getByRole('button', { name: 'back' }))

      await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/overview'))
      expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    })
  })

  it('defaults to no filter, so every page renders as it did before', () => {
    renderPicker()

    // The whole release axis is additive: absent by default, never a sentinel
    // that pages have to special-case.
    expect(picker()).toHaveValue('')
    expect(screen.getByRole('option', { name: 'All releases' })).toBeInTheDocument()
  })

  it('lists the project releases and is usable', () => {
    renderPicker()

    expect(picker()).toBeEnabled()
    expect(screen.getByRole('option', { name: '2026.09 Hardening' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '2026.10 Feature Drop' })).toBeInTheDocument()
  })

  it('is inert in All Projects mode, and says why', () => {
    // Not an edge case: projectStore DEFAULTS here and promotes stale
    // selections back to it, so this is the state users are most often in.
    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    renderPicker()

    expect(picker()).toBeDisabled()
    expect(picker()).toHaveAttribute('title', expect.stringContaining('single project'))
  })

  it('is inert when the project has no releases, and says why', () => {
    loaded([])
    renderPicker()

    expect(picker()).toBeDisabled()
    expect(picker()).toHaveAttribute('title', expect.stringContaining('no releases yet'))
  })

  it('does NOT disable itself while the list is still loading', () => {
    // Disabling on an empty-because-unloaded list would make the control
    // flicker dead on every page load, and would read as "this project has no
    // releases" when the truth is "we have not asked yet".
    stillLoading()
    renderPicker()

    expect(picker()).toBeEnabled()
  })

  it('selecting a release records it against the active project', () => {
    renderPicker()

    fireEvent.change(picker(), { target: { value: REL_1 } })

    expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1)
    // Without the project pairing the selection cannot be validated later.
    expect(useReleaseStore.getState().scopedProjectId).toBe(PROJECT_A)
  })

  it('puts the selection in the URL so the page is shareable', async () => {
    renderPicker()

    fireEvent.change(picker(), { target: { value: REL_1 } })

    await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_1}`))
  })

  it('removes the param when the filter is cleared', async () => {
    renderPicker(`/?release=${REL_1}`)
    await waitFor(() => expect(picker()).toHaveValue(REL_1))

    fireEvent.change(picker(), { target: { value: '' } })

    // A stale `?release=` left behind would re-apply the filter on the next
    // reload — the link would no longer describe the page.
    await waitFor(() => expect(screen.getByTestId('url')).not.toHaveTextContent('release='))
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
  })

  it('preserves other query params when it writes its own', async () => {
    renderPicker('/?tab=failures')

    fireEvent.change(picker(), { target: { value: REL_1 } })

    await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('tab=failures'))
    expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_1}`)
  })

  it('does not put a history entry between the user and the back button', async () => {
    // Changing a filter is not a navigation. If each change pushed an entry,
    // "back" would silently undo filter changes one at a time instead of
    // leaving the page — and a user who changed it five times would have to
    // press back five times to escape.
    render(
      <MemoryRouter initialEntries={['/previous-page', '/?tab=failures']}>
        <ReleasePicker />
        <UrlProbe />
        <BackButton />
      </MemoryRouter>,
    )

    fireEvent.change(picker(), { target: { value: REL_1 } })
    await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent(`release=${REL_1}`))

    fireEvent.click(screen.getByRole('button', { name: 'back' }))

    await waitFor(() =>
      expect(screen.getByTestId('path')).toHaveTextContent('/previous-page'),
    )
  })

  it('applies a release from a deep link', async () => {
    renderPicker(`/?release=${REL_2}`)

    await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBe(REL_2))
    expect(picker()).toHaveValue(REL_2)
  })

  it('drops a deep-linked release the project does not own, and says so', async () => {
    const FOREIGN = 'ffffffff-0000-0000-0000-000000000009'
    renderPicker(`/?release=${FOREIGN}`)

    // Filtering by an id that matches nothing would empty every page with no
    // explanation — the exact "product looks broken" failure.
    await waitFor(() => expect(mocked.toast.error).toHaveBeenCalled())
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(String(mocked.toast.error.mock.calls[0][0])).toMatch(/not in the selected project/i)
  })

  it('keeps showing a selection made before the list loads', () => {
    // Restored from localStorage, or straight off a deep link. With no matching
    // option the select would silently display "All releases" while the filter
    // was in fact applied.
    stillLoading()
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    renderPicker()

    expect(picker()).toHaveValue(REL_1)
    expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1)
  })

  it('clears the filter when the project changes, and announces it', async () => {
    const { rerender } = renderPicker()
    fireEvent.change(picker(), { target: { value: REL_1 } })
    expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1)

    mocked.projectState = { activeProjectId: PROJECT_B }
    loaded([])
    rerender(
      <MemoryRouter>
        <ReleasePicker />
        <UrlProbe />
      </MemoryRouter>,
    )

    await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBeNull())
    expect(mocked.toast).toHaveBeenCalledWith(
      expect.stringMatching(/different project/i),
      expect.anything(),
    )
    // ONE event, ONE explanation. Two effects can clear the same selection in
    // the same commit; when they did, the user got this message AND a
    // contradictory error blaming the release for what was a project change.
    // Asserting the message without the count could not see that.
    expect(mocked.toast).toHaveBeenCalledTimes(1)
    expect(mocked.toast.error).not.toHaveBeenCalled()
  })

  it('does not drop a selection when it has no list at all', async () => {
    // `isLoading` is false for a null SWR key and again after a key change
    // before the fetch starts, so "not loading" alone does not mean "we have a
    // list". Only `data` distinguishes them, and mistaking one for the other
    // drops a valid filter against a list that was never fetched.
    mocked.releasesResult = { data: undefined, isLoading: false, isValidating: false }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    renderPicker()

    await waitFor(() => expect(picker()).toBeEnabled())
    expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1)
    expect(mocked.toast.error).not.toHaveBeenCalled()
    // And it must not claim the project has no releases on that basis either.
    expect(picker()).not.toHaveAttribute('title', expect.stringContaining('no releases yet'))
  })

  it('treats a null active project the same as All Projects', async () => {
    // TopBar renders with `activeProjectId === null` before projects resolve,
    // and projectStore can sit there. A release filter has nothing to resolve
    // against in that state.
    mocked.projectState = { activeProjectId: null }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    renderPicker()

    await waitFor(() => expect(picker()).toBeDisabled())
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
  })

  it('does not drop a selection against a list it is still re-fetching', async () => {
    // SWR hands back the PREVIOUS list with isLoading already false while it
    // revalidates. Treating that as this project's list drops a valid filter —
    // and the user gets an error blaming their selection for a cache timing
    // detail.
    revalidatingWithStale([{ id: REL_2, name: '2026.10 Feature Drop' }])
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    renderPicker()

    await waitFor(() => expect(picker()).toBeEnabled())
    expect(useReleaseStore.getState().activeReleaseId).toBe(REL_1)
    expect(mocked.toast.error).not.toHaveBeenCalled()
  })

  it('reports the real reason when the filter is dropped by widening scope', async () => {
    // Nothing "belonged to a different project" here — the user widened to All
    // Projects. Naming the wrong cause sends them looking for a bug.
    const { rerender } = renderPicker()
    fireEvent.change(picker(), { target: { value: REL_1 } })

    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    rerender(
      <MemoryRouter>
        <ReleasePicker />
        <UrlProbe />
      </MemoryRouter>,
    )

    await waitFor(() => expect(mocked.toast).toHaveBeenCalled())
    expect(String(mocked.toast.mock.calls[0][0])).toMatch(/single project/i)
    expect(String(mocked.toast.mock.calls[0][0])).not.toMatch(/different project/i)
  })

  it('announces a dropped stale selection once, not once per effect pass', async () => {
    // StrictMode double-invokes effects. An announcement read from the render
    // closure fires on both passes, so the user gets the same error twice.
    stillLoading()
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    loaded([{ id: REL_2, name: '2026.10 Feature Drop' }])

    render(
      <StrictMode>
        <MemoryRouter>
          <ReleasePicker />
        </MemoryRouter>
      </StrictMode>,
    )

    await waitFor(() => expect(useReleaseStore.getState().activeReleaseId).toBeNull())
    expect(mocked.toast.error).toHaveBeenCalledTimes(1)
  })

  it('stays quiet when there was no filter to drop', async () => {
    // The overwhelmingly common path. Announcing here would train users to
    // ignore the message that matters.
    renderPicker()
    await waitFor(() => expect(picker()).toBeEnabled())

    expect(mocked.toast).not.toHaveBeenCalled()
    expect(mocked.toast.error).not.toHaveBeenCalled()
  })

  it('does not touch the URL when no filter is set', async () => {
    renderPicker('/?tab=failures')
    await waitFor(() => expect(picker()).toBeEnabled())

    expect(screen.getByTestId('url')).toHaveTextContent('tab=failures')
    expect(screen.getByTestId('url')).not.toHaveTextContent('release=')
  })
})
