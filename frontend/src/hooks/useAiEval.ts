/**
 * The AI-evaluation page's reads and writes (W16 AGT-09 / AGT-13).
 *
 * Every read is `useSWR` with `revalidateOnFocus: false`; no key = no request.
 * Every SWR key starts `['ai-eval', …]` (the page's four older reads keep their
 * own keys). A per-project read keys only on ONE real project (never All
 * Projects); the two ADMIN-only reads (candidate options, a run's items) key
 * only when the caller passes `enabled` (= `isAdmin`), so a QA lead makes no
 * request and sees no 403 toast.
 *
 * A list answer is `{ items, total, … }`: read it as
 * `Array.isArray(data?.items) ? data.items : []` (a catch-all mock or an old
 * server must not crash a render).
 *
 * This module exports HOOKS ONLY: `src/test/aiEvalMocks.ts` replaces it whole
 * in the component vitests (pure helpers live in `@/types/aiEval`).
 */
import { useCallback } from 'react'
import useSWR, { unstable_serialize, useSWRConfig } from 'swr'
import {
  evaluateCandidate,
  getCandidateOptions,
  getEvalManifest,
  getEvalRun,
  labelShadowPair,
  listAgentStackGateRuns,
  listEvalRunItems,
  listEvalRuns,
  listReviewerQualityRuns,
  listShadowPairs,
  listTierComparisonRuns,
  setBaseline,
  type AIEvalRun,
} from '@/services/aiEvalService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import {
  evalRunRefreshInterval,
  evalRunsRefreshInterval,
  type CandidateEvaluationAccepted,
  type EvalCandidate,
  type EvalRunList,
  type ShadowPair,
  type ShadowPairLabel,
} from '@/types/aiEval'

const GATE_RUNS_LIMIT = 20
const EVAL_RUNS_LIMIT = 20
const RUN_ITEMS_LIMIT = 25
const TIER_RUNS_LIMIT = 20
const REVIEWER_RUNS_LIMIT = 20
const SHADOW_PAIRS_LIMIT = 25

const SWR_OPTIONS = { revalidateOnFocus: false } as const

function oneProject(projectId: string | null | undefined): string | null {
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

/**
 * A NUMBER from the cached value, not SWR's function form: SWR reads a function
 * only when its polling effect starts (no data yet → 0 → no timer), so a list
 * that gained a queued run after Start would never poll (W15's rule). New data
 * re-renders the hook, the number changes and SWR re-arms its timer.
 */
function useCachedValue<T>(key: unknown[] | null): T | undefined {
  const { cache } = useSWRConfig()
  return key ? (cache.get(unstable_serialize(key))?.data as T | undefined) : undefined
}

// ── Agent stack ──────────────────────────────────────────────────────────────

export function useAgentStackGateRuns(enabled: boolean) {
  return useSWR(
    enabled ? ['ai-eval', 'gate-runs'] : null,
    () => listAgentStackGateRuns({ limit: GATE_RUNS_LIMIT }),
    SWR_OPTIONS,
  )
}

export function useEvalManifest(checksum: string | null) {
  return useSWR(
    checksum ? ['ai-eval', 'manifest', checksum] : null,
    () => getEvalManifest(checksum as string),
    SWR_OPTIONS,
  )
}

// ── Candidate evaluation ─────────────────────────────────────────────────────

/** ADMIN only: pass `enabled = isAdmin`. */
export function useCandidateOptions(enabled: boolean) {
  return useSWR(enabled ? ['ai-eval', 'candidate-options'] : null, () => getCandidateOptions(), SWR_OPTIONS)
}

export interface EvalRunsQuery {
  dataset_id?: string
  task_type?: string
  metric_kind?: string
  status?: string
  /** Default 20 (a different limit gets its own key). */
  limit?: number
}

/** The page's own dashboard read (its pre-W16 key `['ai-eval/dashboard', days]`). */
function isDashboardKey(key: unknown): boolean {
  return Array.isArray(key) && key[0] === 'ai-eval/dashboard'
}

/**
 * The runs list; polls every 5 s while a run is queued / running, then stops. The poll that sees the last run
 * finish also revalidates the dashboard: its Recent Evaluation Runs (Overview) was read before the run existed.
 */
export function useEvalRuns(q: EvalRunsQuery, enabled: boolean) {
  const { mutate } = useSWRConfig()
  const limit = q.limit ?? EVAL_RUNS_LIMIT
  const base = ['ai-eval', 'runs', q.dataset_id, q.task_type, q.metric_kind, q.status]
  const key = enabled ? (limit === EVAL_RUNS_LIMIT ? base : [...base, limit]) : null
  const polling = evalRunsRefreshInterval(useCachedValue<EvalRunList>(key))
  return useSWR<EvalRunList>(
    key,
    () => listEvalRuns({
      dataset_id: q.dataset_id,
      task_type: q.task_type,
      metric_kind: q.metric_kind,
      status: q.status,
      limit,
    }),
    {
      ...SWR_OPTIONS,
      refreshInterval: polling,
      onSuccess: (data) => {
        if (polling > 0 && evalRunsRefreshInterval(data) === 0) void mutate(isDashboardKey)
      },
    },
  )
}

/** One run; the same polling rule. */
export function useEvalRun(runId: string | null) {
  const key = runId ? ['ai-eval', 'run', runId] : null
  const cached = useCachedValue<AIEvalRun>(key)
  return useSWR<AIEvalRun>(
    key,
    () => getEvalRun(runId as string),
    { ...SWR_OPTIONS, refreshInterval: evalRunRefreshInterval(cached) },
  )
}

export interface EvalRunItemsQuery {
  status?: string
  limit?: number
  offset?: number
}

/** ADMIN only (the route is ADMIN): pass `enabled = isAdmin`. 25 a page by default. */
export function useEvalRunItems(runId: string | null, q: EvalRunItemsQuery, enabled: boolean) {
  const limit = q.limit ?? RUN_ITEMS_LIMIT
  const offset = q.offset ?? 0
  return useSWR(
    enabled && runId ? ['ai-eval', 'run-items', runId, q.status, limit, offset] : null,
    () => listEvalRunItems(runId as string, { status: q.status, limit, offset }),
    SWR_OPTIONS,
  )
}

// ── Per-project reads (one real project only) ───────────────────────────────

/** The project's recorded G2 comparisons. */
export function useTierComparisonRuns(projectId: string | null, agentId?: string) {
  const project = oneProject(projectId)
  return useSWR(
    project ? ['ai-eval', 'tier-runs', project, agentId] : null,
    () => listTierComparisonRuns(project as string, { agent_id: agentId, limit: TIER_RUNS_LIMIT }),
    SWR_OPTIONS,
  )
}

/** The project's G3 history (read-only). */
export function useReviewerQualityRuns(projectId: string | null) {
  const project = oneProject(projectId)
  return useSWR(
    project ? ['ai-eval', 'reviewer-runs', project] : null,
    () => listReviewerQualityRuns(project as string, { limit: REVIEWER_RUNS_LIMIT }),
    SWR_OPTIONS,
  )
}

/**
 * The project's shadow pairs. The Tiers tab badge (`pending_total`) and the
 * labelling queue's default view read the SAME key (one request); a later
 * page (`offset` > 0) adds the offset to the key.
 */
export function useShadowPairs(
  projectId: string | null,
  agentId?: string,
  labelStatus: string = 'pending',
  offset: number = 0,
) {
  const project = oneProject(projectId)
  const base = ['ai-eval', 'shadow-pairs', project, agentId, labelStatus]
  return useSWR(
    project ? (offset > 0 ? [...base, offset] : base) : null,
    () => listShadowPairs(project as string, {
      agent_id: agentId,
      label_status: labelStatus,
      limit: SHADOW_PAIRS_LIMIT,
      offset,
    }),
    SWR_OPTIONS,
  )
}

// ── Mutations ────────────────────────────────────────────────────────────────

/** Whether an SWR key is one of this module's (`['ai-eval', <kind>, …]`). */
function isAiEvalKey(key: unknown, kind: string, projectId?: string): boolean {
  return (
    Array.isArray(key)
    && key[0] === 'ai-eval'
    && key[1] === kind
    && (projectId === undefined || key[2] === projectId)
  )
}

/** Start a candidate replay, then revalidate every runs list (the new run shows `queued`). */
export function useEvaluateCandidate() {
  const { mutate } = useSWRConfig()
  return useCallback(
    async (
      datasetId: string,
      body: { candidate: EvalCandidate; project_id: string; limit?: number },
    ): Promise<CandidateEvaluationAccepted> => {
      const accepted = await evaluateCandidate(datasetId, body)
      await mutate((key) => isAiEvalKey(key, 'runs'))
      return accepted
    },
    [mutate],
  )
}

/** Set a baseline (a replay run's with `eval_run_id`), then revalidate the baselines and the runs. */
export function useSetBaseline() {
  const { mutate } = useSWRConfig()
  return useCallback(
    async (params: Parameters<typeof setBaseline>[0]): Promise<Record<string, unknown>> => {
      const saved = await setBaseline(params)
      await Promise.all([
        // The page's own baselines read keeps its pre-W16 key.
        mutate((key) => key === 'ai-eval/baselines'),
        mutate((key) => isAiEvalKey(key, 'runs') || isAiEvalKey(key, 'run')),
      ])
      return saved
    },
    [mutate],
  )
}

/** Label a pair, then revalidate the project's pairs (the card leaves, the badge drops) and its G2 history. */
export function useLabelShadowPair(projectId: string | null) {
  const { mutate } = useSWRConfig()
  return useCallback(
    async (pairId: string, body: ShadowPairLabel): Promise<ShadowPair> => {
      const project = oneProject(projectId)
      if (!project) throw new Error('Choose one project to label a shadow pair.')
      const saved = await labelShadowPair(pairId, body)
      await Promise.all([
        mutate((key) => isAiEvalKey(key, 'shadow-pairs', project)),
        mutate((key) => isAiEvalKey(key, 'tier-runs', project)),
      ])
      return saved
    },
    [mutate, projectId],
  )
}
