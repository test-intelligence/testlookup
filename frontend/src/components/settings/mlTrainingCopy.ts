import type { MLStatusRead } from '@/services/appSettingsService'
import { shortAgo } from '@/utils/formatters'

/** Copy of the Settings › AI ML card (W04 AIC-05), kept apart so the card file exports only the component. */

export const NIGHTLY_COPY = 'A model is trained nightly at 03:10 UTC once enough labelled samples exist, or on demand.'
export const NIGHTLY_OFF_COPY = 'Nightly training is switched off on this server (ML_RETRAIN_ENABLED); Train now still works.'
export const REFUSAL_FALLBACK = 'Training could not be started. Try again shortly.'

export function samplesLine(s: MLStatusRead): string {
  const model = s.model_available ? `model ${s.version ?? 'unversioned'}` : 'no model yet'
  return `Samples ${s.available_samples} / ${s.min_training_samples} · human labels ${s.human_labels} · ${model}`
}

export function modelLine(s: MLStatusRead): string {
  const accuracy = s.accuracy != null ? s.accuracy.toFixed(2) : '—'
  const trained = s.trained_at ? ` · trained ${shortAgo(s.trained_at)}` : ''
  return `Model ${s.version ?? 'unversioned'} · accuracy ${accuracy}${trained}`
}

/** The message of a refused press: the 409's `detail.message`, a 503's string detail, or a generic line. */
export function refusalMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return REFUSAL_FALLBACK
}
