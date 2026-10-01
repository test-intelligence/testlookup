/**
 * K3 (Wave 2.6): the scope every catalogue request is built from.
 *
 * One function turns a page's scope (project, window, releases, suites) into
 * query parameters, so the rules below are enforced once instead of in five
 * sections:
 *
 *   - **At most 90 days.** No report page offers more than 90 (each snaps its
 *     window to its own option list), but a URL can carry `?window=365` and a
 *     store can hold anything, so the clamp here is the second line of
 *     defence. The chart-specific `extra` cannot widen it: its `days`, `from`
 *     and `to` are dropped — the window is `days`, clamped, and nothing else.
 *     `ROW_GRAIN_MAX_WINDOW_DAYS` is the lever the plan (4.4) keeps for the
 *     slow, per-execution charts; the 2026-09-29 re-measure kept it at 90.
 *   - **Nothing until the project resolves.** `null` means "do not ask yet";
 *     `useCatalogChartData` sends no request for it.
 *   - **All Projects sends no `project_id`** (and no release: a release
 *     belongs to one project, so filtering every project by one project's
 *     release is not a narrower answer but a wrong one — `useReleaseScope`'s
 *     rule, repeated here because a caller may pass ids of its own).
 *   - **The C1 wire rule** (`lib/scopeParams`): an empty selection is an
 *     ABSENT key, one value is a scalar (byte-identical to the legacy
 *     single-release request), several are a sorted list that the shared
 *     serializer sends as a repeated key.
 */
import { useMemo } from 'react'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { normalizeScope, SCOPE_KEY_SEP, scopeKey, scopeParam, type ScopeValue } from '@/lib/scopeParams'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'

/** The longest window any catalogue request may ask for, days. */
export const CATALOGUE_MAX_WINDOW_DAYS = 90

/**
 * The longest window for a row-grain chart (one row per test execution: the
 * Trends suite series, heatmap and duration band). The same 90 today; the plan's
 * lever if a re-measure ever puts the 90-day row-grain request over budget.
 */
export const ROW_GRAIN_MAX_WINDOW_DAYS = 90

/**
 * Keys only the scope may set. A chart's `extra` naming one of them is ignored:
 * the window is `days` (clamped), and the project, releases and suites are the
 * page's.
 */
const SCOPE_KEYS = new Set(['project_id', 'days', 'from', 'to', 'release_id', 'suite_name'])

/** `days` as a whole number of days in `1..max` (and `max` itself never above 90). */
export function clampCatalogueDays(days: number, max: number = CATALOGUE_MAX_WINDOW_DAYS): number {
  const ceiling = Math.min(CATALOGUE_MAX_WINDOW_DAYS, Math.max(1, Math.trunc(max)))
  const whole = Math.trunc(days)
  if (Number.isNaN(whole)) return 1
  return Math.min(ceiling, Math.max(1, whole))
}

export interface CatalogueScopeInput {
  /** The active project id, or `null` while it has not resolved. */
  projectId: string | null
  /** All Projects mode: no `project_id`, no release. */
  allProjects: boolean
  /** The page's window, days. Clamped here. */
  days: number
  releaseIds?: ScopeValue
  suiteNames?: ScopeValue
  /** The chart's own parameters (`metric`, `group_by`, `top_n`, ...). */
  extra?: CatalogParams
  /** A lower cap than 90 for this chart (`ROW_GRAIN_MAX_WINDOW_DAYS`). */
  maxDays?: number
}

/** A chart request's parameters, or `null` while there is nothing to ask for yet. */
export function catalogueParams({
  projectId,
  allProjects,
  days,
  releaseIds,
  suiteNames,
  extra,
  maxDays,
}: CatalogueScopeInput): CatalogParams | null {
  if (!allProjects && !projectId) return null
  const params: CatalogParams = {}
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (!SCOPE_KEYS.has(key)) params[key] = value
  }
  if (!allProjects && projectId) params.project_id = projectId
  params.days = clampCatalogueDays(days, maxDays)
  if (!allProjects) Object.assign(params, scopeParam('release_id', releaseIds))
  Object.assign(params, scopeParam('suite_name', suiteNames))
  return params
}

/** A scope list back from its `scopeKey` string (`null` for none). */
function fromKey(key: string | null): string[] {
  return key === null ? [] : normalizeScope(key.split(SCOPE_KEY_SEP))
}

export interface UseCatalogueParamsOptions {
  maxDays?: number
}

/**
 * `catalogueParams` for the active project and the page's release scope.
 *
 * Identity-stable while the scope is unchanged: the result is keyed on strings
 * (never on an array or object rebuilt during render), so a section may put it
 * in a dependency list or an SWR key without refetching on every render.
 * `extra` must be JSON-shaped (it always is: strings, numbers, lists).
 */
export function useCatalogueParams(
  days: number,
  suiteFilter: ScopeValue,
  extra?: CatalogParams,
  { maxDays }: UseCatalogueParamsOptions = {},
): CatalogParams | null {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const releaseKey = scopeKey(useReleaseScope())
  const suiteKey = scopeKey(suiteFilter)
  const extraKey = extra === undefined ? null : JSON.stringify(extra)
  return useMemo(() => {
    const allProjects = activeProjectId === ALL_PROJECTS_ID
    return catalogueParams({
      projectId: allProjects ? null : activeProjectId,
      allProjects,
      days,
      releaseIds: fromKey(releaseKey),
      suiteNames: fromKey(suiteKey),
      extra: extraKey === null ? undefined : (JSON.parse(extraKey) as CatalogParams),
      maxDays,
    })
  }, [activeProjectId, days, releaseKey, suiteKey, extraKey, maxDays])
}
