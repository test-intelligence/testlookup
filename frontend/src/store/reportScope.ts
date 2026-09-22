/**
 * Report scope — the one read/write API the report presentation layer
 * (filter bar, chips, context header, dataset summary) consumes (VIZ-303,
 * VIZ-306, contract C1 in `contracts/viz/README.md`).
 *
 * It is a facade over four stores — project, release, suite, time window —
 * plus the dropped-value notices, so a component never has to know which
 * store holds what or re-implement the rules:
 *
 *  - `releaseIds` is `[]` unless exactly ONE project is pinned and the
 *    selection belongs to it (a release belongs to one project; the same
 *    guard `useReleaseScope` applies to requests, so what the bar shows is
 *    what is sent).
 *  - `suiteNames` applies in All Projects mode too (a filter by name).
 *  - `setReleaseIds` / `setSuiteNames` enforce the caps (`RELEASE_CAP`,
 *    `SUITE_CAP`) and the "no duplicates" rule; anything cut is added to
 *    `droppedNotice` rather than disappearing silently.
 *  - Arrays are sorted and identity-stable (memoised on content), so they are
 *    safe in dependency lists.
 *
 * URL sync is not here: `useScopeUrlSync` (mounted once in AppLayout) mirrors
 * these same stores into `?release=&suites=&window=`, so writing through this
 * API updates the address bar too.
 */
import { useCallback, useMemo } from 'react'
import { ALL_PROJECTS_ID, useProjectStore } from './projectStore'
import { RELEASE_CAP as STORE_RELEASE_CAP, selectReleaseIds, useReleaseStore } from './releaseStore'
import { SUITE_CAP as STORE_SUITE_CAP, useSuiteStore } from './suiteStore'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from './timeWindowStore'
import { DROP_REASONS, useScopeNoticeStore, type DroppedNotice } from './scopeNoticeStore'
import { normalizeScope, SCOPE_KEY_SEP, scopeKey } from '@/lib/scopeParams'

/** Maximum releases in one selection (C1). The 21st option is disabled. */
export const RELEASE_CAP = STORE_RELEASE_CAP
/** Maximum suites in one selection (C1). The 51st option is disabled. */
export const SUITE_CAP = STORE_SUITE_CAP
/** Default report window in days. */
export const DEFAULT_WINDOW_DAYS = DEFAULT_TIME_WINDOW_DAYS

export type { DroppedNotice }

export interface ReportScope {
  projectId: string | null
  allProjects: boolean
  releaseIds: string[]
  suiteNames: string[]
  windowDays: number
  setReleaseIds(ids: string[]): void
  setSuiteNames(names: string[]): void
  setWindowDays(d: number): void
  clearAll(): void
  droppedNotice: DroppedNotice[]
  dismissDroppedNotice(): void
}

/** Sorted copy of a list, identity-stable across renders with equal content. */
function useStableSorted(values: readonly string[]): string[] {
  const key = scopeKey(values)
  return useMemo(() => (key === null ? [] : normalizeScope(key.split(SCOPE_KEY_SEP))), [key])
}

export function useReportScope(): ReportScope {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const allProjects = !activeProjectId || activeProjectId === ALL_PROJECTS_ID
  const projectId = allProjects ? null : activeProjectId

  const activeReleaseId = useReleaseStore((s) => s.activeReleaseId)
  const storedReleaseIds = useReleaseStore((s) => s.activeReleaseIds)
  const releaseScopedProject = useReleaseStore((s) => s.scopedProjectId)
  const rawSuiteNames = useSuiteStore((s) => s.activeSuiteNames)
  const windowDays = useTimeWindowStore((s) => s.days)
  const notices = useScopeNoticeStore((s) => s.notices)

  const applicableReleases =
    projectId !== null && releaseScopedProject === projectId
      ? selectReleaseIds({ activeReleaseId, activeReleaseIds: storedReleaseIds })
      : []
  const releaseIds = useStableSorted(applicableReleases)
  const suiteNames = useStableSorted(rawSuiteNames)

  const setReleaseIds = useCallback(
    (ids: string[]) => {
      if (projectId === null) {
        // Releases need one project. Say so rather than ignoring the click.
        useScopeNoticeStore
          .getState()
          .pushNotice({ dimension: 'release', values: ids, reason: DROP_REASONS.needsProject })
        return
      }
      const overCap = useReleaseStore.getState().setActiveReleases(ids, projectId)
      useScopeNoticeStore
        .getState()
        .pushNotice({ dimension: 'release', values: overCap, reason: DROP_REASONS.overCap(RELEASE_CAP) })
    },
    [projectId],
  )

  const setSuiteNames = useCallback(
    (names: string[]) => {
      const dropped = useSuiteStore.getState().setActiveSuites(names, activeProjectId)
      useScopeNoticeStore
        .getState()
        .pushNotice({ dimension: 'suite', values: dropped, reason: DROP_REASONS.overCap(SUITE_CAP) })
    },
    [activeProjectId],
  )

  const setWindowDays = useCallback((d: number) => {
    if (!Number.isInteger(d) || d < 1 || d > 365) return
    useTimeWindowStore.getState().setDays(d)
  }, [])

  const clearAll = useCallback(() => {
    useReleaseStore.getState().clearRelease()
    useSuiteStore.getState().clearSuites()
    useTimeWindowStore.getState().setDays(DEFAULT_WINDOW_DAYS)
    useScopeNoticeStore.getState().dismiss()
  }, [])

  const dismissDroppedNotice = useCallback(() => useScopeNoticeStore.getState().dismiss(), [])

  return useMemo(
    () => ({
      projectId,
      allProjects,
      releaseIds,
      suiteNames,
      windowDays,
      setReleaseIds,
      setSuiteNames,
      setWindowDays,
      clearAll,
      droppedNotice: notices,
      dismissDroppedNotice,
    }),
    [
      projectId,
      allProjects,
      releaseIds,
      suiteNames,
      windowDays,
      setReleaseIds,
      setSuiteNames,
      setWindowDays,
      clearAll,
      notices,
      dismissDroppedNotice,
    ],
  )
}
