/**
 * The open item of a page's detail, kept in the URL (`?<key>=<id>`), so a
 * refresh or a shared link reopens it and Back closes it (W27 PAN-01).
 *
 * One key per page, and only a detail rides the URL: a form, a confirmation
 * or a sub-panel never does. Every write is a functional update of the search
 * params, so the page's other params (`?tab=`, filters) are never touched, and
 * `location.state` is carried over on every write.
 *
 * History, `'push-open'` (the default):
 *  - open() from closed PUSHES an entry, marked in `location.state` with
 *    `[DETAIL_STATE_KEY]: key`, so Back closes the detail;
 *  - open() while open (a switch) REPLACES, so Back from B lands on the
 *    closed list, not on A;
 *  - close() POPS our own entry (navigate(-1)) when the current entry carries
 *    our marker, else (a deep link, a refresh of an unmarked entry) REPLACES
 *    with the key removed.
 * `'replace'`: every write replaces (the /test-management behaviour).
 *
 * The value is checked by `accept` (default `isUuid`) before it is returned
 * as `id`: a value that fails is reported as `rejected` and is never handed
 * to the page, so it can never reach a request path.
 */
import { useCallback, useMemo, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { isUuid } from './ids'

export type DetailHistory = 'push-open' | 'replace'

/** `location.state[DETAIL_STATE_KEY] === key`: this history entry was pushed by open(). */
export const DETAIL_STATE_KEY = 'inPageDetail'

export interface DetailParamOptions {
  /**
   * 'push-open' (default): open() from closed PUSHES a history entry (Back
   * closes the detail); open() while open (a switch) REPLACES; close() pops
   * our own pushed entry (navigate(-1)) when the current entry carries the
   * DETAIL_STATE_KEY marker for this key, else replaces with the key removed.
   * 'replace': every write replaces (the /test-management behaviour).
   */
  history?: DetailHistory
  /** A raw value that fails it is never returned as `id` (and so never reaches a request path). Default isUuid. */
  accept?: (raw: string) => boolean
}

export interface DetailParam {
  /** The accepted value of ?<key>=, else null. */
  id: string | null
  /** The raw value when it failed `accept` (the page shows its not-found card), else null. */
  rejected: string | null
  /** No-op when `id` is already open. Every other search param, and location.state, are kept. */
  open(id: string): void
  close(): void
}

function stateRecord(state: unknown): Record<string, unknown> {
  return state !== null && typeof state === 'object' && !Array.isArray(state)
    ? (state as Record<string, unknown>)
    : {}
}

export function useDetailParam(key: string, options: DetailParamOptions = {}): DetailParam {
  const history: DetailHistory = options.history ?? 'push-open'
  const accept = options.accept ?? isUuid
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  // The entry a pop was already asked for: a second close() before the
  // location moves (a double click) must not step back a second entry.
  const poppedFrom = useRef<string | null>(null)

  const raw = searchParams.get(key)
  const accepted = raw !== null && accept(raw)
  const id = accepted ? raw : null
  const rejected = raw !== null && !accepted ? raw : null
  const state: unknown = location.state
  const locationKey = location.key

  const open = useCallback(
    (next: string) => {
      if (next === id) return
      const push = history === 'push-open' && raw === null
      const nextState = push ? { ...stateRecord(state), [DETAIL_STATE_KEY]: key } : state
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev)
          out.set(key, next)
          return out
        },
        { replace: !push, state: nextState },
      )
    },
    [history, id, key, raw, setSearchParams, state],
  )

  const close = useCallback(() => {
    if (raw === null) return
    if (history === 'push-open' && stateRecord(state)[DETAIL_STATE_KEY] === key) {
      if (poppedFrom.current === locationKey) return
      poppedFrom.current = locationKey
      navigate(-1)
      return
    }
    setSearchParams(
      (prev) => {
        const out = new URLSearchParams(prev)
        out.delete(key)
        return out
      },
      { replace: true, state },
    )
  }, [history, key, locationKey, navigate, raw, setSearchParams, state])

  return useMemo(() => ({ id, rejected, open, close }), [id, rejected, open, close])
}
