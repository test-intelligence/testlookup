/**
 * P1 (2026-10-04) — "Views" on the six keyed report pages, now that the report
 * chrome that hosted the saved-views menu is gone for good.
 *
 * A view saves and applies the LEGACY scope, the controls the page shows:
 *  - the top-bar release (`releaseStore`, one release), when the page is
 *    release-scoped (`release`);
 *  - the window (`timeWindowStore`), snapped to the page's own options;
 *  - the page's suite filter (`usePageSuiteFilter`, one suite), when it has one;
 *  - anything else the page keeps (`extraFilters` / `applyExtra`: the Defects
 *    tab, the Summary mode).
 *
 * Opening a view replaces the scope: a view with no release clears the
 * release, a view with no suite clears the suite. A view saved with several
 * releases or suites (the chrome's multi-select) opens with the first, and a
 * toast names what was left out.
 *
 * Call it at the top of the page, before any early return. It returns the
 * menu's props, or null where there is no menu (a route with no saved-views
 * page, or no single project pinned).
 */
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { normalizeScope } from '@/lib/scopeParams'
import { SCOPE_URL_KEYS } from '@/lib/scopeUrl'
import type { SavedView } from '@/services/savedViewsService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import type { SavedViewsMenuProps } from './SavedViewsMenu'
import type { ReportRoute } from './reportRoutes'
import { ROUTE_VIEW_PAGES, type ReportViewScope } from './savedViewsModel'

export interface ReportViewsMenuInput {
  route: ReportRoute
  /** The window the page shows now. */
  windowDays: number
  /** The page's window options; null when the page has no window (Defects). */
  windowOptions: readonly number[] | null
  /** The page's suite filter (`usePageSuiteFilter`); absent when it has none. */
  suite?: { names: readonly string[]; set: (name: string) => void }
  /** Whether the page is scoped by the top-bar release (Defects is project-wide). */
  release: boolean
  /** Saved beside the scope (`{defects: {tab}}`, `{summary: {mode}}`). */
  extraFilters?: Record<string, unknown>
  /** Applies what `extraFilters` saved, from the view being opened. */
  applyExtra?: (view: SavedView) => void
}

export type ReportViewsMenu = Omit<SavedViewsMenuProps, 'variant'>

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

export function useReportViewsMenu({
  route,
  windowDays,
  windowOptions,
  suite,
  release,
  extraFilters,
  applyExtra,
}: ReportViewsMenuInput): ReportViewsMenu | null {
  const page = ROUTE_VIEW_PAGES[route] ?? null
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const releaseScope = useReleaseScope()
  const activeReleaseId = useReleaseStore((s) => s.activeReleaseId)
  const scopedProjectId = useReleaseStore((s) => s.scopedProjectId)
  const [params] = useSearchParams()

  const suiteNames = suite?.names
  const setSuite = suite?.set
  const current: ReportViewScope = useMemo(
    () => ({
      releaseIds: release ? normalizeScope(releaseScope) : [],
      suiteNames: suiteNames ? normalizeScope(suiteNames) : [],
      windowDays,
    }),
    [release, releaseScope, suiteNames, windowDays],
  )

  // A shared link beats my default view: the URL names a release, or a
  // release is already in effect for this project. The second matters with
  // the legacy picker, which writes `?release=` only once its list loads, so
  // the URL alone would lose the race with the default-view effect. The page
  // suite has no URL key, so it cannot say a link was followed. A page with
  // no release (Defects) is never "linked" by one.
  const linked = release && (params.has(SCOPE_URL_KEYS.release) || (activeReleaseId !== null && scopedProjectId === projectId))

  const onApply = useCallback(
    (scope: ReportViewScope, view: SavedView) => {
      const dropped: string[] = []
      if (release && projectId) {
        // A view is the whole scope: no release in it clears the release.
        useReleaseStore.getState().setActiveRelease(scope.releaseIds[0] ?? null, projectId)
        if (scope.releaseIds.length > 1) dropped.push(plural(scope.releaseIds.length - 1, 'other release'))
      }
      if (windowOptions) useTimeWindowStore.getState().setDays(snapToAllowed(scope.windowDays, windowOptions))
      if (setSuite) {
        setSuite(scope.suiteNames[0] ?? '')
        if (scope.suiteNames.length > 1) dropped.push(plural(scope.suiteNames.length - 1, 'other suite'))
      }
      applyExtra?.(view)
      if (dropped.length > 0) {
        toast(`This page filters by one at a time, so "${view.name}" opened with the first. Left out: ${dropped.join(', ')}.`)
      }
    },
    [release, projectId, windowOptions, setSuite, applyExtra],
  )

  return useMemo(
    () => (page && projectId ? { page, projectId, current, onApply, extraFilters, linked } : null),
    [page, projectId, current, onApply, extraFilters, linked],
  )
}
