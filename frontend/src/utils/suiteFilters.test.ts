import { describe, it, expect } from 'vitest'
import {
  collectSuiteOptions,
  collectSuiteOptionsFromRuns,
  normalizeSuiteName,
  runHasSuite,
  suiteMatchesValue,
  type SuiteRunLike,
} from './suiteFilters'

// Suite filtering has a long bug history in this repo: a run's suite can be
// recorded on `primary_suite_name` OR in `suite_names`, and the TestNG listener
// leaves one of them null for session inheritance. Any filter that consults
// only one field silently hides real runs, so the both-fields rule is pinned
// here rather than left to the pages that call in.

describe('normalizeSuiteName', () => {
  it('lowercases and trims', () => {
    expect(normalizeSuiteName('  Regression Suite  ')).toBe('regression suite')
  })

  it('maps null and undefined to the empty string', () => {
    expect(normalizeSuiteName(null)).toBe('')
    expect(normalizeSuiteName(undefined)).toBe('')
    expect(normalizeSuiteName()).toBe('')
  })

  it('maps whitespace-only names to the empty string', () => {
    expect(normalizeSuiteName('   ')).toBe('')
  })
})

describe('suiteMatchesValue', () => {
  it('matches case-insensitively and ignores surrounding whitespace', () => {
    expect(suiteMatchesValue(' Smoke ', 'smoke')).toBe(true)
    expect(suiteMatchesValue('SMOKE', 'smoke')).toBe(true)
  })

  it('does not match a different suite', () => {
    expect(suiteMatchesValue('smoke', 'regression')).toBe(false)
  })

  it('treats a null value as unmatched against a real suite', () => {
    // A live session whose suite_name has not been inherited yet must not be
    // silently counted as a member of whichever suite is selected.
    expect(suiteMatchesValue(null, 'smoke')).toBe(false)
    expect(suiteMatchesValue(undefined, 'smoke')).toBe(false)
  })

  it('matches a null value against an empty selection', () => {
    expect(suiteMatchesValue(null, '')).toBe(true)
  })
})

describe('runHasSuite', () => {
  const run: SuiteRunLike = {
    primary_suite_name: 'Regression',
    suite_names: ['Regression', 'Smoke'],
  }

  it('treats an empty suite selection as "no filter"', () => {
    expect(runHasSuite(run, '')).toBe(true)
    expect(runHasSuite({}, '   ')).toBe(true)
  })

  it('matches on primary_suite_name', () => {
    expect(runHasSuite({ primary_suite_name: 'Regression' }, 'regression')).toBe(true)
  })

  it('matches on a secondary entry in suite_names', () => {
    // The regression that matters: filtering on `Smoke` must not drop a run
    // whose *primary* suite is Regression but which also ran Smoke.
    expect(runHasSuite(run, 'Smoke')).toBe(true)
  })

  it('matches on suite_names when primary_suite_name is null', () => {
    // The TestNG-listener shape: primary left null for session inheritance.
    expect(runHasSuite({ primary_suite_name: null, suite_names: ['Smoke'] }, 'smoke')).toBe(true)
  })

  it('does not match a suite the run never ran', () => {
    expect(runHasSuite(run, 'Performance')).toBe(false)
  })

  it('tolerates a run with no suite fields at all', () => {
    expect(runHasSuite({}, 'smoke')).toBe(false)
    expect(runHasSuite({ primary_suite_name: null, suite_names: null }, 'smoke')).toBe(false)
  })
})

describe('collectSuiteOptions', () => {
  it('dedupes case-insensitively while keeping the first spelling seen', () => {
    expect(collectSuiteOptions(['Smoke', 'smoke', 'SMOKE'])).toEqual(['Smoke'])
  })

  it('drops null, undefined and blank names', () => {
    expect(collectSuiteOptions([null, undefined, '', '   ', 'Smoke'])).toEqual(['Smoke'])
  })

  it('trims the retained spelling', () => {
    expect(collectSuiteOptions(['  Smoke  '])).toEqual(['Smoke'])
  })

  it('sorts the result', () => {
    expect(collectSuiteOptions(['Regression', 'Api', 'smoke'])).toEqual(['Api', 'Regression', 'smoke'])
  })

  it('returns an empty list for no input', () => {
    expect(collectSuiteOptions([])).toEqual([])
  })
})

describe('collectSuiteOptionsFromRuns', () => {
  it('collects from primary_suite_name and suite_names together', () => {
    const runs: SuiteRunLike[] = [
      { primary_suite_name: 'Regression', suite_names: ['Smoke'] },
      { primary_suite_name: 'Api', suite_names: null },
    ]
    expect(collectSuiteOptionsFromRuns(runs)).toEqual(['Api', 'Regression', 'Smoke'])
  })

  it('dedupes across runs case-insensitively', () => {
    const runs: SuiteRunLike[] = [
      { primary_suite_name: 'Smoke' },
      { primary_suite_name: 'smoke', suite_names: ['SMOKE'] },
    ]
    expect(collectSuiteOptionsFromRuns(runs)).toEqual(['Smoke'])
  })

  it('ignores runs that carry no suite information', () => {
    expect(collectSuiteOptionsFromRuns([{}, { primary_suite_name: null, suite_names: [] }])).toEqual([])
  })

  it('returns an empty list for no runs', () => {
    expect(collectSuiteOptionsFromRuns([])).toEqual([])
  })
})
