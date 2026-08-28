/**
 * Regression: the provenance rows on Coverage / Failure Analysis / Trends
 * rendered a HARDCODED age — `'4h ago'`, `'4h ago'`, `'12m ago'` — so a project
 * whose data had just been ingested was told it was hours stale. Found by
 * exploratory testing: a run uploaded three minutes earlier displayed
 * "Updated 4h ago" on /coverage and /failures.
 *
 * Two things are pinned here:
 *  1. the hook reports a real arrival time, and advances when a new payload
 *     lands (that is what "refreshed" means), and
 *  2. no page reintroduces a literal age. The second matters more: the bug was
 *     never a broken function, it was a string typed where a value belonged,
 *     and nothing stopped three pages from doing it.
 */
import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useDataFreshness } from './useDataFreshness'

describe('useDataFreshness', () => {
  it('reports nothing until the first payload arrives', () => {
    const { result } = renderHook(() => useDataFreshness(undefined))
    expect(result.current).toBeNull()
  })

  it('stamps an arrival time once a payload lands', () => {
    const before = Date.now()
    const { result } = renderHook(() => useDataFreshness({ suites: [] }))
    const at = result.current
    expect(at).toBeInstanceOf(Date)
    expect(at?.getTime()).toBeGreaterThanOrEqual(before)
    expect(at?.getTime()).toBeLessThanOrEqual(Date.now())
  })

  it('treats a loaded-but-empty response as an arrival, not as still-loading', () => {
    // `null` is a real answer from the server; only `undefined` is SWR's
    // "no payload yet". Conflating them would leave the row stuck on "just now".
    const { result } = renderHook(() => useDataFreshness(null))
    expect(result.current).toBeInstanceOf(Date)
  })

  it('advances when a new payload arrives and holds steady when it does not', () => {
    const first = { rows: 1 }
    const { result, rerender } = renderHook(({ d }) => useDataFreshness(d), {
      initialProps: { d: first as unknown },
    })
    const t1 = result.current?.getTime() ?? 0
    expect(t1).toBeGreaterThan(0)

    // Same reference — SWR revalidated to identical data, nothing "refreshed".
    rerender({ d: first })
    expect(result.current?.getTime()).toBe(t1)

    // New reference — a genuine refresh, so the age restarts.
    rerender({ d: { rows: 2 } })
    expect(result.current?.getTime()).toBeGreaterThanOrEqual(t1)
  })
})

describe('no page hardcodes a data-freshness age', () => {
  // Page sources are pulled in through Vite's glob rather than node:fs so this
  // runs under the same tsconfig as the app build (`types: [vite/client]`,
  // no node types).
  const pageSources = import.meta.glob('../pages/*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  it('reads the page sources it is meant to guard', () => {
    // A glob that silently matched nothing would make this suite pass forever.
    expect(Object.keys(pageSources).length).toBeGreaterThan(20)
  })

  it('assigns refreshedAt from a real timestamp, never a literal', () => {
    const offenders: string[] = []

    for (const [file, src] of Object.entries(pageSources)) {
      for (const line of src.split(/\r?\n/)) {
        // e.g. `const refreshedAt = '4h ago'` or a ternary of two literals.
        if (/refreshedAt\s*=\s*[^=]*['"`]\s*\d+\s*[smhd]\s+ago\s*['"`]/.test(line)) {
          offenders.push(`${file}: ${line.trim()}`)
        }
      }
    }

    expect(offenders, 'a provenance age must come from useDataFreshness + shortAgo').toEqual([])
  })
})
