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
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

const getAIMode = vi.fn(async () => ({ analysis_mode: 'rules' as const }))
const getAIConfig = vi.fn()
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: { getAIMode: () => getAIMode(), getAIConfig: () => getAIConfig() },
}))

import { isLLMAvailable, useAIConfig } from './useAIConfig'

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
