/**
 * releaseStore — the release filter must never outlive its project.
 *
 * Releases are project-scoped, so a release id from project A matches no run in
 * project B. If a selection survived a project switch, every windowed page
 * would filter by an id nothing matches and render empty — while the picker
 * still showed a release name. The user sees "no data" with nothing explaining
 * why, which is indistinguishable from the product being broken.
 *
 * That is the same class as the 2026-05-15 incident ``projectStore.test.ts``
 * pins, and it is why these tests are mostly about *clearing* rather than
 * selecting.
 *
 * The second theme is that ALL_PROJECTS is the DEFAULT and the RECOVERY state,
 * not an edge case — ``projectStore`` promotes any stale selection back to it —
 * so "no project pinned" is the common path and a release filter must be
 * inert there.
 */
import { beforeEach, describe, expect, it } from 'vitest'

async function loadStore() {
  localStorage.clear()
  const mod = await import('./releaseStore')
  mod.useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
  return mod.useReleaseStore
}

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-0000-0000-000000000002'
const RELEASE_1 = 'rrrrrrrr-0000-0000-0000-000000000009'

describe('releaseStore', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to no filter, so every page behaves as it did before', async () => {
    const store = await loadStore()
    // This is what makes the release axis shippable without touching any
    // page's default rendering: absent, not "all", not a sentinel.
    expect(store.getState().activeReleaseId).toBeNull()
  })

  it('records which project a selection belongs to', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    expect(store.getState().activeReleaseId).toBe(RELEASE_1)
    // Without this the selection cannot be validated after a reload — there
    // would be no way to know which project it came from.
    expect(store.getState().scopedProjectId).toBe(PROJECT_A)
  })

  it('drops the selection when the project changes', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    const dropped = store.getState().syncToProject(PROJECT_B)

    expect(dropped).toBe(true)
    expect(store.getState().activeReleaseId).toBeNull()
    expect(store.getState().scopedProjectId).toBeNull()
  })

  it('reports that it dropped one, so the UI can say so', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    // A filter that vanishes silently is its own confusion: the user set it,
    // the results changed, and nothing connects the two.
    expect(store.getState().syncToProject(PROJECT_B)).toBe(true)
    // Second call has nothing to drop and must say so.
    expect(store.getState().syncToProject(PROJECT_B)).toBe(false)
  })

  it('drops the selection in ALL_PROJECTS mode', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    // The caller maps the ALL_PROJECTS sentinel to null. Releases cannot be
    // enumerated across projects, so a filter cannot apply — and this is the
    // state the product DEFAULTS to, not a corner.
    const dropped = store.getState().syncToProject(null)

    expect(dropped).toBe(true)
    expect(store.getState().activeReleaseId).toBeNull()
  })

  it('leaves the selection alone while the project is unchanged', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    const dropped = store.getState().syncToProject(PROJECT_A)

    expect(dropped).toBe(false)
    expect(store.getState().activeReleaseId).toBe(RELEASE_1)
  })

  it('is a no-op when nothing is selected', async () => {
    const store = await loadStore()

    // The overwhelmingly common path: no filter set. It must not report a
    // drop, or the UI would announce losing something the user never chose.
    expect(store.getState().syncToProject(PROJECT_A)).toBe(false)
    expect(store.getState().syncToProject(null)).toBe(false)
    expect(store.getState().activeReleaseId).toBeNull()
  })

  it('clears the project scope when the release is cleared', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)
    store.getState().setActiveRelease(null, PROJECT_A)

    expect(store.getState().activeReleaseId).toBeNull()
    // A dangling scope with no selection would make the next syncToProject
    // compare against a project nothing is scoped to.
    expect(store.getState().scopedProjectId).toBeNull()
  })

  it('persists the project alongside the release, never the release alone', async () => {
    const store = await loadStore()
    store.getState().setActiveRelease(RELEASE_1, PROJECT_A)

    const raw = localStorage.getItem('tl.release-filter')
    expect(raw).toBeTruthy()
    const persisted = JSON.parse(raw as string).state

    expect(persisted.activeReleaseId).toBe(RELEASE_1)
    // A restored id with no record of its project cannot be validated, and
    // would silently filter whichever project happened to be active on the
    // next load.
    expect(persisted.scopedProjectId).toBe(PROJECT_A)
  })

  it('a restored stale selection is dropped on the first sync', async () => {
    // Simulates the real failure: user filters project A, closes the tab,
    // reopens on project B. Without the pairing check the page would render
    // empty with a release name still showing in the picker.
    localStorage.setItem(
      'tl.release-filter',
      JSON.stringify({ state: { activeReleaseId: RELEASE_1, scopedProjectId: PROJECT_A }, version: 0 }),
    )
    const { useReleaseStore } = await import('./releaseStore')

    useReleaseStore.setState({ activeReleaseId: RELEASE_1, scopedProjectId: PROJECT_A })
    const dropped = useReleaseStore.getState().syncToProject(PROJECT_B)

    expect(dropped).toBe(true)
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
  })
})
