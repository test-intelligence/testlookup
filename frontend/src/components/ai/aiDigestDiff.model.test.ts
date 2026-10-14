/**
 * W03 AIH-02/03/04: `buildDiffModel` (contract §9.4) — the sections in their fixed order with provenance FIRST,
 * each omitted when empty; the mode's labels; the pinned sort orders; root-cause-only rewording split from material
 * changes; and the empty-state words ("first analysis of this run", "No previous run on branch main").
 */
import { describe, expect, it } from 'vitest'

import { crossRunDiff, identicalDiff, versionsDiff } from './aiHistory.fixtures'
import {
  buildDiffModel,
  DIFF_SECTION_ORDER,
  engineLabel,
  engineWord,
  FIRST_ANALYSIS_TEXT,
  previousRunEmptyText,
  runLabel,
  versionLabel,
} from './aiDigestDiff.model'

describe('buildDiffModel — sections', () => {
  it('lists every non-empty section in the fixed order, provenance first', () => {
    const model = buildDiffModel(versionsDiff())
    expect(model.sections).toEqual(['provenance', 'verdict', 'categories', 'clusters', 'tests', 'actions'])
    expect(DIFF_SECTION_ORDER[0]).toBe('provenance')
    expect(model.provenance?.note).toBe('The analysis engine changed between these versions (rules -> llm).')
    expect(model.provenance?.changes).toEqual(['engine rules -> LLM', 'model none -> llama3.1', 'deterministic yes -> no'])
    expect(model.verdict).toMatchObject({ text: 'Verdict CONDITIONAL_GO -> NO_GO', direction: 'worse', risk: 'risk 55 -> 72 (+17)' })
  })

  it('omits a section that has nothing to say', () => {
    const diff = versionsDiff()
    const model = buildDiffModel({
      ...diff,
      provenance: { ...diff.provenance, changed: false },
      actions: { added: [], removed: [] },
    })
    expect(model.sections).toEqual(['verdict', 'categories', 'clusters', 'tests'])
    expect(model.provenance).toBeNull()
  })

  it('an unchanged verdict whose risk moved reads "(unchanged)" with the risk delta; nothing moved: no section', () => {
    const diff = versionsDiff()
    const riskOnly = buildDiffModel({ ...diff, verdict: { changed: false, from: 'NO_GO', to: 'NO_GO', direction: 'same', from_risk: 60, to_risk: 72, risk_delta: 12 } })
    expect(riskOnly.verdict).toMatchObject({ text: 'Verdict NO_GO (unchanged)', risk: 'risk 60 -> 72 (+12)' })
    const still = buildDiffModel({ ...diff, verdict: { changed: false, from: 'NO_GO', to: 'NO_GO', direction: 'same', from_risk: 72, to_risk: 72, risk_delta: 0 } })
    expect(still.verdict).toBeNull()
  })

  it('identical analyses: empty, with the "identical" wording', () => {
    const model = buildDiffModel(identicalDiff())
    expect(model.isEmpty).toBe(true)
    expect(model.sections).toEqual([])
    expect(model.emptyText).toMatch(/^No differences: the two analyses are identical/)
    const notIdentical = buildDiffModel({ ...identicalDiff(), identical: false })
    expect(notIdentical.emptyText).toMatch(/^No differences/)
    expect(notIdentical.emptyText).not.toMatch(/identical/)
  })
})

describe('buildDiffModel — grouping and sorting', () => {
  it('categories by the size of the change, then name: "PRODUCT_BUG +3"', () => {
    const model = buildDiffModel(versionsDiff())
    expect(model.categories.map((c) => c.text)).toEqual(['INFRASTRUCTURE -3', 'PRODUCT_BUG +3', 'FLAKY +1'])
  })

  it('versions mode: clusters Added / Removed / Resized (an unchanged match is not a change), largest first', () => {
    const model = buildDiffModel(versionsDiff(), { mode: 'versions' })
    expect(model.clusters.map((g) => g.label)).toEqual(['Added', 'Removed', 'Resized'])
    expect(model.clusters[0].rows.map((r) => r.label)).toEqual(['Timeout in checkout', 'Small new cluster'])
    expect(model.clusters[0].rows[0].detail).toBe('4 tests')
    const resized = model.clusters[2].rows
    expect(resized).toHaveLength(1)
    expect(resized[0]).toMatchObject({ label: 'Cache misses (renamed)', matchedBy: 'overlap', jaccard: 0.67 })
    expect(resized[0].detail).toBe('3 -> 5 tests · was "Cache misses" · matched by overlap (jaccard 0.67)')
  })

  it('cross-run mode: New root causes / Resolved / Persisting (every match persists)', () => {
    const model = buildDiffModel(crossRunDiff(), { mode: 'cross_run' })
    expect(model.mode).toBe('cross_run')
    expect(model.clusters.map((g) => g.label)).toEqual(['New root causes', 'Resolved', 'Persisting'])
    expect(model.clusters[2].rows.map((r) => r.label)).toEqual(['Cache misses (renamed)', 'Same size'])
    expect(model.tests.groups.map((g) => g.label)).toEqual(['New failing', 'Resolved', 'Changed', 'Reworded root cause'])
  })

  it('tests: Added / Removed / Changed (material first, biggest confidence move first) / Reworded root cause', () => {
    const model = buildDiffModel(versionsDiff())
    expect(model.tests.groups.map((g) => g.id)).toEqual(['added', 'removed', 'changed', 'reworded'])
    const changed = model.tests.groups.find((g) => g.id === 'changed')?.rows ?? []
    expect(changed.map((r) => r.fingerprint)).toEqual(['fp-pay', 'fp-hostile'])
    expect(changed[0].detail).toBe('INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)')
    expect(changed[0]).toMatchObject({ testId: 'tc-pay', side: 'to', category: 'PRODUCT_BUG', material: true })
    expect(changed[1].detail).toBe('confidence 60 -> 70')
    const reworded = model.tests.groups.find((g) => g.id === 'reworded')?.rows ?? []
    expect(reworded.map((r) => r.fingerprint)).toEqual(['fp-reworded'])
    expect(reworded[0]).toMatchObject({ material: false, detail: 'root cause reworded' })
    const removed = model.tests.groups.find((g) => g.id === 'removed')?.rows ?? []
    expect(removed[0]).toMatchObject({ side: 'from', testId: 'old-tc-gone', detail: 'INFRASTRUCTURE (confidence 50)' })
    expect(model.tests.persistingUnchanged).toBe(5)
  })

  it('sorts added tests by category, then confidence (high first), then name', () => {
    const diff = versionsDiff()
    const model = buildDiffModel({
      ...diff,
      tests: {
        ...diff.tests,
        added: [
          { test_fingerprint: 'f3', test_id: 't3', test_name: 'b', suite_name: null, category: 'PRODUCT_BUG', confidence: 50 },
          { test_fingerprint: 'f1', test_id: 't1', test_name: 'z', suite_name: null, category: 'INFRASTRUCTURE', confidence: 10 },
          { test_fingerprint: 'f2', test_id: 't2', test_name: 'a', suite_name: null, category: 'PRODUCT_BUG', confidence: 90 },
          { test_fingerprint: 'f4', test_id: 't4', test_name: 'a', suite_name: null, category: 'PRODUCT_BUG', confidence: 50 },
        ],
      },
    })
    expect(model.tests.groups[0].rows.map((r) => r.fingerprint)).toEqual(['f1', 'f2', 'f4', 'f3'])
  })

  it('actions are sorted; an older stored shape missing blocks does not throw', () => {
    expect(buildDiffModel(versionsDiff()).actions).toEqual({ added: ['Fix the gateway timeout'], removed: ['Restart the database'] })
    const partial = { schema_version: 1, mode: 'versions', identical: false, has_changes: false } as unknown as Parameters<typeof buildDiffModel>[0]
    const model = buildDiffModel(partial)
    expect(model.isEmpty).toBe(true)
  })
})

describe('the words of the AI history UI', () => {
  it('names engines and versions', () => {
    expect(['rules', 'ml', 'llm', 'mixed', null].map(engineWord)).toEqual(['rules', 'ML', 'LLM', 'mixed', 'unknown engine'])
    expect(engineLabel({ analysis_mode: 'llm', llm_model: 'llama3.1' })).toBe('LLM llama3.1')
    expect(engineLabel({ analysis_mode: 'rules', llm_model: 'none' })).toBe('rules')
    expect(versionLabel({ version: 3, analysis_mode: 'llm', llm_model: 'llama3.1', generated_at: null })).toBe('v3 · LLM llama3.1')
    expect(versionLabel({ version: 2, analysis_mode: 'llm', llm_model: 'm', generated_at: new Date(Date.now() - 2 * 3_600_000).toISOString() }))
      .toBe('v2 · LLM m · about 2 hours ago')
  })

  it('names runs by sequence, else build, else id', () => {
    expect(runLabel({ run_seq: 58, build_number: '238', run_id: 'x' })).toBe('#58')
    expect(runLabel({ run_seq: null, build_number: '238', run_id: 'x' })).toBe('#238')
    expect(runLabel({ run_seq: null, build_number: null, run_id: 'abcdefgh-1234' })).toBe('#abcdefgh')
  })

  it('says why there is nothing to compare', () => {
    expect(FIRST_ANALYSIS_TEXT).toBe('First analysis of this run')
    expect(previousRunEmptyText('no_previous_run', { branch: 'main' })).toMatch(/^No previous run on branch main/)
    expect(previousRunEmptyText('no_previous_run', {})).toMatch(/^No previous run on branch no branch/)
    expect(previousRunEmptyText('no_primary_suite')).toMatch(/^This run has no suite attribution/)
    expect(previousRunEmptyText('run_lacks_suite')).toMatch(/^This run has no suite attribution/)
    expect(previousRunEmptyText('baseline_not_analysed', { baseline: { run_seq: 59, build_number: null, run_id: 'x' } })).toBe('#59 has no AI analysis to compare.')
    expect(previousRunEmptyText('current_not_analysed')).toBe('This run has no AI analysis to compare yet.')
    expect(previousRunEmptyText('something_new', { message: 'Server words.' })).toBe('Server words.')
  })
})
