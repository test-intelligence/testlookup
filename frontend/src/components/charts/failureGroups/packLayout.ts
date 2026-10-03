/**
 * The bubble view's layout (VIZ-504): one circle per failure group, packed by
 * `d3-hierarchy`'s `pack()`. A circle's AREA follows the group's failures
 * (pack sizes by value, so the radius goes as the square root of the count —
 * the EPIC's "sqrt scale").
 *
 * Deterministic, which the visual baselines and the determinism snapshot need
 * (plan 3.3.3, spike S3): the children are sorted by a TOTAL order — value
 * descending, then id by UTF-16 code unit, never `localeCompare` (whose order
 * depends on the reader's locale and ICU build) — so the same groups in any
 * order give the same circles; and `pack` itself uses no randomness (S3
 * measured zero `Math.random` calls and byte-identical output across Windows,
 * Linux, Chromium and Node).
 *
 * A MINIMUM RADIUS. The smallest of 200 groups would otherwise be a dot
 * nobody can point at (WCAG 2.5.8: targets of at least 24 x 24 px). So every
 * value is at least a FLOOR, and the floor is raised until the smallest circle
 * reaches `minRadius` (a few passes; `pack` is a millisecond). Those circles
 * are then larger than their counts: `floorValue` is returned so the frame can
 * say "groups under N failures are drawn at the minimum size".
 *
 * Only `components/charts/failureGroups/**` may import `d3-*` (the
 * confinement ratchet), and only lazy section chunks reach this folder.
 */
import { hierarchy, pack } from 'd3-hierarchy'
import { byCodeUnit } from './failureGroups.model'

export interface PackItem {
  id: string
  /** The group's failures: a positive count. */
  value: number
}

export interface PackedCircle {
  id: string
  /** Centre and radius, px, inside the `size` x `size` square. */
  x: number
  y: number
  r: number
}

export interface PackResult {
  circles: PackedCircle[]
  /** Every value below this is drawn as this (the minimum size). `0` when no floor was needed. */
  floorValue: number
}

/** The smallest radius a circle is drawn at, px: a 24 x 24 px target. */
export const MIN_BUBBLE_RADIUS = 12
/** Space between circles, px. */
export const PACK_PADDING = 2
/** How many times the floor is raised before the layout settles for what it has. */
const FLOOR_PASSES = 6

interface Datum {
  id: string
  value: number
  children?: Datum[]
}

function packOnce(items: readonly PackItem[], size: number, padding: number, floor: number): PackedCircle[] {
  const root = hierarchy<Datum>({ id: '', value: 0, children: items.map((item) => ({ id: item.id, value: item.value })) })
    .sum((d) => (d.children ? 0 : Math.max(d.value, floor)))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || byCodeUnit(a.data.id, b.data.id))
  pack<Datum>().size([size, size]).padding(padding)(root)
  return root.leaves().map((leaf) => {
    const circle = leaf as unknown as { x: number; y: number; r: number }
    return { id: leaf.data.id, x: circle.x, y: circle.y, r: circle.r }
  })
}

/**
 * Circles for `items` in a `size` x `size` square, in the input's ID order
 * (byCodeUnit) so the output never depends on the order it was given.
 * Non-positive or non-finite values are left out (a group always has a
 * failure; nothing else can be packed).
 */
export function packLayout(
  items: readonly PackItem[],
  size: number,
  { padding = PACK_PADDING, minRadius = MIN_BUBBLE_RADIUS }: { padding?: number; minRadius?: number } = {},
): PackResult {
  const usable = items.filter((item) => Number.isFinite(item.value) && item.value > 0)
  if (usable.length === 0 || !(size > 0)) return { circles: [], floorValue: 0 }
  const minValue = Math.min(...usable.map((item) => item.value))
  const maxValue = Math.max(...usable.map((item) => item.value))
  let floor = 0
  let circles = packOnce(usable, size, padding, floor)
  for (let pass = 0; pass < FLOOR_PASSES && floor < maxValue; pass++) {
    const smallest = circles.reduce((min, circle) => Math.min(min, circle.r), Infinity)
    if (smallest >= minRadius) break
    // Area goes as value: to grow the smallest circle to `minRadius`, its
    // value must grow by (minRadius / r)^2 — a little more, because raising
    // the small ones shrinks every circle a little (they share one square).
    // Never past the largest value: there every circle is the same size, and
    // a square too small for them all cannot be helped by a floor.
    const base = Math.max(floor, minValue)
    floor = Math.min(maxValue, Math.ceil(base * (minRadius / Math.max(smallest, 0.01)) ** 2 * 1.05))
    circles = packOnce(usable, size, padding, floor)
  }
  return {
    circles: circles.sort((a, b) => byCodeUnit(a.id, b.id)),
    floorValue: floor > minValue ? floor : 0,
  }
}
