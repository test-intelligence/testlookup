/**
 * The agent-action queue's reads and its one write (W15 AGT-01 / AGT-03).
 *
 * The server's list is QA_LEAD-only, so every read is keyed only for a QA
 * lead+ (the caller passes `enabled`, or the hook reads `usePermissions`):
 * for anyone else there is no key, no request and no 403 toast. A project is
 * required (the queue belongs to one); in All Projects mode nothing is asked.
 *
 * - `usePendingAgentActions` — the Inbox badge count AND the Waiting list read
 *   the SAME key (one request); `data.total` is the server's COUNT.
 * - `useDecidedAgentActions` — Recently decided; polls while a row is
 *   approved / executing, then stops.
 * - `useRunAgentActions` — one run's rows for the Decision panel's chips; the
 *   project comes from the Run page's own `useRun(runId)` cache.
 * - `useDecideAgentAction` — approve / reject / undo, then revalidates every
 *   agent-actions key of the project and the investigation details.
 */
import { useCallback } from 'react'
import useSWR, { unstable_serialize, useSWRConfig } from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { usePermissions } from '@/hooks/usePermissions'
import { useRun } from '@/hooks/useRuns'
import { agentActionService } from '@/services/agentActionService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import {
  isAgentActionRunning,
  type AgentAction,
  type AgentActionDecision,
  type AgentActionList,
} from '@/types/agentActions'

// This module exports HOOKS ONLY: `src/test/agentActionMocks.ts` replaces it
// whole in the page vitests (pure helpers live in `@/types/agentActions`).
const PENDING_AGENT_ACTIONS_LIMIT = 200
const DECIDED_AGENT_ACTIONS_LIMIT = 20
const RUN_AGENT_ACTIONS_LIMIT = 50

function oneProject(projectId: string | null | undefined): string | null {
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

/** The decided list's poll: REALTIME while anything runs, else off. Runs on EVERY render now (the cached
 *  value), so an answer without `items` (a catch-all mock, a proxy page) must not throw: it crashed /my-failures
 *  in review-gate's hermetic world. */
function decidedAgentActionsRefreshInterval(latest: AgentActionList | undefined): number {
  const items = latest?.items
  return Array.isArray(items) && items.some(isAgentActionRunning) ? REFRESH_INTERVALS.REALTIME : 0
}

export function usePendingAgentActions(projectId: string | null, enabled: boolean) {
  const project = oneProject(projectId)
  return useSWR<AgentActionList>(
    enabled && project ? ['agent-actions', project, 'pending'] : null,
    () => agentActionService.listAgentActions(project as string, {
      view: 'pending',
      limit: PENDING_AGENT_ACTIONS_LIMIT,
    }),
    { revalidateOnFocus: false },
  )
}

export function useDecidedAgentActions(projectId: string | null, enabled: boolean) {
  const project = oneProject(projectId)
  const key = enabled && project ? ['agent-actions', project, 'decided'] : null
  // A NUMBER derived from the cached list, not SWR's function form: SWR reads a
  // function only when its polling effect starts (no data yet → 0 → no timer)
  // and after a timer it armed fires, so a list that gained a running row
  // after an Approve never started polling. New data re-renders this hook, the
  // number changes, and SWR re-arms its timer at once (`useMLStatus`'s rule).
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(unstable_serialize(key))?.data as AgentActionList | undefined) : undefined
  return useSWR<AgentActionList>(
    key,
    () => agentActionService.listAgentActions(project as string, {
      view: 'decided',
      limit: DECIDED_AGENT_ACTIONS_LIMIT,
    }),
    { revalidateOnFocus: false, refreshInterval: decidedAgentActionsRefreshInterval(cached) },
  )
}

export function useRunAgentActions(runId: string | null) {
  const { isQaLead } = usePermissions()
  const { data: run } = useRun(runId ?? undefined)
  const project = oneProject(run?.project_id)
  return useSWR<AgentActionList>(
    isQaLead && runId && project ? ['agent-actions', project, 'run', runId] : null,
    () => agentActionService.listAgentActions(project as string, {
      test_run_id: runId as string,
      limit: RUN_AGENT_ACTIONS_LIMIT,
    }),
    { revalidateOnFocus: false },
  )
}

/** Whether an SWR key belongs to this project's agent-action reads. */
function isAgentActionsKey(key: unknown, projectId: string): boolean {
  return Array.isArray(key) && key[0] === 'agent-actions' && key[1] === projectId
}

/** Whether an SWR key is an investigation detail (`useInvestigation`). */
function isInvestigationDetailKey(key: unknown): boolean {
  return typeof key === 'string' && key.startsWith('/investigations/')
}

export function useDecideAgentAction(projectId: string | null) {
  const { mutate } = useSWRConfig()
  return useCallback(
    async (actionId: string, body: AgentActionDecision): Promise<AgentAction> => {
      const project = oneProject(projectId)
      if (!project) throw new Error('Choose one project to decide an agent action.')
      const saved = await agentActionService.decideAgentAction(project, actionId, body)
      await Promise.all([
        mutate((key) => isAgentActionsKey(key, project)),
        mutate((key) => isInvestigationDetailKey(key)),
      ])
      return saved
    },
    [mutate, projectId],
  )
}
