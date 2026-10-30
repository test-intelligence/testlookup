import { getData } from './http'

/**
 * W23d AGT-14: the HTTP exchanges one test of one run made, as its reporter captured them (opt-in per run) and
 * the server redacted them before storing.
 *
 * Any member of the run's project (VIEWER included) — reads only: the route writes nothing. Another run's test
 * id is a 404 "Test case not found" (never an empty list); a Mongo read failure is a 503
 * `{code: "exchanges_unavailable", message}` (never an empty list that reads as "none captured"). The answer is
 * the RAW `HttpExchangeListResponse`, read through `parseExchangeList` by `hooks/useHttpExchanges`. No global
 * toast: the panel renders a failed read itself ("Could not load the HTTP exchanges." + Retry).
 *
 * The request path is written out in full in its call (the SPA wire-contract guard reads it).
 */
export function getHttpExchanges(runId: string, testId: string): Promise<unknown> {
  return getData<unknown>(`/api/v1/runs/${runId}/tests/${testId}/http-exchanges`, { suppressToast: true })
}
