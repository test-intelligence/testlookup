import useSWR from 'swr'
import { aiTestHistoryService } from '@/services/aiTestHistoryService'
import type { AITestHistory } from '@/types/aiTestHistory'

/**
 * W03 AIH-05: the stored AI versions of one test result, read when its popover
 * opens (contract §9.8). The key is null until both ids exist, so the history
 * strip itself never asks: one read per opened popover, none per run.
 */
export function useTestAIVersions(runId?: string | null, testId?: string | null) {
  return useSWR<AITestHistory>(
    runId && testId ? ['test-ai-versions', runId, testId] : null,
    ([, run, test]: readonly [string, string, string]) =>
      aiTestHistoryService.getTestAIVersions(run, test),
  )
}
