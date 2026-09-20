/**
 * The flag refresh must work against the cache the APP uses, not SWR's default.
 *
 * `main.tsx` renders `<SWRConfig value={{ ..., provider: () => new Map() }}>`.
 * The `mutate` exported by the `swr` module is bound at import time to SWR's
 * own default cache, so a matcher-mutate through it iterates an empty map and
 * resolves having done nothing. An earlier version of this fix used exactly
 * that and was a silent no-op; the unit test did not notice because it mocked
 * the whole `swr` module, which means it could only ever confirm the call
 * shape the author had already chosen.
 *
 * So this renders for real, under a provider, with a spy fetcher.
 *
 * Second property under test: the refresh must NOT wipe the cache first.
 * `mutate(matcher, undefined, { revalidate: true })` takes SWR's data-write
 * path and writes `undefined` into every matched key, so `useFeatureEnabled`
 * reads `false` for a frame and every gated surface blinks off — the flicker
 * this change exists to prevent.
 */
import { render, screen, waitFor, act } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import { resetAppMutate } from '@/utils/swrCacheMutate'

const status = vi.fn()
const list = vi.fn()

vi.mock('@/services/featureFlagService', () => ({
  featureFlagService: {
    list: (...a: unknown[]) => list(...a),
    status: (...a: unknown[]) => status(...a),
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) =>
    sel({ activeProjectId: 'proj-1' }),
}))

import { refreshFeatureFlags, useFeatureEnabled } from './useFeatureFlags'

function Gate() {
  const enabled = useFeatureEnabled('manual_upload')
  return <div data-testid="gate">{enabled ? 'ON' : 'OFF'}</div>
}

function Harness() {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SwrMutateBridge />
      <Gate />
    </SWRConfig>
  )
}

describe('refreshFeatureFlags against the app cache provider', () => {
  beforeEach(() => {
    list.mockReset()
    status.mockReset()
    list.mockResolvedValue([])
  })
  afterEach(() => resetAppMutate())

  it('refetches the resolved gate key — the whole point of the fix', async () => {
    status.mockResolvedValueOnce({ enabled: true })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('ON'))
    expect(status).toHaveBeenCalledTimes(1)

    // The flag is turned off elsewhere (the admin page).
    status.mockResolvedValueOnce({ enabled: false })
    await act(async () => {
      await refreshFeatureFlags()
    })

    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('OFF'))
    expect(status).toHaveBeenCalledTimes(2)
  })

  it('never blanks the gate while revalidating', async () => {
    status.mockResolvedValueOnce({ enabled: true })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('ON'))

    let resolveSecond: (v: unknown) => void = () => {}
    status.mockReturnValueOnce(new Promise((r) => { resolveSecond = r }))

    await act(async () => { refreshFeatureFlags() })

    // Still ON while the refetch is in flight. The 3-argument mutate wrote
    // `undefined` here, which rendered OFF and blinked the sidebar.
    expect(screen.getByTestId('gate')).toHaveTextContent('ON')

    await act(async () => {
      resolveSecond({ enabled: true })
    })
    expect(screen.getByTestId('gate')).toHaveTextContent('ON')
  })
})
