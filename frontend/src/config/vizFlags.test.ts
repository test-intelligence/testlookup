// @vitest-environment node
/**
 * `VIZ_FLAGS` is held to `contracts/viz/flags.json` — the same file the
 * backend's flag constants and migration 0192 are held to.
 *
 * Loaded through `import.meta.glob(..., '?raw')` rather than `node:fs`: this
 * tsconfig has no `@types/node` and `npm run build` type-checks test files too
 * (see `routeScope.ratchet.test.ts`). A glob that matches nothing yields `{}`,
 * so the first test fails closed instead of comparing against an empty list.
 */
import { describe, expect, it } from 'vitest'
import { VIZ_FLAGS, VIZ_FLAG_KEYS, type VizFlagKey } from './vizFlags'

// frontend/src/config → three levels up is the repo root.
const RAW = Object.values(
  import.meta.glob('../../../contracts/viz/flags.json', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>,
)[0]

interface FlagsContract {
  contract: string
  version: number
  flags: { key: string; description: string }[]
}

const contract = (): FlagsContract => JSON.parse(RAW ?? '{"flags":[]}') as FlagsContract

describe('VIZ_FLAGS matches contracts/viz/flags.json', () => {
  it('finds the contract file', () => {
    expect(RAW, 'contracts/viz/flags.json was not found three levels up').toBeTypeOf('string')
    expect(contract().contract).toBe('flags')
    expect(contract().flags).toHaveLength(6)
  })

  it('has the same keys in the same order', () => {
    expect(Object.values(VIZ_FLAGS)).toEqual(contract().flags.map((flag) => flag.key))
    expect(VIZ_FLAG_KEYS).toEqual(contract().flags.map((flag) => flag.key))
  })

  it('has no duplicate key, and every key is one the flag API can create', () => {
    expect(new Set(VIZ_FLAG_KEYS).size).toBe(VIZ_FLAG_KEYS.length)
    // The API's own key pattern, plus the viz_ prefix that groups the rollout.
    for (const key of VIZ_FLAG_KEYS) expect(key).toMatch(/^viz_[a-z0-9_]+$/)
    for (const key of VIZ_FLAG_KEYS) expect(key).not.toContain('.')
  })

  it('types a key as one of the six literals', () => {
    const key: VizFlagKey = VIZ_FLAGS.multiFilters
    expect(key).toBe('viz_multi_filters')
    // @ts-expect-error — a misspelt key must not type-check.
    const wrong: VizFlagKey = 'viz_multi_filter'
    expect(VIZ_FLAG_KEYS).not.toContain(wrong)
  })
})
