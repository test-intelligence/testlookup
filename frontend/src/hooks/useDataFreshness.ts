import { useMemo } from 'react'

/**
 * When this view's data actually arrived.
 *
 * The provenance rows on Coverage / Failure Analysis / Trends tell the reader
 * how much to trust the numbers above them ("coverage analyzer v1 · N evidence
 * items · last refreshed X"). Every one of them used to render a *hardcoded*
 * age — `'4h ago'`, `'12m ago'` — so a project whose data had just been ingested
 * was told it was hours stale. A fabricated freshness claim in the component
 * whose whole job is trustworthiness is worse than showing nothing.
 *
 * The backend does not expose a snapshot age, but it does not need to: these
 * pages fetch live, so the honest answer is when this client last received the
 * payload. Pass the SWR payload — the stamp advances whenever SWR hands back a
 * new object, which is exactly what a refresh is.
 *
 * Deliberately a `useMemo` rather than `useState` + `useEffect`: stamping in an
 * effect would set state synchronously on every payload and trip this repo's
 * `set-state-in-effect` rule, for a value that is a pure function of "which
 * payload am I holding".
 *
 * Returns `null` until the first payload lands, so callers can distinguish
 * "not loaded yet" from "loaded a moment ago" instead of guessing.
 */
export function useDataFreshness(data: unknown): Date | null {
  return useMemo(
    // `undefined` is SWR's "still loading" — only a real payload counts as an
    // arrival. `null` is a legitimate loaded-but-empty response.
    () => (data === undefined ? null : new Date()),
    [data],
  )
}
