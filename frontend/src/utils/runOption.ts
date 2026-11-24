/**
 * How a run is named on screen: its build label first, `Run #N` second.
 *
 * D-67 / D-22 / S-C-3 (2026-11-21): `Run #N` is a per-(project, suite) rank
 * the server computes on every read. Every suite has a "Run #1", and a late
 * upload whose label sorts lower renumbers the runs above it, so it is not a
 * name for a run. The build label (`main-1865`, `cli-demo-1`) is what the
 * reporter sent and what a user searches for; `Run #N` is kept as a muted
 * ordinal beside it. Every picker and list that prints a run uses these
 * helpers so the same run reads the same way on every page.
 */
import type { TestRun } from '@/types/runs'

// The three naming helpers live in `./formatters`, which the run pages already load: importing them
// from here made this module another chunk in those routes' eager preload lists (batch 6).
import { runBuildLabel, runSeqLabel, type RunNameFields } from './formatters'

export { runBuildLabel, runName, runSeqLabel } from './formatters'

/** `main-1865 · Run #12`, or the label alone when the run has no number. */
export function runIdent(run: RunNameFields): string {
  const seq = runSeqLabel(run)
  return seq ? `${runBuildLabel(run)} · ${seq}` : runBuildLabel(run)
}

/** `main-1865 · Run #12 · FAILED · main · R2.4 · <local start time>`. */
export function formatRunOption(run: TestRun): string {
  const branch = run.branch ? ` · ${run.branch}` : ''
  const release = run.release_name ? ` · ${run.release_name}` : ''
  const created = new Date(run.created_at).toLocaleString()
  return `${runIdent(run)} · ${run.status}${branch}${release} · ${created}`
}
