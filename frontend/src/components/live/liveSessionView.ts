/**
 * How the Live page names a session and words a stream event (pure helpers).
 *
 * D-86 / D-88 / D-89 / D-90 (2026-11-21 product findings).
 */
import type { LiveSessionState } from '@/types/live-stream'
import { runBuildLabel, runSeqLabel } from '@/utils/runOption'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * D-86: the id a row's run page answers to, or null. The SDK's own id
 * (`ci-build-42`) is not one: /runs/{id} refuses it ("Invalid run ID"), so a
 * row that does not know its canonical TestRun id yet prints its label as text.
 */
export function sessionRunPageId(s: Pick<LiveSessionState, 'run_id' | 'test_run_id'>): string | null {
  if (s.test_run_id) return s.test_run_id
  return UUID_RE.test(s.run_id) ? s.run_id : null
}

/**
 * D-88: a session's name is its build label (the shared helper the Runs list
 * uses), never `Run #N`.
 */
export function sessionLabel(s: Pick<LiveSessionState, 'run_id' | 'test_run_id' | 'build_number'>): string {
  return runBuildLabel({ id: s.test_run_id || s.run_id, build_number: s.build_number })
}

/**
 * D-88: `Run #N` as a muted ordinal, for a finished run only. While a run
 * streams its suite may not be stamped yet, so the server numbers it in the
 * wrong group: it read "Run #1" until it completed, then "Run #10".
 */
export function sessionOrdinal(s: Pick<LiveSessionState, 'status' | 'run_seq'>): string | null {
  return s.status === 'running' ? null : runSeqLabel(s)
}

/** D-89: "the last 24 hours" / "the last 7 days", the window completed sessions come from. */
export function windowPhrase(days: number): string {
  return days === 1 ? 'the last 24 hours' : `the last ${days} days`
}

type Counts = Pick<LiveSessionState, 'total' | 'passed' | 'failed' | 'broken' | 'skipped'>

/** D-89: tests running sessions have declared but not reported yet (never below zero per session). */
export function testsStillToRun(sessions: Counts[]): number {
  return sessions.reduce((n, s) => {
    const done = (s.passed || 0) + (s.failed || 0) + (s.broken || 0) + (s.skipped || 0)
    return n + Math.max(0, (s.total || 0) - done)
  }, 0)
}

/** D-90: the stream frames that are not one of the four live-run frames, in words. */
const STREAM_EVENT_WORDS: Record<string, string> = {
  pipeline_progress: 'Analysis in progress',
  run_started: 'Run started',
  run_completed: 'Results saved',
  rollup_finalized: 'Run summary saved',
  test_failed: 'Test failed',
}

/** D-90: a test status in words (`FAILED` reads "Failed"). */
export function statusWords(status: string | undefined | null): string | undefined {
  if (!status) return undefined
  const s = status.toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** D-90: an event type in words, never the raw `snake_case` frame name. */
export function eventTypeWords(type: string): string {
  const known = STREAM_EVENT_WORDS[type]
  if (known) return known
  const words = type.replace(/^live_/, '').replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Stream event'
}
