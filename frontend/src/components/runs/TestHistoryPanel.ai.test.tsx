/**
 * W03 AIH-05: the History tab mounts the "AI analysis across runs" strip from
 * the SAME history read (`ai` per point, `ai_changes`), between the pass/fail
 * dots and the duration section, and only when a point has an analysis. An
 * older server (no `ai`, no `ai_changes`) gets the panel exactly as before.
 */
import { fireEvent, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const historyState: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }
vi.mock('@/hooks/useRuns', () => ({
  useTestCaseHistory: () => historyState,
}))

const versionCalls: unknown[][] = []
vi.mock('@/hooks/useTestAIVersions', () => ({
  useTestAIVersions: (...args: unknown[]) => {
    versionCalls.push(args)
    return { data: undefined, error: undefined, isLoading: true, mutate: vi.fn() }
  },
}))

import TestHistoryPanel from './TestHistoryPanel'

const ai = (testId: string, category: string, confidence: number | null) => ({
  test_id: testId,
  category,
  confidence,
  root_cause_excerpt: 'gateway timeout',
  root_cause_sha: 'aaaaaaaaaaaaaaaa',
  analysis_mode: 'rules',
  model: null,
  is_flaky: false,
  requires_human_review: false,
  version_count: 2,
})

const point = (n: number, status: string, extra: Record<string, unknown> = {}) => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  build_number: String(n),
  run_seq: n,
  status,
  duration_ms: 900,
  created_at: `2026-10-${String(n).padStart(2, '0')}T10:00:00Z`,
  ...extra,
})

const BASE = {
  run_id: 'run-4',
  test_id: 't4',
  test_fingerprint: 'fp',
  test_name: 'test_pay',
  flakiness: { is_flaky: false, failure_rate: 0.5, failure_rate_pct: 50, impact_score: 1, classification: 'HEALTHY', window_days: 30, total_runs: 4, passed: 2, failed: 2 },
  metadata: { suite: 'checkout', first_seen_at: null, last_seen_at: null },
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <TestHistoryPanel runId="run-4" testId="t4" />
    </MemoryRouter>,
  )
}

describe('TestHistoryPanel — AI analysis across runs', () => {
  beforeEach(() => {
    versionCalls.length = 0
    historyState.isLoading = false
    historyState.data = {
      ...BASE,
      // Newest first, as the API sends it.
      history: [
        point(4, 'FAILED', { ai: { ...ai('t4', 'PRODUCT_BUG', 85), analysis_mode: 'llm', model: 'llama3.1' } }),
        point(3, 'PASSED', { ai: null }),
        point(2, 'FAILED', { ai: ai('t2', 'INFRASTRUCTURE', 40) }),
        point(1, 'PASSED', { ai: null }),
      ],
      ai_changes: [
        { run_id: 'run-4', run_label: 'Run #4', from_category: 'INFRASTRUCTURE', to_category: 'PRODUCT_BUG', from_confidence: 40, to_confidence: 85, kinds: ['category', 'root_cause'] },
      ],
    }
  })

  it('mounts the strip between the pass/fail dots and the duration section', () => {
    const { container } = renderPanel()
    const strip = container.querySelector('[data-test-ai-strip]') as HTMLElement
    expect(strip).not.toBeNull()
    expect(Array.from(strip.querySelectorAll('[data-test-ai-chip]')).map((c) => c.getAttribute('data-run-id'))).toEqual(['run-2', 'run-4'])
    expect(strip.querySelector('[data-test-ai-change]')?.textContent).toBe(
      'Category changed on Run #4: INFRASTRUCTURE → PRODUCT_BUG (confidence 40% → 85%)',
    )
    const dots = Array.from(container.querySelectorAll('p')).find((p) => p.textContent === 'Recent runs (oldest → newest)') as HTMLElement
    const duration = container.querySelector('[data-duration-history]') as HTMLElement
    expect(dots.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(strip.compareDocumentPosition(duration) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('reads no versions until a chip is clicked, then exactly that point\'s', () => {
    const { container } = renderPanel()
    expect(versionCalls).toEqual([])
    fireEvent.click(container.querySelectorAll('[data-test-ai-chip]')[0])
    expect(versionCalls[versionCalls.length - 1]).toEqual(['run-2', 't2'])
    expect(container.querySelector('[data-test-ai-popover]')).not.toBeNull()
  })

  it('an older server (no ai, no ai_changes): no strip, the rest of the panel unchanged', () => {
    historyState.data = { ...BASE, history: [point(2, 'FAILED'), point(1, 'PASSED')] }
    const { container } = renderPanel()
    expect(container.querySelector('[data-test-ai-strip]')).toBeNull()
    expect(container.querySelectorAll('[aria-label*="Run #"]').length).toBe(2)
    expect(container.querySelector('[data-duration-history]')).not.toBeNull()
  })

  it('no point with an analysis (every run passed): no strip', () => {
    historyState.data = { ...BASE, history: [point(2, 'PASSED', { ai: null }), point(1, 'PASSED', { ai: null })], ai_changes: [] }
    const { container } = renderPanel()
    expect(container.querySelector('[data-test-ai-strip]')).toBeNull()
  })
})
