/**
 * W39 STRAT-07: the run's stored observability evidence (`GET /api/v1/runs/{run_id}/observability-timeline`).
 * A read of what the AI analysis already fetched — the server fetches nothing and writes nothing for it. Through the
 * shared API client; `suppressToast`: the card renders its own failure as a sentence.
 */
import { getData } from './http'
import type { RunObservabilityTimeline } from '@/types/observabilityTimeline'

export const observabilityTimelineService = {
  getRunObservabilityTimeline: (runId: string) =>
    getData<RunObservabilityTimeline>(`/api/v1/runs/${encodeURIComponent(runId)}/observability-timeline`, {
      suppressToast: true,
    }),
}
