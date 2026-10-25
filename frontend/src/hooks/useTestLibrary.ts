/**
 * Every W13 "test-library" READ (TCL-01 saved case views and the owner list,
 * TCL-03 library health and the evidence-gap pages, TCL-05 the timeline,
 * TCL-06 requirements and gaps).
 *
 * ONE module on purpose: the /test-management page vitests spread the real
 * `@/hooks/useTestManagement` into their mocks, and `src/test/networkGuard.ts`
 * fails any test that makes a real request, so a new read added there would
 * break nine files. These hooks are stubbed in one line instead
 * (`src/test/testLibraryMocks.ts`).
 *
 * Each read asks only when it can be answered and is wanted: no key without a
 * project (All Projects included), none while disabled. `revalidateOnFocus`
 * is off throughout (the health keeps the old 200-row roll's background
 * cadence).
 */
import { useCallback, useMemo } from 'react'
import useSWR from 'swr'
import useSWRInfinite from 'swr/infinite'

import { savedViewsKey } from '@/components/reports/savedViewsModel'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectMembers } from '@/hooks/useUserManagement'
import { listSavedViews, type SavedView } from '@/services/savedViewsService'
import { testLibraryService } from '@/services/testLibraryService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { TestCaseEvidenceGapKind } from '@/types/test-management'
import type { TestCaseTimeline, TimelineEntry } from '@/types/test-library'
import { appMutate } from '@/utils/swrCacheMutate'

const OPTS = { revalidateOnFocus: false } as const

/** The saved-views `page` and `filters.kind` of a saved case view (the server's vocabulary). */
export const CASE_VIEW_PAGE = 'test_management'
export const CASE_VIEW_KIND = 'test_case_view'

/** One project, or null: All Projects asks nothing. */
function oneProject(projectId: string | null | undefined): string | null {
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

/** `GET /test-management/health?project_id=` — one read per page view. */
export function useLibraryHealth(projectId: string | null) {
  const project = oneProject(projectId)
  return useSWR(
    project ? ['tm-library-health', project] : null,
    ([, id]) => testLibraryService.getHealth(id as string),
    { ...OPTS, refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
}

/** Revalidate every library-health read (after an import, a target change …). */
export function refreshLibraryHealth() {
  return appMutate((key) => Array.isArray(key) && key[0] === 'tm-library-health')
}

/**
 * `GET /cases/{id}/timeline`, keyset-paged: each page's key carries the
 * previous page's `next_cursor`; a page with no cursor is the start of the
 * timeline and stops the paging. Only while the Timeline tab is open.
 */
export function useTestCaseTimeline(caseId: string | null | undefined, kinds: string[], enabled: boolean) {
  const kindsKey = kinds.join(',')
  const getKey = useCallback(
    (pageIndex: number, previous: TestCaseTimeline | null) => {
      if (!enabled || !caseId) return null
      if (pageIndex > 0 && !previous?.next_cursor) return null
      return ['tm-case-timeline', caseId, kindsKey, pageIndex === 0 ? null : previous?.next_cursor ?? null] as const
    },
    [enabled, caseId, kindsKey],
  )
  const { data, error, isLoading, isValidating, size, setSize, mutate } = useSWRInfinite<TestCaseTimeline>(
    getKey,
    ([, id, joined, cursor]) =>
      testLibraryService.getTimeline(id as string, {
        kinds: joined ? String(joined).split(',') : undefined,
        cursor: (cursor as string | null) ?? null,
      }),
    { ...OPTS, revalidateFirstPage: false },
  )
  const pages = useMemo(() => data ?? [], [data])
  const items = useMemo<TimelineEntry[]>(() => pages.flatMap((page) => page.items), [pages])
  const lastPage = pages[pages.length - 1]
  return {
    /** Every loaded entry, newest first. */
    items,
    pages,
    /** Undefined until the first page lands. */
    automationLinked: pages[0]?.automation_linked,
    /** False once a page came back without a cursor ("Start of the timeline."). */
    hasMore: Boolean(lastPage?.next_cursor),
    loadMore: () => setSize(size + 1),
    isLoading,
    isLoadingMore: isValidating && size > pages.length,
    error,
    mutate,
  }
}

/** `GET /cases/{id}/requirements` — only with the flag on and an authored case open. */
export function useCaseRequirements(caseId: string | null | undefined, enabled: boolean) {
  return useSWR(
    enabled && caseId ? ['tm-case-requirements', caseId] : null,
    ([, id]) => testLibraryService.listRequirements(id as string),
    OPTS,
  )
}

/** `GET /traceability/gaps?project_id=&page=` — only while **Show all gaps** is open. */
export function useTraceabilityGaps(projectId: string | null, page: number, enabled: boolean) {
  const project = oneProject(projectId)
  return useSWR(
    enabled && project ? ['tm-traceability-gaps', project, page] : null,
    ([, id, p]) => testLibraryService.getTraceabilityGaps(id as string, p as number, 25),
    OPTS,
  )
}

/** `GET /cases/evidence-gaps?kind=&page=&size=25` — only while **Show all** is open. */
export function useEvidenceGapPage(
  projectId: string | null,
  kind: TestCaseEvidenceGapKind,
  page: number,
  enabled: boolean,
) {
  const project = oneProject(projectId)
  return useSWR(
    enabled && project ? ['tm-evidence-gap-page', project, kind, page] : null,
    ([, id, k, p]) => testLibraryService.getEvidenceGapPage(id as string, k as TestCaseEvidenceGapKind, p as number, 25),
    OPTS,
  )
}

/** A saved_views row that is a saved case view (`filters.kind === 'test_case_view'`). */
export function isCaseView(view: SavedView): boolean {
  return Boolean(view.filters) && (view.filters as Record<string, unknown>).kind === CASE_VIEW_KIND
}

/**
 * The project's saved case views (mine, and those shared in the project):
 * `GET /saved-views?project_id=&page=test_management`, keyed exactly as every
 * other saved-views reader (`savedViewsKey`), filtered to the case-view kind.
 */
export function useCaseViews(projectId: string | null) {
  const project = oneProject(projectId)
  const { data, error, isLoading, mutate } = useSWR(
    project ? savedViewsKey(project, CASE_VIEW_PAGE) : null,
    ([, id]) => listSavedViews(id as string, CASE_VIEW_PAGE),
    OPTS,
  )
  const views = useMemo(() => (data ?? []).filter(isCaseView), [data])
  return { views, error, isLoading, mutate }
}

export interface CaseOwnerOption {
  value: string
  label: string
}

/**
 * The Owner filter's options: `Me`, then the project's members by name (full
 * name, else username). Reads through `useProjectMembers` itself, so the key
 * and the request are the bulk bar's (one shared request). None in All Projects.
 */
export function useCaseOwnerOptions(projectId: string | null) {
  const { data: members, error, isLoading, mutate } = useProjectMembers(oneProject(projectId))
  const options = useMemo<CaseOwnerOption[]>(
    () => [
      { value: 'me', label: 'Me' },
      ...(members ?? [])
        .map((member) => ({ value: member.user_id, label: member.full_name || member.username }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    ],
    [members],
  )
  return { options, error, isLoading, mutate }
}
