/**
 * VIZ-609 — named report views: what the saved-views manager stores and how
 * it reads a view back. Pure: no React, no fetching.
 *
 * A named view is a `saved_views` row whose `filters` carry
 * `kind: 'report_view'` and the report scope: the page, the releases, the
 * suites and the window. Layout rows (`useAnalyticsView`: `{page, instances,
 * version}`) carry no kind and are never listed here; the layout hook skips
 * report views in turn, so the two never read each other's row.
 *
 * Reading a view back never trusts it: the server re-authorises every
 * release for the reader (`view.release`), and anything malformed is dropped
 * rather than applied.
 */
import type { SavedView } from '@/services/savedViewsService'
import type { ReportRoute } from './reportRoutes'

export const REPORT_VIEW_KIND = 'report_view'

export type SavedViewPage = NonNullable<SavedView['page']>

/** The saved-views `page` of each report route that offers views. */
export const ROUTE_VIEW_PAGES: Partial<Record<ReportRoute, SavedViewPage>> = {
  '/overview': 'dashboard',
  '/trends': 'trends',
  '/coverage': 'coverage',
  '/failures': 'failures',
  '/defects': 'defects',
  '/reports/summary': 'summary_report',
}

export interface ReportViewScope {
  releaseIds: string[]
  suiteNames: string[]
  windowDays: number
}

/** The `filters` object a named view is saved with. */
export function reportViewFilters(page: SavedViewPage, scope: ReportViewScope): Record<string, unknown> {
  const filters: Record<string, unknown> = { kind: REPORT_VIEW_KIND, page, window: scope.windowDays }
  if (scope.releaseIds.length > 0) filters.release_ids = [...scope.releaseIds]
  // One release also under the key the digests and the server's per-reader
  // release verdict read (`SAVED_VIEW_RELEASE_KEY`).
  if (scope.releaseIds.length === 1) filters.release_id = scope.releaseIds[0]
  if (scope.suiteNames.length > 0) filters.suites = [...scope.suiteNames]
  return filters
}

export function isReportView(view: Pick<SavedView, 'filters'>): boolean {
  return view.filters?.kind === REPORT_VIEW_KIND
}

const stringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.length > 0) : []

/**
 * The scope a view applies. Releases are only those the server still lets
 * THIS reader apply: when `view.release` says a release was not applied (it
 * was archived away, or belongs to a project the reader cannot see), every
 * release is dropped and `releaseNote` says why.
 */
export function readReportView(
  view: Pick<SavedView, 'filters' | 'release'>,
  fallbackWindow: number,
): ReportViewScope & { releaseNote: string | null } {
  const window = view.filters?.window
  const windowDays = typeof window === 'number' && Number.isInteger(window) && window >= 1 && window <= 365 ? window : fallbackWindow
  let releaseIds = stringList(view.filters?.release_ids)
  let releaseNote: string | null = null
  if (view.release && view.release.applied === false && view.release.release_id) {
    releaseIds = releaseIds.filter((id) => id !== view.release?.release_id)
    releaseNote = view.release.reason ?? 'A release in this view is no longer available to you.'
  }
  return { releaseIds, suiteNames: stringList(view.filters?.suites), windowDays, releaseNote }
}

/** Another view of this page already has that name (allowed, but the reader is told). */
export function nameTaken(views: readonly Pick<SavedView, 'name'>[], name: string): boolean {
  const wanted = name.trim().toLowerCase()
  return wanted.length > 0 && views.some((view) => view.name.trim().toLowerCase() === wanted)
}

/** Views in the menu's order: the default first, then mine, then shared, each by name. */
export function orderViews<T extends Pick<SavedView, 'name' | 'is_default' | 'user_id'>>(views: readonly T[], userId: string | null): T[] {
  const rank = (view: T) => (view.is_default && view.user_id === userId ? 0 : view.user_id === userId ? 1 : 2)
  return [...views].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
}

export const VIEW_NAME_MIN = 2
export const VIEW_NAME_MAX = 255

/** Per browser tab: the default view is applied once per project and page. */
export const defaultAppliedKey = (projectId: string, page: SavedViewPage) => `testlookup.defaultViewApplied.${projectId}.${page}`
