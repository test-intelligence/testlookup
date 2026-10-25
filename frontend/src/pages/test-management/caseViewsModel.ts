/**
 * W13 TCL-01 — the Cases card's Views row: the copy and pure helpers of the
 * presets, the saved case views and their forms (no React, no fetching).
 *
 * A saved case view is a `saved_views` row (`page: 'test_management'`,
 * `filters.kind: 'test_case_view'`); its filters are read back through
 * `caseFilterState.filtersFromView` (a stored view never applies an unknown
 * value). `owner: 'me'` in a shared view means the READER.
 */
import type { SavedView } from '@/services/savedViewsService'
import { filtersFromView, sameFilters, viewFilters, type CaseFilterState } from './caseFilterState'

export const VIEWS_LABEL = 'Views'
export const SAVE_VIEW_TEXT = 'Save view'
export const MANAGE_TEXT = 'Manage'
export const SAVE_TITLE = 'Save this view'
export const SAVE_TITLE_ID = 'case-view-save-title'
export const MANAGE_TITLE = 'Manage your views'
export const RENAME_TITLE = 'Rename the view'
export const SHARED_TEXT = 'Shared'
export const VIEW_NAME_MIN = 2
export const VIEW_NAME_MAX = 255
export const NAME_PROBLEM = 'A view name is 2 to 255 characters.'
export const SAVE_FAILED = 'The view could not be saved. Try again.'
export const UPDATE_FAILED = 'The view could not be changed. Try again.'
export const DELETE_FAILED = 'The view could not be deleted. Try again.'
export const VIEWS_ERROR = 'Could not load the saved views.'
/** = `hooks/useTestLibrary.CASE_VIEW_PAGE` (the server's `TEST_CASE_VIEW_PAGE`). */
export const CASE_VIEW_PAGE = 'test_management' as const

export function isMine(view: Pick<SavedView, 'user_id'>, userId: string | null): boolean {
  return Boolean(userId) && view.user_id === userId
}

/** Mine first, then those shared in the project, each by name. */
export function orderCaseViews<T extends Pick<SavedView, 'name' | 'user_id'>>(views: readonly T[], userId: string | null): T[] {
  const rank = (view: T) => (isMine(view, userId) ? 0 : 1)
  return [...views].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
}

/** The state a saved view applies (it REPLACES every filter key). */
export function viewState(view: Pick<SavedView, 'filters'>): CaseFilterState {
  return filtersFromView(view.filters)
}

/** A saved chip is active exactly when the URL state equals its filters. */
export function viewIsActive(view: Pick<SavedView, 'filters'>, current: CaseFilterState): boolean {
  return sameFilters(viewState(view), current)
}

/** The trimmed name, or the sentence when it is not 2-255 characters. */
export function nameProblem(name: string): string | null {
  const trimmed = name.trim()
  return trimmed.length < VIEW_NAME_MIN || trimmed.length > VIEW_NAME_MAX ? NAME_PROBLEM : null
}

/** The body of `createSavedView` for the current filters. */
export function savePayload(projectId: string, name: string, current: CaseFilterState, isShared: boolean) {
  return {
    project_id: projectId,
    name: name.trim(),
    page: CASE_VIEW_PAGE,
    filters: viewFilters(current) as unknown as Record<string, unknown>,
    is_shared: isShared,
  }
}

/** "Share with Payments" (the one project the view is shared in). */
export function shareLabel(projectName: string | null | undefined): string {
  return projectName ? `Share with ${projectName}` : 'Share with this project'
}
