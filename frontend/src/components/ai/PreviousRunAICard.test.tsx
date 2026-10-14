/**
 * W03 AIH-03/04: "vs previous run". Its states (loading, expanded, collapsed when nothing changed, empty with the
 * reason in words, unavailable with Retry), what the expanded card says (the baseline chip, the verdict change, new
 * / resolved / persisting root causes, the changed tests linking to the test, the narrative and its label), the
 * baseline picker writing the choice, and AI text rendered as text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PreviousRunAICard from './PreviousRunAICard'
import { baselineChipText } from './aiDigestDiff.model'
import { aiCompare, brief, HOSTILE, identicalDiff, PREV_RUN_ID, previousRun, RUN_ID } from './aiHistory.fixtures'

const state = vi.hoisted(() => ({
  previous: undefined as unknown,
  compare: undefined as unknown,
  compareError: undefined as unknown,
  compareMutate: vi.fn(),
  compareCalls: [] as unknown[][],
  runsCalls: [] as unknown[][],
}))

vi.mock('@/hooks/useAIHistory', () => ({
  usePreviousRun: () => ({ data: state.previous, error: undefined, isLoading: false, mutate: vi.fn() }),
  useAICompare: (runId: string, options: unknown) => {
    state.compareCalls.push([runId, options])
    return { data: state.compare, error: state.compareError, isLoading: !state.compare && !state.compareError, mutate: state.compareMutate }
  },
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: (params: unknown, opts: unknown) => {
    state.runsCalls.push([params, opts])
    return {
      data: {
        items: [
          { id: RUN_ID, run_seq: 60, build_number: 240, branch: 'main', created_at: '2026-09-18T10:00:00Z' },
          { id: PREV_RUN_ID, run_seq: 58, build_number: 238, branch: 'main', created_at: '2026-09-18T07:00:00Z' },
          { id: '44444444-4444-4444-8444-100000000005', run_seq: 55, build_number: 235, branch: 'release/1.2', created_at: '2026-09-15T07:00:00Z' },
        ],
      },
      isLoading: false,
    }
  },
}))

beforeEach(() => {
  state.previous = previousRun()
  state.compare = aiCompare()
  state.compareError = undefined
  state.compareMutate.mockReset()
  state.compareCalls = []
  state.runsCalls = []
})

function renderCard(baselineRunId: string | null = null) {
  const onChangeBaseline = vi.fn()
  const view = render(
    <MemoryRouter>
      <PreviousRunAICard runId={RUN_ID} baselineRunId={baselineRunId} onChangeBaseline={onChangeBaseline} />
    </MemoryRouter>,
  )
  const card = view.container.querySelector('[data-previous-run-card]') as HTMLElement
  return { ...view, card, onChangeBaseline }
}

describe('PreviousRunAICard — expanded', () => {
  it('names the baseline and says what changed: verdict, new / resolved / persisting causes, tests, narrative', () => {
    const { card } = renderCard()
    expect(card).toHaveAttribute('data-previous-run-state', 'expanded')
    expect(card.querySelector('[data-previous-baseline]')).toHaveTextContent('Compared with #58 (Auth, main, 3 hours earlier)')
    // Provenance first: an engine change explains the category churn.
    expect(card.querySelector('[data-previous-provenance]')).toHaveTextContent(/^Baseline analysed by rules; current by llm\./)
    expect(card.querySelector('[data-previous-verdict]')).toHaveTextContent('Verdict GO -> NO_GO')
    expect(card.querySelector('[data-previous-new-causes]')).toHaveTextContent('Timeout in checkout(4 tests)')
    expect(card.querySelector('[data-previous-resolved]')).toHaveTextContent(HOSTILE)
    expect(card.querySelector('[data-previous-persisting]')).toHaveTextContent('Cache misses (renamed)')
    const tests = card.querySelector('[data-previous-tests-changed]') as HTMLElement
    const pay = tests.querySelector('[data-test-fingerprint="fp-pay"]') as HTMLElement
    expect(pay).toHaveAttribute('href', `/runs/${RUN_ID}/tests/tc-pay`)
    expect(pay).toHaveTextContent('test_pay')
    expect(pay).toHaveTextContent('INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)')
    // Root-cause-only rewording is not a "changed conclusion".
    expect(tests.querySelector('[data-test-fingerprint="fp-reworded"]')).toBeNull()
    expect(card.querySelector('[data-previous-narrative-label]')).toHaveTextContent('deterministic summary')
    expect(within(card).getByText('Verdict GO -> NO_GO (risk 35 -> 72)')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'Open Run Compare' }))
      .toHaveAttribute('href', `/runs/compare?mode=manual&left=${PREV_RUN_ID}&right=${RUN_ID}&suite=Auth`)
  })

  it('labels an LLM-written narrative "AI narrative"', () => {
    const compare = aiCompare()
    state.compare = { ...compare, narrative: { ...compare.narrative, label: 'AI narrative', source: 'llm', status: 'ready' } }
    const { card } = renderCard()
    expect(card.querySelector('[data-previous-narrative-label]')).toHaveTextContent('AI narrative')
  })

  it('flags an explicit baseline on another branch', () => {
    const compare = aiCompare()
    state.compare = { ...compare, baseline: { ...compare.baseline, branch: 'release/1.2', selection: 'explicit', branch_mismatch: true } }
    const { card } = renderCard('44444444-4444-4444-8444-100000000005')
    expect(card.querySelector('[data-previous-branch-mismatch]')).toHaveTextContent('different branch (release/1.2 vs main)')
    expect(state.compareCalls[0]).toEqual([RUN_ID, { baselineRunId: '44444444-4444-4444-8444-100000000005' }])
  })
})

describe('PreviousRunAICard — the other states', () => {
  it('nothing changed: one collapsed row', () => {
    state.compare = { ...aiCompare(), diff: identicalDiff() }
    const { card } = renderCard()
    expect(card).toHaveAttribute('data-previous-run-state', 'collapsed')
    expect(card.querySelector('[data-previous-no-changes]')).toHaveTextContent('No changes vs #58')
    expect(card.querySelector('[data-previous-verdict]')).toBeNull()
    expect(card.querySelector('[data-previous-tests-changed]')).toBeNull()
  })

  it('no previous run on this branch: says so, by branch', () => {
    state.compare = { ...aiCompare(), baseline: null, diff: null, narrative: null, sides: null, reason: 'no_previous_run', message: 'No earlier completed run of "Auth" on branch "main" found.' }
    const { card } = renderCard()
    expect(card).toHaveAttribute('data-previous-run-state', 'empty')
    expect(card.querySelector('[data-previous-empty]')).toHaveTextContent(/^No previous run on branch main/)
    // The reader can still pick a baseline of another branch.
    expect(card.querySelector('[data-previous-change-baseline]')).not.toBeNull()
  })

  it('no suite attribution: says so, and offers no baseline picker', () => {
    state.previous = previousRun({ baseline: null, reason: 'no_primary_suite', current: brief({ primary_suite_name: null }) })
    state.compare = { ...aiCompare(), baseline: null, diff: null, narrative: null, sides: null, reason: 'no_primary_suite' }
    const { card } = renderCard()
    expect(card.querySelector('[data-previous-empty]')).toHaveTextContent(/^This run has no suite attribution/)
    expect(card.querySelector('[data-previous-change-baseline]')).toBeNull()
  })

  it('the baseline was never analysed: "#58 has no AI analysis to compare"', () => {
    state.compare = { ...aiCompare(), diff: null, narrative: null, reason: 'baseline_not_analysed' }
    const { card } = renderCard()
    expect(card.querySelector('[data-previous-empty]')).toHaveTextContent('#58 has no AI analysis to compare.')
    expect(card.querySelector('[data-previous-baseline]')).toHaveTextContent('Compared with #58')
  })

  it('a failed read is "unavailable" with Retry, and never blocks', () => {
    state.compare = undefined
    state.compareError = new Error('503')
    const { card } = renderCard()
    expect(card).toHaveAttribute('data-previous-run-state', 'unavailable')
    fireEvent.click(within(card).getByRole('button', { name: /Retry/ }))
    expect(state.compareMutate).toHaveBeenCalled()
  })

  it('loading', () => {
    state.compare = undefined
    const { card } = renderCard()
    expect(card).toHaveAttribute('data-previous-run-state', 'loading')
  })
})

describe('PreviousRunAICard — change baseline', () => {
  it('lists the suite\'s runs (not this one) with the default first, and writes the choice', () => {
    const { card, onChangeBaseline } = renderCard()
    expect(state.runsCalls).toEqual([])
    fireEvent.click(card.querySelector('[data-previous-change-baseline]') as HTMLElement)
    expect(state.runsCalls[0]).toEqual([{ page: 1, size: 50, days: 0, suite_name: 'Auth' }, { ignoreGlobalRelease: true, quiet: true }])
    const select = screen.getByRole('combobox', { name: 'Baseline run' })
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent ?? '')
    expect(labels).toHaveLength(3)
    expect(labels[0]).toBe('Default (previous on this branch)')
    // `formatRunWhen` is the browser's local time: only the shape is pinned.
    expect(labels[1]).toMatch(/^#58 · main · Sep 1[78], \d\d:\d\d$/)
    expect(labels[2]).toMatch(/^#55 · release\/1\.2 · Sep 1[45], \d\d:\d\d$/)
    fireEvent.change(select, { target: { value: '44444444-4444-4444-8444-100000000005' } })
    expect(onChangeBaseline).toHaveBeenLastCalledWith('44444444-4444-4444-8444-100000000005')
  })

  it('"Default" writes null', () => {
    const { card, onChangeBaseline } = renderCard(PREV_RUN_ID)
    fireEvent.click(card.querySelector('[data-previous-change-baseline]') as HTMLElement)
    fireEvent.change(screen.getByRole('combobox', { name: 'Baseline run' }), { target: { value: '' } })
    expect(onChangeBaseline).toHaveBeenLastCalledWith(null)
  })
})

describe('PreviousRunAICard — XSS', () => {
  it('a hostile cluster label, test name and narrative line render as text', () => {
    const { container } = renderCard()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;')
  })
})

describe('baselineChipText', () => {
  it('falls back to the build and a relative time', () => {
    const base = brief({ run_seq: null, build_number: '238', finished_at: null })
    expect(baselineChipText(base, null)).toBe('Compared with #238 (Auth, main)')
  })
})
