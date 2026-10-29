/**
 * The bisect narrowing block's copy (W23b FAIL-12). Pure: `BisectNarrowing`
 * and its tests import the same strings.
 *
 * The answer is `GET /api/v1/runs/{run_id}/bisect?fingerprint=` (observe-only):
 * the test's own last pass and first failure on the run's branch (or on main /
 * master, labelled), the runs in between that did not run the test, and — when
 * a git host answered — the commits between the two builds. The sentences are
 * the server vocabulary's (`BISECT_SENTENCES`, mirrored in `types/bisect.ts`);
 * the six git-host reasons reuse the Changes tab's `COMMIT_RANGE_REASON_TEXT`.
 *
 * Build numbers become links to `/runs/{uuid}` (built here from server ids),
 * SHAs show their first 7 characters, and every name is React text.
 */
import { BISECT_SENTENCES, type BisectNarrowing, type BisectRunRef } from '@/types/bisect'
import { COMMIT_RANGE_REASON_TEXT, UNKNOWN_REASON_LINE, safeCommitHref } from '@/pages/run/commitRangeReasons'

/** The block's heading on `/failures` (inside the row's Suspects section). */
export const BLOCK_HEADING = "Narrowed to this test's last pass"
/** The block's heading under a new failure on the run page's Changes tab. */
export function inlineHeading(testName: string): string {
  return `Narrowed range for ${testName}`
}
export const LOADING_LINE = 'Narrowing…'
export const LOAD_ERROR_LINE = 'Could not load the narrowed range.'
export const RETRY_LABEL = 'Retry'
/** A run with no branch walks the project's runs with no branch. */
export const NO_BRANCH_LABEL = 'runs without a branch'
export const NO_LOCATION_LINE =
  'No source location was derivable for this test: commits are ordered by recency, not path overlap.'
export const TRUNCATED_LINE = 'The git host capped the list at 100 commits.'
export const NO_COMMITS_LINE = 'No commits between the two builds.'

/** One piece of a filled sentence: plain text, a build linked to its run, or a short SHA. */
export type Segment =
  | { kind: 'text'; text: string }
  | { kind: 'build'; text: string; runId: string }
  | { kind: 'sha'; text: string }

const text = (t: string): Segment => ({ kind: 'text', text: t })

/** The first 7 characters of a SHA, or `no commit recorded`. */
export function shortSha(sha: string | null | undefined): string {
  const s = (sha ?? '').trim()
  return s ? s.slice(0, 7) : BISECT_SENTENCES.no_commit
}

/** A link target only for an http(s) URL; anything else (a `javascript:` URL) renders no link. */
export function isHttp(url: string | null | undefined): boolean {
  return safeCommitHref(url) !== null
}

/** `/runs/{uuid}`, SPA-built from the server's id. */
export function runHref(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}`
}

function buildSeg(ref: BisectRunRef | null | undefined): Segment {
  if (!ref) return text('an unknown build')
  return { kind: 'build', text: `#${ref.build_number}`, runId: ref.run_id }
}

function shaSeg(ref: BisectRunRef | null | undefined): Segment {
  return { kind: 'sha', text: shortSha(ref?.commit_hash) }
}

/** The series label: the run's branch as stored, or "runs without a branch". */
export function branchLabel(n: Pick<BisectNarrowing, 'series_branch'>): string {
  return n.series_branch ? n.series_branch : NO_BRANCH_LABEL
}

/** Split a `{name}` template into segments, each placeholder replaced by its value. */
export function fill(template: string, values: Record<string, Segment>): Segment[] {
  const out: Segment[] = []
  const re = /\{(\w+)\}/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(template)) !== null) {
    if (m.index > last) out.push(text(template.slice(last, m.index)))
    out.push(values[m[1]] ?? text(m[0]))
    last = m.index + m[0].length
  }
  if (last < template.length) out.push(text(template.slice(last)))
  return out
}

/** The plain text of a filled sentence. */
export function joinSegments(segments: Segment[]): string {
  return segments.map((s) => s.text).join('')
}

/**
 * The state sentence, filled. `runBuild` names the run itself for `not_failing`
 * (the answer carries only its id); without it the sentence says "this run".
 */
export function stateSegments(n: BisectNarrowing, opts: { runBuild?: string | null } = {}): Segment[] {
  const branch = text(branchLabel(n))
  switch (n.state) {
    case 'narrowed':
      if (n.anchor === 'main_fallback') {
        return fill(BISECT_SENTENCES.narrowed_main, {
          branch, fallback: text(n.fallback_branch || 'main'),
          good: buildSeg(n.good), good_sha: shaSeg(n.good), bad: buildSeg(n.bad), bad_sha: shaSeg(n.bad),
        })
      }
      return fill(BISECT_SENTENCES.narrowed, {
        good: buildSeg(n.good), good_sha: shaSeg(n.good), bad: buildSeg(n.bad), bad_sha: shaSeg(n.bad),
      })
    case 'never_passed':
      return fill(BISECT_SENTENCES.never_passed, { branch, runs: text(String(n.runs_read)) })
    case 'no_pass_within_bound':
      return fill(BISECT_SENTENCES.no_pass_within_bound, { branch })
    case 'not_failing': {
      if (opts.runBuild) {
        return fill(BISECT_SENTENCES.not_failing, {
          run: { kind: 'build', text: `#${opts.runBuild}`, runId: n.run_id },
        })
      }
      return fill(BISECT_SENTENCES.not_failing.replace('build {run}', '{run}'), {
        run: { kind: 'build', text: 'this run', runId: n.run_id },
      })
    }
    default:
      return [text(LOAD_ERROR_LINE)]
  }
}

/** The state sentence as plain text (tests, titles). */
export function stateLine(n: BisectNarrowing, opts: { runBuild?: string | null } = {}): string {
  return joinSegments(stateSegments(n, opts))
}

/** Line 2's text parts (`·`-joined by the block): the streak, and the main-fallback anchor. */
export function metaParts(n: BisectNarrowing): string[] {
  const parts: string[] = []
  if (n.state === 'not_failing') return parts
  const k = n.streak_length
  if (k > 0) parts.push(`${k} failing run${k === 1 ? '' : 's'} on ${branchLabel(n)}`)
  if (n.state === 'narrowed' && n.anchor === 'main_fallback') {
    parts.push(`Anchored on ${n.fallback_branch || 'main'} (no pass on ${branchLabel(n)})`)
  }
  return parts
}

/** `metaParts` joined, as the block shows it. */
export function metaLine(n: BisectNarrowing): string {
  return metaParts(n).join(' · ')
}

/** `{n} run(s) in between did not run this test.`, or null when none (or not narrowed). */
export function untestedLine(n: BisectNarrowing): string | null {
  if (n.state !== 'narrowed' || n.untested_count <= 0) return null
  return BISECT_SENTENCES.untested.replace('{n}', String(n.untested_count))
}

/** `and {k} more` under the listed untested builds when the list was capped. */
export function untestedMoreLine(n: BisectNarrowing): string | null {
  const more = n.untested_count - n.untested_runs.length
  return more > 0 ? `and ${more} more` : null
}

/** The six git-host reasons shared with the Changes tab (their sentences imported, not copied). */
const HOST_REASONS: ReadonlySet<string> = new Set([
  'offline_mode', 'github_checks_off', 'no_integration', 'integration_disabled', 'repo_mismatch', 'no_token',
])
/** Bisect's own reasons, each with a sentence of its own. */
const OWN_REASONS: ReadonlySet<string> = new Set([
  'no_good_commit', 'no_bad_commit', 'same_commit', 'cache_unavailable', 'timed_out', 'fetch_failed',
])

/**
 * The one line under the builds when no commit list is shown: the git-host
 * reason (the Changes tab's sentence) or bisect's own; null when the list was
 * fetched, or when nothing was narrowed (the state sentence already says why).
 */
export function compareLine(n: BisectNarrowing): string | null {
  const c = n.compare
  if (c.state === 'fetched') return null
  const reason = c.reason ?? ''
  if (reason === 'not_narrowed') return null
  if (c.state === 'timed_out') return BISECT_SENTENCES.timed_out
  if (HOST_REASONS.has(reason)) return COMMIT_RANGE_REASON_TEXT[reason as keyof typeof COMMIT_RANGE_REASON_TEXT]
  if (OWN_REASONS.has(reason)) return BISECT_SENTENCES[reason as keyof typeof BISECT_SENTENCES]
  return UNKNOWN_REASON_LINE
}

/** True when the compare ran out of time: the block offers Retry. */
export function canRetry(n: BisectNarrowing): boolean {
  return n.compare.state === 'timed_out'
}

const RUN_LEVEL_FROM: Record<string, string> = {
  green_baseline: 'since the last green run',
  last_completed_run: 'since the last completed run',
  supplied: 'supplied with the run',
}

/** `Narrowed to {k} commits (the run-level range since the last green run has {m})`. */
export function narrowedHeading(n: BisectNarrowing): string {
  const c = n.compare
  const k = c.commit_count
  if (k === 0) return NO_COMMITS_LINE
  let head = `Narrowed to ${k} commit${k === 1 ? '' : 's'}`
  const m = c.run_level_commit_count
  if (typeof m === 'number') {
    const from = RUN_LEVEL_FROM[n.narrowed_from]
    head += from ? ` (the run-level range ${from} has ${m})` : ` (the run-level range has ${m})`
  }
  return head
}

/** `Showing the top {listed} of {k}.` when the server listed fewer than it fetched. */
export function listedLine(n: BisectNarrowing): string | null {
  const listed = n.compare.commits.length
  return listed > 0 && n.compare.commit_count > listed ? `Showing the top ${listed} of ${n.compare.commit_count}.` : null
}

/** The overlapping files of a commit's rationale, when the server sent a string list. */
export function overlappingFiles(rationale: unknown): string[] {
  if (!rationale || typeof rationale !== 'object') return []
  const files = (rationale as Record<string, unknown>).overlapping_files
  return Array.isArray(files) ? files.filter((f): f is string => typeof f === 'string') : []
}

/** The merge / rebase caveat beside every fetched list, with the two short SHAs. */
export function mergeCaveat(n: BisectNarrowing): string {
  return BISECT_SENTENCES.merge_caveat
    .replace('{good_sha}', shortSha(n.good?.commit_hash))
    .replace('{bad_sha}', shortSha(n.bad?.commit_hash))
}
