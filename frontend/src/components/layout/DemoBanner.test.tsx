/**
 * The public demo's standing notice, and the switch that decides whether the
 * app shell loads it: shown only when the server says it is the demo
 * (`GET /auth/options` → `demo_mode`), never on a real deployment, and never
 * when the answer is unknown.
 */
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockAuthOptions = vi.fn()
vi.mock('@/services/authOptions', () => ({
  getAuthOptions: () => mockAuthOptions(),
}))

import DemoBanner, { GET_TESTLOOKUP_URL } from './DemoBanner'
import { useDemoMode } from '@/hooks/useDemoMode'

function freshCache({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
}

/** Let the fetcher's promise settle and SWR re-render, so "false" is the settled answer. */
async function settle() {
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
  }
}

describe('DemoBanner', () => {
  it('says it is a read-only demo and links to the product', () => {
    render(<DemoBanner />)
    const banner = screen.getByTestId('demo-banner')
    expect(banner).toHaveTextContent(/live demo/i)
    expect(banner).toHaveTextContent(/read-only/i)
    expect(banner).toHaveTextContent(/reset every night/i)
    expect(screen.getByRole('link', { name: /get testlookup/i })).toHaveAttribute('href', GET_TESTLOOKUP_URL)
  })
})

describe('useDemoMode', () => {
  beforeEach(() => {
    mockAuthOptions.mockReset()
  })

  it('is true when the server is the public demo', async () => {
    mockAuthOptions.mockResolvedValue({ registration_enabled: false, demo_mode: true })
    const { result } = renderHook(() => useDemoMode(), { wrapper: freshCache })
    await waitFor(() => expect(result.current).toBe(true))
  })

  it.each([false, undefined])('is false when demo_mode is %s', async (flag) => {
    mockAuthOptions.mockResolvedValue({ registration_enabled: false, demo_mode: flag })
    const { result } = renderHook(() => useDemoMode(), { wrapper: freshCache })
    await waitFor(() => expect(mockAuthOptions).toHaveBeenCalled())
    await settle()
    expect(result.current).toBe(false)
  })

  it('is false when the options request fails', async () => {
    mockAuthOptions.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useDemoMode(), { wrapper: freshCache })
    await waitFor(() => expect(mockAuthOptions).toHaveBeenCalled())
    await settle()
    expect(result.current).toBe(false)
  })
})
