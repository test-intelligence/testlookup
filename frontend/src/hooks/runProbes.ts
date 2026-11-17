/**
 * The params of an existence probe ("has this project had a run?", "its newest
 * run"): one row, ALL TIME. `days: 0` is all time on `GET /runs`; an omitted
 * `days` is the server's 30-day default (D-0001, D-0002). Shared by Overview's
 * probes and `useEverHadRun`, which must stay identical.
 *
 * Its own module, not `useRuns`: dozens of test files mock `@/hooks/useRuns`
 * with a factory that lists only `useRuns`, and a constant read at import time
 * from that mock would be missing in every one of them.
 */
export const ALL_TIME_PROBE = { page: 1, size: 1, days: 0 } as const
