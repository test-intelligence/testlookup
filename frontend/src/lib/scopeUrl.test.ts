import { describe, expect, it } from 'vitest'
import {
  isScopeRoute,
  parseScopeParams,
  RESERVED_URL_KEYS,
  SCOPE_URL_KEYS,
  writeScopeParams,
} from './scopeUrl'
import { bulkWriteSuite, normalizeScope, scopeArg, scopeKey, scopeParam, singleScopeArg, keyPart } from './scopeParams'

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

describe('parseScopeParams', () => {
  it('reads repeated release and suites, and the window', () => {
    const p = parseScopeParams(`?release=${R2}&release=${R1}&suites=payments&suites=cart&window=14`)
    expect(p.releaseIds).toEqual([R2, R1])
    expect(p.suiteNames).toEqual(['payments', 'cart'])
    expect(p.windowDays).toBe(14)
    expect(p.dropped).toEqual([])
  })

  it('an absent key is null; the legacy single ?release= still reads', () => {
    const p = parseScopeParams(`?release=${R1}`)
    expect(p.releaseIds).toEqual([R1])
    expect(p.suiteNames).toBeNull()
    expect(p.windowDays).toBeNull()
  })

  it('keeps the unattributed sentinel', () => {
    expect(parseScopeParams('?release=unattributed').releaseIds).toEqual(['unattributed'])
  })

  it('drops a malformed release and names it; the rest still apply', () => {
    const p = parseScopeParams(`?release=${R1}&release=not-a-uuid`)
    expect(p.releaseIds).toEqual([R1])
    expect(p.dropped).toEqual([{ dimension: 'release', values: ['not-a-uuid'], reason: expect.any(String) }])
  })

  it('drops releases beyond 20 and suites beyond 50, naming them', () => {
    const ids = Array.from({ length: 22 }, (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`)
    const suites = Array.from({ length: 52 }, (_, i) => `s${i}`)
    const qs = new URLSearchParams()
    ids.forEach(id => qs.append('release', id))
    suites.forEach(s => qs.append('suites', s))
    const p = parseScopeParams(qs)
    expect(p.releaseIds).toHaveLength(20)
    expect(p.suiteNames).toHaveLength(50)
    const rel = p.dropped.find(d => d.dimension === 'release')
    const sui = p.dropped.find(d => d.dimension === 'suite')
    expect(rel?.values).toEqual(ids.slice(20))
    expect(rel?.reason).toContain('Limit reached (20)')
    expect(sui?.values).toEqual(['s50', 's51'])
    expect(sui?.reason).toContain('Limit reached (50)')
  })

  it('dedupes, UUIDs case-insensitively', () => {
    const p = parseScopeParams(`?release=${R1}&release=${R1.toUpperCase()}&suites=a&suites=a`)
    expect(p.releaseIds).toEqual([R1])
    expect(p.suiteNames).toEqual(['a'])
  })

  it('rejects windows that are not an integer 1-365', () => {
    for (const w of ['0', '366', '7.5', '-1', '1e2', ' 7', 'abc', '0x10', '']) {
      expect(parseScopeParams(`?window=${encodeURIComponent(w)}`).windowDays, w).toBeNull()
    }
    expect(parseScopeParams('?window=365').windowDays).toBe(365)
  })

  it('never reads a reserved page-local key', () => {
    const qs = new URLSearchParams()
    for (const k of RESERVED_URL_KEYS) qs.set(k, R1)
    const p = parseScopeParams(qs)
    expect(p.releaseIds).toBeNull()
    expect(p.suiteNames).toBeNull()
    expect(p.windowDays).toBeNull()
  })
})

describe('parseScopeParams fuzz: malformed params never throw', () => {
  // Deterministic PRNG so a failure reproduces.
  let seed = 0x5eed
  const rand = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff
    return seed / 0x7fffffff
  }
  const alphabet = ['%', '%2', '%zz', '&', '=', '?', '#', '+', ' ', '\u0000', '￿', '\ud800', '😀', 'release', 'suites', 'window', '[]', '9', 'a', '-', 'unattributed', R1]
  const randomString = () => {
    const n = Math.floor(rand() * 40)
    let s = ''
    for (let i = 0; i < n; i++) s += alphabet[Math.floor(rand() * alphabet.length)]
    return s
  }

  it('2 000 random query strings parse without throwing, within caps, to valid values', () => {
    for (let i = 0; i < 2000; i++) {
      const input = randomString()
      let parsed: ReturnType<typeof parseScopeParams> | undefined
      expect(() => { parsed = parseScopeParams(input) }, JSON.stringify(input)).not.toThrow()
      if (!parsed) throw new Error('parse returned nothing')
      const p = parsed
      expect((p.releaseIds ?? []).length).toBeLessThanOrEqual(20)
      expect((p.suiteNames ?? []).length).toBeLessThanOrEqual(50)
      for (const id of p.releaseIds ?? []) expect(id === 'unattributed' || /^[0-9a-f-]{36}$/.test(id)).toBe(true)
      if (p.windowDays !== null) expect(p.windowDays >= 1 && p.windowDays <= 365).toBe(true)
    }
  })

  it('tolerates null, undefined and huge inputs', () => {
    expect(() => parseScopeParams(null)).not.toThrow()
    expect(() => parseScopeParams(undefined)).not.toThrow()
    expect(() => parseScopeParams(`?suites=${'x'.repeat(100_000)}&release=${'y'.repeat(100_000)}`)).not.toThrow()
    const p = parseScopeParams(`?suites=${'x'.repeat(501)}`)
    expect(p.suiteNames).toEqual([])
    expect(p.dropped[0].values[0].length).toBeLessThanOrEqual(80)
  })
})

describe('writeScopeParams', () => {
  it('writes sorted repeated keys and leaves every other key exactly as it was', () => {
    const base = new URLSearchParams('suite=legacy&days=7&tab=x&page=2&q=a%26b&status=FAILED&tab=y')
    const next = writeScopeParams(base, { releaseIds: [R2, R1], suiteNames: ['b', 'a'], windowDays: 14 })
    expect(next.getAll('release')).toEqual([R1, R2])
    expect(next.getAll('suites')).toEqual(['a', 'b'])
    expect(next.get('window')).toBe('14')
    for (const key of RESERVED_URL_KEYS) expect(next.getAll(key), key).toEqual(base.getAll(key))
  })

  it('[] or null removes a key; undefined leaves it alone', () => {
    const base = new URLSearchParams(`release=${R1}&suites=a&window=7`)
    const next = writeScopeParams(base, { releaseIds: [], suiteNames: undefined, windowDays: null })
    expect(next.has('release')).toBe(false)
    expect(next.getAll('suites')).toEqual(['a'])
    expect(next.has('window')).toBe(false)
  })

  it('round-trips through parse', () => {
    const scope = { releaseIds: [R1, 'unattributed'], suiteNames: ['payments', 'cart & co'], windowDays: 90 }
    const back = parseScopeParams(writeScopeParams('', scope))
    expect(back.releaseIds?.sort()).toEqual([...scope.releaseIds].sort())
    expect(back.suiteNames?.sort()).toEqual([...scope.suiteNames].sort())
    expect(back.windowDays).toBe(90)
  })

  it('the scope keys are exactly release, suites, window', () => {
    expect(Object.values(SCOPE_URL_KEYS)).toEqual(['release', 'suites', 'window'])
    for (const key of Object.values(SCOPE_URL_KEYS)) {
      expect(RESERVED_URL_KEYS as readonly string[]).not.toContain(key)
    }
  })
})

describe('isScopeRoute', () => {
  it('covers every VIZ-301 report route', () => {
    for (const p of [
      '/overview', '/trends', '/coverage', '/coverage/suite', '/failures', '/defects', '/reports/summary',
      '/value-metrics', '/intelligence', '/runs/abc/intelligence', '/release-gate', '/release-gate/abc',
      '/runs/compare', '/flaky-coach',
    ]) expect(isScopeRoute(p), p).toBe(true)
  })
  it('excludes settings and detail pages', () => {
    for (const p of ['/settings', '/settings/ai', '/runs/abc', '/releases', '/test-management']) {
      expect(isScopeRoute(p), p).toBe(false)
    }
  })
})

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
})

describe('E3 round A nits', () => {
  it('"All time" (0 days) is never written as ?window=0 — the key is omitted', () => {
    const base = new URLSearchParams('window=14&tab=x')
    const next = writeScopeParams(base, { windowDays: 0 })
    expect(next.has('window')).toBe(false)
    expect(next.get('tab')).toBe('x')
    // And nothing outside the contract's 1-365 either.
    expect(writeScopeParams('', { windowDays: 400 }).has('window')).toBe(false)
    expect(writeScopeParams('', { windowDays: 30 }).get('window')).toBe('30')
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
