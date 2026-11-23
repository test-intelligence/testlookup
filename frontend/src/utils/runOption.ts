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

type RunNameFields = { id?: string | null; build_number?: number | string | null; run_seq?: number | null }

/**
 * The run's build label: `main-1865`, or `Build 240` when the label is only a
 * number (a bare "240" reads as a count), or the short id when there is none.
 */
export function runBuildLabel(run: { id?: string | null; build_number?: number | string | null }): string {
  const raw = run.build_number == null ? '' : String(run.build_number).trim()
  if (raw === '') return run.id ? `Run ${run.id.slice(0, 8)}` : 'Run'
  return /^\d+$/.test(raw) ? `Build ${raw}` : raw
}

/** `Run #12`, the per-suite ordinal; null when the run has not been numbered. */
export function runSeqLabel(run: { run_seq?: number | null }): string | null {
  return run.run_seq != null ? `Run #${run.run_seq}` : null
}

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
