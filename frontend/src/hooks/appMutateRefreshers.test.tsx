/**
 * Refresh-after-write helpers must refetch under the app's cache PROVIDER.
 *
 * `main.tsx` renders every SWR key into `provider: () => new Map()`, so the
 * `mutate` exported by the `swr` module (bound to SWR's default cache) matches
 * nothing. Ten helpers shipped on it — among them `refreshIntegrationHealth`
 * (Integration Health's "Probe All Now" stored results the page never showed
 * until a reload: the UX redesign's browser E2E pass, 2026-10-07) and
 * `refreshSuites` (a created suite never appeared in the list). They now go
 * through `appMutate`, and ESLint refuses the `swr` import in app code.
 *
 * A mocked `swr` cannot see this bug (it removes WHICH cache the mutate
 * belongs to), so these render under a real provider, as
 * `useFeatureFlags.invalidation.test.tsx` does.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import { resetAppMutate } from '@/utils/swrCacheMutate'

const getAllStatus = vi.fn()
const listSuites = vi.fn()

vi.mock('@/services/integrationHealthService', () => ({
  getAllStatus: (...a: unknown[]) => getAllStatus(...a),
  getHealthTrends: vi.fn(),
  getProviderHistory: vi.fn(),
}))
vi.mock('@/services/suitesService', () => ({
  suitesService: { list: (...a: unknown[]) => listSuites(...a) },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: 'proj-1' }),
}))

import { refreshIntegrationHealth, useIntegrationStatus } from './useIntegrationHealth'
import { refreshSuites, useSuites } from './useSuites'

function Statuses() {
  const { statuses } = useIntegrationStatus()
  return <div data-testid="statuses">{statuses.length}</div>
}

function Suites() {
  const { data } = useSuites()
  return <div data-testid="suites">{data ? data.items.length : '…'}</div>
}

function Harness() {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SwrMutateBridge />
      <Statuses />
      <Suites />
    </SWRConfig>
  )
}

describe('refresh helpers against the app cache provider', () => {
  beforeEach(() => {
    getAllStatus.mockReset()
    listSuites.mockReset()
  })
  afterEach(() => resetAppMutate())

  it('refreshIntegrationHealth: "Probe All Now" shows the probes it stored, without a reload', async () => {
    getAllStatus.mockResolvedValueOnce([])
    listSuites.mockResolvedValue({ items: [], total: 0 })
    render(<Harness />)
    await waitFor(() => expect(getAllStatus).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('statuses')).toHaveTextContent('0')

    getAllStatus.mockResolvedValueOnce([{ provider: 'github', status: 'skipped' }, { provider: 'jira', status: 'skipped' }])
    await act(async () => {
      await refreshIntegrationHealth()
    })
    await waitFor(() => expect(screen.getByTestId('statuses')).toHaveTextContent('2'))
    expect(getAllStatus).toHaveBeenCalledTimes(2)
  })

  it('refreshSuites: a created suite appears in the list', async () => {
    getAllStatus.mockResolvedValue([])
    listSuites.mockResolvedValueOnce({ items: [{ id: 's1' }], total: 1 })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('suites')).toHaveTextContent('1'))

    listSuites.mockResolvedValueOnce({ items: [{ id: 's1' }, { id: 's2' }], total: 2 })
    await act(async () => {
      await refreshSuites()
    })
    await waitFor(() => expect(screen.getByTestId('suites')).toHaveTextContent('2'))
    expect(listSuites).toHaveBeenCalledTimes(2)
  })
})
