/**
 * W23b FAIL-12 (contract §4.4 G-D): the bisect narrowing block and its model.
 * The hook is mocked (the unit-test network guard): each case hands the block
 * one parsed answer built from the shared mocks.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BisectCompare, BisectNarrowing as Narrowing, BisectRunRef } from '@/types/bisect'
import { BISECT_SENTENCES } from '@/types/bisect'
import { narrowing, runRef } from '@/test/bisectDriftMocks'
import { COMMIT_RANGE_REASON_TEXT, UNKNOWN_REASON_LINE } from '@/pages/run/commitRangeReasons'
import {
  BLOCK_HEADING, LOAD_ERROR_LINE, LOADING_LINE, compareLine, mergeCaveat, metaLine, narrowedHeading,
  shortSha, stateLine, untestedLine,
} from './bisectModel'

const hook = vi.hoisted(() => ({
  value: { data: null, error: undefined, isLoading: false, retry: vi.fn() } as {
    data: unknown
    error: unknown
    isLoading: boolean
    retry: () => unknown
  },
}))
vi.mock('@/hooks/useBisectNarrowing', () => ({ useBisectNarrowing: vi.fn(() => hook.value) }))

import BisectNarrowing from './BisectNarrowing'

const GOOD: BisectRunRef = runRef({
  run_id: 'run-good', build_number: '101', commit_hash: 'goodsentinel01', ci_run_url: 'https://ci.example.com/job/101',
  outcome: 'pass',
})
const BAD: BisectRunRef = runRef({
  run_id: 'run-bad', build_number: '104', commit_hash: 'badsentinel002', ci_run_url: 'https://ci.example.com/job/104',
  outcome: 'fail',
})

function compare(over: Partial<BisectCompare> = {}): BisectCompare {
  return {
    state: 'unavailable', reason: 'offline_mode', host: null, cached: false, commit_count: 0, truncated: false,
    commits: [], has_location_signal: false, caveat: null, run_level_commit_count: null,
    ...over,
  } as BisectCompare
}

function answer(over: Partial<Narrowing> = {}): Narrowing {
  return narrowing({
    run_id: 'run-bad', state: 'narrowed', series_branch: 'feature/pay', anchor: 'same_branch', fallback_branch: null,
    good: GOOD, bad: BAD, streak_length: 2, untested_runs: [], untested_count: 0, runs_read: 6, bounded: false,
    narrowed_from: 'green_baseline', compare: compare(),
    ...over,
  })
}

function renderBlock(data: Narrowing | null, extra: Partial<typeof hook.value> = {}, runBuild: string | null = '104') {
  hook.value = { data, error: undefined, isLoading: false, retry: vi.fn(), ...extra }
  render(
    <MemoryRouter>
      <BisectNarrowing runId="run-bad" fingerprint="fp-sentinel-checkout" runBuild={runBuild} />
    </MemoryRouter>,
  )
  return screen.getByRole('region', { name: 'Narrowed range' })
}

const stateEl = () => document.querySelector('[data-bisect-state]') as HTMLElement

describe('BisectNarrowing — the state sentence', () => {
  beforeEach(() => vi.clearAllMocks())

  it('narrowed on the same branch: both builds link to their runs, each SHA is 7 characters', () => {
    const block = renderBlock(answer())
    expect(within(block).getByRole('heading', { level: 3, name: BLOCK_HEADING })).toBeInTheDocument()
    expect(stateEl()).toHaveTextContent('Last passed in build #101 (goodsen), first failed in build #104 (badsent).')
    expect(within(stateEl()).getByRole('link', { name: '#101' })).toHaveAttribute('href', '/runs/run-good')
    expect(within(stateEl()).getByRole('link', { name: '#104' })).toHaveAttribute('href', '/runs/run-bad')
    expect(within(block).getByText('2 failing runs on feature/pay', { exact: false })).toBeInTheDocument()
  })

  it('main fallback: labelled, never presented as the same branch', () => {
    renderBlock(answer({ anchor: 'main_fallback', fallback_branch: 'main' }))
    expect(stateEl()).toHaveTextContent(
      'Never passed on feature/pay in the runs read; last passed on main in build #101 (goodsen). '
      + 'First failed on feature/pay in build #104 (badsent).',
    )
    expect(document.querySelector('[data-bisect-meta]')).toHaveTextContent('Anchored on main (no pass on feature/pay)')
    expect(stateEl()).not.toHaveTextContent(/^Last passed in build/)
  })

  it('never passed: says so with the runs read', () => {
    renderBlock(answer({ state: 'never_passed', good: null, runs_read: 12, compare: compare({ state: 'not_attempted', reason: 'not_narrowed' }) }))
    expect(stateEl()).toHaveTextContent('This test never passed on feature/pay: every run of it there failed (12 runs read).')
    expect(document.querySelector('[data-bisect-compare-reason]')).toBeNull()
  })

  it('no pass within the 200-run bound: older history was not searched — never "never"', () => {
    renderBlock(answer({
      state: 'no_pass_within_bound', good: null, runs_read: 200, bounded: true,
      compare: compare({ state: 'not_attempted', reason: 'not_narrowed' }),
    }))
    expect(stateEl()).toHaveTextContent('No pass in the last 200 runs of feature/pay; older history was not searched.')
    expect(stateEl().textContent).not.toMatch(/never/i)
  })

  it('not failing: nothing to narrow, the run named by its build', () => {
    renderBlock(answer({
      state: 'not_failing', good: null, bad: null, streak_length: 0,
      compare: compare({ state: 'not_attempted', reason: 'not_narrowed' }),
    }))
    expect(stateEl()).toHaveTextContent('This test did not fail in build #104: there is nothing to narrow.')
    expect(within(stateEl()).getByRole('link', { name: '#104' })).toHaveAttribute('href', '/runs/run-bad')
  })

  it('a build without a commit reads "no commit recorded", never an empty pair of brackets', () => {
    renderBlock(answer({
      good: { ...GOOD, commit_hash: null },
      compare: compare({ state: 'not_attempted', reason: 'no_good_commit' }),
    }))
    expect(stateEl()).toHaveTextContent('Last passed in build #101 (no commit recorded), first failed in build #104 (badsent).')
    expect(document.querySelector('[data-bisect-compare-reason]')).toHaveTextContent(BISECT_SENTENCES.no_good_commit)
  })

  it('a run with no branch walks "runs without a branch"', () => {
    renderBlock(answer({ series_branch: null }))
    expect(document.querySelector('[data-bisect-meta]')).toHaveTextContent('2 failing runs on runs without a branch')
  })

  it('a <script> sentinel branch renders as text, never as markup', () => {
    const branch = '<script>window.__bisectSentinel=1</script>'
    renderBlock(answer({ state: 'never_passed', good: null, series_branch: branch, compare: compare({ state: 'not_attempted', reason: 'not_narrowed' }) }))
    expect(stateEl()).toHaveTextContent(`This test never passed on ${branch}`)
    expect(document.querySelector('[data-bisect-narrowing] script')).toBeNull()
    expect((window as unknown as Record<string, unknown>).__bisectSentinel).toBeUndefined()
  })
})

describe('BisectNarrowing — the runs in between and the links', () => {
  beforeEach(() => vi.clearAllMocks())

  it('untested runs: a native <details> listing 20 build links, then "and 5 more"', () => {
    const untested = Array.from({ length: 20 }, (_, i) => runRef({ run_id: `run-u${i}`, build_number: `u${i}`, outcome: 'untested' }))
    renderBlock(answer({ untested_runs: untested, untested_count: 25 }))
    const details = document.querySelector('details[data-bisect-untested]') as HTMLDetailsElement
    expect(details).not.toBeNull()
    expect(details.querySelector('summary')).toHaveTextContent('25 run(s) in between did not run this test.')
    const links = within(details).getAllByRole('link', { hidden: true })
    expect(links).toHaveLength(20)
    expect(links[0]).toHaveAttribute('href', '/runs/run-u0')
    expect(within(details).getByText('and 5 more')).toBeInTheDocument()
  })

  it('no untested run: no <details>', () => {
    renderBlock(answer())
    expect(document.querySelector('[data-bisect-untested]')).toBeNull()
  })

  it('the CI builds link only to an http(s) URL; a javascript: URL renders no link', () => {
    renderBlock(answer({ bad: { ...BAD, ci_run_url: 'javascript:alert(1)' } }))
    const meta = document.querySelector('[data-bisect-meta]') as HTMLElement
    const ci = within(meta).getAllByRole('link')
    expect(ci.map((a) => a.getAttribute('href'))).toEqual(['https://ci.example.com/job/101'])
    expect(meta.innerHTML).not.toContain('javascript:')
  })
})

describe('BisectNarrowing — the commits between the two builds', () => {
  beforeEach(() => vi.clearAllMocks())

  const commits = [
    { sha: 'commitsentinelaa', author: 'Ada', message: 'touch checkout', committed_at: null, score: 88, rationale: { overlapping_files: ['app/checkout.py'] }, commit_url: 'https://git.example.com/c/a' },
    { sha: 'commitsentinelbb', author: 'Bo', message: 'docs', committed_at: null, score: 41, rationale: {}, commit_url: 'javascript:alert(2)' },
    { sha: 'commitsentinelcc', author: null, message: '', committed_at: null, score: 12, rationale: {}, commit_url: null },
  ]

  it('offline: the Changes tab\'s offline line, verbatim; no list', () => {
    renderBlock(answer())
    expect(document.querySelector('[data-bisect-compare-reason]')).toHaveTextContent(COMMIT_RANGE_REASON_TEXT.offline_mode)
    expect(COMMIT_RANGE_REASON_TEXT.offline_mode).toBe('Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).')
    expect(document.querySelector('[data-bisect-commits]')).toBeNull()
  })

  it('fetched: "Narrowed to 3 commits" with the run-level comparison, three ranked rows and both caveats', () => {
    renderBlock(answer({
      compare: compare({
        state: 'fetched', reason: null, host: 'github', commit_count: 3, commits, has_location_signal: true,
        caveat: 'Suspects, not culprits: in a monorepo a path overlap can be a coincidence.', run_level_commit_count: 17,
      }),
    }))
    expect(document.querySelector('[data-bisect-narrowed-heading]')).toHaveTextContent(
      'Narrowed to 3 commits (the run-level range since the last green run has 17)',
    )
    const rows = document.querySelectorAll('[data-bisect-commit]')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('#1')
    expect(rows[0]).toHaveTextContent('commitse')
    expect(rows[0]).toHaveTextContent('88')
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'commitse' })).toHaveAttribute('href', 'https://git.example.com/c/a')
    // A javascript: commit URL renders no link; a missing author / message reads honestly.
    expect(within(rows[1] as HTMLElement).queryByRole('link')).toBeNull()
    expect(rows[2]).toHaveTextContent('unknown author')
    expect(rows[2]).toHaveTextContent('(no message)')
    expect(document.querySelector('[data-bisect-caveat]')).toHaveTextContent('Suspects, not culprits')
    expect(document.querySelector('[data-bisect-merge-caveat]')).toHaveTextContent(
      "Compared as the git host's goodsen...badsent (commits reachable from the failure and not from the pass)",
    )
  })

  it('same commit: no code change explains the failure', () => {
    renderBlock(answer({ bad: { ...BAD, commit_hash: 'GOODSENTINEL01' }, compare: compare({ state: 'not_attempted', reason: 'same_commit' }) }))
    expect(document.querySelector('[data-bisect-compare-reason]')).toHaveTextContent(BISECT_SENTENCES.same_commit)
  })

  it('timed out: the sentence and Retry, which calls the hook\'s retry', () => {
    const retry = vi.fn()
    renderBlock(answer({ compare: compare({ state: 'timed_out', reason: 'timed_out' }) }), { retry })
    const line = document.querySelector('[data-bisect-compare-reason]') as HTMLElement
    expect(line).toHaveTextContent(BISECT_SENTENCES.timed_out)
    fireEvent.click(within(line).getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('no Retry for any other state', () => {
    renderBlock(answer({ compare: compare({ state: 'unavailable', reason: 'fetch_failed' }) }))
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })
})

describe('BisectNarrowing — loading and failure', () => {
  it('loading: the spinner line', () => {
    renderBlock(null, { isLoading: true })
    expect(screen.getByText(LOADING_LINE)).toBeInTheDocument()
  })

  it('a failed read: "Could not load the narrowed range."', () => {
    renderBlock(null, { error: new Error('404') })
    expect(document.querySelector('[data-bisect-error]')).toHaveTextContent(LOAD_ERROR_LINE)
    expect(LOAD_ERROR_LINE).toBe('Could not load the narrowed range.')
  })

  it('a malformed answer (parsed to null): the same sentence, no crash', () => {
    renderBlock(null)
    expect(document.querySelector('[data-bisect-error]')).toHaveTextContent(LOAD_ERROR_LINE)
  })
})

describe('bisectModel — the copy, pure', () => {
  it('shortSha: the first 7 characters, or "no commit recorded"', () => {
    expect(shortSha('goodsentinel01')).toBe('goodsen')
    expect(shortSha(null)).toBe('no commit recorded')
    expect(shortSha('  ')).toBe(BISECT_SENTENCES.no_commit)
  })

  it('stateLine: without the run\'s build the not-failing sentence names "this run"', () => {
    const n = answer({ state: 'not_failing', good: null, bad: null, streak_length: 0 })
    expect(stateLine(n, { runBuild: '7' })).toBe('This test did not fail in build #7: there is nothing to narrow.')
    expect(stateLine(n)).toBe('This test did not fail in this run: there is nothing to narrow.')
  })

  it('compareLine: the six git-host reasons are the Changes tab\'s sentences', () => {
    for (const reason of ['offline_mode', 'github_checks_off', 'no_integration', 'integration_disabled', 'repo_mismatch', 'no_token'] as const) {
      expect(compareLine(answer({ compare: compare({ reason }) }))).toBe(COMMIT_RANGE_REASON_TEXT[reason])
    }
  })

  it('compareLine: bisect\'s own reasons are its own sentences (fetch_failed is NOT the 6-hour line)', () => {
    for (const reason of ['no_good_commit', 'no_bad_commit', 'same_commit', 'cache_unavailable', 'fetch_failed'] as const) {
      expect(compareLine(answer({ compare: compare({ reason }) }))).toBe(BISECT_SENTENCES[reason])
    }
    expect(compareLine(answer({ compare: compare({ reason: 'fetch_failed' }) }))).not.toBe(COMMIT_RANGE_REASON_TEXT.fetch_failed)
  })

  it('compareLine: an unknown reason is the unknown line; not_narrowed and fetched say nothing', () => {
    expect(compareLine(answer({ compare: compare({ reason: 'from_the_future' }) }))).toBe(UNKNOWN_REASON_LINE)
    expect(compareLine(answer({ compare: compare({ state: 'not_attempted', reason: 'not_narrowed' }) }))).toBeNull()
    expect(compareLine(answer({ compare: compare({ state: 'fetched', reason: null }) }))).toBeNull()
  })

  it('narrowedHeading: singular, the completed-run anchor, no stored range, and zero commits', () => {
    const fetched = (over: Partial<BisectCompare>, from = 'green_baseline') =>
      narrowedHeading(answer({ narrowed_from: from, compare: compare({ state: 'fetched', reason: null, ...over }) }))
    expect(fetched({ commit_count: 1, run_level_commit_count: 4 })).toBe('Narrowed to 1 commit (the run-level range since the last green run has 4)')
    expect(fetched({ commit_count: 2, run_level_commit_count: 9 }, 'last_completed_run')).toBe(
      'Narrowed to 2 commits (the run-level range since the last completed run has 9)',
    )
    expect(fetched({ commit_count: 2, run_level_commit_count: null })).toBe('Narrowed to 2 commits')
    expect(fetched({ commit_count: 0 })).toBe('No commits between the two builds.')
  })

  it('metaLine / untestedLine / mergeCaveat', () => {
    expect(metaLine(answer({ streak_length: 1 }))).toBe('1 failing run on feature/pay')
    expect(untestedLine(answer({ untested_count: 2 }))).toBe('2 run(s) in between did not run this test.')
    expect(untestedLine(answer({ anchor: 'main_fallback', untested_count: 0 }))).toBeNull()
    expect(mergeCaveat(answer())).toContain('goodsen...badsent')
  })
})
