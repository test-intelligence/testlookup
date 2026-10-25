import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DecisionReportEvalCycle, ReportEvalReadiness } from './aiEvalService'

// W16: the request paths are the backend contract (the SPA wire-contract guard
// reads them), so every new one is pinned here in full with its params.
vi.mock('./http', () => ({
  getData: vi.fn(async () => ({})),
  postData: vi.fn(async () => ({})),
}))
const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { getData, postData } from './http'
import * as service from './aiEvalService'

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

describe('W16 AI-eval request paths', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.post.mockResolvedValue({ data: {} })
  })

  it.each<[string, () => Promise<unknown>, string, unknown]>([
    ['agent-stack history', () => service.listAgentStackGateRuns({ limit: 20 }),
      '/api/v1/ai-eval/agent-stack-release-gate/runs', { params: { limit: 20 } }],
    ['a manifest (the checksum encoded)', () => service.getEvalManifest('ab/cd ef'),
      '/api/v1/ai-eval/gates/ab%2Fcd%20ef', undefined],
    ['candidate options', () => service.getCandidateOptions(), '/api/v1/ai-eval/candidate-options', undefined],
    ['the runs list with every filter', () => service.listEvalRuns({
      dataset_id: 'dsA1', task_type: 'classification', metric_kind: 'replay', status: 'completed', limit: 20,
    }), '/api/v1/ai-eval/runs', {
      params: { dataset_id: 'dsA1', task_type: 'classification', metric_kind: 'replay', status: 'completed', limit: 20 },
    }],
    ['the runs list with no filter', () => service.listEvalRuns(), '/api/v1/ai-eval/runs', { params: {} }],
    ['one run', () => service.getEvalRun('rn/1'), '/api/v1/ai-eval/runs/rn%2F1', undefined],
    ['a run’s items', () => service.listEvalRunItems('rnA1', { status: 'incorrect', limit: 25, offset: 25 }),
      '/api/v1/ai-eval/runs/rnA1/items', { params: { status: 'incorrect', limit: 25, offset: 25 } }],
    ['the G2 history', () => service.listTierComparisonRuns('prA1', { agent_id: 'agent.summary.v1', limit: 20 }),
      '/api/v1/ai-eval/tier-comparison/runs', { params: { project_id: 'prA1', agent_id: 'agent.summary.v1', limit: 20 } }],
    ['the G3 history', () => service.listReviewerQualityRuns('prA1', { limit: 20 }),
      '/api/v1/ai-eval/reviewer-quality/runs', { params: { project_id: 'prA1', limit: 20 } }],
    ['the shadow pairs', () => service.listShadowPairs('prA1', { label_status: 'pending', limit: 25, offset: 0 }),
      '/api/v1/ai-eval/shadow-pairs', { params: { project_id: 'prA1', label_status: 'pending', limit: 25, offset: 0 } }],
  ])('GET %s', async (_name, call, path, config) => {
    await call()
    expect(getData).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getData).mock.calls[0]).toEqual(config === undefined ? [path] : [path, config])
  })

  it.each<[string, () => Promise<unknown>, string, unknown]>([
    ['the agent-stack gate', () => service.runAgentStackGate({ change_id: 'manual-2026-10-25' }),
      '/api/v1/ai-eval/agent-stack-release-gate', { change_id: 'manual-2026-10-25' }],
    ['a candidate evaluation (the dataset id encoded)', () => service.evaluateCandidate('ds/A1', {
      candidate: { tier: 'rules' }, project_id: 'prA1', limit: 12,
    }), '/api/v1/ai-eval/runs/evaluate/ds%2FA1', { candidate: { tier: 'rules' }, project_id: 'prA1', limit: 12 }],
    ['a tier comparison', () => service.runTierComparison({
      project_id: 'prA1', agent_id: 'agent.root_cause_analysis.v1', capability: 'root_cause_analysis',
      incumbent_tier: 'llm', candidate_tier: 'slm', delta: 0.05, pairs: [], include_live_labels: true, persist: true,
    }), '/api/v1/ai-eval/tier-comparison', {
      project_id: 'prA1', agent_id: 'agent.root_cause_analysis.v1', capability: 'root_cause_analysis',
      incumbent_tier: 'llm', candidate_tier: 'slm', delta: 0.05, pairs: [], include_live_labels: true, persist: true,
    }],
    ['a shadow-pair label (the pair id encoded)', () => service.labelShadowPair('sp A1', {
      verdict: 'candidate', failure_category: 'PRODUCT_BUG',
    }), '/api/v1/ai-eval/shadow-pairs/sp%20A1/label', { verdict: 'candidate', failure_category: 'PRODUCT_BUG' }],
  ])('POST %s without the shared toast', async (_name, call, path, body) => {
    await call()
    expect(vi.mocked(postData).mock.calls).toEqual([[path, body, { suppressToast: true }]])
  })

  it('the legacy gate body is today’s; a candidate run adds candidate_run_id', async () => {
    await service.runPreReleaseGate('classification', 'AnalysisAgent')
    await service.runPreReleaseGate('duplicate_detection', 'DefectPromotionAgent', undefined, 'rnA1')
    expect(apiMock.post.mock.calls).toEqual([
      ['/api/v1/ai-eval/pre-release-gate',
        { task_type: 'classification', agent_name: 'AnalysisAgent', dataset_id: null }, { suppressToast: true }],
      ['/api/v1/ai-eval/pre-release-gate',
        { task_type: 'duplicate_detection', agent_name: 'DefectPromotionAgent', dataset_id: null, candidate_run_id: 'rnA1' },
        { suppressToast: true }],
    ])
  })

  it('a baseline from a replay run sends eval_run_id', async () => {
    await service.setBaseline({ task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: 'rnA1' })
    expect(apiMock.post).toHaveBeenCalledWith(
      '/api/v1/ai-eval/baselines',
      { task_type: 'classification', agent_name: 'AnalysisAgent', eval_run_id: 'rnA1' },
      { suppressToast: true },
    )
  })

  it('the legacy evaluation still POSTs with no body', async () => {
    await service.runEvaluation('dsA1')
    expect(apiMock.post).toHaveBeenCalledWith('/api/v1/ai-eval/runs/evaluate/dsA1')
  })
})
