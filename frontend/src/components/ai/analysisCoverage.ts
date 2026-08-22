/**
 * Analysis coverage, read from the decision report's `gap_report`.
 *
 * `gap_detection_agent` classifies every failed test in a deep workflow into
 * five buckets and computes `coverage_ratio` / `integrity_ok`. All of it was
 * threaded through the pipeline into `quality_review.gap_report` and then
 * read by nobody: not the markdown, not `report_status`, not
 * `requires_human_review`, not this UI.
 *
 * The visible consequence was a badge that said the opposite of the truth.
 * "Verified with analysis gaps" fired on `status === 'degraded'`, which means
 * *a specialist stage was missing or the persisted payload was truncated* —
 * so a report where every failure was analysed cleanly but one specialist
 * timed out was labelled as having analysis gaps, while a report whose
 * release decision rested on 1 analysed failure out of 50 was labelled a
 * clean "Verified".
 *
 * Everything here is defensive: `gap_report` crosses an agent boundary, and a
 * malformed one must degrade to "unknown" rather than invent a coverage
 * number. `null` means "this report does not carry coverage data" — which is
 * true of every report generated before the field was published, and is NOT
 * the same as "no gaps".
 */

export interface AnalysisCoverage {
  failedCount: number
  analyzedCount: number
  /** Failures with no usable analysis at all: skipped + errored. */
  uncoveredCount: number
  /** Analysed, but the conclusion is weak: inconclusive + no-evidence. */
  weakCount: number
  coverageRatio: number
  /** The agent's own arithmetic reconciled (analyzed + skipped + errored === failed). */
  integrityOk: boolean
  /** Something a reader of this release decision should be told. */
  hasAnalysisGaps: boolean
}

function toCount(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return Math.floor(value)
}

/**
 * Returns `null` when the report carries no usable coverage data, so callers
 * can fall back instead of rendering a fabricated 0/0.
 */
export function readAnalysisCoverage(
  gapReport: Record<string, unknown> | null | undefined,
): AnalysisCoverage | null {
  if (!gapReport || typeof gapReport !== 'object') return null

  const failedCount = toCount(gapReport.failed_count)
  const analyzedCount = toCount(gapReport.analyzed_count)
  if (failedCount === null || analyzedCount === null) return null

  const skipped = toCount(gapReport.skipped_count) ?? 0
  const errored = toCount(gapReport.errored_count) ?? 0
  const inconclusive = toCount(gapReport.inconclusive_count) ?? 0
  const noEvidence = toCount(gapReport.no_evidence_count) ?? 0

  // Trust the counts over the ratio: the ratio is a float the agent already
  // derived, and a disagreement between them is exactly what integrity_ok
  // exists to catch.
  const coverageRatio = failedCount > 0 ? analyzedCount / failedCount : 1

  const integrityOk = typeof gapReport.integrity_ok === 'boolean'
    ? gapReport.integrity_ok
    : analyzedCount + skipped + errored === failedCount

  const uncoveredCount = skipped + errored
  const weakCount = inconclusive + noEvidence

  return {
    failedCount,
    analyzedCount,
    uncoveredCount,
    weakCount,
    coverageRatio,
    integrityOk,
    // A weak conclusion is still a conclusion; an unanalysed failure is not.
    // Integrity failure counts on its own -- the agent is telling us its own
    // numbers do not add up, and that must never render as a clean report.
    hasAnalysisGaps: uncoveredCount > 0 || !integrityOk,
  }
}

/** One sentence a release reader can act on. Never fabricates when data is absent. */
export function describeAnalysisCoverage(coverage: AnalysisCoverage | null): string | null {
  if (!coverage) return null
  if (!coverage.integrityOk) {
    return `Coverage audit did not reconcile: ${coverage.analyzedCount} analysed `
      + `+ ${coverage.uncoveredCount} unanalysed does not account for `
      + `${coverage.failedCount} failure(s). Treat this report's coverage as unknown.`
  }
  if (coverage.uncoveredCount > 0) {
    return `${coverage.uncoveredCount} of ${coverage.failedCount} failure(s) were `
      + `never analysed, so this recommendation rests on `
      + `${coverage.analyzedCount}.`
  }
  if (coverage.weakCount > 0) {
    return `All ${coverage.failedCount} failure(s) were analysed, but `
      + `${coverage.weakCount} produced no usable conclusion.`
  }
  return `All ${coverage.failedCount} failure(s) were analysed.`
}
