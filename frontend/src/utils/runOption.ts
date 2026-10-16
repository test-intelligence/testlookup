/**
 * A run in a picker's words (moved from `RunComparePage`, FAIL-06): its stable
 * `Run #N` (else the short id), status, branch, release and start time.
 *
 * User feedback 2026-05-19: identify the run by its stable Run #N (or short
 * UUID) rather than the SDK-supplied build_number, which is often a noisy
 * timestamp slug (e.g. `testng-1779170467999`). Falls back to the short id
 * when the run has not been numbered yet.
 */
import type { TestRun } from '@/types/runs'

/** `Run #12`, or `Run 1a2b3c4d` for a run not numbered yet. */
export function runIdent(run: Pick<TestRun, 'id' | 'run_seq'>): string {
  return run.run_seq != null ? `Run #${run.run_seq}` : `Run ${run.id.slice(0, 8)}`
}

/** `Run #12 · FAILED · main · R2.4 · <local start time>`. */
export function formatRunOption(run: TestRun): string {
  const branch = run.branch ? ` · ${run.branch}` : ''
  const release = run.release_name ? ` · ${run.release_name}` : ''
  const created = new Date(run.created_at).toLocaleString()
  return `${runIdent(run)} · ${run.status}${branch}${release} · ${created}`
}
