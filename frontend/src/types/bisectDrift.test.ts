/**
 * W23b: the defensive parsers of the bisect answer and of the regression-diff answer's `config_drift`.
 *
 * A malformed answer is `null` (the block says "Could not load the narrowed range." / the drift card is not
 * rendered) and never crashes its section; an unknown `state` / `context_state` is `null`; an unknown
 * `compare.state` reads as `unavailable` with no reason (`UNKNOWN_REASON_LINE`); unknown drift sections /
 * changes are DROPPED; missing optional fields are defaulted.
 */
import { describe, expect, it } from 'vitest'
import { BISECT_MAX_RUNS, parseBisect } from './bisect'
import { parseConfigDrift } from './configDrift'
import { drift, driftEntry, fetchedCompare, narrowing, runRef } from '@/test/bisectDriftMocks'

const RUN = '00000000-0000-4000-8000-00000000b0a1'
const FP = 'fp-sentinel-checkout'

/** A wire answer with only the required fields. */
function minimal(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { run_id: RUN, fingerprint: FP, state: 'never_passed', ...extra }
}

describe('parseBisect', () => {
  it('passes a complete answer through unchanged', () => {
    const answer = narrowing({ run_id: RUN, compare: fetchedCompare() })
    expect(parseBisect(answer)).toEqual(answer)
  })

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string', 'narrowed'],
    ['an array', [narrowing()]],
    ['no run id', { fingerprint: FP, state: 'narrowed' }],
    ['no fingerprint', { run_id: RUN, state: 'narrowed' }],
    ['a numeric run id', { run_id: 7, fingerprint: FP, state: 'never_passed' }],
  ])('is null for a malformed answer: %s', (_label, raw) => {
    expect(parseBisect(raw)).toBeNull()
  })

  it('is null for an unknown state (never guessed)', () => {
    expect(parseBisect(minimal({ state: 'bisecting' }))).toBeNull()
    expect(parseBisect(minimal({ state: undefined }))).toBeNull()
  })

  it('is null for a present but malformed good / bad build, and for a narrowed answer missing either', () => {
    expect(parseBisect(narrowing({ good: { run_id: RUN } as never }))).toBeNull()
    expect(parseBisect(narrowing({ bad: 'build-sentinel-812' as never }))).toBeNull()
    expect(parseBisect(narrowing({ good: null }))).toBeNull()
    expect(parseBisect(narrowing({ bad: null }))).toBeNull()
  })

  it('reads an unknown compare state as unavailable with no reason (UNKNOWN_REASON_LINE), commits dropped', () => {
    const parsed = parseBisect(
      narrowing({ compare: { ...fetchedCompare(), state: 'queued' as never, reason: 'offline_mode', run_level_commit_count: 17 } }),
    )
    expect(parsed?.compare).toMatchObject({ state: 'unavailable', reason: null, commits: [], commit_count: 0 })
    expect(parsed?.compare.run_level_commit_count).toBe(17)
  })

  it('reads a missing or non-object compare as unavailable with no reason', () => {
    expect(parseBisect(minimal())?.compare).toMatchObject({ state: 'unavailable', reason: null, commits: [] })
    expect(parseBisect(minimal({ compare: 'fetched' }))?.compare.state).toBe('unavailable')
  })

  it('keeps an unknown compare REASON as text (the block maps it to UNKNOWN_REASON_LINE)', () => {
    const parsed = parseBisect(narrowing({ compare: { ...narrowing().compare, reason: 'brand_new_reason' } }))
    expect(parsed?.compare).toMatchObject({ state: 'unavailable', reason: 'brand_new_reason' })
  })

  it('keeps commits only on a fetched compare, dropping malformed ones', () => {
    const fetched = fetchedCompare()
    const parsed = parseBisect(narrowing({ compare: { ...fetched, commits: [...fetched.commits, 'junk' as never, null as never] } }))
    expect(parsed?.compare.commits).toHaveLength(3)
    const notFetched = parseBisect(narrowing({ compare: { ...fetched, state: 'timed_out', reason: 'timed_out' } }))
    expect(notFetched?.compare.commits).toEqual([])
    expect(notFetched?.compare.state).toBe('timed_out')
  })

  it('defaults a commit missing optional fields', () => {
    const parsed = parseBisect(narrowing({ compare: { ...fetchedCompare(), commits: [{ sha: 'cafesentinel01' } as never] } }))
    expect(parsed?.compare.commits[0]).toEqual({
      sha: 'cafesentinel01', author: null, message: '', committed_at: null, score: 0, rationale: {}, commit_url: null,
    })
  })

  it('defaults every missing optional field', () => {
    const parsed = parseBisect(minimal())
    expect(parsed).toEqual({
      run_id: RUN,
      fingerprint: FP,
      test_name: '',
      state: 'never_passed',
      series_branch: null,
      anchor: null,
      fallback_branch: null,
      good: null,
      bad: null,
      streak_length: 0,
      untested_runs: [],
      untested_count: 0,
      runs_read: 0,
      bounded: false,
      narrowed_from: 'unavailable',
      compare: {
        state: 'unavailable', reason: null, host: null, cached: false, commit_count: 0, truncated: false,
        commits: [], has_location_signal: false, caveat: null, run_level_commit_count: null,
      },
      generated_at: '',
    })
  })

  it('derives bounded from runs_read when the server omits it, and keeps the count beside a capped list', () => {
    expect(parseBisect(minimal({ state: 'no_pass_within_bound', runs_read: BISECT_MAX_RUNS }))?.bounded).toBe(true)
    const untested = Array.from({ length: 20 }, (_, i) => runRef({ build_number: `u${i}`, outcome: 'untested' }))
    const parsed = parseBisect(narrowing({ untested_runs: untested, untested_count: 25 }))
    expect(parsed?.untested_runs).toHaveLength(20)
    expect(parsed?.untested_count).toBe(25)
  })

  it('drops malformed untested runs, reads an unknown outcome as untested, an unknown anchor as null', () => {
    const parsed = parseBisect(
      narrowing({
        anchor: 'somewhere_else' as never,
        untested_runs: [runRef({ outcome: 'skipped' as never }), { build_number: 'x' } as never],
        untested_count: undefined as never,
      }),
    )
    expect(parsed?.anchor).toBeNull()
    expect(parsed?.untested_runs).toHaveLength(1)
    expect(parsed?.untested_runs[0].outcome).toBe('untested')
    expect(parsed?.untested_count).toBe(1)
  })

  it('keeps a hostile string as data (rendering is React text)', () => {
    const parsed = parseBisect(narrowing({ series_branch: '<script>alert(1)</script>' }))
    expect(parsed?.series_branch).toBe('<script>alert(1)</script>')
  })
})

describe('parseConfigDrift', () => {
  it('passes a complete drift through unchanged', () => {
    const value = drift()
    expect(parseConfigDrift(value)).toEqual(value)
  })

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a list', [drift()]],
    ['no baseline run id', { context_state: 'recorded', entries: [] }],
  ])('is null for a malformed value: %s', (_label, raw) => {
    expect(parseConfigDrift(raw)).toBeNull()
  })

  it('is null for an unknown context_state (the section is not rendered)', () => {
    expect(parseConfigDrift(drift({ context_state: 'partially_recorded' as never }))).toBeNull()
    expect(parseConfigDrift({ ...drift(), context_state: undefined })).toBeNull()
  })

  it('drops rows of an unknown section or change, and malformed rows', () => {
    const parsed = parseConfigDrift(
      drift({
        entries: [
          driftEntry('runtime', 'python', 'changed', '3.11.8', '3.11.9'),
          driftEntry('kernel' as never, 'linux', 'changed', '6.1', '6.8'),
          driftEntry('env', 'TZ', 'renamed' as never, 'UTC', 'Europe/Sentinel'),
          { section: 'env', change: 'added', new: 'x' } as never,
          'junk' as never,
        ],
        entries_total: 5,
      }),
    )
    expect(parsed?.entries).toEqual([driftEntry('runtime', 'python', 'changed', '3.11.8', '3.11.9')])
    expect(parsed?.entries_total).toBe(5)
  })

  it('defaults missing optional fields', () => {
    const parsed = parseConfigDrift({ baseline_run_id: RUN, context_state: 'run_missing' })
    expect(parsed).toEqual({
      baseline_run_id: RUN, baseline_build_number: null, context_state: 'run_missing', entries: [], entries_total: 0,
    })
    const noTotal = parseConfigDrift({ baseline_run_id: RUN, context_state: 'recorded', entries: [driftEntry('run', 'branch', 'changed', 'main', 'dev')] })
    expect(noTotal?.entries_total).toBe(1)
  })

  it('keeps null old / new values (added / removed)', () => {
    const parsed = parseConfigDrift(drift({ entries: [driftEntry('env', 'TZ', 'added', null, 'UTC'), driftEntry('runtime', 'java', 'removed', '21', null)] }))
    expect(parsed?.entries.map((e) => [e.old, e.new])).toEqual([[null, 'UTC'], ['21', null]])
  })
})
