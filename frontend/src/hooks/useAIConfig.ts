import useSWR from 'swr'
import {
  appSettingsService,
  type AIConfigRead,
  type AIModeRead,
  type AIModelStatusRead,
  type FallbackChainEntry,
} from '@/services/appSettingsService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

/**
 * The AI configuration as every role may read it: the analysis mode
 * (`GET /settings/ai/mode`). The sidebar's Ask AI entry and the chat,
 * release-gate, run evidence and pipeline pages branch on it for every role;
 * they read `GET /settings/ai` (QA lead and above), so every lower role got
 * "Requires at least QA_LEAD role" toasts on every page and never saw Ask AI
 * (the UX redesign's browser E2E pass). The full config is the AI settings
 * page's own read.
 */
export function useAIConfig() {
  return useSWR<AIModeRead>(
    'settings/ai-mode',
    () => appSettingsService.getAIMode(),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
  )
}

/**
 * Live model-presence + fallback-chain state (US-13.2).
 *
 * Polls on the ACTIVE tier rather than BACKGROUND: an operator side-loading
 * a model pack into an air-gapped box is watching this page to see the
 * import land, and a 60 s wait reads as "it didn't work".
 */
export function useAIModelStatus() {
  return useSWR<AIModelStatusRead>(
    'settings/ai-model-status',
    () => appSettingsService.getAIModelStatus(),
    { refreshInterval: REFRESH_INTERVALS.ACTIVE, revalidateOnFocus: true },
  )
}

/**
 * The tier that will actually run, or null while loading.
 *
 * Auto mode walks the chain and takes the first available tier. A pinned
 * mode does NOT — it runs its engine, and degrades straight to rules when
 * that engine is unavailable (analysis_router's terminal fallback). Showing
 * the first-available tier for a pinned mode would be a lie.
 */
export function activeTier(status: AIModelStatusRead | undefined): FallbackChainEntry | null {
  if (!status) return null
  const chain = status.fallback_chain
  if (status.analysis_mode !== 'auto') {
    const pinned = chain.find(e => e.mode === status.analysis_mode)
    if (pinned?.available) return pinned
    return chain.find(e => e.mode === 'rules') ?? null
  }
  return chain.find(e => e.available) ?? null
}

/** True when the configured mode can invoke an LLM (llm or auto). */
export function isLLMAvailable(config: AIModeRead | undefined): boolean {
  if (!config) return true // safe default while loading: show everything
  return config.analysis_mode === 'llm' || config.analysis_mode === 'auto'
}

/** True when deep investigation can be triggered. */
export function isDeepEnabled(config: AIConfigRead | undefined): boolean {
  if (!config) return true
  return config.deep_investigation_enabled && config.analysis_mode !== 'rules'
}
