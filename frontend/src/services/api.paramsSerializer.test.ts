import { describe, expect, it } from 'vitest'
import { api } from './api'

/**
 * Array query params must serialize as `?a=1&a=2`, not `?a[]=1&a[]=2`.
 *
 * Every endpoint here is FastAPI, and a `list[str] = Query(None)` is read from
 * REPEATED bare keys. FastAPI does not recognise the bracketed form — it drops
 * the parameter silently. No error, no 422, the filter just does nothing.
 *
 * Reported against /activity: picking a category changed nothing. Verified on
 * the deployment at the time: `?category=configuration` correctly returned 0
 * rows, `?category[]=configuration` returned all of them.
 */
describe('shared axios instance — array param serialization', () => {
  const serialize = (params: Record<string, unknown>): string => {
    const s = api.defaults.paramsSerializer
    // Axios 1.x accepts either a function or `{ indexes, serialize }`.
    if (typeof s === 'function') return (s as (p: unknown) => string)(params)
    const fn = (s as { serialize?: (p: unknown) => string } | undefined)?.serialize
    if (typeof fn === 'function') return fn(params)
    // `{ indexes: null }` has no serialize fn of its own — assert the option
    // that produces repeat form is set, which is what axios acts on.
    expect((s as { indexes?: unknown } | undefined)?.indexes).toBeNull()
    return ''
  }

  it('is configured for repeat form, not bracket form', () => {
    const s = api.defaults.paramsSerializer
    expect(s, 'no paramsSerializer: axios defaults to a[]= which FastAPI drops').toBeDefined()
    serialize({ category: ['runs', 'release'] })
  })

  it('never emits the bracketed form for an array', () => {
    const out = serialize({ category: ['runs', 'release'] })
    if (out) {
      expect(out).not.toContain('[]')
      expect(out).toContain('category=runs')
      expect(out).toContain('category=release')
    }
  })
})
