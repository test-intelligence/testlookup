/**
 * `useAIConfig` reads the analysis mode every role may read
 * (`GET /settings/ai/mode`), never the QA-lead-only config (`GET /settings/ai`).
 *
 * The sidebar's Ask AI entry and the chat, release-gate, run evidence and
 * pipeline pages call it for every role. It asked `/settings/ai`, so a QA
 * engineer, tester or viewer got "Requires at least QA_LEAD role" toasts on
 * every page and never saw Ask AI (the UX redesign's browser E2E pass).
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

const getAIMode = vi.fn(async () => ({ analysis_mode: 'rules' as const }))
const getAIConfig = vi.fn()
const getMLStatus = vi.fn()
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getAIMode: () => getAIMode(),
    getAIConfig: () => getAIConfig(),
    getMLStatus: () => getMLStatus(),
  },
}))

import type { MLStatusRead } from '@/services/appSettingsService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import {
  DEEP_DISABLED_MESSAGE,
  DEEP_RULES_MODE_MESSAGE,
  ML_STATUS_TRAINING_REFRESH_MS,
  deepInvestigationAvailability,
  isDeepEnabled,
  isLLMAvailable,
  mlStatusRefreshInterval,
  useAIConfig,
  useMLStatus,
} from './useAIConfig'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

describe('useAIConfig', () => {
  it('asks the mode every role may read, and never the QA-lead-only config', async () => {
    const { result } = renderHook(() => useAIConfig(), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ analysis_mode: 'rules' }))
    expect(getAIMode).toHaveBeenCalledTimes(1)
    expect(getAIConfig).not.toHaveBeenCalled()
  })

  it('the mode alone decides whether an LLM is available', () => {
    expect(isLLMAvailable({ analysis_mode: 'rules' })).toBe(false)
    expect(isLLMAvailable({ analysis_mode: 'ml' })).toBe(false)
    expect(isLLMAvailable({ analysis_mode: 'auto' })).toBe(true)
    expect(isLLMAvailable({ analysis_mode: 'llm' })).toBe(true)
  })
})

/**
 * W04 §4.5: the deep CTAs follow the Settings › AI toggle and the mode, from
 * the read every role may make (`GET /settings/ai/mode`). Mirrors the server's
 * gate: the toggle wins over the mode; only Rules refuses.
 */
describe('deepInvestigationAvailability', () => {
  it('offers deep investigation while the mode is still loading', () => {
    expect(deepInvestigationAvailability(undefined)).toEqual({ available: true, reason: null, message: null })
    expect(isDeepEnabled(undefined)).toBe(true)
  })

  it('is off with the toggle off, whatever the mode', () => {
    for (const mode of ['auto', 'llm', 'ml', 'rules'] as const) {
      expect(deepInvestigationAvailability({ analysis_mode: mode, ai_offline_mode: true, deep_investigation_enabled: false }))
        .toEqual({ available: false, reason: 'disabled', message: 'Deep investigation is turned off in Settings › AI.' })
    }
    expect(DEEP_DISABLED_MESSAGE).toBe('Deep investigation is turned off in Settings › AI.')
  })

  it('is off in Rules mode with the toggle on', () => {
    expect(deepInvestigationAvailability({ analysis_mode: 'rules', ai_offline_mode: true, deep_investigation_enabled: true }))
      .toEqual({ available: false, reason: 'rules_mode', message: DEEP_RULES_MODE_MESSAGE })
    expect(DEEP_RULES_MODE_MESSAGE).toBe('Deep investigation needs LLM or Auto mode (current mode: Rules).')
  })

  it('is on in LLM, Auto and ML mode with the toggle on', () => {
    for (const mode of ['auto', 'llm', 'ml'] as const) {
      const config = { analysis_mode: mode, ai_offline_mode: false, deep_investigation_enabled: true }
      expect(deepInvestigationAvailability(config)).toEqual({ available: true, reason: null, message: null })
      expect(isDeepEnabled(config)).toBe(true)
    }
  })

  it('treats a missing toggle (a pod of the previous build) as on', () => {
    expect(deepInvestigationAvailability({ analysis_mode: 'auto', ai_offline_mode: true }).available).toBe(true)
    expect(isDeepEnabled({ analysis_mode: 'rules', ai_offline_mode: true })).toBe(false)
  })
})

describe('useMLStatus', () => {
  const idle = { state: 'idle', task_id: null, queued_at: null, started_at: null, triggered_by: null }
  const status = (state: string) => ({ training: { ...idle, state } }) as unknown as MLStatusRead

  it('polls every 3 s while a job is queued or running, else on the background tier', () => {
    expect(ML_STATUS_TRAINING_REFRESH_MS).toBe(3000)
    expect(mlStatusRefreshInterval(status('queued'))).toBe(3000)
    expect(mlStatusRefreshInterval(status('running'))).toBe(3000)
    expect(mlStatusRefreshInterval(status('idle'))).toBe(REFRESH_INTERVALS.BACKGROUND)
    // No data (loading, or the route is failing): no fast poll.
    expect(mlStatusRefreshInterval(undefined)).toBe(REFRESH_INTERVALS.BACKGROUND)
  })

  it('reads GET /settings/ai/ml/status through the service', async () => {
    getMLStatus.mockResolvedValue(status('idle'))
    const { result } = renderHook(() => useMLStatus(), { wrapper })
    await waitFor(() => expect(result.current.data?.training.state).toBe('idle'))
    expect(getMLStatus).toHaveBeenCalledTimes(1)
  })

  it('re-arms the poll at 3 s the moment a job is queued, not after the 60 s idle timer', async () => {
    // SWR re-reads a function-form refreshInterval only when its pending timer
    // fires: after "Train now" the card sat on "Training…" for up to a minute.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      getMLStatus.mockReset()
      getMLStatus.mockResolvedValueOnce(status('idle')).mockResolvedValue(status('queued'))
      const { result } = renderHook(() => useMLStatus(), { wrapper })
      await waitFor(() => expect(result.current.data?.training.state).toBe('idle'))

      await act(async () => {
        await result.current.mutate() // what the card does after the POST
      })
      await waitFor(() => expect(result.current.data?.training.state).toBe('queued'))
      const before = getMLStatus.mock.calls.length

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_100)
      })
      expect(getMLStatus.mock.calls.length).toBeGreaterThan(before)
    } finally {
      vi.useRealTimers()
    }
  })
})
