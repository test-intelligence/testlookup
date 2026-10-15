/**
 * REL-04: the /releases rail reads the packs that exist
 * (`GET /api/v1/compliance-packs`), one project's or every accessible
 * project's, and follows a generate made in a release panel.
 *
 * The service is real and `getData` is captured, so the request (path and
 * params) is asserted as sent; the refresh helper runs under a real cache
 * provider with the app's mutate bridge (a mocked `swr` cannot see WHICH
 * cache a mutate belongs to: `appMutateRefreshers.test.tsx`).
 */
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SwrMutateBridge } from '@/components/SwrMutateBridge'
import { resetAppMutate } from '@/utils/swrCacheMutate'

const getData = vi.fn()

vi.mock('@/services/http', () => ({
  getData: (...a: unknown[]) => getData(...a),
  postData: vi.fn(),
}))

import { RAIL_PACK_LIMIT, refreshProjectCompliancePacks, useProjectCompliancePacks } from './useProjectCompliancePacks'

const PROJECT = '11111111-1111-4111-8111-111111111111'

const pack = (id: string) => ({
  id,
  release_id: 'rel-1',
  release_name: '2026.10',
  release_version: '2026.10',
  project_id: PROJECT,
  project_name: 'Checkout',
  bytes: 422_195,
  file_count: 9,
  manifest_sha256: 'ab12cd34'.repeat(8),
  generated_at: '2026-09-18T11:00:00Z',
  retention_expires_at: '2033-09-18T11:00:00Z',
  retired: false,
})

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } }, children)
}

describe('useProjectCompliancePacks', () => {
  beforeEach(() => {
    getData.mockReset()
  })
  afterEach(() => resetAppMutate())

  it('asks for one project’s packs, capped, and reports the server’s total (not the page length)', async () => {
    getData.mockResolvedValue({ items: [pack('p-1'), pack('p-2')], total: 12 })
    const { result } = renderHook(() => useProjectCompliancePacks(PROJECT), { wrapper })

    await waitFor(() => expect(result.current.packs).toHaveLength(2))
    expect(result.current.total).toBe(12)
    expect(result.current.isError).toBe(false)
    expect(getData).toHaveBeenCalledTimes(1)
    expect(getData).toHaveBeenCalledWith('/api/v1/compliance-packs', {
      params: { project_id: PROJECT, limit: RAIL_PACK_LIMIT },
    })
    expect(RAIL_PACK_LIMIT).toBe(10)
  })

  it('sends no project id in the All-projects view (every accessible project)', async () => {
    getData.mockResolvedValue({ items: [], total: 0 })
    const { result } = renderHook(() => useProjectCompliancePacks(null), { wrapper })

    await waitFor(() => expect(result.current.total).toBe(0))
    expect(getData).toHaveBeenCalledWith('/api/v1/compliance-packs', { params: { limit: RAIL_PACK_LIMIT } })
  })

  it('a failed read is an error with an unknown total, never "0 packs"', async () => {
    getData.mockRejectedValue(new Error('503'))
    const { result } = renderHook(() => useProjectCompliancePacks(PROJECT), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.total).toBeNull()
    expect(result.current.packs).toEqual([])
  })
})

function Rail({ projectId }: { projectId: string | null }) {
  const { packs } = useProjectCompliancePacks(projectId)
  return <div data-testid={`packs-${projectId ?? 'all'}`}>{packs.length}</div>
}

describe('refreshProjectCompliancePacks', () => {
  beforeEach(() => {
    getData.mockReset()
  })
  afterEach(() => resetAppMutate())

  it('refetches every scope of the list under the app cache provider', async () => {
    getData.mockResolvedValue({ items: [], total: 0 })
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <SwrMutateBridge />
        <Rail projectId={PROJECT} />
        <Rail projectId={null} />
      </SWRConfig>,
    )
    await waitFor(() => expect(getData).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId(`packs-${PROJECT}`)).toHaveTextContent('0')

    getData.mockResolvedValue({ items: [pack('p-new')], total: 1 })
    await act(async () => {
      await refreshProjectCompliancePacks()
    })

    await waitFor(() => expect(screen.getByTestId(`packs-${PROJECT}`)).toHaveTextContent('1'))
    expect(screen.getByTestId('packs-all')).toHaveTextContent('1')
    expect(getData).toHaveBeenCalledTimes(4)
  })
})
