/**
 * W03 AIH-05: the History tab's "AI analysis across runs" strip (contract §9.8):
 * a chip per analysed run oldest -> newest, coloured by category, the markers
 * where the category changed, a chip opening that run's versions, and no
 * request until a popover opens.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TestHistoryAI, TestHistoryAIChange, TestHistoryAIPoint } from '@/types/aiTestHistory'

const versionsState: { data: unknown; error: unknown; isLoading: boolean; mutate: () => void } = {
  data: undefined,
  error: undefined,
  isLoading: false,
  mutate: vi.fn(),
}
const hookCalls: unknown[][] = []
vi.mock('@/hooks/useTestAIVersions', () => ({
  useTestAIVersions: (...args: unknown[]) => {
    hookCalls.push(args)
    return versionsState
  },
}))

import TestAIHistoryStrip, { MAX_VISIBLE_CHANGES } from './TestAIHistoryStrip'

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
  version_count: 3,
  ...over,
})

const point = (n: number, a: TestHistoryAI | null): TestHistoryAIPoint => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  build_number: String(n),
  run_seq: n,
  status: a ? 'FAILED' : 'PASSED',
  duration_ms: 100,
  created_at: `2026-10-${String(n).padStart(2, '0')}T10:00:00Z`,
  ai: a,
})

const change = (n: number, over: Partial<TestHistoryAIChange> = {}): TestHistoryAIChange => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  from_category: 'INFRASTRUCTURE',
  to_category: 'PRODUCT_BUG',
  from_confidence: null,
  to_confidence: null,
  kinds: ['category'],
  ...over,
})

// Newest first, as the API sends it.
const HISTORY = [
  point(4, ai({ test_id: 't4' })),
  point(3, null),
  point(2, ai({ test_id: 't2', category: 'INFRASTRUCTURE', confidence: 40, analysis_mode: 'rules', model: null })),
  point(1, ai({ test_id: 't1', category: 'INFRASTRUCTURE', confidence: 45, analysis_mode: 'rules', model: null, version_count: 0 })),
]

function renderStrip(props: Partial<Parameters<typeof TestAIHistoryStrip>[0]> = {}) {
  return render(
    <MemoryRouter>
      <TestAIHistoryStrip history={HISTORY} changes={[change(4)]} runId="run-4" {...props} />
    </MemoryRouter>,
  )
}

const chips = (container: HTMLElement) => Array.from(container.querySelectorAll<HTMLElement>('[data-test-ai-chip]'))

describe('TestAIHistoryStrip', () => {
  beforeEach(() => {
    hookCalls.length = 0
    versionsState.data = undefined
    versionsState.error = undefined
    versionsState.isLoading = false
  })

  it('draws a chip per analysed run, oldest -> newest, coloured by category, with the run named in its title', () => {
    const { container } = renderStrip()
    const all = chips(container)
    expect(all.map((c) => c.dataset.runId)).toEqual(['run-1', 'run-2', 'run-4'])
    expect(all.map((c) => c.dataset.category)).toEqual(['INFRASTRUCTURE', 'INFRASTRUCTURE', 'PRODUCT_BUG'])
    expect(all.map((c) => c.textContent)).toEqual(['INFRASTRUCTURE 45%', 'INFRASTRUCTURE 40%', 'PRODUCT_BUG 85%'])
    expect(all[2].title).toBe('Run #4 - PRODUCT_BUG 85% - LLM llama3.1')
    expect(all[2].className).toContain('text-[var(--status-failed)]') // categoryColor(PRODUCT_BUG)
    expect(all[0].className).toContain('text-[var(--status-broken)]') // categoryColor(INFRASTRUCTURE)
    // The page's own run is outlined; the run where the category changed is ringed.
    expect(all[2]).toHaveAttribute('data-current-run')
    expect(all[2]).toHaveAttribute('data-changed', 'category')
    expect(all[0]).not.toHaveAttribute('data-changed')
    // The strip asks nothing by itself.
    expect(hookCalls).toEqual([])
  })

  it('says where the category changed, and that a root-cause-only change is a rewording', () => {
    const { container } = renderStrip({
      changes: [change(2, { kinds: ['root_cause'] }), change(4, { from_confidence: 40, to_confidence: 85 })],
    })
    const markers = Array.from(container.querySelectorAll('[data-test-ai-change]')).map((m) => m.textContent)
    expect(markers).toEqual([
      'Category changed on Run #4: INFRASTRUCTURE → PRODUCT_BUG (confidence 40% → 85%)',
      'Root cause reworded on Run #2',
    ])
  })

  it('renders nothing when no run has an analysis (and an older server without ai_changes still works)', () => {
    const { container } = renderStrip({ history: [point(2, null), point(1, null)], changes: undefined })
    expect(container.querySelector('[data-test-ai-strip]')).toBeNull()
    const second = renderStrip({ changes: undefined })
    expect(second.container.querySelectorAll('[data-test-ai-chip]')).toHaveLength(3)
    expect(second.container.querySelector('[data-test-ai-change]')).toBeNull()
  })

  it('a chip opens that run\'s versions (one read, keyed by that point\'s run and test), a second click closes it', () => {
    versionsState.data = {
      run_id: 'run-4',
      test_id: 't4',
      test_fingerprint: 'fp',
      test_name: 'test_pay',
      current: null,
      versions: [
        { version_id: 'c', version: 3, trigger: 'human_correction', pipeline_run_id: null, actor: { user_id: 'u', name: 'anna' }, failure_category: 'PRODUCT_BUG', confidence_score: 90, analysis_mode: null, llm_provider: null, llm_model: null, is_flaky: false, requires_human_review: false, root_cause_excerpt: 'tax line skipped', content_sha256: 'c'.repeat(64), created_at: '2026-10-14T10:00:00Z' },
        { version_id: 'b', version: 2, trigger: 'pipeline', pipeline_run_id: 'p2', actor: null, failure_category: 'PRODUCT_BUG', confidence_score: 85, analysis_mode: 'llm', llm_provider: 'ollama', llm_model: 'llama3.1', is_flaky: false, requires_human_review: false, root_cause_excerpt: 'tax line skipped', content_sha256: 'b'.repeat(64), created_at: '2026-10-13T10:00:00Z' },
        { version_id: 'a', version: 1, trigger: 'pipeline', pipeline_run_id: 'p1', actor: null, failure_category: 'INFRASTRUCTURE', confidence_score: 40, analysis_mode: 'rules', llm_provider: null, llm_model: null, is_flaky: false, requires_human_review: false, root_cause_excerpt: 'gateway timeout', content_sha256: 'a'.repeat(64), created_at: '2026-10-12T10:00:00Z' },
      ],
      total: 3,
    }
    const { container } = renderStrip()
    expect(container.querySelector('[data-test-ai-popover]')).toBeNull()

    fireEvent.click(chips(container)[2])
    const popover = container.querySelector('[data-test-ai-popover]') as HTMLElement
    expect(popover).not.toBeNull()
    expect(hookCalls[hookCalls.length - 1]).toEqual(['run-4', 't4'])
    expect(within(popover).getByText('v1 rules · v2 LLM · v3 human correction (anna)')).toBeInTheDocument()
    expect(Array.from(popover.querySelectorAll('[data-test-ai-version]')).map((li) => li.getAttribute('data-version'))).toEqual(['1', '2', '3'])
    expect(within(popover).getByRole('link', { name: /Open this run's result/ })).toHaveAttribute('href', '/runs/run-4/tests/t4')
    expect(chips(container)[2]).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(chips(container)[2])
    expect(container.querySelector('[data-test-ai-popover]')).toBeNull()
  })

  it('a run with no stored version says so without asking the server', () => {
    const { container } = renderStrip()
    fireEvent.click(chips(container)[0]) // run-1: version_count 0
    const popover = container.querySelector('[data-test-ai-popover]') as HTMLElement
    expect(within(popover).getByText('No versions stored yet')).toBeInTheDocument()
    // The hook is called with a null run id: SWR's null key, no request.
    expect(hookCalls[hookCalls.length - 1]).toEqual([null, 't1'])
  })

  it('shows the first markers and the rest on demand', () => {
    const many = Array.from({ length: MAX_VISIBLE_CHANGES + 2 }, (_, i) => change(i + 1, { kinds: ['root_cause'] }))
    const history = Array.from({ length: MAX_VISIBLE_CHANGES + 2 }, (_, i) => point(MAX_VISIBLE_CHANGES + 2 - i, ai()))
    const { container } = renderStrip({ history, changes: many })
    expect(container.querySelectorAll('[data-test-ai-change]')).toHaveLength(MAX_VISIBLE_CHANGES)
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 more changes' }))
    expect(container.querySelectorAll('[data-test-ai-change]')).toHaveLength(MAX_VISIBLE_CHANGES + 2)
  })

  it('renders hostile run labels and categories as text', () => {
    const hostile = '<img src=x onerror=alert(1)>'
    const { container } = renderStrip({
      history: [{ ...point(1, ai({ category: hostile })), run_label: hostile }],
      changes: [change(1, { run_label: hostile, to_category: hostile })],
    })
    expect(container.querySelector('img')).toBeNull()
    expect(chips(container)[0].textContent).toBe(`${hostile} 85%`)
    expect(container.querySelector('[data-test-ai-change]')?.textContent).toContain(hostile)
  })
})
