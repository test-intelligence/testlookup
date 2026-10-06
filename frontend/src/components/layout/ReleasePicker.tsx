import { useEffect, useMemo, useRef } from 'react'
import { useNavigationType, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useReleases } from '@/hooks/useReleases'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import type { Release } from '@/types/releases'

/** The query-string key that carries the filter in a shared link. */
export const RELEASE_PARAM = 'release'

/** Asks for runs no release claims — `primary_release_id IS NULL`.
 *
 *  Must equal `UNATTRIBUTED` in `backend/app/core/release_filter.py`. The two
 *  halves are a producer and a consumer of one vocabulary: if they drift, the
 *  backend either 422s it as a malformed UUID or matches no rows, and BOTH read
 *  to the user as "this release has no runs" — the same answer a working filter
 *  gives for an empty release. Indistinguishable, and wrong.
 *
 *  In a healthy system this bucket is empty: every project has an active
 *  release and the attribution ladder always lands somewhere. A run appears
 *  here when the linker failed and swallowed the error, which is otherwise
 *  invisible — so this is an operational view, not a routine filter. */
export const UNATTRIBUTED_RELEASE = 'unattributed'

/** Sentinel for the "no release filter" option. Empty string, because that is
 *  what a `<select>` hands back for an option with no value — mapping it to
 *  `null` in one place beats scattering `|| null` across every read site. */
const NO_RELEASE_VALUE = ''

/**
 * The third global filter, beside the project picker and the time window.
 *
 * Disabled when no single project is pinned
 * -----------------------------------------
 * Releases belong to exactly one project, and cross-project release trains are
 * an explicit non-goal — so there is no list to offer in All Projects mode.
 * That is not a rare corner: `projectStore` DEFAULTS to the All Projects
 * sentinel and promotes stale selections back to it, so it is the state a user
 * is most often in. The control stays visible but disabled and says why, rather
 * than disappearing; a filter that vanishes and reappears as you switch
 * projects is harder to understand than one that is present and explains
 * itself.
 *
 * Why it clears itself, out loud
 * ------------------------------
 * A release id from project A matches no run in project B, so a selection that
 * survived a project switch would filter every page to nothing while the picker
 * still displayed a release name — indistinguishable from the product being
 * broken. `syncToProject` drops it and *reports* that it did, and that report is
 * why there are toasts below: a filter that disappears silently leaves the user
 * looking at changed numbers with nothing connecting them to the change.
 *
 * The corollary is that it must clear itself exactly ONCE per event. Two
 * effects can clear the same selection in the same commit — and did — leaving
 * the user with two toasts giving two different reasons for one project switch.
 * That is why the drop below reads live store state rather than the value its
 * render closure captured.
 */
export function ReleasePicker() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const activeReleaseId = useReleaseStore(s => s.activeReleaseId)
  const setActiveRelease = useReleaseStore(s => s.setActiveRelease)
  const syncToProject = useReleaseStore(s => s.syncToProject)
  const [searchParams, setSearchParams] = useSearchParams()

  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || activeProjectId === null
  const scopeProjectId = isAllProjects ? null : activeProjectId

  // The list is server data, so it stays in SWR — the store holds only the
  // selection.
  const { data, isLoading, isValidating } = useReleases()
  const releases: Release[] = useMemo(() => data?.items ?? [], [data])

  // "We hold a list we can trust for THIS project" — a stricter claim than
  // "we are not loading", and all three clauses carry weight:
  //   `!isLoading`     — the obvious one.
  //   `!isValidating`  — while SWR revalidates it hands back the PREVIOUS
  //                      project's list with `isLoading` already false.
  //   `data !== undefined` — after a key change `isLoading` is false BEFORE the
  //                      fetch starts, and it is false for a null key too.
  // Believing any of those three states would drop a valid selection and blame
  // the user's choice for a cache timing detail. Waiting only ever delays a
  // drop; it never causes a wrong one.
  const listLoaded = !isLoading && !isValidating && data !== undefined

  // Whether the current selection appears in the list we hold. Declared here
  // because both the URL reflection and the stale-drop below gate on it.
  const selectedIsKnown =
    activeReleaseId === UNATTRIBUTED_RELEASE || releases.some(r => r.id === activeReleaseId)

  // (1) Reconcile against the active project. Runs on mount too: a selection
  // restored from localStorage may belong to a project other than the one now
  // active.
  useEffect(() => {
    if (!syncToProject(scopeProjectId)) return
    // Name the actual cause. Widening to All Projects is not "a different
    // project", and telling the user it was sends them looking for a bug.
    toast(
      scopeProjectId === null
        ? 'Release filter cleared — pick a single project to filter by release'
        : 'Release filter cleared — it belonged to a different project',
      { icon: 'ℹ️' },
    )
  }, [scopeProjectId, syncToProject])

  // (2) Keep the URL and the store agreeing, in whichever direction moved.
  //
  // Deliberately ONE effect rather than a hydrate-once pass plus a write-back
  // pass. The picker lives in TopBar, under the `/*` route, so it mounts once
  // and never unmounts: "on mount" logic is really "once per browser session",
  // which is the wrong lifetime for a URL the user keeps changing. A one-shot
  // read ignored every later `?release=` — in-app links, back/forward, and any
  // link opened before a project had resolved — and the write-back pass then
  // deleted the very param it had declined to read.
  //
  // `lastWrittenRef` is what THIS component last put in the URL, so anything
  // else appearing there arrived by navigation and should be adopted. It starts
  // at `null`, meaning "no param, as far as we know", which is what makes a
  // fresh load with no param reflect a persisted selection outward rather than
  // clearing it.
  const lastWrittenRef = useRef<string | null>(null)
  const navigationType = useNavigationType()
  useEffect(() => {
    const fromUrl = searchParams.get(RELEASE_PARAM)

    // With no project pinned there is nothing to resolve the id against. Do
    // NOT consume it and do NOT strip it: deleting a param we merely cannot act
    // on *yet* destroys the link for good, so a reload could not recover it
    // either. Leave it alone and adopt it once a project resolves.
    if (isAllProjects) return

    if (fromUrl !== null && fromUrl !== lastWrittenRef.current) {
      // The URL names a DIFFERENT release — a deep link, an in-app `<Link>`
      // carrying one, or the back button. It wins.
      lastWrittenRef.current = fromUrl
      if (fromUrl !== activeReleaseId) {
        setActiveRelease(fromUrl, scopeProjectId)
      }
      return
    }

    // An ABSENT param clears only when the user went BACK.
    //
    // Two navigations look identical from here — the param is gone either way —
    // and they mean opposite things:
    //
    //   PUSH  (an in-app link, /live -> /coverage): the link simply does not
    //         carry `?release=`. Clearing here was the reported bug — pick a
    //         release, click to another page, filter gone. Falling through
    //         lets the store win and the write-back below republishes the
    //         selection into the new page's URL.
    //
    //   POP   (back/forward): the user is asking for an EARLIER state, and
    //         that state had no release. Keeping it would make the filter
    //         outlive the page that carried it, and Back would stop undoing
    //         what it just did.
    //
    // The store still clears on an explicit "All releases", because that moves
    // the STORE rather than the URL and never reaches this branch.
    // Three conditions, and each one earned its place by breaking something:
    //
    //   `fromUrl === null`  — only an ABSENT param can mean "went back past
    //       it". Without this the branch also fired while the store was being
    //       reflected outward, so clearing the filter cleared it twice and
    //       never removed the param.
    //   `POP`               — a link that omits the param is not a request to
    //       drop the filter. This is the reported bug.
    //   `lastWrittenRef.current !== null` — separates a real Back from the
    //       FIRST render, which React Router also reports as POP. Without it
    //       every page load cleared a persisted selection.
    if (fromUrl === null && navigationType === 'POP' && lastWrittenRef.current !== null) {
      lastWrittenRef.current = null
      if (activeReleaseId !== null) setActiveRelease(null, scopeProjectId)
      return
    }

    // Otherwise the store moved, so reflect it out and keep the address bar a
    // shareable description of what is on screen. `replace` keeps the back
    // button meaningful: changing a filter is not a navigation, and pushing
    // would turn Back into an undo stack for filter changes.
    if (fromUrl === activeReleaseId) return

    // Publish a selection only once it is known to be real. Writing an
    // unvalidated id produces a link to a filter that is about to be dropped —
    // and worse, it lets this effect re-adopt its OWN write as if it had
    // arrived by navigation, resurrecting a selection effect (3) just cleared.
    // Clearing is always safe to reflect, so only the write is gated.
    if (activeReleaseId !== null && !(listLoaded && selectedIsKnown)) return

    const next = new URLSearchParams(searchParams)
    if (activeReleaseId === null) {
      next.delete(RELEASE_PARAM)
    } else {
      next.set(RELEASE_PARAM, activeReleaseId)
    }
    lastWrittenRef.current = activeReleaseId
    setSearchParams(next, { replace: true })
  }, [
    searchParams,
    navigationType,
    activeReleaseId,
    isAllProjects,
    scopeProjectId,
    listLoaded,
    selectedIsKnown,
    setActiveRelease,
    setSearchParams,
  ])

  // (3) A selection the loaded list does not contain is stale — deleted, or
  // from a link into a project that does not own it. Drop it rather than filter
  // by an id nothing matches.
  useEffect(() => {
    if (!listLoaded || isAllProjects) return
    // The Unattributed bucket is a real selection that will never appear in the
    // release list. Without this it would be dropped as "stale" the moment the
    // list loaded, with an error blaming the user's choice.
    if (useReleaseStore.getState().activeReleaseId === UNATTRIBUTED_RELEASE) return
    // Read LIVE store state, not the render closure. Effect (1) may already
    // have cleared this selection in the same commit — on a project switch it
    // does exactly that — and acting on the captured value would clear an
    // already-cleared filter and fire a second, contradictory toast blaming the
    // release for what was really a project change. Reading live state is also
    // what makes this idempotent under StrictMode's double invocation.
    const current = useReleaseStore.getState().activeReleaseId
    if (current === null) return
    if (releases.some(r => r.id === current)) return
    setActiveRelease(null, scopeProjectId)
    toast.error('That release is not in the selected project — filter cleared')
  }, [listLoaded, activeReleaseId, isAllProjects, releases, scopeProjectId, setActiveRelease])

  const disabledReason = isAllProjects
    ? 'Pick a single project to filter by release — releases belong to one project'
    : listLoaded && releases.length === 0
      ? 'This project has no releases yet'
      : undefined

  return (
    <select
      aria-label="Filter by release"
      title={disabledReason}
      disabled={Boolean(disabledReason)}
      className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] disabled:opacity-50 disabled:cursor-not-allowed"
      value={activeReleaseId ?? NO_RELEASE_VALUE}
      onChange={e => {
        const next = e.target.value === NO_RELEASE_VALUE ? null : e.target.value
        setActiveRelease(next, scopeProjectId)
      }}
    >
      {/* Default, and first: no filter is the normal state, and every page
          renders exactly as it did before the release axis existed while it is
          selected. */}
      <option value={NO_RELEASE_VALUE}>All releases</option>
      {releases.map(r => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
      {/* Last, and separated: it is not a release, it is the absence of one.
          Listing it among the releases would invite reading it as one. */}
      <option value={UNATTRIBUTED_RELEASE}>— Unattributed —</option>
      {/* A filter applied before the list arrives — from localStorage or a
          deep link. Without a matching option the `<select>` would fall back to
          showing "All releases" while the filter was in fact applied, which
          misreports what the page is showing. Effect (3) removes the selection
          for real if the loaded list turns out not to contain it. */}
      {activeReleaseId !== null && !selectedIsKnown && (
        <option value={activeReleaseId}>{listLoaded ? 'Unknown release' : 'Loading…'}</option>
      )}
    </select>
  )
}
