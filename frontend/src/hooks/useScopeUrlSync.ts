/**
 * Keep the report scope and the address bar agreeing (VIZ-306), and keep the
 * scope consistent with the active project (VIZ-303). Mounted ONCE, in
 * `AppLayout` — lazily: `ScopeUrlSyncGate` downloads it (with the rest of the
 * flag-on UI, `components/layout/multiFiltersRuntime.ts`) only once the flag
 * answers ON, and until then publishes only 'off' itself. So 'on' is always
 * published by this hook, never before it runs.
 *
 * Behind `viz_multi_filters`. The hook always resolves the flag and publishes
 * it to `multiFiltersFlag` (the one place it is fetched); with the flag off it
 * does nothing else, and `ReleasePicker`'s own `?release=` loop runs exactly as
 * before. With the flag on this REPLACES that loop — two writers on one key
 * would fight — and `ReleasePicker` stands its effects down.
 *
 * The rules, per dimension (release, suites, window)
 * --------------------------------------------------
 * These are `ReleasePicker`'s rules, generalised; each was earned by a bug
 * that loop hit first.
 *
 *  1. The URL wins over the persisted store when it names something this hook
 *     did not itself write: a pasted link, an in-app `<Link>`, Back/Forward.
 *  2. An ABSENT key clears the dimension only on a POP (Back/Forward) — the
 *     earlier state had no filter — and never on the first render (which
 *     React Router also reports as POP), and never on a PUSH: a link that does
 *     not carry the filter is not a request to drop it. On a PUSH the store
 *     wins and is written into the new page's URL.
 *  3. Otherwise the store moved, so it is written out with `replace` —
 *     refining a filter is not a navigation, and pushing would turn Back into
 *     an undo stack of filter tweaks.
 *  4. Release ids are only PUBLISHED once the server confirms them (or they
 *     are `unattributed`), and dropped — with a notice naming them — only on
 *     positive evidence they are unknown: absent from a COMPLETE release
 *     list, or a by-id lookup answering 400/403/404 (lib/scopeUrlValidation).
 *     An id that could not be checked is kept, silently. In All Projects mode the `release` key is
 *     neither read nor stripped: releases belong to one project, and deleting
 *     a param we merely cannot act on YET would destroy the link.
 *  5. A project change drops releases (they belong to the old project) and
 *     suites that are not on the new project's suite list (`/api/v1/suites`,
 *     the whole list), with a notice naming them. If that list cannot be
 *     read, nothing is dropped.
 *  6. Every change this hook makes to the selection (a link adopted, a
 *     reconciliation) settles the data scope at once (`settleScopeNow`); only
 *     picker refinements wait out the 250 ms debounce.
 *
 * Writing happens only on scope routes (`isScopeRoute`); any route adopts.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigationType, useSearchParams } from 'react-router-dom'
import useSWR from 'swr'
import { useFeatureFlagStatus } from '@/hooks/useFeatureFlags'
import { useProjectScopedSWR } from '@/hooks/useProjectScopedSWR'
import { VIZ_FLAGS } from '@/config/vizFlags'
import { releasesService } from '@/services/releasesService'
import { suitesService } from '@/services/suitesService'
import { useMultiFiltersEnabled, useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { useSuiteStore } from '@/store/suiteStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { DROP_REASONS, useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { settleScopeNow } from '@/store/settledScope'
import { classifySuiteNames, isCompleteList, lookupReleaseIds } from '@/lib/scopeUrlValidation'
import { scopeKey, SCOPE_KEY_SEP } from '@/lib/scopeParams'
import {
  canonicalValues,
  isScopeRoute,
  parseScopeParams,
  SCOPE_URL_KEYS,
  UNATTRIBUTED,
  writeScopeParams,
} from '@/lib/scopeUrl'
import type { Release } from '@/types/releases'

/** Resolve the flag and publish it; run the sync when it is on. */
export function useScopeUrlSync(): void {
  // `undefined` = not answered yet — at first load, AND on the first visit to
  // each project (the key is per project). Unknown publishes NOTHING: the
  // store keeps its last answer across a project switch, so the flag never
  // reads "off" while the new project's answer is in flight — that flicker
  // re-ran the legacy single-release effects and collapsed a multi-selection
  // (E3 review, proof B). On first load the store stays unresolved, which
  // holds every legacy side effect until the answer arrives (proof A).
  const flag = useFeatureFlagStatus(VIZ_FLAGS.multiFilters)
  const setEnabled = useMultiFiltersFlagStore((s) => s.setEnabled)
  useEffect(() => {
    if (flag !== undefined) setEnabled(flag)
  }, [flag, setEnabled])
  useScopeUrlSyncCore()
}

/** Raw (unvalidated) serialisation of one key, to tell "the URL changed" from
 *  "we cleaned what it said". */
function rawKey(params: URLSearchParams, key: string): string | null {
  return params.has(key) ? JSON.stringify(params.getAll(key)) : null
}

/**
 * The sync itself, reading the flag from the store. Exported for tests, which
 * set the flag store directly rather than stubbing the flag endpoint.
 */
export function useScopeUrlSyncCore(): void {
  const multi = useMultiFiltersEnabled()
  const location = useLocation()
  const navigationType = useNavigationType()
  const [searchParams, setSearchParams] = useSearchParams()

  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const concreteProjectId = activeProjectId && !isAllProjects ? activeProjectId : null

  const activeReleaseId = useReleaseStore((s) => s.activeReleaseId)
  const storedReleaseIds = useReleaseStore((s) => s.activeReleaseIds)
  const releaseScopedProject = useReleaseStore((s) => s.scopedProjectId)
  const releaseIds = useMemo(
    () => selectReleaseIds({ activeReleaseId, activeReleaseIds: storedReleaseIds }),
    [activeReleaseId, storedReleaseIds],
  )
  const suiteNames = useSuiteStore((s) => s.activeSuiteNames)
  const suiteScopedProject = useSuiteStore((s) => s.scopedProjectId)
  const windowDays = useTimeWindowStore((s) => s.days)
  const pushNotice = useScopeNoticeStore((s) => s.pushNotice)

  // The release list, for validation. Same key and fetcher as `useReleases`,
  // so it shares ReleasePicker's cache entry rather than adding a request —
  // and `enabled` keeps it silent with the flag off.
  const releasesQuery = useProjectScopedSWR(
    'releases-list',
    (projectId) => releasesService.list(projectId, undefined),
    undefined,
    [''],
    multi && concreteProjectId !== null,
  )
  const releasesLoaded =
    !releasesQuery.isLoading && !releasesQuery.isValidating && releasesQuery.data !== undefined
  const releaseList: Release[] = useMemo(() => releasesQuery.data?.items ?? [], [releasesQuery.data])
  const releaseListComplete = releasesLoaded && isCompleteList(releasesQuery.data)

  // Ids the loaded list does not confirm. When that list is incomplete, each
  // is looked up by id before anything is dropped.
  const releaseIdsKey = scopeKey(releaseIds)
  const unconfirmedKey = useMemo(() => {
    if (releaseIdsKey === null) return null
    const onList = new Set(releaseList.map((r) => r.id))
    return scopeKey(releaseIdsKey.split(SCOPE_KEY_SEP).filter((id) => id !== UNATTRIBUTED && !onList.has(id)))
  }, [releaseIdsKey, releaseList])
  const releaseLookup = useSWR(
    multi &&
      concreteProjectId !== null &&
      releasesLoaded &&
      !releaseListComplete &&
      releaseScopedProject === concreteProjectId &&
      unconfirmedKey !== null
      ? ['scope-release-lookup', concreteProjectId, unconfirmedKey]
      : null,
    ([, projectId, key]: [string, string, string]) => lookupReleaseIds(projectId, key.split(SCOPE_KEY_SEP)),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  /** Every release id the server has confirmed for this project. */
  const confirmedReleaseIds = useMemo(() => {
    const known = new Set(releaseList.map((r) => r.id))
    for (const id of releaseLookup.data?.known ?? []) known.add(id)
    return known
  }, [releaseList, releaseLookup.data])

  // Names of releases seen, so a notice can name a dropped release after its
  // project's list is gone.
  const releaseNamesRef = useRef(new Map<string, string>())
  useEffect(() => {
    for (const r of releaseList) releaseNamesRef.current.set(r.id, r.name)
  }, [releaseList])

  // The (new) project's suites, fetched only when a suite selection must be
  // re-checked against a project it was not made in. The WHOLE list
  // (`/api/v1/suites`), not a page of recent runs: "does this project have
  // the suite at all?" is an existence question, and a scan of the 200 most
  // recent runs dropped every valid suite older than that.
  const suiteCheckNeeded =
    multi && suiteNames.length > 0 && concreteProjectId !== null && suiteScopedProject !== concreteProjectId
  const suiteCheck = useProjectScopedSWR(
    'scope-suite-list',
    (projectId) => suitesService.list(projectId),
    { revalidateOnFocus: false },
    [],
    suiteCheckNeeded,
  )

  const lastWritten = useRef<{ release: string | null; suites: string | null; window: string | null }>({
    release: null,
    suites: null,
    window: null,
  })

  // (A) Project reconciliation — releases and suites that do not belong to the
  // active project are dropped, and named.
  useEffect(() => {
    if (!multi) return
    if (releaseIds.length > 0 && activeProjectId !== null) {
      if (isAllProjects || releaseScopedProject !== activeProjectId) {
        const names = releaseIds.map((id) =>
          id === UNATTRIBUTED ? 'Unattributed' : releaseNamesRef.current.get(id) ?? id,
        )
        useReleaseStore.getState().clearRelease()
        settleScopeNow()
        pushNotice({
          dimension: 'release',
          values: names,
          reason: isAllProjects ? DROP_REASONS.needsProject : DROP_REASONS.projectChanged,
        })
      }
    }
  }, [multi, activeProjectId, isAllProjects, releaseScopedProject, releaseIds, pushNotice])

  useEffect(() => {
    if (!multi || suiteNames.length === 0 || activeProjectId === null) return
    if (suiteScopedProject === activeProjectId) return
    if (isAllProjects) {
      // A suite NAME is a meaningful filter across projects; keep it.
      useSuiteStore.getState().setActiveSuites(suiteNames, activeProjectId)
      return
    }
    // Not loaded, or failed: keep the selection (pending) — never drop what
    // could not be checked.
    if (!suiteCheck.data || suiteCheck.isValidating || suiteCheck.error) return
    const check = classifySuiteNames(suiteNames, suiteCheck.data)
    const dropped = new Set(check.unknown)
    useSuiteStore.getState().setActiveSuites(suiteNames.filter((n) => !dropped.has(n)), activeProjectId)
    settleScopeNow()
    pushNotice({ dimension: 'suite', values: check.unknown, reason: DROP_REASONS.projectChanged })
  }, [multi, suiteNames, suiteScopedProject, activeProjectId, isAllProjects, suiteCheck.data, suiteCheck.isValidating, suiteCheck.error, pushNotice])

  // (B) Releases the server says are unknown are stale — deleted, not
  // accessible, or from a link into a project that does not own them. Only
  // positive evidence drops an id (rule 4): absent from a COMPLETE list, or a
  // by-id lookup that said so. Unchecked ids stay.
  const lookupUnknownKey = scopeKey(releaseLookup.data?.unknown ?? [])
  useEffect(() => {
    if (!multi || !releasesLoaded || concreteProjectId === null) return
    if (releaseScopedProject !== concreteProjectId) return
    const current = selectReleaseIds(useReleaseStore.getState())
    if (current.length === 0) return
    let unknown: Set<string>
    if (releaseListComplete) {
      unknown = new Set(current.filter((id) => id !== UNATTRIBUTED && !confirmedReleaseIds.has(id)))
    } else {
      unknown = new Set(lookupUnknownKey === null ? [] : lookupUnknownKey.split(SCOPE_KEY_SEP))
    }
    const dropped = current.filter((id) => unknown.has(id))
    if (dropped.length === 0) return
    useReleaseStore.getState().setActiveReleases(current.filter((id) => !unknown.has(id)), concreteProjectId)
    settleScopeNow()
    pushNotice({ dimension: 'release', values: dropped, reason: DROP_REASONS.unknownRelease })
  }, [
    multi,
    releasesLoaded,
    releaseListComplete,
    confirmedReleaseIds,
    lookupUnknownKey,
    concreteProjectId,
    releaseScopedProject,
    releaseIds,
    pushNotice,
  ])

  // (C) URL <-> store.
  useEffect(() => {
    if (!multi) return
    const onScopeRoute = isScopeRoute(location.pathname)
    const isPop = navigationType === 'POP'
    const parsed = parseScopeParams(searchParams)
    const written = lastWritten.current
    const url = {
      release: rawKey(searchParams, SCOPE_URL_KEYS.release),
      suites: rawKey(searchParams, SCOPE_URL_KEYS.suites),
      window: rawKey(searchParams, SCOPE_URL_KEYS.window),
    }
    let storeChanged = false

    // ── release (skipped entirely without a single project — rule 4) ──────
    const releaseActive = concreteProjectId !== null
    if (releaseActive) {
      if (url.release !== null && url.release !== written.release) {
        written.release = url.release
        const ids = parsed.releaseIds ?? []
        if (canonicalValues(ids) !== canonicalValues(releaseIds) || releaseScopedProject !== concreteProjectId) {
          useReleaseStore.getState().setActiveReleases(ids, concreteProjectId)
          storeChanged = true
        }
        for (const d of parsed.dropped) if (d.dimension === 'release') pushNotice(d)
      } else if (url.release === null && isPop && onScopeRoute && written.release !== null) {
        written.release = null
        if (releaseIds.length > 0) {
          useReleaseStore.getState().clearRelease()
          storeChanged = true
        }
      }
    }

    // ── suites ─────────────────────────────────────────────────────────────
    if (url.suites !== null && url.suites !== written.suites) {
      written.suites = url.suites
      const names = parsed.suiteNames ?? []
      if (canonicalValues(names) !== canonicalValues(suiteNames)) {
        useSuiteStore.getState().setActiveSuites(names, activeProjectId)
        storeChanged = true
      }
      for (const d of parsed.dropped) if (d.dimension === 'suite') pushNotice(d)
    } else if (url.suites === null && isPop && onScopeRoute && written.suites !== null) {
      written.suites = null
      if (suiteNames.length > 0) {
        useSuiteStore.getState().clearSuites()
        storeChanged = true
      }
    }

    // ── window (never cleared: it has a default, not an absence) ──────────
    if (url.window !== null && url.window !== written.window) {
      written.window = url.window
      if (parsed.windowDays !== null && parsed.windowDays !== windowDays) {
        useTimeWindowStore.getState().setDays(parsed.windowDays)
        storeChanged = true
      }
    }

    // A store changed: the effect runs again with the new values and writes
    // the cleaned-up URL then. Writing now would publish the stale values.
    // A link or Back/Forward is one discrete jump, not a burst of picker
    // ticks: the data follows it at once, not 250 ms later (rule 6).
    if (storeChanged) settleScopeNow()
    if (storeChanged || !onScopeRoute) return

    // ── store -> URL (rule 3) ─────────────────────────────────────────────
    const scope: Parameters<typeof writeScopeParams>[1] = {
      suiteNames: [...suiteNames],
      windowDays,
    }
    if (releaseActive) {
      const verified =
        releaseScopedProject === concreteProjectId &&
        releaseIds.every((id) => id === UNATTRIBUTED || (releasesLoaded && confirmedReleaseIds.has(id)))
      // Publish only a verified selection; clearing is always safe.
      if (releaseIds.length === 0 || verified) scope.releaseIds = [...releaseIds]
    }
    const next = writeScopeParams(searchParams, scope)
    if (next.toString() === searchParams.toString()) {
      written.release = releaseActive && scope.releaseIds !== undefined
        ? rawKey(next, SCOPE_URL_KEYS.release)
        : written.release
      written.suites = rawKey(next, SCOPE_URL_KEYS.suites)
      written.window = rawKey(next, SCOPE_URL_KEYS.window)
      return
    }
    if (scope.releaseIds !== undefined) written.release = rawKey(next, SCOPE_URL_KEYS.release)
    written.suites = rawKey(next, SCOPE_URL_KEYS.suites)
    written.window = rawKey(next, SCOPE_URL_KEYS.window)
    setSearchParams(next, { replace: true })
  }, [
    multi,
    location.pathname,
    navigationType,
    searchParams,
    setSearchParams,
    concreteProjectId,
    activeProjectId,
    releaseIds,
    releaseScopedProject,
    suiteNames,
    windowDays,
    releasesLoaded,
    confirmedReleaseIds,
    pushNotice,
  ])
}
