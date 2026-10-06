/**
 * A chart's zoom state (VIZ-407): LOCAL to the frame — component state, never
 * a store and never the URL — because a zoom is one reader looking closer at
 * one chart, not a filter the rest of the page should follow.
 *
 * It is cleared, not merely hidden, when:
 *
 *   - the global scope changes (project, releases, suites or window). A zoom
 *     taken on last month's data is not a question about this month's; and a
 *     scope that changes BACK must not resurrect it, so the state is dropped
 *     rather than filtered by a key it might match again;
 *   - the data no longer contains the zoomed days (`resolveZoom`): the range
 *     is remembered as day keys, and days that left the axis end the zoom
 *     instead of quietly re-pointing it at other days.
 *
 * While the frame has no drawn data (`xs` empty: loading, an error) the zoom
 * is kept but not applied — a revalidation that briefly has no model must not
 * throw away the reader's zoom.
 */
import { useCallback, useMemo, useState } from 'react'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { resolveZoom, zoomKeysFor, type ZoomKeys, type ZoomRange } from './zoomModel'

interface Held extends ZoomKeys {
  /** The global scope the zoom was taken under. */
  scope: string
}

export interface ChartZoom {
  /** The zoom on the current axis, or `null` when unzoomed. */
  range: ZoomRange | null
  /** Zoom to `range` (a whole-axis range, or `null`, resets). */
  setRange: (range: ZoomRange | null) => void
  /** The global window in force, in days. */
  windowDays: number
  /** Set the global window (1-365 days; anything else is ignored). */
  setWindowDays: (days: number) => void
}

function setWindowDays(days: number): void {
  if (!Number.isInteger(days) || days < 1 || days > 365) return
  useTimeWindowStore.getState().setDays(days)
}

export function useChartZoom(xs: readonly string[], initial?: ZoomKeys | null): ChartZoom {
  // The global scope a zoom is held under: the project, the release a request
  // is scoped to (`useReleaseScope`: none outside one project) and the window.
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = !activeProjectId || activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
  const releaseId = useReleaseScope()
  const windowDays = useTimeWindowStore((s) => s.days)
  const key = JSON.stringify([projectId, releaseId, windowDays])
  const [held, setHeld] = useState<Held | null>(() => (initial ? { ...initial, scope: key } : null))

  // A scope change DROPS the zoom (the React "adjust state during render"
  // pattern: no effect, so no frame is ever drawn zoomed under the new scope).
  const heldUnderScope = held !== null && held.scope === key ? held : null
  if (held !== null && heldUnderScope === null) setHeld(null)

  const range = useMemo(() => (xs.length > 0 ? resolveZoom(xs, heldUnderScope) : null), [xs, heldUnderScope])
  // The data moved on and the zoomed days are gone: drop it.
  if (heldUnderScope !== null && xs.length > 0 && range === null) setHeld(null)

  const setRange = useCallback(
    (next: ZoomRange | null) => {
      const keys = zoomKeysFor(xs, next)
      setHeld(keys ? { ...keys, scope: key } : null)
    },
    [xs, key],
  )

  return { range, setRange, windowDays, setWindowDays }
}
