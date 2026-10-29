/**
 * W23a TRND-03: the expected run cadences of ONE project and their status (Settings › Expected runs, the
 * Trends callout, the Coverage dimension).
 *
 * Keyed by the request URL (`cadencesKey` / `cadenceStatusKey`); no key — no request — without a single
 * project (All Projects asks nothing). The status answer goes through `parseCadenceStatus`: a malformed
 * one comes back as `data: null` WITH an `error` ("Could not load the expected runs."), never a crash and
 * never "all met". `days` snaps onto `STATUS_DAYS` (`snapStatusDays`), so the page windows 1 / 7 / 14 /
 * 30 / 90 ask exactly themselves.
 *
 * No polling (a cadence changes on a click; misses are decided every 15 minutes). After a write, call
 * `refreshRunCadences(projectId)` to revalidate the list and every status key of the project.
 */
import { useMemo } from 'react'
import useSWR from 'swr'
import { cadenceStatus, listCadences } from '@/services/runCadenceService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { appMutate } from '@/utils/swrCacheMutate'
import {
  STATUS_DAYS,
  parseCadence,
  parseCadenceStatus,
  type RunCadence,
  type RunCadenceStatus,
  type StatusDays,
} from '@/types/runCadence'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_CADENCES = 'The expected runs answer could not be read.'

const DEFAULT_STATUS_DAYS: StatusDays = 14

function singleProject(projectId: string | null | undefined): string | null {
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

/** Any page window (in days; 24 h = 1) onto the nearest `STATUS_DAYS` value (a tie takes the smaller). */
export function snapStatusDays(days: number): StatusDays {
  if (!Number.isFinite(days)) return DEFAULT_STATUS_DAYS
  let best: StatusDays = STATUS_DAYS[0]
  for (const candidate of STATUS_DAYS) {
    if (Math.abs(candidate - days) < Math.abs(best - days)) best = candidate
  }
  return best
}

/** The SWR key (= the request URL) of the cadence list, or `null` without a single project. */
export function cadencesKey(projectId: string | null | undefined): string | null {
  const id = singleProject(projectId)
  return id ? `/api/v1/projects/${id}/run-cadences` : null
}

/** The SWR key (= the request URL) of the status read, `days` snapped; `null` without a single project. */
export function cadenceStatusKey(projectId: string | null | undefined, days: number): string | null {
  const id = singleProject(projectId)
  return id ? `/api/v1/projects/${id}/run-cadences/status?days=${snapStatusDays(days)}` : null
}

export interface RunCadencesState {
  /** `null` while loading, on a failed read, or for a malformed answer. */
  data: RunCadence[] | null
  /** The list's `total` (`null` until a first good answer — an unknown count is not 0). */
  total: number | null
  error: unknown
  isLoading: boolean
  mutate: () => Promise<unknown>
}

export interface RunCadenceStatusState {
  /** `null` while loading, on a failed read, or for a malformed answer. */
  data: RunCadenceStatus | null
  /** Set on a failed read AND on a malformed answer. */
  error: unknown
  isLoading: boolean
  mutate: () => Promise<unknown>
}

function parseList(raw: unknown): { items: RunCadence[]; total: number } | null {
  if (typeof raw !== 'object' || raw === null || !Array.isArray((raw as { items?: unknown }).items)) return null
  const items: RunCadence[] = []
  for (const row of (raw as { items: unknown[] }).items) {
    const parsed = parseCadence(row)
    if (!parsed) return null
    items.push(parsed)
  }
  const total = (raw as { total?: unknown }).total
  return { items, total: typeof total === 'number' && Number.isFinite(total) ? total : items.length }
}

export function useRunCadences(projectId: string | null): RunCadencesState {
  const id = singleProject(projectId)
  const swr = useSWR<unknown>(cadencesKey(id), id ? () => listCadences(id) : null)
  const raw = swr.data
  const parsed = useMemo(() => (raw === undefined ? null : parseList(raw)), [raw])
  const malformed = raw !== undefined && parsed === null
  return {
    data: parsed?.items ?? null,
    total: parsed?.total ?? null,
    error: swr.error ?? (malformed ? new Error(MALFORMED_CADENCES) : undefined),
    isLoading: Boolean(swr.isLoading),
    mutate: () => swr.mutate(),
  }
}

export function useRunCadenceStatus(projectId: string | null, days: number): RunCadenceStatusState {
  const id = singleProject(projectId)
  const snapped = snapStatusDays(days)
  const swr = useSWR<unknown>(cadenceStatusKey(id, snapped), id ? () => cadenceStatus(id, snapped) : null)
  const raw = swr.data
  const parsed = useMemo(() => (raw === undefined ? null : parseCadenceStatus(raw)), [raw])
  const malformed = raw !== undefined && parsed === null
  return {
    data: parsed,
    error: swr.error ?? (malformed ? new Error(MALFORMED_CADENCES) : undefined),
    isLoading: Boolean(swr.isLoading),
    mutate: () => swr.mutate(),
  }
}

/** Revalidate the cadence list and every status key of one project (`appMutate`: the provider's cache). */
export function refreshRunCadences(projectId: string | null): Promise<unknown> {
  const id = singleProject(projectId)
  if (!id) return Promise.resolve(undefined)
  const list = `/api/v1/projects/${id}/run-cadences`
  const status = `/api/v1/projects/${id}/run-cadences/status?`
  return appMutate((key: unknown) => typeof key === 'string' && (key === list || key.startsWith(status)))
}
