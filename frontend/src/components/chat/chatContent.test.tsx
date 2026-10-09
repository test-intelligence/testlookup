import { describe, expect, it } from 'vitest'
import { askAboutRun, buildLabel, metaLine, sourceLinks, starterPrompts } from './chatContent'

describe('chat text helpers', () => {
  it('labels builds the way they are written', () => {
    expect(buildLabel('105')).toBe('build 105')
    expect(buildLabel(105, true)).toBe('Build 105')
    // Never "build build-2029" (the doubled label a model misread).
    expect(buildLabel('build-2029')).toBe('build-2029')
    expect(buildLabel(null)).toBe('the latest run')
  })

  it('writes the run page question and the starters', () => {
    expect(askAboutRun('105')).toBe('What failed in build 105, and why? Are any of these failures new since the previous build?')
    expect(askAboutRun(undefined)).toMatch(/^What failed in this run/)
    const prompts = starterPrompts('Checkout Service', 'build-2029').map((p) => p.prompt)
    expect(prompts).toContain('What failed in build-2029, and why?')
    expect(prompts).toContain('Is Checkout Service ready to release? What blocks it?')
  })

  it('links only what can be opened, once', () => {
    expect(sourceLinks([
      { type: 'test_run', id: 'r1', build: '105' },
      { type: 'test_run', id: 'r1', build: '105' },
      { type: 'test_case', id: 'c1', run_id: 'r1', name: 'test_refund_flow' },
      { type: 'ai_analysis', id: 'x' },
      { type: 'test_case', id: 'c2', name: 'no run id' },
    ])).toEqual([
      { key: '/runs/r1', to: '/runs/r1', label: 'Build 105', kind: 'run' },
      { key: '/runs/r1/tests/c1', to: '/runs/r1/tests/c1', label: 'test_refund_flow', kind: 'test' },
    ])
  })

  it('says how an answer was produced', () => {
    expect(metaLine({ status: 'complete', model: 'mistralai/mistral-nemo', first_token_ms: 850, total_ms: 6200, tool_calls: 2 }))
      .toBe('mistral-nemo · first words in 850 ms · answered in 6.2 s · 2 lookups')
    expect(metaLine({ status: 'stopped', total_ms: 3000 })).toBe('stopped after 3.0 s')
    expect(metaLine(null)).toBeNull()
  })
})
