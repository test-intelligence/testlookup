import { getData } from './http'

/**
 * W23b FAIL-12: one failing test of one run, narrowed to its own last pass.
 *
 * Any member of the run's project (VIEWER included; a project-bound key of that project) — reads only:
 * the route writes no row. The answer is the RAW `BisectResponse`, read through `parseBisect` by
 * `hooks/useBisectNarrowing`. A fingerprint that is not a test of THIS run is a 404
 * `{code: "fingerprint_not_in_run", message}`; a git-host failure, a timeout or the cache being down is
 * never an error status (it is `compare.state` / `compare.reason` in a 200).
 *
 * The request path is written out in full in its call (the SPA wire-contract guard reads it).
 */
export function getBisect(runId: string, fingerprint: string): Promise<unknown> {
  return getData<unknown>(`/api/v1/runs/${runId}/bisect`, { params: { fingerprint } })
}
