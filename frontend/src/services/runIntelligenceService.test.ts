import { describe, expect, it } from 'vitest'
import type { DecisionReportVersion, StructuredSummary } from './runIntelligenceService'

describe('DecisionReportV1 projection contract', () => {
  it('keeps the published version identity separate from the latest attempt', () => {
    const report: DecisionReportVersion = {
      report_id: 'report-1',
      report_version: 2,
      supersedes_report_id: 'report-0',
      status: 'published',
      generated_at: '2026-08-12T00:00:00Z',
    }
    const summary: Pick<StructuredSummary, 'decision_report' | 'latest_decision_attempt'> = {
      decision_report: report,
      latest_decision_attempt: {
        pipeline_run_id: 'pipeline-rejected',
        status: 'rejected',
        verification_status: 'failed',
        at: '2026-08-12T00:01:00Z',
      },
    }

    expect(summary.decision_report?.report_version).toBe(2)
    expect(summary.latest_decision_attempt?.status).toBe('rejected')
    expect(summary.decision_report?.report_id).not.toBe(summary.latest_decision_attempt?.pipeline_run_id)
  })
})