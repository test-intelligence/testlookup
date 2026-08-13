import { describe, expect, it } from 'vitest'
import type { DecisionReportEvalCycle, ReportEvalReadiness } from './aiEvalService'

describe('DecisionReportEvalCycle contract', () => {
  it('represents an honest pilot gate state', () => {
    const cycle: DecisionReportEvalCycle = {
      id: 'cycle-1',
      cycle_key: 'pilot-v1:cycle-1',
      corpus_version: 'pilot-v1',
      corpus_sha256: 'a'.repeat(64),
      report_count: 24,
      status: 'warn',
      metrics: { citation_validity: 0.99 },
      checks: [{ name: 'utility', status: 'not_evaluated' }],
      unavailable_metrics: ['utility'],
      consecutive_passes: 0,
      evaluated_by: null,
      evaluated_at: null,
    }
    expect(cycle.status).toBe('warn')
    expect(cycle.unavailable_metrics).toContain('utility')
    expect(cycle.consecutive_passes).toBe(0)
  })

  it('accepts action governance metrics in the extensible cycle projection', () => {
    const cycle: DecisionReportEvalCycle = {
      id: 'cycle-1',
      cycle_key: 'pilot-v1:cycle-1',
      corpus_version: 'pilot-v1',
      corpus_sha256: 'a'.repeat(64),
      report_count: 1,
      status: 'warn',
      metrics: { action_count: 2, action_resolution_rate: 0.5 },
      checks: [{ name: 'action_governance', status: 'warn' }],
      unavailable_metrics: [],
      consecutive_passes: 0,
      evaluated_by: null,
      evaluated_at: null,
    }
    expect(cycle.metrics.action_count).toBe(2)
    expect(cycle.metrics.action_resolution_rate).toBe(0.5)
  })

  it('represents fail-closed pilot readiness reasons', () => {
    const readiness: ReportEvalReadiness = {
      status: 'not_ready',
      latest_status: 'pass',
      consecutive_passes: 1,
      required_consecutive_passes: 2,
      utility_rate: null,
      minimum_utility_rate: 0.8,
      corpus_version: 'pilot-v1',
      corpus_sha256: 'a'.repeat(64),
      reasons: ['consecutive_passes_below_threshold', 'qualified_user_utility_unavailable'],
    }
    expect(readiness.status).toBe('not_ready')
    expect(readiness.reasons).toHaveLength(2)
  })
})
