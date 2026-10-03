/**
 * The "Related groups" layout (VIZ-504): the largest failure groups placed by
 * `d3-force` so that groups which fail in the SAME tests (an edge: Jaccard of
 * their affected-test sets, >= 0.2, from the server) are pulled together.
 * Position means nothing else — the caption says so in those words.
 *
 * Deterministic (plan 3.3.3, spike S3, BINDING): the simulation is built,
 * stopped before it can run on a timer, ticked exactly `FORCE_TICKS` times once
 * per data change, and read. It never sees `Math.random`:
 *
 *   - initial positions come from the pack layout, so d3 never has to invent
 *     them (its phyllotaxis start is deterministic too, but the pack start
 *     settles faster and keeps big groups apart);
 *   - the NODES are in id order (code unit) and the LINKS are sorted by
 *     (source, target) BEFORE the simulation: S3 measured that the same links
 *     in a different order give a different layout (`forceLink` applies them
 *     in array order), and the API's edge order is the server's business;
 *   - d3-force 3's own jiggle uses its internal LCG, seeded per simulation.
 *
 * The result is then scaled into the drawing box, keeping the aspect, so the
 * picture fills the frame whatever the forces did.
 */
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import { byCodeUnit } from './failureGroups.model'
import { MIN_BUBBLE_RADIUS, type PackedCircle } from './packLayout'

/** At most this many groups are laid out ("showing the 60 largest groups"); the server links the top 60 too. */
export const FORCE_NODE_CAP = 60
/** Ticks per data change: d3's default cooling reaches `alphaMin` at 300 (S3: final alpha 0.001). */
export const FORCE_TICKS = 300
/** A relation node is drawn at this share of its packed radius (never below `MIN_BUBBLE_RADIUS`). */
export const FORCE_RADIUS_SCALE = 0.6
/** The visible length of a link between two circles' edges before the fit, px (R2-B F-01). */
export const LINK_GAP = 40
/** Space the collision keeps around a circle, px. */
const COLLIDE_PADDING = 4
/** The fit may grow the picture until a circle is its packed (bubble) size again, never more. */
const MAX_FIT_SCALE = 1 / FORCE_RADIUS_SCALE

export interface ForceItem {
  id: string
  value: number
}

export interface ForceEdge {
  source: string
  target: string
  weight: number
}

export interface ForceNode {
  id: string
  x: number
  y: number
  r: number
}

export interface ForceLink {
  source: string
  target: string
  weight: number
}

export interface ForceResult {
  /** In id order. */
  nodes: ForceNode[]
  /** Sorted by (source, target); only between laid-out nodes. */
  links: ForceLink[]
}

interface SimNode extends SimulationNodeDatum {
  id: string
  r: number
}

interface SimLink {
  source: string | SimNode
  target: string | SimNode
  weight: number
}

/** The links, by (source, target) code unit: the order the simulation sees them in. */
export function sortLinks<T extends { source: string; target: string }>(links: readonly T[]): T[] {
  return [...links].sort((a, b) => byCodeUnit(a.source, b.source) || byCodeUnit(a.target, b.target))
}

/**
 * Lay out the `cap` largest `items` (value desc, id), starting from their
 * packed circles, in a `width` x `height` box. Items without a packed circle
 * are left out (they cannot be placed without inventing a position).
 */
export function forceLayout(
  items: readonly ForceItem[],
  edges: readonly ForceEdge[],
  packed: readonly PackedCircle[],
  { width, height, cap = FORCE_NODE_CAP, margin = 4 }: { width: number; height: number; cap?: number; margin?: number },
): ForceResult {
  if (!(width > 0) || !(height > 0)) return { nodes: [], links: [] }
  const circles = new Map(packed.map((circle) => [circle.id, circle]))
  const top = [...items]
    .filter((item) => circles.has(item.id))
    .sort((a, b) => b.value - a.value || byCodeUnit(a.id, b.id))
    .slice(0, cap)
  const nodes: SimNode[] = top
    .map((item) => {
      const circle = circles.get(item.id) as PackedCircle
      return { id: item.id, x: circle.x, y: circle.y, r: Math.max(MIN_BUBBLE_RADIUS, circle.r * FORCE_RADIUS_SCALE) }
    })
    .sort((a, b) => byCodeUnit(a.id, b.id))
  const ids = new Set(nodes.map((node) => node.id))
  const links: ForceLink[] = sortLinks(
    edges
      .filter((edge) => edge.source !== edge.target && ids.has(edge.source) && ids.has(edge.target))
      .map(({ source, target, weight }) => ({ source, target, weight })),
  )
  if (nodes.length === 0) return { nodes: [], links: [] }

  // The pack square's centre is where the forces pull to.
  const cx = packed.reduce((sum, c) => sum + c.x, 0) / Math.max(1, packed.length)
  const cy = packed.reduce((sum, c) => sum + c.y, 0) / Math.max(1, packed.length)
  const simLinks: SimLink[] = links.map((link) => ({ ...link }))
  // A wide box pulls less across than down, so the picture spreads to the box's shape.
  const aspect = width / height
  const simulation = forceSimulation<SimNode>(nodes)
    .stop()
    .force(
      'link',
      forceLink<SimNode, SimLink>(simLinks)
        .id((node) => node.id)
        .strength((link) => link.weight)
        // Centre to centre: both radii AND a visible stretch of line (R2-B F-01: d3's default 30 px is
        // shorter than two touching circles, so every edge was a 1-3 px stub between them).
        .distance((link) => (link.source as SimNode).r + (link.target as SimNode).r + LINK_GAP),
    )
    .force('charge', forceManyBody<SimNode>().strength(-60))
    .force(
      'collide',
      forceCollide<SimNode>((node) => node.r + COLLIDE_PADDING).strength(0.7),
    )
    .force('x', forceX<SimNode>(cx).strength(0.05 * Math.min(1, 1 / aspect)))
    .force('y', forceY<SimNode>(cy).strength(0.05 * Math.min(1, aspect)))
  simulation.tick(FORCE_TICKS)

  // Fit the result into the box, keeping its aspect, centred.
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  // Every node started with a position (the pack's), so the simulation's x and y are numbers.
  for (const node of nodes) {
    const x = node.x as number
    const y = node.y as number
    minX = Math.min(minX, x - node.r)
    minY = Math.min(minY, y - node.r)
    maxX = Math.max(maxX, x + node.r)
    maxY = Math.max(maxY, y + node.r)
  }
  const spanX = Math.max(1, maxX - minX)
  const spanY = Math.max(1, maxY - minY)
  // Grown to fill the box (R2-B F-02: the picture used ~200 of 684 px), but never past the bubbles' own size.
  const scale = Math.min((width - 2 * margin) / spanX, (height - 2 * margin) / spanY, MAX_FIT_SCALE)
  const offsetX = (width - spanX * scale) / 2 - minX * scale
  const offsetY = (height - spanY * scale) / 2 - minY * scale
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      x: (node.x as number) * scale + offsetX,
      y: (node.y as number) * scale + offsetY,
      r: Math.max(MIN_BUBBLE_RADIUS, node.r * scale),
    })),
    links,
  }
}
