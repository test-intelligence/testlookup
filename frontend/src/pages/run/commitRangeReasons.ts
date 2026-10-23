/**
 * The run Changes tab's commit list, the copy (W10 FAIL-09). Pure: the tab and
 * its tests import the same strings.
 *
 * The commits are the stored per-project range (the Suspects panel's), read from
 * the project's GitHub / GHE integration. When there are none the server says
 * why (`commit_range_status.reason`); each reason has one line, and a link where
 * the reader can fix it.
 */
import type { CommitRangeReason, CommitRangeStatus } from '@/types/regressionDiff'

/** One line per reason. `no_commits` is a template: see `reasonLine`. */
export const COMMIT_RANGE_REASON_TEXT: Record<CommitRangeReason, string> = {
  offline_mode: 'Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).',
  github_checks_off: 'Commit list unavailable: GitHub checks are turned off for this project.',
  no_integration: 'Commit list unavailable: this project has no GitHub integration.',
  integration_disabled: "Commit list unavailable: this project's GitHub integration is turned off.",
  repo_mismatch: "Commit list unavailable: this run's repository is not the project's GitHub repository.",
  no_token: 'Commit list unavailable: no GitHub token is stored for this project.',
  no_commit_hash: 'Commit list unavailable: this run carries no commit hash.',
  no_baseline_commit: 'Commit list unavailable: no earlier run of this branch carries a commit hash to compare with.',
  not_resolved: 'Commit list not resolved yet.',
  fetch_failed: 'Commit list unavailable: GitHub did not answer the comparison. It is tried again after 6 hours.',
  no_commits: 'No commits between build #{base_build_number} and this run.',
}

/** Any reason this build does not know. */
export const UNKNOWN_REASON_LINE = 'Commit list unavailable.'
/** `no_commits` when the range's base build has no number. */
export const NO_COMMITS_NO_BUILD_LINE = 'No commits between the baseline build and this run.'

/** Under the heading when the range is counted from the last completed run, not the last green one. */
export const WEAK_ANCHOR_LINE = 'Counted from the last completed run, not the last green one: some of these may predate the failure.'
/** Under the heading when the run brought its own commit list. */
export const SUPPLIED_LINE = 'Commits supplied with the run (no GitHub call).'

function isKnownReason(reason: string): reason is CommitRangeReason {
  return Object.prototype.hasOwnProperty.call(COMMIT_RANGE_REASON_TEXT, reason)
}

/** The reason line for a status whose commits are not listed. */
export function reasonLine(status: Pick<CommitRangeStatus, 'reason' | 'base_build_number'>): string {
  const reason = status.reason ?? ''
  if (!isKnownReason(reason)) return UNKNOWN_REASON_LINE
  if (reason === 'no_commits') {
    return status.base_build_number
      ? COMMIT_RANGE_REASON_TEXT.no_commits.replace('{base_build_number}', status.base_build_number)
      : NO_COMMITS_NO_BUILD_LINE
  }
  return COMMIT_RANGE_REASON_TEXT[reason]
}

export interface ReasonLink {
  to: string
  label: string
  /** A project-scoped settings page: rendered as a ScopedLink. */
  scoped: boolean
}

/** Where the reader can fix it, when somewhere can: Feature flags (admins only) or Settings › GitHub. */
export function reasonLink(reason: string | null | undefined, isAdmin: boolean): ReasonLink | null {
  switch (reason) {
    case 'github_checks_off':
      return isAdmin ? { to: '/settings/feature-flags', label: 'Feature flags', scoped: false } : null
    case 'no_integration':
      return { to: '/settings/github', label: 'Connect GitHub in Settings › GitHub', scoped: true }
    case 'integration_disabled':
    case 'repo_mismatch':
    case 'no_token':
      return { to: '/settings/github', label: 'Settings › GitHub', scoped: true }
    default:
      return null
  }
}

/** `Commits since build #N (count)`, or today's `Commits since baseline (n)` when the base build has no number. */
export function commitsHeading(status: Pick<CommitRangeStatus, 'base_build_number' | 'commit_count'> | undefined, listed: number): string {
  if (status?.base_build_number) return `Commits since build #${status.base_build_number} (${status.commit_count})`
  return `Commits since baseline (${listed})`
}

const SAFE_HREF = /^https?:\/\//

/** A commit link only when the server-built URL is http(s); anything else (a `javascript:` URL) renders no link. */
export function safeCommitHref(url: string | null | undefined): string | null {
  return typeof url === 'string' && SAFE_HREF.test(url) ? url : null
}
