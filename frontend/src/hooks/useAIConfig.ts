import useSWR, { useSWRConfig } from 'swr'
import {
  appSettingsService,
  type AIModeRead,
  type AIModelStatusRead,
  type FallbackChainEntry,
  type MLStatusRead,
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
export function isLLMAvailable(config: Pick<AIModeRead, 'analysis_mode'> | undefined): boolean {
  if (!config) return true // safe default while loading: show everything
  return config.analysis_mode === 'llm' || config.analysis_mode === 'auto'
}

/**
 * ML classifier status for the Settings › AI card (W04 AIC-05; QA lead and above).
 *
 * Polls every 3 s while a training job is queued or running, so "Training…"
 * flips to the new model line without a reload; otherwise the BACKGROUND tier.
 * No fast poll while there is no data (loading or an error): an unreachable
 * status route must not be hammered every 3 s.
 */
export const ML_STATUS_TRAINING_REFRESH_MS = 3000

export function mlStatusRefreshInterval(data: MLStatusRead | undefined): number {
  return data && data.training.state !== 'idle'
    ? ML_STATUS_TRAINING_REFRESH_MS
    : REFRESH_INTERVALS.BACKGROUND
}

const ML_STATUS_KEY = 'settings/ai-ml-status'

export function useMLStatus() {
  // A NUMBER derived from the cached status, not SWR's function form: SWR
  // re-reads a function only when its pending timer fires, so after "Train now"
  // the 60 s idle timer stayed armed and the 3 s polling began up to a minute
  // late (the card sat on "Training…"). New data re-renders this hook, the
  // number changes, and SWR re-arms its timer at once.
  const { cache } = useSWRConfig()
  const cached = cache.get(ML_STATUS_KEY)?.data as MLStatusRead | undefined
  return useSWR<MLStatusRead>(
    ML_STATUS_KEY,
    () => appSettingsService.getMLStatus(),
    { refreshInterval: mlStatusRefreshInterval(cached), revalidateOnFocus: false },
  )
}

export type DeepUnavailableReason = 'disabled' | 'rules_mode'

export interface DeepInvestigationAvailability {
  available: boolean
  reason: DeepUnavailableReason | null
  message: string | null
}

export const DEEP_DISABLED_MESSAGE = 'Deep investigation is turned off in Settings › AI.'
export const DEEP_RULES_MODE_MESSAGE = 'Deep investigation needs LLM or Auto mode (current mode: Rules).'

/**
 * Whether the deep-investigation CTAs are offered, and the note that replaces
 * them when they are not (W04 contract §4.5). Mirrors the server's gate
 * (`services/deep_investigation_gate.py`): the Settings › AI toggle wins over
 * the mode, and only Rules mode refuses (ML and Auto are allowed).
 *
 * While the mode is loading it answers "available", so the CTAs render as they
 * always did. A missing `deep_investigation_enabled` (a pod of the previous
 * build) also means "available": the server still answers 422 if it is off.
 */
export function deepInvestigationAvailability(config?: AIModeRead): DeepInvestigationAvailability {
  if (!config) return { available: true, reason: null, message: null }
  if (config.deep_investigation_enabled === false) {
    return { available: false, reason: 'disabled', message: DEEP_DISABLED_MESSAGE }
  }
  if (config.analysis_mode === 'rules') {
    return { available: false, reason: 'rules_mode', message: DEEP_RULES_MODE_MESSAGE }
  }
  return { available: true, reason: null, message: null }
}

/** True when deep investigation can be triggered (the toggle is on and the mode is not Rules). */
export function isDeepEnabled(config?: AIModeRead): boolean {
  return deepInvestigationAvailability(config).available
}
