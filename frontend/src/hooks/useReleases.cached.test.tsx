/**
 * Wave 2.6 fix round 2 (B0 finding 1): with the catalogue flag on, `GET
 * /releases` went out twice on Overview, Trends, Summary and the gate.
 *
 * Not a key mismatch: the release markers read the SAME key as the top bar's
 * `ReleasePicker`. The sections mount late (a lazy chunk, `LazySection`), after
 * SWR's dedupe window, find the top bar's data already cached, and revalidate
 * it because `revalidateIfStale` defaults to true. `useReleases(undefined, { cached: true })` reads
 * that same entry and never re-asks for it — but still fetches when nothing is
 * cached yet.
 *
 * `dedupingInterval: 0` stands in for "mounted after the 2 s window" without a
 * clock.
 */
import { act, render, renderHook, waitFor } from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()
vi.mock('@/services/releasesService', () => ({
  releasesService: { list: (...args: unknown[]) => list(...args), get: vi.fn() },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: 'proj-1' }),
}))

import { useReleases } from './useReleases'

const useCachedReleases = () => useReleases(undefined, { cached: true })

const PAGE = { items: [{ id: 'r1', name: '2026.09' }], total: 1 }

const Provider = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>{children}</SWRConfig>
)

function TopBar() {
  useReleases()
  return null
}

function LateSection({ read }: { read: () => { data?: unknown } }) {
  const { data } = read()
  return <p data-testid="late">{data ? 'has releases' : 'none'}</p>
}

/** The top bar mounts first; a section mounts later, once the list is cached. */
function Page({ read }: { read: () => { data?: unknown } }) {
  const [late, setLate] = useState(false)
  return (
    <>
      <TopBar />
      <button type="button" onClick={() => setLate(true)}>
        mount
      </button>
      {late ? <LateSection read={read} /> : null}
    </>
  )
}

describe('useReleases({ cached: true }) (fix round 2, B0 finding 1)', () => {
  beforeEach(() => {
    list.mockReset()
    list.mockResolvedValue(PAGE)
  })

  it('a section mounting after the top bar reads its list: ONE request in all', async () => {
    const view = render(
      <Provider>
        <Page read={useCachedReleases} />
      </Provider>,
    )
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    await act(async () => {
      view.getByRole('button').click()
    })
    await waitFor(() => expect(view.getByTestId('late').textContent).toBe('has releases'))
    // Give a revalidation every chance to fire.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('control: the plain read re-asks on a late mount (the reported second request)', async () => {
    const view = render(
      <Provider>
        <Page read={() => useReleases()} />
      </Provider>,
    )
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    await act(async () => {
      view.getByRole('button').click()
    })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  })

  it('fetches when nothing is cached yet (a page without the top bar’s list)', async () => {
    const { result } = renderHook(() => useCachedReleases(), { wrapper: Provider })
    await waitFor(() => expect(result.current.data).toEqual(PAGE))
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith('proj-1', undefined)
  })

  it('uses the SAME key as the top bar’s read', async () => {
    const { result } = renderHook(() => ({ top: useReleases(), cached: useCachedReleases() }), { wrapper: Provider })
    await waitFor(() => expect(result.current.cached.data).toEqual(PAGE))
    expect(result.current.top.data).toBe(result.current.cached.data)
    expect(list).toHaveBeenCalledTimes(1)
  })
})
