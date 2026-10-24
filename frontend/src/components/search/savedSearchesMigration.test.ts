import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  LEGACY_RECENT_KEY, LEGACY_SAVED_KEY, migrateLegacySavedSearches, readLegacyEntry,
} from './savedSearchesMigration'

// W12 SRCH-03: the one-time move of `tl.search.saved` to the server.
const LOGIN = { id: 's1', label: 'Login checks', query: 'login', mode: 'keyword', scope: 'tests', slot: 1 }
const CHECKOUT = { id: 's2', label: 'Checkout', query: 'checkout timeout', mode: 'hybrid', scope: 'tests', slot: 2 }
const ON_SERVER = { id: 's3', label: 'Flaky', query: 'Flaky  Login', mode: 'keyword', scope: 'all', slot: 3 }

const stored = () => {
  const raw = localStorage.getItem(LEGACY_SAVED_KEY)
  return raw === null ? null : (JSON.parse(raw) as Array<{ id: string }>)
}

beforeEach(() => localStorage.clear())

describe('migrateLegacySavedSearches', () => {
  it('POSTs only the entries the server does not hold, one each, and empties the key', async () => {
    localStorage.setItem(LEGACY_SAVED_KEY, JSON.stringify([LOGIN, CHECKOUT, ON_SERVER]))
    localStorage.setItem(LEGACY_RECENT_KEY, JSON.stringify([{ id: 'r', query: 'old', ts: 1 }]))
    const create = vi.fn().mockResolvedValue({})

    const result = await migrateLegacySavedSearches({
      serverViews: [{ filters: { kind: 'search', q: 'flaky login', scope: 'all', mode: 'keyword' } }],
      projectId: 'p1',
      create,
    })

    expect(result).toEqual({ posted: 2, kept: 0 })
    expect(create.mock.calls.map(([payload]) => payload)).toEqual([
      { project_id: 'p1', name: 'Login checks', page: 'search', filters: { kind: 'search', q: 'login', scope: 'tests', mode: 'keyword' }, is_shared: false },
      { project_id: 'p1', name: 'Checkout', page: 'search', filters: { kind: 'search', q: 'checkout timeout', scope: 'tests', mode: 'hybrid' }, is_shared: false },
    ])
    expect(stored()).toBeNull()
    expect(localStorage.getItem(LEGACY_RECENT_KEY)).toBeNull()
  })

  it('removes each entry as it lands, and keeps one whose POST failed for the next load', async () => {
    localStorage.setItem(LEGACY_SAVED_KEY, JSON.stringify([LOGIN, CHECKOUT]))
    const create = vi.fn()
      .mockImplementationOnce(async () => {
        // The first is not removed before its POST answers.
        expect(stored()?.map((e) => e.id)).toEqual(['s1', 's2'])
        return {}
      })
      .mockRejectedValueOnce(new Error('422'))

    const result = await migrateLegacySavedSearches({ serverViews: [], projectId: null, create })

    expect(result).toEqual({ posted: 1, kept: 1 })
    expect(create.mock.calls[0][0].project_id).toBeNull()
    expect(stored()?.map((e) => e.id)).toEqual(['s2'])
  })

  it('deletes the local recents unread even with no saved key, and posts nothing', async () => {
    localStorage.setItem(LEGACY_RECENT_KEY, '[{"query":"x"}]')
    const create = vi.fn()
    expect(await migrateLegacySavedSearches({ serverViews: [], projectId: 'p1', create })).toEqual({ posted: 0, kept: 0 })
    expect(create).not.toHaveBeenCalled()
    expect(localStorage.getItem(LEGACY_RECENT_KEY)).toBeNull()
  })

  it('posts nothing while the server list is unreadable (no duplicate can be ruled out)', async () => {
    localStorage.setItem(LEGACY_SAVED_KEY, JSON.stringify([LOGIN]))
    const create = vi.fn()
    expect(await migrateLegacySavedSearches({ serverViews: null, projectId: 'p1', create })).toEqual({ posted: 0, kept: 1 })
    expect(create).not.toHaveBeenCalled()
    expect(stored()?.map((e) => e.id)).toEqual(['s1'])
  })

  it('drops malformed entries and posts a duplicate pair once', async () => {
    localStorage.setItem(LEGACY_SAVED_KEY, JSON.stringify([null, { id: 'x' }, LOGIN, { ...LOGIN, id: 'dup', query: ' LOGIN ' }]))
    const create = vi.fn().mockResolvedValue({})
    expect(await migrateLegacySavedSearches({ serverViews: [], projectId: 'p1', create })).toEqual({ posted: 1, kept: 0 })
    expect(create).toHaveBeenCalledTimes(1)
    expect(stored()).toBeNull()
  })

  it('a key that is not JSON is treated as empty and removed', async () => {
    localStorage.setItem(LEGACY_SAVED_KEY, '{not json')
    const create = vi.fn()
    await migrateLegacySavedSearches({ serverViews: [], projectId: 'p1', create })
    expect(create).not.toHaveBeenCalled()
    expect(stored()).toBeNull()
  })
})

describe('readLegacyEntry', () => {
  it('names a short label after the query, and moves a semantic mode on another scope to Tests', () => {
    expect(readLegacyEntry({ query: 'x', label: 'a', mode: 'semantic', scope: 'all' }))
      .toEqual({ raw: { query: 'x', label: 'a', mode: 'semantic', scope: 'all' }, search: { q: 'x', scope: 'tests', mode: 'semantic' }, name: 'Search: x' })
    expect(readLegacyEntry({ query: 'checkout', mode: 'weird', scope: 'nope' })?.search).toEqual({ q: 'checkout', scope: 'all', mode: 'keyword' })
    expect(readLegacyEntry({ query: '   ' })).toBeNull()
  })
})
