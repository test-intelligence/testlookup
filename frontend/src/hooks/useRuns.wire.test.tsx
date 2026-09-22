/**
 * E3: `useRuns`, `useMyFailures` and `useMyFailuresCount` on the WIRE.
 *
 * Both backends now declare `release_id` / `suite_name` repeatable, so every
 * selected value is sent. What reaches the URL is what FastAPI reads:
 *
 *   - none  → no parameter at all;
 *   - one   → `release_id=R1`, byte-identical to the legacy request;
 *   - many  → `release_id=R1&release_id=R2` — a repeated key, never axios's
 *             default `release_id[]=`, which FastAPI silently drops.
 *
 * The real services and the real shared axios instance run; only the adapter
 * (the network) is replaced, and it records the URL axios built.
 */
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocked = vi.hoisted(() => ({
  projectState: { activeProjectId: null as string | null },
}))

vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.projectState) => unknown) =>
      selector ? selector(mocked.projectState) : mocked.projectState,
  }
})

import { api } from '@/services/api'
import { useReleaseStore } from '@/store/releaseStore'
import { useSuiteStore } from '@/store/suiteStore'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { settleScopeNow } from '@/store/settledScope'
import { useRuns } from './useRuns'
import { useMyFailures, useMyFailuresCount } from './useMyFailures'

const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

const urls: string[] = []
const originalAdapter = api.defaults.adapter

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
}

function selectReleases(ids: string[]) {
  useReleaseStore.getState().setActiveReleases(ids, PROJECT_A)
  settleScopeNow()
}

function selectSuites(names: string[]) {
  useSuiteStore.getState().setActiveSuites(names, PROJECT_A)
  settleScopeNow()
}

/** The query string of the last request to `path`, decoded. */
async function queryOf(path: string): Promise<URLSearchParams> {
  await waitFor(() => expect(urls.some((u) => new URL(u, 'http://x').pathname.endsWith(path))).toBe(true))
  const hit = [...urls].reverse().find((u) => new URL(u, 'http://x').pathname.endsWith(path)) as string
  expect(hit).not.toMatch(/%5B%5D|\[\]/) // never `key[]=`
  return new URL(hit, 'http://x').searchParams
}

beforeEach(() => {
  urls.length = 0
  localStorage.clear()
  mocked.projectState = { activeProjectId: PROJECT_A }
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  useMultiFiltersFlagStore.setState({ enabled: true })
  settleScopeNow()
  api.defaults.adapter = async (config) => {
    urls.push(api.getUri(config))
    const data = config.url?.endsWith('/count') ? { count: 0 } : { items: [], total: 0 }
    return { data, status: 200, statusText: 'OK', headers: {}, config }
  }
})

afterEach(() => {
  api.defaults.adapter = originalAdapter
})

describe('/runs', () => {
  it('no selection: no release_id and no suite_name at all', async () => {
    renderHook(() => useRuns({ page: 1 }), { wrapper })
    const q = await queryOf('/api/v1/runs')
    expect(q.has('release_id')).toBe(false)
    expect(q.has('suite_name')).toBe(false)
  })

  it('one release is the legacy scalar', async () => {
    selectReleases([R1])
    renderHook(() => useRuns({ page: 1, suite_name: ['payments'] }), { wrapper })
    const q = await queryOf('/api/v1/runs')
    expect(q.getAll('release_id')).toEqual([R1])
    expect(q.getAll('suite_name')).toEqual(['payments'])
  })

  it('several releases and suites are repeated keys', async () => {
    selectReleases([R2, R1])
    renderHook(() => useRuns({ page: 1, suite_name: ['payments', 'cart'] }), { wrapper })
    const q = await queryOf('/api/v1/runs')
    expect(q.getAll('release_id')).toEqual([R1, R2])
    expect(q.getAll('suite_name')).toEqual(['cart', 'payments'])
  })

  it('the existence probe stays filter-free under a multi-selection', async () => {
    selectReleases([R1, R2])
    selectSuites(['payments', 'cart'])
    renderHook(() => useRuns({ page: 1, size: 1 }, { ignoreGlobalRelease: true }), { wrapper })
    const q = await queryOf('/api/v1/runs')
    expect(q.has('release_id')).toBe(false)
    expect(q.has('suite_name')).toBe(false)
  })
})

describe('/me/assigned-failures and its count', () => {
  it('no selection: neither axis is sent', async () => {
    renderHook(() => { useMyFailures({ days: 30 }); useMyFailuresCount({ days: 30 }) }, { wrapper })
    for (const path of ['/api/v1/me/assigned-failures', '/api/v1/me/assigned-failures/count']) {
      const q = await queryOf(path)
      expect(q.has('release_id'), path).toBe(false)
      expect(q.has('suite_name'), path).toBe(false)
    }
  })

  it('every selected release and suite, as repeated keys, on the list AND the badge', async () => {
    selectReleases([R2, R1])
    selectSuites(['payments', 'cart'])
    renderHook(() => { useMyFailures({ days: 30 }); useMyFailuresCount({ days: 30 }) }, { wrapper })
    for (const path of ['/api/v1/me/assigned-failures', '/api/v1/me/assigned-failures/count']) {
      const q = await queryOf(path)
      expect(q.getAll('release_id'), path).toEqual([R1, R2])
      expect(q.getAll('suite_name'), path).toEqual(['cart', 'payments'])
    }
  })

  it('one release is the legacy scalar', async () => {
    selectReleases([R1])
    renderHook(() => useMyFailures({ days: 30 }), { wrapper })
    const q = await queryOf('/api/v1/me/assigned-failures')
    expect(q.getAll('release_id')).toEqual([R1])
    expect(q.has('suite_name')).toBe(false)
  })

  it('flag off: the global suite selection is not sent (the pre-E3 request)', async () => {
    useMultiFiltersFlagStore.setState({ enabled: false })
    selectSuites(['payments'])
    renderHook(() => useMyFailures({ days: 30 }), { wrapper })
    const q = await queryOf('/api/v1/me/assigned-failures')
    expect(q.has('suite_name')).toBe(false)
  })
})
