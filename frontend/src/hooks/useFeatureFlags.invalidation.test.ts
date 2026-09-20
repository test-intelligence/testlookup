/**
 * A feature-flag write must reach the key the app GATES on, not just the table.
 *
 * There are two SWR key spaces for one fact:
 *   'feature-flags'                             — the admin table
 *   ['feature-flag-status', key, projectId]     — the resolved per-project gate
 *
 * FeatureFlagsPage refreshed only the first, through `useFeatureFlags().refresh`.
 * So toggling a flag updated the row the admin was looking at and changed
 * nothing the app actually branches on.
 *
 * It did not self-heal either. `useFeatureEnabled` sets revalidateOnFocus:false
 * with no refreshInterval, and its most important consumer, Sidebar, renders
 * outside <Outlet/> and never unmounts — so SWR's revalidate-on-mount repair
 * never fires. Disabling `manual_upload` left the Upload nav entry present for
 * the whole session.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'

const mutateSpy = vi.fn()

vi.mock('swr', () => ({
  default: () => ({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() }),
  mutate: (...args: unknown[]) => mutateSpy(...args),
}))

import { refreshFeatureFlags } from './useFeatureFlags'

type Matcher = (key: unknown) => boolean

describe('refreshFeatureFlags', () => {
  beforeEach(() => mutateSpy.mockClear())

  it('revalidates with a matcher, not a single key', () => {
    refreshFeatureFlags()
    expect(mutateSpy).toHaveBeenCalledTimes(1)
    expect(typeof mutateSpy.mock.calls[0][0]).toBe('function')
    expect(mutateSpy.mock.calls[0][2]).toMatchObject({ revalidate: true })
  })

  it('matches BOTH the admin table and the resolved gate', () => {
    refreshFeatureFlags()
    const matches = mutateSpy.mock.calls[0][0] as Matcher

    expect(matches('feature-flags')).toBe(true)
    // The one that was missing — this is what Sidebar reads.
    expect(matches(['feature-flag-status', 'manual_upload', null])).toBe(true)
    expect(matches(['feature-flag-status', 'ask_ai_chat', 'proj-1'])).toBe(true)
  })

  it('does not invalidate unrelated keys', () => {
    refreshFeatureFlags()
    const matches = mutateSpy.mock.calls[0][0] as Matcher

    expect(matches('settings/ai-config')).toBe(false)
    expect(matches(['releases-list', 'proj-1', ''])).toBe(false)
    expect(matches(null)).toBe(false)
    expect(matches(undefined)).toBe(false)
  })
})
