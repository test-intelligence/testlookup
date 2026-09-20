/**
 * A handler that mutates one SWR key must also refresh the sibling surface
 * showing the same fact.
 *
 * Each case below is a CONFIRMED staleness bug where the correct mutator was
 * already in scope in the same file and simply was not called. These are
 * source-level guards: the alternative is mounting whole pages with their
 * router, store and service stack, which pins far more than the contract.
 *
 * Only cases that do NOT self-heal are guarded here. Where a poll or an unmount
 * repairs the surface within seconds the omission is a stale first paint, not a
 * durable lie, and a guard would be noise.
 */
import { describe, expect, it } from 'vitest'

const sources = import.meta.glob('./**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

function source(suffix: string): string {
  const hit = Object.entries(sources).find(([path]) => path.endsWith(suffix))
  if (!hit) throw new Error(`source not found: ${suffix} (have ${Object.keys(sources).length} files)`)
  return hit[1]
}

describe('OwnershipEditorPage — the coverage badge sits above the table being edited', () => {
  // ['codeowners-coverage', projectId] has no poll and no focus revalidation,
  // and the page does not unmount while you edit. Refreshing only the rule list
  // left the badge quoting coverage from rules that no longer exist.
  const src = source('OwnershipEditorPage.tsx')

  it('pairs refreshCoverage with refresh in every rule mutation', () => {
    const paired = src.match(/Promise\.all\(\[refresh\(\), refreshCoverage\(\)\]\)/g) ?? []
    expect(paired.length).toBe(3) // create, toggle, delete
  })

  it('leaves no bare refresh() in a rule handler', () => {
    for (const handler of ['handleCreate', 'handleToggle', 'handleDelete']) {
      const body = src.slice(src.indexOf(`const ${handler} =`), src.indexOf(`const ${handler} =`) + 1200)
      expect(body, `${handler} refreshes the rules without the coverage badge`).not.toMatch(/\n\s+refresh\(\);/)
    }
  })
})

describe('ReleasesPage — the detail route renders entirely from the detail key', () => {
  // `refetch` is the LIST mutator; on /releases/:id the page renders from
  // ['release-detail', id], which the list route does not even subscribe to.
  const src = source('ReleasesPage.tsx')

  it('the edit modal refreshes the edited release detail key, not just the list', () => {
    // Keyed off the EDITED release, not the route: the same detail key is read
    // by ReleaseDetailPanel inside an expanded row on the list route, which a
    // route-scoped refresh would leave stale.
    expect(src).toContain("appMutate(['release-detail', editRelease.id])")
    expect(src).toContain('refetch()')
  })
})

describe('UserManagementPage — editing yourself changes the header identity', () => {
  // authStore.user is client state persisted to localStorage, written only by
  // setAuth/fetchUser; fetchUser's one caller runs once per page load.
  const src = source('UserManagementPage.tsx')

  it('routes every self-edit path through one helper', () => {
    expect(src).toContain('async function syncSelfIfEdited(')
    expect(src).toContain('useAuthStore.getState().fetchUser()')
  })

  it('covers all THREE write paths, not just the modal', () => {
    // The modal, the inline role dropdown and the active/inactive toggle all
    // write the same record; usePermissions() reads authStore.user.role.
    const calls = src.match(/syncSelfIfEdited\(/g) ?? []
    expect(calls.length).toBe(4) // 1 definition + 3 call sites
  })
})

describe('QuarantinePage — the stat tiles sit above the table being acted on', () => {
  const src = source('QuarantinePage.tsx')

  it('every row action refreshes the tiles too', () => {
    expect(src).toContain('refresh: refreshStats')
    expect(src).toContain('Promise.all([refresh(), refreshStats()])')
  })
})

describe('NotificationsPage — the unread badge lives in the persistent TopBar', () => {
  const src = source('settings/NotificationsPage.tsx')

  it('the per-row dismiss fans out like handleMarkAll does', () => {
    // handleMarkAll already called invalidateNotifications; the per-row handler
    // in the same component did not, so the badge kept the old count.
    const calls = src.match(/invalidateNotifications\(\)/g) ?? []
    expect(calls.length).toBeGreaterThanOrEqual(4)
  })
})

describe('AIConfigPage — five other surfaces read the shared key', () => {
  const src = source('settings/AIConfigPage.tsx')

  it('saving invalidates the shared key and the active-tier panel', () => {
    expect(src).toContain("appMutate('settings/ai-config')")
    expect(src).toContain('refreshModelStatus()')
  })
})

describe('TestManagementPage — the review headline is a different tm-cases roll', () => {
  const src = source('TestManagementPage.tsx')

  it('mutateReviews refreshes every tm-cases key, not just its two queues', () => {
    expect(src).toContain('refreshTestCases()')
  })
})
