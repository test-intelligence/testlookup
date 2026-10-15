/**
 * W03 AIH-03/04: Run Compare's "AI analysis" section. Both runs side by side (verdict, engine / model, deterministic
 * chip, categories, top root causes), then the cross-run diff and the narrative label; nothing is fetched for two
 * runs of different projects; a run without analysis or a failed read is a note; AI text renders as text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AIAnalysisCompareSection from './AIAnalysisCompareSection'
import { aiCompare, HOSTILE, PREV_RUN_ID, RUN_ID } from './aiHistory.fixtures'

const state = vi.hoisted(() => ({
  data: undefined as unknown,
  error: undefined as unknown,
  mutate: vi.fn(),
  calls: [] as unknown[][],
}))

vi.mock('@/hooks/useAIHistory', () => ({
  useAICompare: (runId: string | null, options: unknown) => {
    state.calls.push([runId, options])
    return { data: runId ? state.data : undefined, error: runId ? state.error : undefined, isLoading: false, mutate: state.mutate }
  },
}))

beforeEach(() => {
  state.data = aiCompare()
  state.error = undefined
  state.mutate.mockReset()
  state.calls = []
})

function renderSection(sameProject = true) {
  return render(
    <MemoryRouter>
      <AIAnalysisCompareSection
        leftRunId={PREV_RUN_ID}
        rightRunId={RUN_ID}
        suiteName="Auth"
        sameProject={sameProject}
        leftLabel="Build #238"
        rightLabel="Build #240"
      />
    </MemoryRouter>,
  )
}

describe('AIAnalysisCompareSection', () => {
  it('asks the target run for a compare against the left run as the baseline', () => {
    renderSection()
    expect(state.calls[0]).toEqual([RUN_ID, { baselineRunId: PREV_RUN_ID, suiteName: 'Auth' }])
  })

  it('shows both runs side by side, then what changed, then the narrative label', () => {
    const { container } = renderSection()
    const section = container.querySelector('[data-ai-compare-section]') as HTMLElement
    const baseline = section.querySelector('[data-ai-compare-side="baseline"]') as HTMLElement
    const current = section.querySelector('[data-ai-compare-side="current"]') as HTMLElement
    expect(baseline).toHaveTextContent('Baseline')
    expect(baseline).toHaveTextContent('Build #238')
    expect(baseline.querySelector('[data-ai-compare-verdict]')).toHaveTextContent('GO')
    expect(baseline).toHaveTextContent('rules')
    expect(baseline).toHaveTextContent('deterministic')
    expect(baseline).toHaveTextContent('INFRASTRUCTURE 6')
    expect(current).toHaveTextContent('Target')
    expect(current.querySelector('[data-ai-compare-verdict]')).toHaveTextContent('NO_GO')
    expect(current).toHaveTextContent('LLM llama3.1')
    expect(current).toHaveTextContent('risk 72/100')
    expect(within(current).getByText('Timeout in checkout')).toBeInTheDocument()
    const diff = section.querySelector('[data-ai-diff]') as HTMLElement
    expect(diff).toHaveAttribute('data-ai-diff-mode', 'cross_run')
    expect(diff).toHaveTextContent('Verdict GO -> NO_GO')
    // A resolved test belongs to the baseline run: it opens there.
    expect(diff.querySelector('[data-test-fingerprint="fp-gone"]')).toHaveAttribute('href', `/runs/${PREV_RUN_ID}/tests/old-tc-gone`)
    expect(diff.querySelector('[data-test-fingerprint="fp-pay"]')).toHaveAttribute('href', `/runs/${RUN_ID}/tests/tc-pay`)
    expect(section.querySelector('[data-ai-compare-narrative-label]')).toHaveTextContent('deterministic summary')
  })

  it('two runs of different projects: a note, and nothing fetched', () => {
    const { container } = renderSection(false)
    expect(container.querySelector('[data-ai-compare-note="different_projects"]'))
      .toHaveTextContent('AI analysis comparison needs two runs of the same project')
    expect(state.calls.every(([runId]) => runId === null)).toBe(true)
  })

  it('a run that was never analysed: the reason, no diff', () => {
    const compare = aiCompare()
    state.data = { ...compare, reason: 'baseline_not_analysed', diff: null, narrative: null, sides: { baseline: null, current: compare.sides?.current ?? null } }
    const { container } = renderSection()
    expect(container.querySelector('[data-ai-compare-note="baseline_not_analysed"]')).toHaveTextContent('#58 has no AI analysis to compare.')
    expect(container.querySelector('[data-ai-diff]')).toBeNull()
    expect(container.querySelector('[data-ai-compare-side="baseline"]')).toHaveTextContent('No AI analysis for this run.')
  })

  it('a failed read is a note with Retry, never a toast', () => {
    state.data = undefined
    state.error = new Error('503')
    const { container } = renderSection()
    expect(container.querySelector('[data-ai-compare-note="unavailable"]')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(state.mutate).toHaveBeenCalled()
  })

  it('XSS: a hostile root-cause label renders as text', () => {
    const { container } = renderSection()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect(screen.getAllByText(HOSTILE).length).toBeGreaterThanOrEqual(2)
  })
})
