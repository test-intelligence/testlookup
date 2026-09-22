/**
 * Validating a report scope against the server (VIZ-303 / VIZ-306): which
 * release ids and suite names in a selection are genuinely unknown to the
 * active project, and may therefore be dropped with a notice.
 *
 * The one rule both halves follow: **never drop what could not be checked.**
 * A value is dropped only on positive evidence that it is unknown — it is
 * absent from a list the server says is COMPLETE, or a lookup of that exact
 * id answered "not found / not yours". A partial list, a network error, a 5xx
 * or a 429 proves nothing, so the value is kept and no notice is raised.
 *
 * Releases. `GET /api/v1/releases?project_id=` returns every release of the
 * project in one response today (`total == len(items)`, no paging), but the
 * client types it as a `PaginatedResponse` and a first-page read is exactly
 * how a valid release on "page 2" would be lost the day it pages. So a list
 * whose `total` exceeds its `items` is treated as incomplete, and each id not
 * on it is looked up by id (`GET /api/v1/releases/{id}`, guarded by project
 * membership) — at most `RELEASE_CAP` lookups, since the store caps there.
 *
 * Suites. `GET /api/v1/suites?project_id=` is the authoritative list of a
 * project's suites (ingestion materialises a `TestSuite` row per suite name,
 * and the endpoint backfills any the live stream missed from ALL of the
 * project's runs), returned whole. It replaces a scan of the project's 200
 * most recent runs, which dropped any valid suite older than that.
 */
import axios from 'axios'
import { api } from '@/services/api'
import { RELEASE_CAP } from '@/store/releaseStore'
import { normalizeSuiteName } from '@/utils/suiteFilters'

/** What a server-side check concluded about a set of values. */
export interface ScopeCheck {
  /** Known to the project. */
  known: string[]
  /** Positively unknown: absent from a complete list, or 400/403/404 by id. */
  unknown: string[]
  /** Could not be checked (network, 5xx, 429, incomplete list): KEEP these. */
  unchecked: string[]
}

/** A list response and whether it is the whole list. */
export function isCompleteList(list: { items?: readonly unknown[]; total?: unknown } | undefined): boolean {
  if (!list || !Array.isArray(list.items)) return false
  return typeof list.total !== 'number' || list.total <= list.items.length
}

/** HTTP answers that mean "this id is not a release you can use here". */
const NOT_YOURS = new Set([400, 403, 404, 422])

/**
 * Look up release ids one by one. Never rejects: every id lands in exactly one
 * bucket. A release that exists but belongs to another project is `unknown`
 * (a release belongs to one project).
 */
export async function lookupReleaseIds(projectId: string, ids: readonly string[]): Promise<ScopeCheck> {
  const out: ScopeCheck = { known: [], unknown: [], unchecked: [] }
  const capped = ids.slice(0, RELEASE_CAP)
  out.unchecked.push(...ids.slice(RELEASE_CAP))
  const results = await Promise.all(
    capped.map(async (id) => {
      try {
        const { data } = await api.get<{ id?: string; project_id?: string }>(
          `/api/v1/releases/${encodeURIComponent(id)}`,
          // The caller names what it drops; a 403 toast would double it.
          { suppressToast: true },
        )
        return { id, verdict: data?.project_id === projectId ? 'known' : 'unknown' } as const
      } catch (error) {
        const status = axios.isAxiosError(error) ? error.response?.status : undefined
        return { id, verdict: status !== undefined && NOT_YOURS.has(status) ? 'unknown' : 'unchecked' } as const
      }
    }),
  )
  for (const { id, verdict } of results) out[verdict].push(id)
  return out
}

/**
 * Classify suite names against a project's suite list. With the list missing
 * or incomplete nothing is `unknown` — everything not on it is `unchecked`.
 */
export function classifySuiteNames(
  names: readonly string[],
  list: { items?: ReadonlyArray<{ name?: string | null }>; total?: unknown } | undefined,
): ScopeCheck {
  const out: ScopeCheck = { known: [], unknown: [], unchecked: [] }
  const complete = isCompleteList(list)
  const onList = new Set((list?.items ?? []).map((s) => normalizeSuiteName(s?.name)))
  for (const name of names) {
    if (onList.has(normalizeSuiteName(name))) out.known.push(name)
    else if (complete) out.unknown.push(name)
    else out.unchecked.push(name)
  }
  return out
}
