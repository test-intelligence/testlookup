import useSWR from 'swr'
import { appSettingsService, type AIConfigRead } from '@/services/appSettingsService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useAIConfig() {
  return useSWR<AIConfigRead>(
    'settings/ai-config',
    () => appSettingsService.getAIConfig(),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
  )
}

/** True when the configured mode can invoke an LLM (llm or auto). */
export function isLLMAvailable(config: AIConfigRead | undefined): boolean {
  if (!config) return true // safe default while loading: show everything
  return config.analysis_mode === 'llm' || config.analysis_mode === 'auto'
}

/** True when deep investigation can be triggered. */
export function isDeepEnabled(config: AIConfigRead | undefined): boolean {
  if (!config) return true
  return config.deep_investigation_enabled && config.analysis_mode !== 'rules'
}
