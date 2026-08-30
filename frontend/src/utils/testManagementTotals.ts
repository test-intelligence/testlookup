/**
 * Pure helpers for deriving the two distinct "totals" that the Test
 * Management page (Test Cases tab) renders:
 *
 *  - ``authoredTotal`` — the count of rows in ``managed_test_cases``
 *    only. The Library Health verdict panel summarises the AUTHORED
 *    catalog, so it must NOT include automation-synthesised rows.
 *
 *  - ``casesTotal`` — the count of rows the table is actually showing
 *    (managed + automation, since the table fetches with
 *    ``include_automation=true``). The table header "Cases N of M"
 *    must reflect this; otherwise it reads "Cases 25 of 0" when
 *    automation rows merged into the view but no managed rows exist.
 *
 * Splitting the two killed a recurring regression where one shared
 * ``totalCases`` value flowed into both UI sites and the table header
 * appeared empty even with rows rendered — third occurrence of
 * "Test Cases tab shows nothing", 2026-05-16.
 */

interface CasesResponse {
  /** Total rows in the response. May reflect the merged
   * managed+automation set (cases query) OR the authored-only set
   * (Library Health snapshot) depending on what was requested. */
  total?: number
}

export interface CasesTotals {
  /** Managed-only count, for the Library Health panel. */
  authoredTotal: number
  /** Merged count (or fallback to currently-rendered row count when
   * the cases query hasn't returned yet), for the table header. */
  casesTotal: number
}

/**
 * What the Library Health percentages are actually computed over.
 *
 * The panel derives every rate (automated %, avg age, >180d share, stale and
 * deprecated scores) from `healthRoll.items`, a page capped at `size: 200` —
 * while rendering `healthRoll.total`, the true server count, in the same
 * sentence. Past 200 authored cases those percentages silently describe an
 * arbitrary 200-row slice presented as a statement about the library.
 *
 * Returning `null` when nothing is truncated keeps the common case clean; the
 * caller renders the note only when the two numbers really disagree.
 */
export function describeStatBasis(sampleSize: number, authoredTotal: number): string | null {
  if (!Number.isFinite(sampleSize) || !Number.isFinite(authoredTotal)) return null
  if (sampleSize <= 0 || authoredTotal <= sampleSize) return null
  return `Rates below cover ${sampleSize} of ${authoredTotal} cases — the library-health sample, not the whole catalog.`
}

export function deriveTestManagementTotals(args: {
  /** Wider read fetched WITHOUT include_automation — the managed-only
   * snapshot driving Library Health. */
  healthRoll: CasesResponse | undefined | null
  /** Active paginated query fetched WITH include_automation — drives
   * the table. */
  data: CasesResponse | undefined | null
  /** Currently-rendered rows; the fallback when ``data`` is mid-flight. */
  casesLength: number
}): CasesTotals {
  return {
    authoredTotal: args.healthRoll?.total ?? 0,
    casesTotal: args.data?.total ?? args.casesLength,
  }
}
