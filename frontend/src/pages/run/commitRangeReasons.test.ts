/**
 * W10 FAIL-09 (contract §2.6 test 4): every reason's sentence and link, an
 * unknown reason, `no_commits` with and without a build number, the safe href.
 */
import { describe, expect, it } from 'vitest'

import { COMMIT_RANGE_REASONS } from '@/types/regressionDiff'
import {
  COMMIT_RANGE_REASON_TEXT,
  SUPPLIED_LINE,
  UNKNOWN_REASON_LINE,
  WEAK_ANCHOR_LINE,
  commitsHeading,
  reasonLine,
  reasonLink,
  safeCommitHref,
} from './commitRangeReasons'

const line = (reason: string | null, base_build_number: string | null = '40') => reasonLine({ reason, base_build_number })

describe('reasonLine', () => {
  it.each([
    ['offline_mode', 'Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).'],
    ['github_checks_off', 'Commit list unavailable: GitHub checks are turned off for this project.'],
    ['no_integration', 'Commit list unavailable: this project has no GitHub integration.'],
    ['integration_disabled', "Commit list unavailable: this project's GitHub integration is turned off."],
    ['repo_mismatch', "Commit list unavailable: this run's repository is not the project's GitHub repository."],
    ['no_token', 'Commit list unavailable: no GitHub token is stored for this project.'],
    ['no_commit_hash', 'Commit list unavailable: this run carries no commit hash.'],
    ['no_baseline_commit', 'Commit list unavailable: no earlier run of this branch carries a commit hash to compare with.'],
    ['not_resolved', 'Commit list not resolved yet.'],
    ['fetch_failed', 'Commit list unavailable: GitHub did not answer the comparison. It is tried again after 6 hours.'],
    ['no_commits', 'No commits between build #40 and this run.'],
  ])('%s', (reason, expected) => {
    expect(line(reason)).toBe(expected)
  })

  it('covers every reason the server can send, and nothing else', () => {
    expect(Object.keys(COMMIT_RANGE_REASON_TEXT).sort()).toEqual([...COMMIT_RANGE_REASONS].sort())
  })

  it('no_commits without a build number', () => {
    expect(line('no_commits', null)).toBe('No commits between the baseline build and this run.')
    expect(line('no_commits', '')).toBe('No commits between the baseline build and this run.')
  })

  it('an unknown reason, a missing one, and a key of Object.prototype read "Commit list unavailable."', () => {
    expect(UNKNOWN_REASON_LINE).toBe('Commit list unavailable.')
    expect(line('a_new_reason')).toBe(UNKNOWN_REASON_LINE)
    expect(line(null)).toBe(UNKNOWN_REASON_LINE)
    expect(line('toString')).toBe(UNKNOWN_REASON_LINE)
  })
})

describe('reasonLink', () => {
  it('Feature flags for github_checks_off, admins only, as a plain link', () => {
    expect(reasonLink('github_checks_off', true)).toEqual({ to: '/settings/feature-flags', label: 'Feature flags', scoped: false })
    expect(reasonLink('github_checks_off', false)).toBeNull()
  })

  it('Settings › GitHub (a scoped link) for what the project\'s GitHub settings fix, whatever the role', () => {
    expect(reasonLink('no_integration', false)).toEqual({ to: '/settings/github', label: 'Connect GitHub in Settings › GitHub', scoped: true })
    for (const reason of ['integration_disabled', 'repo_mismatch', 'no_token']) {
      expect(reasonLink(reason, false)).toEqual({ to: '/settings/github', label: 'Settings › GitHub', scoped: true })
    }
  })

  it('no link where the settings fix nothing', () => {
    for (const reason of ['offline_mode', 'no_commit_hash', 'no_baseline_commit', 'not_resolved', 'fetch_failed', 'no_commits', 'x', null, undefined]) {
      expect(reasonLink(reason, true)).toBeNull()
    }
  })
})

describe('the listed range', () => {
  it('the heading names its own base build and count; without a number, today\'s heading', () => {
    expect(commitsHeading({ base_build_number: '40', commit_count: 3 }, 3)).toBe('Commits since build #40 (3)')
    expect(commitsHeading({ base_build_number: null, commit_count: 3 }, 2)).toBe('Commits since baseline (2)')
    expect(commitsHeading(undefined, 1)).toBe('Commits since baseline (1)')
  })

  it('the weak-anchor and supplied lines', () => {
    expect(WEAK_ANCHOR_LINE).toBe('Counted from the last completed run, not the last green one: some of these may predate the failure.')
    expect(SUPPLIED_LINE).toBe('Commits supplied with the run (no GitHub call).')
  })

  it('a commit link only for an http(s) URL', () => {
    expect(safeCommitHref('https://ghe.fake.test/acme/payments/commit/abc')).toBe('https://ghe.fake.test/acme/payments/commit/abc')
    expect(safeCommitHref('http://github.local/acme/x/commit/1')).toBe('http://github.local/acme/x/commit/1')
    expect(safeCommitHref('javascript:alert(1)')).toBeNull()
    expect(safeCommitHref(' https://x.test')).toBeNull()
    expect(safeCommitHref('//evil.test/x')).toBeNull()
    expect(safeCommitHref('data:text/html,hi')).toBeNull()
    expect(safeCommitHref(null)).toBeNull()
    expect(safeCommitHref(undefined)).toBeNull()
  })
})
