/**
 * Every piece of visible text inside a chart frame that is NOT wholly drawn
 * inside it: outside the frame's box, cut off by the svg it sits in, or wider
 * than the HTML box that holds it.
 *
 * `textContent` cannot see any of this — a clipped label still holds its whole
 * text — so the check compares painted geometry: the box of each text node's
 * RANGE (the glyphs), not its element's layout box.
 *
 * One copy, used by the chart gallery spec (the donuts, the VIZ-404
 * comparisons) and the VIZ-405 trend-overlay spec. It is handed to
 * `locator.evaluate`, which serialises it into the browser, so it must not
 * reference anything outside its own body.
 */
export function textEscapes(root: Element): string[] {
  const frameBox = root.getBoundingClientRect()
  const problems: string[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = (node.textContent ?? '').trim()
    const element = node.parentElement
    if (!text || !element) continue
    // Visible text only: `sr-only` is a 1 px clipped box on purpose, and a
    // closed tooltip is `hidden`.
    if (!element.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue
    const layout = element.getBoundingClientRect()
    if (!(element instanceof SVGElement) && (layout.width <= 1 || layout.height <= 1)) continue
    const range = document.createRange()
    range.selectNodeContents(node)
    const box = range.getBoundingClientRect()
    if (box.width === 0 && box.height === 0) continue
    const inside = (outer: DOMRect) =>
      box.left >= outer.left - 0.5 &&
      box.right <= outer.right + 0.5 &&
      box.top >= outer.top - 0.5 &&
      box.bottom <= outer.bottom + 0.5
    if (!inside(frameBox)) problems.push(`"${text}" is outside the frame`)
    // An svg clips its own overflow: text past its edge is cut off.
    const svg = element instanceof SVGElement ? element.ownerSVGElement : null
    if (svg && !inside(svg.getBoundingClientRect())) problems.push(`"${text}" is cut off by its svg`)
    if (!(element instanceof SVGElement) && element.clientWidth > 0 && element.scrollWidth > element.clientWidth) {
      problems.push(`"${text}" overflows its box (${element.scrollWidth} > ${element.clientWidth})`)
    }
  }
  return problems
}
