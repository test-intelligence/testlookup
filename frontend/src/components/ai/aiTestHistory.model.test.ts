/**
 * W03 AIH-05: the pure model of the History tab's AI strip (contract §9.8):
 * chips oldest -> newest, only for points with an analysis; change markers
 * (category first, then rewordings); the version labels for every trigger.
 */
import { describe, expect, it } from 'vitest'
import type { AITestVersion, TestHistoryAI, TestHistoryAIChange, TestHistoryAIPoint } from '@/types/aiTestHistory'
import {
  buildStripModel,
  changeText,
  confidenceText,
  engineText,
  engineWord,
  versionLabel,
  versionSummary,
} from './aiTestHistory.model'

const ai = (over: Partial<TestHistoryAI> = {}): TestHistoryAI => ({
  test_id: 'tc',
  category: 'PRODUCT_BUG',
  confidence: 85,
  root_cause_excerpt: 'the gateway timed out',
  root_cause_sha: 'aaaaaaaaaaaaaaaa',
  analysis_mode: 'llm',
  model: 'llama3.1',
  is_flaky: false,
  requires_human_review: false,
  version_count: 2,
  ...over,
})

const point = (n: number, a: TestHistoryAI | null | undefined): TestHistoryAIPoint => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  build_number: String(n),
  run_seq: n,
  status: a ? 'FAILED' : 'PASSED',
  duration_ms: 100,
  created_at: `2026-10-${String(n).padStart(2, '0')}T10:00:00Z`,
  ...(a === undefined ? {} : { ai: a }),
})

const change = (n: number, over: Partial<TestHistoryAIChange> = {}): TestHistoryAIChange => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  from_category: 'INFRASTRUCTURE',
  to_category: 'PRODUCT_BUG',
  from_confidence: 40,
  to_confidence: 85,
  kinds: ['category'],
  ...over,
})

const version = (n: number, trigger: string, over: Partial<AITestVersion> = {}): AITestVersion => ({
  version_id: `v-${n}`,
  version: n,
  trigger,
  pipeline_run_id: null,
  actor: null,
  failure_category: 'PRODUCT_BUG',
  confidence_score: 80,
  analysis_mode: null,
  llm_provider: null,
  llm_model: null,
  is_flaky: false,
  requires_human_review: false,
  root_cause_excerpt: '',
  content_sha256: 'f'.repeat(64),
  created_at: '2026-10-14T10:00:00Z',
  ...over,
})

describe('versionLabel — every trigger', () => {
  it.each([
    [version(1, 'pipeline', { analysis_mode: 'rules' }), 'v1 rules'],
    [version(2, 'pipeline', { analysis_mode: 'llm' }), 'v2 LLM'],
    [version(2, 'pipeline', { analysis_mode: 'ml' }), 'v2 ML'],
    [version(2, 'pipeline', { analysis_mode: 'mixed' }), 'v2 mixed'],
    [version(4, 'pipeline', { analysis_mode: null }), 'v4 pipeline'],
    [version(3, 'human_correction', { actor: { user_id: 'u', name: 'anna' } }), 'v3 human correction (anna)'],
    [version(3, 'human_correction', { actor: null }), 'v3 human correction'],
    [version(2, 'manual_analyze', { actor: { user_id: 'u', name: 'anna' } }), 'v2 manual analysis'],
    [version(2, 'bulk_reclassify', { actor: { user_id: 'u', name: 'anna' } }), 'v2 bulk reclassify (anna)'],
    [version(1, 'backfill', { analysis_mode: 'rules' }), 'v1 rules (backfilled)'],
    [version(1, 'backfill', { analysis_mode: null }), 'v1 backfilled'],
    [version(0, 'current'), 'current'],
    // A trigger this client does not know keeps its own word (shown, not hidden).
    [version(5, 'replay'), 'v5 replay'],
  ])('%#: %s', (v, expected) => {
    expect(versionLabel(v as AITestVersion)).toBe(expected)
  })

  it('summarises oldest -> newest whatever the input order (the API sends newest first)', () => {
    const versions = [
      version(3, 'human_correction', { actor: { user_id: 'u', name: 'anna' } }),
      version(1, 'pipeline', { analysis_mode: 'rules' }),
      version(2, 'pipeline', { analysis_mode: 'llm' }),
    ]
    expect(versionSummary(versions)).toBe('v1 rules · v2 LLM · v3 human correction (anna)')
  })
})

describe('the words for missing values', () => {
  it('never turns a missing confidence or engine into a number', () => {
    expect(confidenceText(null)).toBe('confidence not reported')
    expect(confidenceText(0)).toBe('0%')
    expect(engineText(null, null)).toBe('engine not reported')
    expect(engineText('llm', 'llama3.1')).toBe('LLM llama3.1')
    expect(engineText(null, 'llama3.1')).toBe('llama3.1')
    expect(engineWord('RULES')).toBe('rules')
    expect(engineWord('quantum')).toBe('quantum')
    expect(engineWord('')).toBeNull()
  })
})

describe('changeText', () => {
  it('names the category change, with the confidence when both sides have one', () => {
    expect(changeText(change(60))).toBe('AI changed its mind on Run #60: INFRASTRUCTURE → PRODUCT_BUG (confidence 40% → 85%)')
    expect(changeText(change(60, { from_confidence: null }))).toBe('AI changed its mind on Run #60: INFRASTRUCTURE → PRODUCT_BUG')
    expect(changeText(change(60, { from_category: null }))).toContain('uncategorized → PRODUCT_BUG')
  })

  it('a root-cause-only change reads as a rewording; both kinds read as the category change', () => {
    expect(changeText(change(58, { kinds: ['root_cause'] }))).toBe('AI reworded its root cause on Run #58')
    expect(changeText(change(58, { kinds: ['category', 'root_cause'] }))).toMatch(/^AI changed its mind on Run #58/)
    expect(changeText(change(58, { kinds: [] }))).toBe('AI analysis changed on Run #58')
  })
})

describe('buildStripModel', () => {
  it('one chip per point WITH an analysis, oldest -> newest; passed / older-server points are skipped', () => {
    // Newest first, as the API sends it: #5 analysed, #4 passed (ai null), #3 analysed, #2 older server (no key), #1 analysed.
    const history = [
      point(5, ai({ test_id: 't5', category: 'PRODUCT_BUG', confidence: 85 })),
      point(4, null),
      point(3, ai({ test_id: 't3', category: 'INFRASTRUCTURE', confidence: 40, analysis_mode: 'rules', model: null })),
      point(2, undefined),
      point(1, ai({ test_id: 't1', category: null, confidence: null })),
    ]
    const model = buildStripModel({ history, changes: [], runId: 'run-5' })
    expect(model.chips.map((c) => c.runLabel)).toEqual(['Run #1', 'Run #3', 'Run #5'])
    expect(model.chips.map((c) => c.testId)).toEqual(['t1', 't3', 't5'])
    expect(model.chips.map((c) => c.text)).toEqual(['uncategorized', 'INFRASTRUCTURE 40%', 'PRODUCT_BUG 85%'])
    expect(model.chips[1].title).toBe('Run #3 - INFRASTRUCTURE 40% - rules')
    expect(model.chips[0].title).toBe('Run #1 - uncategorized confidence not reported - LLM llama3.1')
    expect(model.chips.map((c) => c.isCurrentRun)).toEqual([false, false, true])
  })

  it('no analysis anywhere -> no chips (the panel mounts nothing)', () => {
    expect(buildStripModel({ history: [point(2, null), point(1, undefined)] }).chips).toEqual([])
  })

  it('marks the chip where the AI changed, and lists category changes before rewordings, newest first', () => {
    const history = [point(4, ai()), point(3, ai()), point(2, ai()), point(1, ai())]
    // The API emits changes oldest -> newest. The rewording (#3) is NEWER than
    // the category change on #2, and still listed after it.
    const changes = [
      change(2, { kinds: ['category'], from_category: 'PRODUCT_BUG', to_category: 'INFRASTRUCTURE' }),
      change(3, { kinds: ['root_cause'] }),
      change(4, { kinds: ['category', 'root_cause'] }),
    ]
    const model = buildStripModel({ history, changes })
    expect(model.changes.map((c) => [c.runLabel, c.tone])).toEqual([
      ['Run #4', 'category'],
      ['Run #2', 'category'],
      ['Run #3', 'root_cause'],
    ])
    expect(model.chips.map((c) => c.changeKinds)).toEqual([[], ['category'], ['root_cause'], ['category', 'root_cause']])
  })

  it('a change of a legacy point without a run id is matched by its label', () => {
    const legacy = { ...point(1, ai()), run_id: null }
    const model = buildStripModel({ history: [point(2, ai()), legacy], changes: [change(1, { run_id: null })] })
    expect(model.chips[0].changeKinds).toEqual(['category'])
    expect(model.chips[0].key).toBe('ai-0')
  })
})
