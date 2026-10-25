/**
 * W14 "agent-ops" reads (AGT-05, AGT-06, AGT-08, AGT-12, AGT-07): every new
 * read of the wave is a hook here. Each `useSWR` has `revalidateOnFocus: false`
 * and no key (so no request) until its id is known.
 */
import useSWR from 'swr'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import agentService from '@/services/agentService'
import { deepInvestigationService } from '@/services/deepInvestigationService'
import { getWorkflowDefaults, listWorkflows } from '@/services/workflowService'
import type {
  AgenticRun,
  BulkJob,
  DeepEstimate,
  DeepEstimateQuery,
  DeepHistory,
  DeepPreScan,
  PipelineCompare,
  PipelineReplay,
  WorkflowDefault,
  WorkflowDefaults,
} from '@/types/agentOps'
import type { WorkflowItem } from '@/types/workflowDefinition'

/** AGT-05: the replay (audit) document; polled only while the pipeline is in progress. */
export function usePipelineReplay(pipelineId: string | null, live: boolean) {
  return useSWR<PipelineReplay>(
    pipelineId ? ['agent-replay', pipelineId] : null,
    () => agentService.getReplay(pipelineId ?? ''),
    { refreshInterval: live ? REFRESH_INTERVALS.REALTIME : 0, revalidateOnFocus: false },
  )
}

/** AGT-06: the task graph the generic compute canvas draws. */
export function useAgenticRuntime(pipelineId: string | null, live: boolean) {
  return useSWR<AgenticRun>(
    pipelineId ? ['agent-runtime', pipelineId] : null,
    () => agentService.getAgenticRuntime(pipelineId ?? ''),
    { refreshInterval: live ? REFRESH_INTERVALS.REALTIME : 0, revalidateOnFocus: false },
  )
}

/** AGT-05: two pipelines side by side; no key unless both ids are known and differ. */
export function usePipelineCompare(a: string | null, b: string | null) {
  return useSWR<PipelineCompare>(
    a && b && a !== b ? ['agent-compare', a, b] : null,
    () => agentService.comparePipelines(a ?? '', b ?? ''),
    { revalidateOnFocus: false },
  )
}

/** What the workflow picker offers: the built-ins, each published fork's newest published version, the defaults. */
export interface WorkflowChoices {
  builtins: WorkflowItem[]
  forks: WorkflowItem[]
  defaults: WorkflowDefault[]
}

/** Each fork id's newest PUBLISHED version (drafts never execute), the latest published first, ties by id.
 *  Not exported: the module's only exports are hooks (and types), so `src/test/agentOpsMocks.ts` can stand in
 *  for the whole module in a page test. */
function publishedForks(items: WorkflowItem[]): WorkflowItem[] {
  const newest = new Map<string, WorkflowItem>()
  for (const item of items) {
    if (item.built_in || item.status !== 'published') continue
    const seen = newest.get(item.workflow_id)
    if (!seen || item.version > seen.version) newest.set(item.workflow_id, item)
  }
  return [...newest.values()].sort(
    (x, y) => (y.published_at ?? '').localeCompare(x.published_at ?? '') || x.workflow_id.localeCompare(y.workflow_id),
  )
}

/** AGT-08: the picker's choices, read only once the picker opens (`enabled`). */
export function useWorkflowChoices(projectId: string | null, enabled: boolean) {
  return useSWR<WorkflowChoices>(
    projectId && enabled ? ['workflow-choices', projectId] : null,
    async () => {
      const id = projectId ?? ''
      const [items, defaults] = await Promise.all([listWorkflows(id), getWorkflowDefaults(id)])
      return {
        builtins: items.filter((item) => item.built_in),
        forks: publishedForks(items),
        defaults: defaults.defaults,
      }
    },
    { revalidateOnFocus: false },
  )
}

/** AGT-08: the project's default per base (the editor's "Use for new runs" switch). */
export function useWorkflowDefaults(projectId: string | null) {
  return useSWR<WorkflowDefaults>(
    projectId ? ['workflow-defaults', projectId] : null,
    () => getWorkflowDefaults(projectId ?? ''),
    { revalidateOnFocus: false },
  )
}

/** AGT-12: a bulk job's progress; polled while active, stopped once finished / cancelled. */
export function useBulkJob(jobId: string | null) {
  return useSWR<BulkJob>(
    jobId ? ['bulk-job', jobId] : null,
    () => agentService.getBulkJob(jobId ?? ''),
    {
      refreshInterval: (data?: BulkJob) => (data?.state === 'active' ? REFRESH_INTERVALS.REALTIME : 0),
      revalidateOnFocus: false,
    },
  )
}

/** AGT-07: the error-signature pre-scan of a run's failing results. */
export function usePreScan(runId: string | null) {
  return useSWR<DeepPreScan>(
    runId ? ['deep-prescan', runId] : null,
    () => deepInvestigationService.getPreScan(runId ?? ''),
    { revalidateOnFocus: false },
  )
}

/** AGT-07: the project's deep-investigated runs, one page. */
export function useDeepHistory(projectId: string | null, page: number) {
  return useSWR<DeepHistory>(
    projectId ? ['deep-history', projectId, page] : null,
    () => deepInvestigationService.getHistory(projectId ?? '', page),
    { revalidateOnFocus: false },
  )
}

/** AGT-07: the measured estimate for a run configuration; the previous answer stays while a new one loads. */
export function useDeepEstimate(runId: string | null, q: DeepEstimateQuery) {
  return useSWR<DeepEstimate>(
    runId ? ['deep-estimate', runId, JSON.stringify(q)] : null,
    () => deepInvestigationService.getEstimate(runId ?? '', q),
    { revalidateOnFocus: false, keepPreviousData: true },
  )
}
