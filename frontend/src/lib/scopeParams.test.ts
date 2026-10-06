/**
 * `scopeParams`: one scope value (none, one, or several ids/names) to the
 * request shape the API takes. Moved out of `scopeUrl.test.ts` when the
 * multi-filter URL codec was removed (Phase D, M1-M3); the services and
 * catalogue still build their params with these.
 */
import { describe, expect, it } from 'vitest'
import { bulkWriteSuite, keyPart, normalizeScope, scopeArg, scopeKey, scopeParam, singleScopeArg } from './scopeParams'

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

describe('scopeParams', () => {
  it('none / one / many', () => {
    expect(scopeArg(null)).toBeNull()
    expect(scopeArg([])).toBeNull()
    expect(scopeArg('')).toBeNull()
    expect(scopeArg([R1])).toBe(R1)
    expect(scopeArg(R1)).toBe(R1)
    expect(scopeArg([R2, R1, R2])).toEqual([R1, R2])
    expect(scopeParam('release_id', [])).toEqual({})
    expect(scopeParam('release_id', [R1])).toEqual({ release_id: R1 })
    expect(scopeParam('release_id', [R2, R1])).toEqual({ release_id: [R1, R2] })
  })

  it('keys are strings, order-free; a scalar key is untouched', () => {
    expect(scopeKey([R2, R1])).toBe(scopeKey([R1, R2]))
    expect(typeof scopeKey([R2, R1])).toBe('string')
    expect(keyPart(R1)).toBe(R1)
    expect(keyPart(null)).toBeNull()
    expect(keyPart(undefined)).toBeUndefined()
    expect(keyPart([R2, R1])).toBe(scopeKey([R1, R2]))
  })

  it('singleScopeArg only ever yields one value', () => {
    expect(singleScopeArg([R1])).toBe(R1)
    expect(singleScopeArg([R1, R2])).toBeNull()
    expect(singleScopeArg(null)).toBeNull()
  })

  it('normalizeScope never throws on a non-string, non-array value (a corrupted store)', () => {
    for (const bad of [42, {}, true, { length: 2 }] as unknown[]) {
      expect(() => normalizeScope(bad as never)).not.toThrow()
      expect(normalizeScope(bad as never)).toEqual([])
      expect(scopeArg(bad as never)).toBeNull()
    }
    expect(normalizeScope([R1, 7, null, R1] as never)).toEqual([R1])
  })

  it('a bulk write takes exactly one suite, none, or refuses several', () => {
    expect(bulkWriteSuite([])).toEqual({ kind: 'none' })
    expect(bulkWriteSuite(['cart'])).toEqual({ kind: 'one', name: 'cart' })
    expect(bulkWriteSuite(['cart', 'payments'])).toEqual({ kind: 'several' })
  })
})
