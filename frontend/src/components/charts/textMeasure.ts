/**
 * How wide a chart's text really is, in the font the reader's browser draws
 * it in (the PR 165 Linux fixes).
 *
 * Chart text is set in `system-ui`: Segoe UI on Windows, San Francisco on a
 * Mac, and on a Linux machine whatever fontconfig picks — DejaVu Sans on the
 * CI runner, where "200ms – 500ms" at 11 px is 12 % wider than on Windows and
 * `<` and `=` are wider still. A reserve sized in pixels for one of them cuts
 * the text of another: the histogram's rotated bucket labels ran 5 px off the
 * bottom of its svg, and a shortened line-end label 4 px off its right. So a
 * reserve that depends on what a label SAYS is sized from the label, measured.
 *
 * The measure is a canvas `measureText` in the chart element's own computed
 * font family, which Chromium, Firefox and Safari shape exactly as they shape
 * the SVG `<text>` (both come from the same font matching). It exists only
 * once the element has a layout: under jsdom, or in a tab that has not laid
 * the chart out, `useTextMeasure` returns `null`, and each caller keeps the
 * fixed reserve it had (which is what every unit test sees).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/** The width, in px, of `text` at `fontSize` px in the chart's font. */
export type TextMeasure = (text: string, fontSize: number) => number

let context: CanvasRenderingContext2D | null | undefined

function canvasContext(): CanvasRenderingContext2D | null {
  if (context === undefined) {
    try {
      context = document.createElement('canvas').getContext('2d')
    } catch {
      context = null
    }
  }
  return context
}

/**
 * `text`'s width in `font` (a CSS `font` shorthand), from a shared canvas.
 * `null` where there is no canvas to measure with.
 */
export function measureTextWidth(text: string, font: string): number | null {
  const ctx = canvasContext()
  if (!ctx) return null
  ctx.font = font
  return ctx.measureText(text).width
}

/**
 * `[ref, measure]`: put `ref` on an element of the chart; `measure` is a
 * `TextMeasure` in the font family that element is drawn in once it has a
 * layout, and `null` until then (and always, where nothing is laid out).
 *
 * Read when the element is attached — in the commit, so the first frame the
 * reader sees is already sized from it — or, for an element attached before
 * it has a layout (a hidden tab), when it first gets one. The family is read
 * once: chart text follows the page's font, which does not change under a
 * mounted chart. Weight and style are the normal ones — the axis ticks and
 * line-end labels this sizes are neither bold nor italic.
 */
export function useTextMeasure<T extends Element>(): [(node: T | null) => void, TextMeasure | null] {
  const [family, setFamily] = useState<string | null>(null)
  const observer = useRef<ResizeObserver | null>(null)

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect()
    observer.current = null
    if (!node) return
    const read = () => {
      if (node.getBoundingClientRect().width === 0) return false
      const found = getComputedStyle(node).fontFamily
      if (!found || measureTextWidth('0', `11px ${found}`) === null) return false
      setFamily(found)
      return true
    }
    if (read() || typeof ResizeObserver === 'undefined') return
    const next = new ResizeObserver(() => {
      if (read()) next.disconnect()
    })
    next.observe(node)
    observer.current = next
  }, [])

  useEffect(() => () => observer.current?.disconnect(), [])

  const measure = useMemo<TextMeasure | null>(
    () => (family === null ? null : (text, fontSize) => measureTextWidth(text, `${fontSize}px ${family}`) ?? 0),
    [family],
  )
  return [ref, measure]
}
