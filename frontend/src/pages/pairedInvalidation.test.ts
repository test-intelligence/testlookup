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

  it('the edit modal refreshes the detail key as well as the list', () => {
    expect(src).toMatch(/onSaved=\{\(\) => Promise\.all\(\[refetch\(\), refetchRoutedRelease\(\)\]\)\}/)
  })
})

describe('UserManagementPage — editing yourself changes the header identity', () => {
  // authStore.user is client state persisted to localStorage, written only by
  // setAuth/fetchUser; fetchUser's one caller runs once per page load.
  const src = source('UserManagementPage.tsx')

  it('refetches the auth store when the edited user is the current user', () => {
    expect(src).toMatch(/user\.id === useAuthStore\.getState\(\)\.user\?\.id/)
    expect(src).toMatch(/useAuthStore\.getState\(\)\.fetchUser\(\)/)
  })
})
