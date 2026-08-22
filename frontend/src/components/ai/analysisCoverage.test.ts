/**
 * Regression guard: a decision report must not hide how much of the failure
 * set it actually analysed.
 *
 * The defect: `gap_detection_agent` classified every failed test and computed
 * `coverage_ratio` / `integrity_ok`, all of which reached
 * `quality_review.gap_report` -- and no consumer read any of it. The visible
 * result was a badge that said the opposite of the truth: "Verified with
 * analysis gaps" fired on `status === 'degraded'` (a specialist stage missing,
 * or the payload truncated), so a report whose release recommendation rested
 * on 1 analysed failure out of 50 rendered as a clean "Verified".
 */
import { describe, it, expect } from 'vitest'
import { readAnalysisCoverage, describeAnalysisCoverage } from './analysisCoverage'

const full = {
  failed_count: 50, analyzed_count: 50, skipped_count: 0, errored_count: 0,
  integrity_ok: true, inconclusive_count: 0, no_evidence_count: 0,
}

describe('readAnalysisCoverage', () => {
  it('flags the case the badge used to call clean: 1 of 50 analysed', () => {
    const coverage = readAnalysisCoverage({
      ...full, analyzed_count: 1, skipped_count: 49,
    })
    expect(coverage?.hasAnalysisGaps).toBe(true)
    expect(coverage?.uncoveredCount).toBe(49)
    expect(coverage?.coverageRatio).toBeCloseTo(0.02)
  })

  it('counts errored failures as uncovered, not as analysed', () => {
    const coverage = readAnalysisCoverage({
      ...full, analyzed_count: 40, skipped_count: 0, errored_count: 10,
    })
    expect(coverage?.uncoveredCount).toBe(10)
    expect(coverage?.hasAnalysisGaps).toBe(true)
  })

  it('treats a failed integrity check as a gap even when nothing is uncovered', () => {
    // The agent is saying its own numbers do not add up. That must never
    // render as a clean report.
    const coverage = readAnalysisCoverage({ ...full, integrity_ok: false })
    expect(coverage?.hasAnalysisGaps).toBe(true)
  })

  it('does not treat a weak conclusion as an unanalysed failure', () => {
    const coverage = readAnalysisCoverage({
      ...full, inconclusive_count: 3, no_evidence_count: 2,
    })
    expect(coverage?.weakCount).toBe(5)
    expect(coverage?.uncoveredCount).toBe(0)
    expect(coverage?.hasAnalysisGaps).toBe(false)
  })

  it('reports full coverage as no gaps', () => {
    expect(readAnalysisCoverage(full)?.hasAnalysisGaps).toBe(false)
  })

  it('returns null when the report carries no coverage data', () => {
    // Absent is NOT "no gaps": every report generated before gap_report was
    // published has none, and claiming 0/0 coverage for them would be a lie
    // in the other direction.
    expect(readAnalysisCoverage(null)).toBeNull()
    expect(readAnalysisCoverage(undefined)).toBeNull()
    expect(readAnalysisCoverage({})).toBeNull()
  })

  it('refuses malformed counts rather than inventing a number', () => {
    expect(readAnalysisCoverage({ failed_count: 'ten', analyzed_count: 1 })).toBeNull()
    expect(readAnalysisCoverage({ failed_count: -5, analyzed_count: 1 })).toBeNull()
    expect(readAnalysisCoverage({ failed_count: 10 })).toBeNull()
  })

  it('does not divide by zero when a run had no failures', () => {
    const coverage = readAnalysisCoverage({
      ...full, failed_count: 0, analyzed_count: 0,
    })
    expect(coverage?.coverageRatio).toBe(1)
    expect(coverage?.hasAnalysisGaps).toBe(false)
  })

  it('derives integrity from the counts when the flag is absent', () => {
    const coverage = readAnalysisCoverage({
      failed_count: 10, analyzed_count: 3, skipped_count: 2, errored_count: 1,
    })
    expect(coverage?.integrityOk).toBe(false)
  })
})

describe('describeAnalysisCoverage', () => {
  it('names both numbers so the reader can judge the recommendation', () => {
    const note = describeAnalysisCoverage(readAnalysisCoverage({
      ...full, analyzed_count: 1, skipped_count: 49,
    }))
    expect(note).toContain('49')
    expect(note).toContain('50')
  })

  it('says coverage is unknown when the audit did not reconcile', () => {
    const note = describeAnalysisCoverage(readAnalysisCoverage({
      ...full, integrity_ok: false,
    }))
    expect(note).toMatch(/did not reconcile/i)
  })

  it('returns null for a report with no coverage data', () => {
    expect(describeAnalysisCoverage(null)).toBeNull()
  })
})
