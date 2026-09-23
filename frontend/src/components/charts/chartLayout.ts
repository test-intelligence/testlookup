/**
 * How wide a chart actually IS, so it can draw differently when it is narrow
 * (fix round A, SC 1.4.10).
 *
 * A horizontal bar chart spends a fixed 210 px on the category axis. In a
 * 1280 px window that is a quarter of the chart; at 320 px it is two thirds of
 * it, the plot area collapses to a few dozen pixels, and Recharts drops the
 * whole value axis and every per-bar value label — the chart still draws bars,
 * but there is no longer a number anywhere on it. A reader at 320 px (or at
 * 400% zoom, which is the same thing) is left with lengths and no scale.
 *
 * There is no media query that can answer this: the chart's width is its
 * CONTAINER's, and the same chart is full-width on one page and a third of a
 * grid on another. So it measures itself.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Under this many CSS px a bar chart goes compact: a much narrower category
 * axis, shorter labels and smaller margins, so the value axis and the value
 * labels keep their room.
 */
export const COMPACT_CHART_WIDTH = 460

/**
 * `[ref, width]` — put `ref` on the element to measure. `width` is 0 until it
 * has been measured (and stays 0 where there is no layout at all, such as
 * jsdom), which every caller must read as "not narrow" rather than "narrow".
 */
export function useContainerWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0)
  const observer = useRef<ResizeObserver | null>(null)

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect()
    observer.current = null
    if (!node) return
    setWidth(node.getBoundingClientRect().width)
    if (typeof ResizeObserver === 'undefined') return
    const next = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width
      if (typeof measured === 'number') setWidth(measured)
    })
    next.observe(node)
    observer.current = next
  }, [])

  useEffect(() => () => observer.current?.disconnect(), [])

  return [ref, width]
}
