/**
 * Regression guard: a deliberately un-probed service is not a degraded system.
 *
 * Bug (homelab, 2026-08-16): `/health/details` returned
 *   "ollama": { "status": "skipped", "detail": "AI_OFFLINE_MODE=false — using cloud LLM" }
 * alongside an overall "status": "healthy". This hook counted every check whose
 * status was not exactly 'ok' as unavailable, so a fully working OpenRouter
 * deployment showed a permanent "Degraded: ollama unreachable" banner — the
 * opposite of what the backend said in the very same payload.
 *
 * The class is a consumer rendering from an older copy of a producer's
 * vocabulary. The cross-language half of this guard lives in
 * `backend/tests/regression/test_health_status_vocabulary_is_shared.py`, which
 * pins the backend's emitted statuses against the benign set below so a new
 * status on either side forces a decision instead of silently defaulting.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'
import axios from 'axios'

import { useSystemHealth } from './useSystemHealth'

vi.mock('axios')

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

function healthPayload(checks: Record<string, { status: string }>) {
  return {
    data: {
      status: 'healthy',
      version: '0.0.1',
      env: 'production',
      uptime_seconds: 1,
      timestamp: '2026-08-16T00:00:00Z',
      checks,
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useSystemHealth', () => {
  it('does not treat a skipped check as unavailable', async () => {
    // The exact live payload that produced the false banner.
    vi.mocked(axios.get).mockResolvedValue(
      healthPayload({
        postgres: { status: 'ok' },
        mongo: { status: 'ok' },
        redis: { status: 'ok' },
        minio: { status: 'ok' },
        ollama: { status: 'skipped' },
        chromadb: { status: 'ok' },
      }),
    )

    const { result } = renderHook(() => useSystemHealth(), { wrapper })
    await waitFor(() => expect(result.current.data).not.toBeNull())

    expect(result.current.unavailable).toEqual([])
    expect(result.current.isDegraded).toBe(false)
  })

  it('still reports a genuinely degraded service', async () => {
    // The fix must not turn the banner off altogether.
    vi.mocked(axios.get).mockResolvedValue(
      healthPayload({
        postgres: { status: 'ok' },
        ollama: { status: 'skipped' },
        minio: { status: 'degraded' },
      }),
    )

    const { result } = renderHook(() => useSystemHealth(), { wrapper })
    await waitFor(() => expect(result.current.data).not.toBeNull())

    expect(result.current.unavailable).toEqual(['minio'])
    expect(result.current.isDegraded).toBe(true)
  })

  it('treats an unrecognised status as a problem', async () => {
    // Fail loud, not silent: a status neither side knows should surface rather
    // than be assumed harmless.
    vi.mocked(axios.get).mockResolvedValue(
      healthPayload({ chromadb: { status: 'some_new_status' } }),
    )

    const { result } = renderHook(() => useSystemHealth(), { wrapper })
    await waitFor(() => expect(result.current.data).not.toBeNull())

    expect(result.current.isDegraded).toBe(true)
  })

  it('renders nothing rather than a false alarm when health is unreachable', async () => {
    vi.mocked(axios.get).mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useSystemHealth(), { wrapper })
    await waitFor(() => expect(result.current.data).toBeNull())

    expect(result.current.isDegraded).toBe(false)
  })
})
