/**
 * A write must refresh every key that shows the fact it changed.
 *
 * Six confirmed staleness bugs, all the same two shapes: *one resource under
 * two SWR key spaces*, and *a sibling hook on the same screen that no handler
 * mutates*. In nearly every case the missing mutator was already in scope in
 * the same file.
 *
 * The matcher helpers are tested behaviourally. The pairings are asserted
 * against source, because a behavioural test would have to mount whole pages
 * with their router, store and service stack — which pins far more than the
 * contract and tends to pass for the wrong reason.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'

const mutateSpy = vi.fn()

vi.mock('@/utils/swrCacheMutate', () => ({
  appMutate: (...args: unknown[]) => {
    mutateSpy(...args)
    return Promise.resolve(undefined)
  },
}))

vi.mock('swr', () => ({
  default: () => ({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() }),
}))

vi.mock('./useProjectScopedSWR', () => ({
  useActiveProjectId: () => 'p1',
  useProjectScopedSWR: () => ({ data: undefined, mutate: vi.fn() }),
}))

import { refreshAgentGovernance } from './useAgentGovernance'
import { refreshApiKeys } from './useApiKeys'
import { refreshTestCases } from './useTestManagement'

type Matcher = (key: unknown) => boolean

function matcherFrom(call: unknown[]): Matcher {
  expect(typeof call[0]).toBe('function')
  return call[0] as Matcher
}

describe('refreshAgentGovernance', () => {
  beforeEach(() => mutateSpy.mockClear())

  it('covers BOTH keys that render one agent config', () => {
    refreshAgentGovernance('p1')
    const m = matcherFrom(mutateSpy.mock.calls[0])
    // The panel's key and the policy cards' key — the cards' key is a path
    // extension of the panel's, which is why one prefix matcher suffices.
    expect(m('/projects/p1/agent-configs')).toBe(true)
    expect(m('/projects/p1/agent-configs/investigator')).toBe(true)
  })

  it('does not touch another project', () => {
    refreshAgentGovernance('p1')
    const m = matcherFrom(mutateSpy.mock.calls[0])
    expect(m('/projects/p2/agent-configs')).toBe(false)
  })

  it('is a no-op without a project rather than a wildcard', () => {
    refreshAgentGovernance(null)
    expect(mutateSpy).not.toHaveBeenCalled()
  })
})

describe('refreshTestCases', () => {
  beforeEach(() => mutateSpy.mockClear())

  it('covers every tm-cases roll, whatever its params', () => {
    refreshTestCases()
    const m = matcherFrom(mutateSpy.mock.calls[0])
    // Three param sets are three different keys: the paginated table, the
    // library-health roll, and each review queue.
    expect(m(['tm-cases', 'p1', { page: 1, size: 200 }])).toBe(true)
    expect(m(['tm-cases', 'p1', { status: 'review_requested', size: 50 }])).toBe(true)
    expect(m(['tm-cases', 'p1', { status: 'under_review', size: 50 }])).toBe(true)
  })

  it('leaves unrelated keys alone', () => {
    refreshTestCases()
    const m = matcherFrom(mutateSpy.mock.calls[0])
    expect(m(['tm-case', 'abc'])).toBe(false)
    expect(m('settings/ai-config')).toBe(false)
    expect(m(null)).toBe(false)
  })
})

describe('refreshApiKeys', () => {
  beforeEach(() => mutateSpy.mockClear())

  it('goes through the provider-bound mutate', () => {
    // The module-level mutate from `swr` is bound to a different, empty cache
    // in this app and matches nothing. `swr` is mocked here WITHOUT a `mutate`
    // export, so importing it would be a TypeError rather than a silent pass.
    refreshApiKeys()
    expect(mutateSpy).toHaveBeenCalledTimes(1)
  })

  it('matches the array key space', () => {
    refreshApiKeys()
    const m = matcherFrom(mutateSpy.mock.calls[0])
    expect(m(['api-keys', 'me'])).toBe(true)
    expect(m(['api-keys', 'p1'])).toBe(true)
    expect(m('/api/v1/keys')).toBe(false)
  })
})

describe('the duplicate api-key hook is gone', () => {
  it('useUserManagement no longer exports its own pair', async () => {
    // Two key spaces for one resource: neither matcher could ever match the
    // other's key, so a revoke on one page left the other listing the key.
    const mod = await import('./useUserManagement')
    expect('useApiKeys' in mod).toBe(false)
    expect('refreshApiKeys' in mod).toBe(false)
  })
})
