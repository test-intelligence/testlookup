/**
 * Does every diagram label fit inside the box Mermaid drew for it?
 *
 * A diagram can render perfectly and still be unreadable. Mermaid sizes each
 * node box by MEASURING its label, and if the font it measures in differs from
 * the font the SVG finally renders in, every box comes out too narrow and the
 * labels are cut off — "Backend AP", "MCP serve", "CI syst".
 *
 * No text assertion can see that. `textContent` still holds the whole label
 * when it is visually clipped, so the entire probe suite passed on a diagram
 * nobody could read. This compares the painted geometry instead.
 *
 * One copy, used by two callers: the CI check that renders the guide's diagrams
 * in a harness, and the live probe that reads them off the deployment. They ran
 * as two copies for about an hour and that was already one copy too many.
 *
 * This function is serialised into the browser by `page.evaluate`, so it must
 * not reference anything outside its own body.
 */
export interface DiagramGeometry {
  /** How many node boxes were measured. Zero means the check looked at nothing. */
  nodes: number
  /** One entry per label wider than the box it was given. */
  clipped: string[]
}

export function measureDiagramLabels(rootSelector: string): DiagramGeometry {
  const clipped: string[] = []
  const nodeEls = Array.from(document.querySelectorAll(`${rootSelector} g.node`))

  for (const node of nodeEls) {
    // Mermaid puts each label in a <foreignObject> whose width it computed by
    // measuring the text. Compare that allocated width against what the HTML
    // inside actually needs.
    //
    // getBBox() on the foreignObject is NOT this measurement — it returns the
    // allocated width, so it always "fits". The first version of this check
    // used it and passed on a page where every label was visibly cut off.
    const fo = node.querySelector('foreignObject')
    const inner = fo?.firstElementChild as HTMLElement | null
    if (!fo || !inner) continue

    const allocated = fo.getBBox().width
    const needed = inner.scrollWidth
    // 1px of tolerance for sub-pixel rounding.
    if (needed > allocated + 1) {
      const label = inner.textContent?.trim() ?? '(unnamed)'
      clipped.push(`${label} (needs ${Math.round(needed)}px, got ${Math.round(allocated)}px)`)
    }
  }

  return { nodes: nodeEls.length, clipped }
}
