/**
 * W23c AGT-11: the reads of `/agents` › Try an agent.
 *
 * - `useAgentCatalog(enabled)` — keyed ONLY while the section is open (`enabled`): the page's default
 *   load asks nothing new.
 * - `useAgentCatalogAgent(agentId)` — the picked agent's detail (its `inline_input_json_schema`).
 * - `useInvocation(invocationId)` — one invocation, read through `parseInvocation` (a malformed answer is
 *   `data: null` WITH an `error`: "Could not load the invocation."). It polls every
 *   `REFRESH_INTERVALS.REALTIME` ONLY while the invocation is `in_progress`, then stops. The interval is a
 *   NUMBER derived from the cached answer, never SWR's function form: SWR reads a function only when its
 *   polling effect starts (no data yet → 0 → no timer) and never re-arms it from 0 (the W22b demo bug).
 *   New data re-renders this hook, the number changes, and SWR re-arms at once.
 *
 * No focus revalidation anywhere (`swrConfig`'s `resumePollingWhenVisible` refetches a polling hook once
 * when the tab comes back).
 */
import useSWR, { unstable_serialize, useSWRConfig } from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { getCatalogAgent, getInvocation, listCatalog } from '@/services/agentInvokeService'
import {
  parseInvocation,
  type AgentCatalogDetail,
  type AgentCatalogEntry,
  type AgentInvocation,
} from '@/types/agentInvoke'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_INVOCATION = 'The invocation answer could not be read.'

/** The poll of one invocation: REALTIME while it runs, else off (pure; exported for the tests). */
export function invocationRefreshInterval(invocation: AgentInvocation | null | undefined): number {
  return invocation?.status === 'in_progress' ? REFRESH_INTERVALS.REALTIME : 0
}

export function invocationKey(invocationId: string | null | undefined): readonly [string, string] | null {
  return invocationId ? ['agent-invocation', invocationId] : null
}

export function useAgentCatalog(enabled: boolean) {
  return useSWR<AgentCatalogEntry[]>(enabled ? ['agent-catalog'] : null, () => listCatalog(), {
    revalidateOnFocus: false,
  })
}

export function useAgentCatalogAgent(agentId: string | null | undefined) {
  return useSWR<AgentCatalogDetail>(
    agentId ? ['agent-catalog', agentId] : null,
    () => getCatalogAgent(agentId as string),
    { revalidateOnFocus: false },
  )
}

async function fetchInvocation(invocationId: string): Promise<AgentInvocation> {
  const parsed = parseInvocation(await getInvocation(invocationId))
  if (parsed === null) throw new Error(MALFORMED_INVOCATION)
  return parsed
}

export function useInvocation(invocationId: string | null | undefined) {
  const key = invocationKey(invocationId)
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(unstable_serialize(key))?.data as AgentInvocation | undefined) : undefined
  return useSWR<AgentInvocation>(key, () => fetchInvocation(invocationId as string), {
    revalidateOnFocus: false,
    refreshInterval: invocationRefreshInterval(cached),
  })
}
