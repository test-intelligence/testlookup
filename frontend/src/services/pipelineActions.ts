/**
 * W04 AGT-04: what a person's Retry / Stop on a pipeline did, in words.
 *
 * Pure helpers shared by the `/agents` pipeline rows and the Deep
 * Investigation page (`DeepInvestigationPage`, `RunDeepClusters`). Kept out of
 * `agentService` so a test that mocks the service still gets the real copy.
 *
 * Every refusal is shown: a 409 carries the server's `detail.message`
 * (verbatim), anything else (a 422 from the deep-investigation gate, a 403, a
 * 5xx) is read through `extractErrorMessage`. Nothing fails silently.
 */
import type { CancelPipelineResponse, PipelineActionConflict, RetryPipelineResponse } from '@/types/agent'
import { extractErrorMessage } from './apiErrors'

interface ErrorWithResponse {
  response?: { status?: number; data?: { detail?: unknown } }
  message?: string
}

/** The 409 `detail` of a retry/cancel refusal, or null for any other failure. */
export function readPipelineActionConflict(err: unknown): PipelineActionConflict | null {
  const response = (err as ErrorWithResponse | null)?.response
  if (response?.status !== 409) return null
  const detail = response.data?.detail
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  const message = (detail as { message?: unknown }).message
  if (typeof message !== 'string' || !message.trim()) return null
  return detail as PipelineActionConflict
}

/** The server's own words for a failed request, never silence. */
export function pipelineActionError(err: unknown, fallback: string): string {
  const e = err as ErrorWithResponse | null
  return extractErrorMessage(e?.response?.data?.detail, e?.message || fallback)
}

/** The copy of a 409: review rejection has its own line; everything else is the server's message. */
export function conflictText(conflict: PipelineActionConflict): string {
  if (conflict.reason === 'review_rejected') return 'Rejected in review — start a new run.'
  return conflict.message
}

/** The `/agents` row note after a successful Retry. */
export function retryOutcomeText(res: RetryPipelineResponse): string {
  if (res.mode === 'resume') {
    return `Resumed (attempt ${res.attempt}/${res.max_attempts}) — keeps the completed stages.`
  }
  if (res.mode === 'rerun') {
    return res.reason === 'no_frozen_plan'
      ? 'Started a new run — this run has no saved plan to resume.'
      : 'Started a new run — the analysis configuration changed since this run started.'
  }
  return `Retry accepted (${res.mode}).`
}

/** The Deep Investigation note after a successful "Retry from last successful stage". */
export function deepRetryOutcomeText(res: RetryPipelineResponse): string {
  if (res.mode === 'resume') {
    return `Resuming from the last successful stage (attempt ${res.attempt}/${res.max_attempts}).`
  }
  if (res.mode === 'rerun') {
    return res.reason === 'no_frozen_plan'
      ? 'This run has no saved plan to resume, so a new deep run was started instead.'
      : 'The configuration changed, so a new deep run was started instead.'
  }
  return `Retry accepted (${res.mode}).`
}

/** The `/agents` row note after a successful Stop. */
export function cancelOutcomeText(res: CancelPipelineResponse): string {
  return res.terminal ? 'Stopped.' : 'Stop requested — the run ends at its next stage boundary.'
}

/** The toast after "Start a new run" (and the header trigger): queued, or the server's "already in progress". */
export const PIPELINE_QUEUED = 'Pipeline queued — it will appear in the list shortly.'
