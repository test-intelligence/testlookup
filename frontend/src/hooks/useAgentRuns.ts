import useSWR from 'swr'
import agentService from '@/services/agentService'
import type { ActiveLiveRun, AgentPipelineRun, AgentStageResult, PipelineTimeline, RunSummary } from '@/types/agent'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function usePipelines(runId?: string, limit = 100) {
  const projectId = useActiveProjectId()
  // Map "all" sentinel to undefined so backend receives no project_id filter
  const fetchProjectId = projectId === ALL_PROJECTS_ID ? undefined : (projectId ?? undefined)
  const key = runId
    ? `/pipelines?run=${runId}`
    : fetchProjectId
      ? `/pipelines?project=${fetchProjectId}&limit=${limit}`
      : `/pipelines?limit=${limit}`
  return useSWR<AgentPipelineRun[]>(
    key,
    () => agentService.listPipelines(runId, fetchProjectId, undefined, limit),
    { refreshInterval: REFRESH_INTERVALS.REALTIME, revalidateOnFocus: false },
  )
}

export function usePipelineStages(pipelineId: string | null) {
  return useSWR<AgentStageResult[]>(
    pipelineId ? `/pipelines/${pipelineId}/stages` : null,
    () => agentService.getStages(pipelineId ?? ''),
    { refreshInterval: REFRESH_INTERVALS.REALTIME, revalidateOnFocus: false },
  )
}

export function usePipelineTimeline(pipelineId: string | null) {
  return useSWR<PipelineTimeline>(
    pipelineId ? `/pipelines/${pipelineId}/timeline` : null,
    () => agentService.getTimeline(pipelineId ?? ''),
    { refreshInterval: REFRESH_INTERVALS.REALTIME, revalidateOnFocus: false },
  )
}

export function useRunSummary(runId: string | null) {
  return useSWR<RunSummary>(
    runId ? `/run-summary/${runId}` : null,
    () => agentService.getRunSummary(runId ?? ''),
    { revalidateOnFocus: false },
  )
}

export function useActiveLiveRuns() {
  return useSWR<ActiveLiveRun[]>(
    '/active-live-runs',
    () => agentService.getActiveLiveRuns().then((response) => response.active_runs),
    { refreshInterval: REFRESH_INTERVALS.REALTIME, revalidateOnFocus: false },
  )
}
