/**
 * Keyboard exploration for canvas charts (VIZ-105).
 *
 * A canvas has no DOM per data point, so the chart container is the one focus
 * stop and the "focused point" is a highlighted index:
 *
 *   Arrow keys  move the highlighted point (the renderer dispatches ECharts'
 *               `highlight` + `showTip`, so the tooltip is the SAME formatter
 *               output the mouse gets)
 *   Home / End  first / last point in the row; with Ctrl (or ⌘): the whole chart
 *   Escape      clears the highlight and keeps focus on the container (APG
 *               grid); it stops propagating only when it cleared something
 *   Tab         leaves the chart (it is one tab stop)
 *
 * The highlighted point's tooltip text is exposed as `announcement` for a
 * polite live region, so a screen reader hears what a sighted user sees.
 */
import { useCallback, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react'

export type NavKey = 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown' | 'Home' | 'End'

export interface NavRequest {
  key: NavKey
  /** Ctrl / ⌘ held: Home / End jump across the whole chart. */
  whole: boolean
}

const NAV_KEYS = new Set<string>(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'])

export function isNavKey(key: string): key is NavKey {
  return NAV_KEYS.has(key)
}

interface GridCell {
  x: number
  y: number
}

/**
 * Next index in a (possibly sparse) matrix of cells. `y` grows UPWARD, as on
 * an ECharts category y axis, so ArrowUp moves to a larger `y`. Returns the
 * current index when there is nowhere to go, and the top-left cell when
 * nothing is highlighted yet.
 */
export function moveInMatrix(cells: readonly GridCell[], current: number | null, { key, whole }: NavRequest): number | null {
  if (cells.length === 0) return null
  const indices = cells.map((_, i) => i)
  const byReadingOrder = (a: number, b: number) => cells[b].y - cells[a].y || cells[a].x - cells[b].x
  const ordered = [...indices].sort(byReadingOrder)
  if (current === null || current < 0 || current >= cells.length) {
    return key === 'End' ? ordered[ordered.length - 1] : ordered[0]
  }
  const here = cells[current]
  const pick = (candidates: number[], score: (i: number) => number): number => {
    if (candidates.length === 0) return current
    return candidates.reduce((best, i) => (score(i) < score(best) ? i : best))
  }
  switch (key) {
    case 'ArrowRight':
      return pick(indices.filter((i) => cells[i].y === here.y && cells[i].x > here.x), (i) => cells[i].x)
    case 'ArrowLeft':
      return pick(indices.filter((i) => cells[i].y === here.y && cells[i].x < here.x), (i) => -cells[i].x)
    case 'ArrowUp':
      return pick(
        indices.filter((i) => cells[i].y > here.y),
        (i) => (cells[i].y - here.y) * 1e6 + Math.abs(cells[i].x - here.x),
      )
    case 'ArrowDown':
      return pick(
        indices.filter((i) => cells[i].y < here.y),
        (i) => (here.y - cells[i].y) * 1e6 + Math.abs(cells[i].x - here.x),
      )
    case 'Home':
      return whole ? ordered[0] : pick(indices.filter((i) => cells[i].y === here.y), (i) => cells[i].x)
    case 'End':
      return whole ? ordered[ordered.length - 1] : pick(indices.filter((i) => cells[i].y === here.y), (i) => -cells[i].x)
  }
}

export interface ChartKeyboardOptions {
  /** Number of navigable points; a change clears the highlight. */
  count: number
  move: (current: number | null, request: NavRequest) => number | null
  /** Highlight `index` and show its tooltip. */
  onActivate: (index: number) => void
  /** Remove the highlight from `index` and hide the tooltip. */
  onClear: (index: number) => void
  /** The tooltip text for `index`, announced to assistive tech. */
  describe: (index: number) => string
}

export function useChartKeyboard({ count, move, onActivate, onClear, describe }: ChartKeyboardOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [active, setActive] = useState<number | null>(null)
  // New data: the old index means nothing now (adjusted during render, not in an effect).
  const [seenCount, setSeenCount] = useState(count)
  if (seenCount !== count) {
    setSeenCount(count)
    setActive(null)
  }

  const clear = useCallback(() => {
    if (active !== null) onClear(active)
    setActive(null)
  }, [active, onClear])

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === 'Escape') {
        // Consumed ONLY when it cleared something: inside a SidePanel / dialog
        // the first Escape clears the highlight, the second reaches the panel.
        if (active !== null) {
          event.preventDefault()
          event.stopPropagation()
        }
        clear()
        containerRef.current?.focus()
        return
      }
      if (!isNavKey(event.key) || count === 0) return
      event.preventDefault()
      const next = move(active, { key: event.key, whole: event.ctrlKey || event.metaKey })
      if (next === null || next === active) return
      if (active !== null) onClear(active)
      onActivate(next)
      setActive(next)
    },
    [active, clear, count, move, onActivate, onClear],
  )

  const onBlur = useCallback(
    (event: FocusEvent<HTMLElement>) => {
      const to = event.relatedTarget as Node | null
      if (to && containerRef.current?.contains(to)) return
      clear()
    },
    [clear],
  )

  const current = active !== null && active < count ? active : null
  return {
    activeIndex: current,
    announcement: current === null ? '' : describe(current),
    containerRef,
    containerProps: { tabIndex: 0, onKeyDown, onBlur },
  }
}
