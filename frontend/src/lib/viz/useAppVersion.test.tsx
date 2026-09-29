import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { describe, expect, it, vi } from 'vitest'
import { useAppVersion } from './useAppVersion'

const get = vi.fn()
vi.mock('axios', () => ({ default: { get: (...args: unknown[]) => get(...args) } }))

function wrapper(cached?: unknown) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <SWRConfig value={{ provider: () => new Map(), fallback: cached ? { 'system-health': cached } : {} }}>
        {children}
      </SWRConfig>
    )
  }
}

describe('useAppVersion (VIZ-606)', () => {
  it('reads the version the health poll cached', () => {
    const { result } = renderHook(() => useAppVersion(), { wrapper: wrapper({ version: '1.4.2' }) })
    expect(result.current).toBe('1.4.2')
  })

  it('"unknown" (a local build) and nothing cached are both null — the footer leaves the version out', () => {
    expect(renderHook(() => useAppVersion(), { wrapper: wrapper({ version: 'unknown' }) }).result.current).toBeNull()
    expect(renderHook(() => useAppVersion(), { wrapper: wrapper() }).result.current).toBeNull()
  })

  it('never makes a request of its own', () => {
    renderHook(() => useAppVersion(), { wrapper: wrapper() })
    expect(get).not.toHaveBeenCalled()
  })

  // Review N9: SWR 2 uses a GLOBAL fetcher when the hook's own is null.
  it('does not fetch even under an SWRConfig that supplies a global fetcher', async () => {
    const fetcher = vi.fn(async () => ({ version: '9.9.9' }))
    const { result } = renderHook(() => useAppVersion(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <SWRConfig value={{ provider: () => new Map(), fetcher }}>{children}</SWRConfig>
      ),
    })
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current).toBeNull()
  })
})
