import { describe, expect, it } from 'vitest'
import { askAboutRun, askAboutRunHref, buildLabel, metaLine, sourceLinks, starterPrompts } from './chatContent'

describe('chat text helpers', () => {
  it("links a run's question to the run's own project (homelab, 2026-10-10)", () => {
    const href = askAboutRunHref({ build_number: 'viz-3043', project_id: '9eb9d19f-4b40' })
    const url = new URL(href, 'http://x')
    expect(url.pathname).toBe('/chat')
    expect(url.searchParams.get('prompt')).toBe(askAboutRun('viz-3043'))
    expect(url.searchParams.get('project')).toBe('9eb9d19f-4b40')
    expect(new URL(askAboutRunHref({ build_number: '105' }), 'http://x').searchParams.has('project')).toBe(false)
  })

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

  it('counts the lookups made before the model ran (homelab, 2026-10-10)', () => {
    // A flaky-tests answer: the list was looked up up front, the model called
    // nothing. The footer said nothing while "How I looked this up" said 1.
    expect(metaLine({ status: 'complete', model: 'anthropic/claude-haiku-5.5', first_token_ms: 5600, total_ms: 7300, tool_calls: 0, lookups: 1 }))
      .toBe('claude-haiku-5.5 · first words in 5.6 s · answered in 7.3 s · 1 lookup')
    expect(metaLine({ status: 'complete', total_ms: 4000, tool_calls: 1, lookups: 3 })).toBe('answered in 4.0 s · 3 lookups')
  })
})
