/**
 * D-108: a refused Generate pack raised the same toast twice, once from the
 * global Axios interceptor and once from the panel's catch. These drive the
 * REAL `compliancePackService` through the REAL interceptor (a stubbed axios
 * adapter), so a request that drops `suppressToast` toasts twice here.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())
const packsState = vi.hoisted(() => ({ packs: [] as unknown[] }))

vi.mock('@/store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 'access-token', refreshAccessToken: vi.fn() }) },
}))
vi.mock('react-hot-toast', () => ({ default: { error: toastError, success: vi.fn() } }))
vi.mock('@/hooks/useCompliancePacks', () => ({
  useCompliancePacks: () => ({ packs: packsState.packs, isLoading: false, isError: false, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useProjectCompliancePacks', () => ({ refreshProjectCompliancePacks: vi.fn() }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ canAccessManagement: true }) }))

const { api } = await import('@/services/api')
const { default: CompliancePackPanel } = await import('./CompliancePackPanel')

const REFUSAL = 'Release has no recorded release gate decision — evaluate the gate first'

function refuse(status: number, detail: unknown) {
  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const response = { status, statusText: 'Conflict', data: { detail }, headers: {}, config } as AxiosResponse
    throw new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', config, {}, response)
  }
}

const signedPack = {
  id: 'pack-9', release_id: 'rel-1', generated_at: '2026-10-21T12:00:00Z', bytes: 4096, file_count: 13,
  manifest_sha256: 'cd'.repeat(32), notes: null, signing_key_id: 'k-1a2b3c4d5e6f7a8b',
}

describe('CompliancePackPanel toasts each failure once (D-108)', () => {
  beforeEach(() => {
    toastError.mockReset()
    packsState.packs = []
  })
  afterEach(() => {
    api.defaults.adapter = undefined
  })

  it('a refused generate toasts the server sentence once', async () => {
    refuse(409, REFUSAL)
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: /Generate pack/ }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    await screen.findByRole('button', { name: /Generate pack/ })
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith(REFUSAL)
  })

  it('a failed ZIP download and a failed public key download toast once each', async () => {
    packsState.packs = [signedPack]
    refuse(500, 'storage unavailable')
    render(<CompliancePackPanel releaseId="rel-1" />)

    fireEvent.click(screen.getByRole('button', { name: /ZIP/ }))
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1))
    expect(String(toastError.mock.calls[0][0])).toMatch(/^Download failed/)

    fireEvent.click(screen.getByRole('button', { name: /Public key/ }))
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(2))
    // Let any second (interceptor) toast land before counting.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(toastError).toHaveBeenCalledTimes(2)
  })
})
