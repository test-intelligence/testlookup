/**
 * The AGT-09 tabs' pure model. The Gate tab read `PASS` / `FAIL` against a server that answers `pass` / `fail` /
 * `insufficient_samples` — every run toasted "No baseline configured" — so the verdict cases below feed the
 * SERVER's lowercase values and assert what a person reads.
 */
import { describe, expect, it } from 'vitest'

import {
  COPY,
  TONE_CLASS,
  baselineConfirmSentence,
  candidateLabel,
  defaultChangeId,
  gateFor,
  isActiveRun,
  itemCells,
  itemStatusChip,
  metricChip,
  metricText,
  prettyJson,
  refusalText,
  runCounts,
  runMetric,
  runProgress,
  runStatusChip,
  shortChecksum,
  verdictLabel,
  verdictTone,
  workflowGateResult,
} from './aiEvalModel'

describe('verdicts are read in the server vocabulary', () => {
  it('maps pass / fail / insufficient_samples to what a person reads', () => {
    expect(verdictLabel('pass')).toBe('PASS')
    expect(verdictLabel('fail')).toBe('FAIL')
    expect(verdictLabel('insufficient_samples')).toBe('Not enough evidence')
  })

  it('colours them: pass success, fail danger, not enough evidence warning', () => {
    expect(verdictTone('pass')).toBe('success')
    expect(verdictTone('fail')).toBe('danger')
    expect(verdictTone('insufficient_samples')).toBe('warning')
  })

  it('never guesses: the old uppercase vocabulary is not a pass, an unknown verdict renders as itself', () => {
    expect(verdictTone('PASS')).toBe('muted')
    expect(verdictLabel('needs_review')).toBe('needs_review')
    expect(verdictLabel(undefined)).toBe('—')
  })

  it('hands the workflow timeline its legacy words, so a passing gate is not drawn as a failed stage', () => {
    expect(workflowGateResult({ status: 'pass', x: 1 })).toEqual({ status: 'PASS', x: 1 })
    expect(workflowGateResult({ status: 'fail' })?.status).toBe('FAIL')
    expect(workflowGateResult({ status: 'insufficient_samples' })?.status).toBe('WARN')
    expect(workflowGateResult(null)).toBeNull()
  })

  it('paints every tone with --ui-* or muted tokens only', () => {
    for (const cls of Object.values(TONE_CLASS)) expect(cls).not.toMatch(/--status-/)
  })
})

describe('metrics: absence is not health', () => {
  it('formats a measured rate', () => {
    expect(metricText({ value: 0.84, measured: true })).toBe('84.0%')
  })

  it('shows — for a metric computed from zero scored items, never 0%', () => {
    expect(metricText({ value: 0, measured: false })).toBe('—')
    expect(metricText({ value: null, measured: true })).toBe('—')
    expect(metricText(undefined)).toBe('—')
    expect(COPY.notMeasured).toBe('Not measured')
  })

  it("reads a run's accuracy as not measured when the replay scored nothing", () => {
    expect(metricText(runMetric({ accuracy: 0, measured: false }, 'accuracy'))).toBe('—')
    expect(metricText(runMetric({ f1_score: 0.801 }, 'f1_score'))).toBe('80.1%')
  })
})

describe('legacy vs replay runs', () => {
  it('labels a run without metric_kind (a legacy row, an older server) as the legacy label-agreement metric', () => {
    expect(metricChip({})).toEqual({ label: 'Legacy label agreement', tone: 'muted', title: 'Legacy label-agreement metric' })
    expect(metricChip({ metric_kind: 'label_agreement' }).label).toBe('Legacy label agreement')
  })

  it('labels a replay with its tier', () => {
    expect(metricChip({ metric_kind: 'replay', candidate: { tier: 'rules' } }).label).toBe('Replay · Rules')
    expect(metricChip({ metric_kind: 'replay', candidate: { tier: 'ml' } }).tone).toBe('info')
  })

  it('reads status: a legacy row is completed; queued / running are active', () => {
    expect(runStatusChip(undefined)).toEqual({ label: 'Completed', tone: 'success' })
    expect(runStatusChip('partial').tone).toBe('warning')
    expect(runStatusChip('failed').tone).toBe('danger')
    expect(isActiveRun({ status: 'queued' })).toBe(true)
    expect(isActiveRun({ status: 'running' })).toBe(true)
    expect(isActiveRun({ status: 'completed' })).toBe(false)
    expect(isActiveRun({})).toBe(false)
  })

  it('counts progress over every stored item, and the terminal line by state', () => {
    const run = { total_items: 12, scored_items: 3, unscored_items: 1, error_items: 1 }
    expect(runProgress(run)).toBe('5 of 12 items')
    expect(runCounts({ scored_items: 9, unscored_items: 1, error_items: 2 })).toBe('9 scored · 1 unscored · 2 errors')
    expect(runCounts({ scored_items: 9, unscored_items: 0, error_items: 1 })).toBe('9 scored · 0 unscored · 1 error')
  })
})

describe('candidate options', () => {
  it('labels the tiers, a local model with its provider and model', () => {
    expect(candidateLabel({ tier: 'rules' })).toBe('Rules')
    expect(candidateLabel({ tier: 'ml' })).toBe('ML')
    expect(candidateLabel({ tier: 'llm', provider: 'ollama', model: 'llama3.1:8b-instruct' })).toBe(
      'Local model · ollama · llama3.1:8b-instruct',
    )
  })

  it('never calls a cloud model local', () => {
    expect(candidateLabel({ tier: 'llm', provider: 'openai', model: 'gpt-4o' })).toBe('Language model · openai · gpt-4o')
  })
})

describe('items: expected vs candidate per task type', () => {
  it('classification compares failure categories', () => {
    const cells = itemCells('classification', {
      item_index: 0,
      item_key: 'clear_product_bug_npe',
      expected: { failure_category: 'PRODUCT_BUG' },
      output: { failure_category: 'INFRASTRUCTURE' },
      mode_used: 'rules',
      latency_ms: 14,
    })
    expect(cells).toEqual({ item: 'clear_product_bug_npe', expected: 'PRODUCT_BUG', candidate: 'INFRASTRUCTURE', engine: 'Rules', ms: '14' })
  })

  it('root cause reads the expected category; duplicate detection the decision and its ratio', () => {
    expect(itemCells('root_cause', { item_index: 1, expected: { category: 'PRODUCT_BUG' }, output: { failure_category: 'PRODUCT_BUG' } }).expected).toBe('PRODUCT_BUG')
    const dup = itemCells('duplicate_detection', {
      item_index: 2,
      input: { title_a: 'Login timeout on staging' },
      expected: { is_duplicate: true },
      output: { predicted_duplicate: false, ratio: 0.4123 },
    })
    expect(dup).toMatchObject({ item: 'Login timeout on staging', expected: 'Duplicate', candidate: 'Distinct (0.41)' })
  })

  it('release decision compares recommendations; an errored item without output shows —', () => {
    expect(itemCells('release_decision', { item_index: 3, expected: { recommendation: 'GO' }, output: null })).toMatchObject({
      item: 'Item 4',
      expected: 'GO',
      candidate: '—',
      engine: '—',
      ms: '—',
    })
  })

  it('never puts an object into a cell, and pretty-prints the diff as JSON text', () => {
    expect(itemCells('classification', { item_index: 0, expected: { failure_category: { x: 1 } } }).expected).toBe('—')
    expect(prettyJson({ a: '<script>alert(1)</script>' })).toBe('{\n  "a": "<script>alert(1)</script>"\n}')
    expect(prettyJson(null)).toBe('—')
  })

  it('labels item statuses', () => {
    expect(itemStatusChip('correct')).toEqual({ label: 'Correct', tone: 'success' })
    expect(itemStatusChip('incorrect').tone).toBe('danger')
    expect(itemStatusChip('unscored').tone).toBe('warning')
    expect(itemStatusChip('error').label).toBe('Error')
  })
})

describe('gates and baselines', () => {
  it('gates duplicate detection with DefectPromotionAgent (the stack default), not ClusterAgent', () => {
    expect(gateFor('duplicate_detection')?.agent_name).toBe('DefectPromotionAgent')
    expect(gateFor('classification')?.agent_name).toBe('AnalysisAgent')
    expect(gateFor('release_decision')?.agent_name).toBe('ReleaseRiskAgent')
  })

  it('asks the Set-as-baseline question with the task and agent', () => {
    const sentence = baselineConfirmSentence('classification', 'AnalysisAgent')
    expect(sentence).toMatch(/^Make this run the .+ baseline for AnalysisAgent\? /)
    expect(sentence).toMatch(/The gate compares later candidates against its accuracy and F1\.$/)
  })

  it('shortens a checksum to 12 characters and builds the default change id from a day', () => {
    expect(shortChecksum('a'.repeat(64))).toBe('a'.repeat(12))
    expect(shortChecksum(null)).toBe('—')
    expect(defaultChangeId(new Date(2026, 9, 5, 12))).toBe('manual-2026-10-05')
  })

  it("shows a refused write's sentence, not the code", () => {
    const err = { response: { data: { detail: { code: 'candidate_run_not_complete', message: 'Only a completed replay run can be gated; this one is running.' } } } }
    expect(refusalText(err, 'x')).toBe('Only a completed replay run can be gated; this one is running.')
    expect(refusalText(new Error('boom'), 'The gate did not run.')).toBe('The gate did not run.')
  })
})
