/**
 * The two d3 layouts are deterministic (plan 3.3.3, spike S3, M-504a): the same
 * groups give the same picture, in any input order, on any machine, and
 * neither layout ever calls `Math.random`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { failureGroupsSeries } from './failureGroups.fixtures'
import { failureGroupsModel } from './failureGroups.model'
import { FORCE_NODE_CAP, forceLayout, sortLinks, type ForceEdge } from './forceLayout'
import { MIN_BUBBLE_RADIUS, packLayout, type PackItem } from './packLayout'

/** A stable fingerprint of a layout: every coordinate rounded to 0.01 px. */
const fingerprint = (rows: readonly { id: string; x: number; y: number; r: number }[]) =>
  rows.map((c) => `${c.id}|${c.x.toFixed(2)}|${c.y.toFixed(2)}|${c.r.toFixed(2)}`).join('\n')

/** The FNV-1a hash the spike used, so a snapshot is one short string. */
function fnv1a(text: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

const model = failureGroupsModel(failureGroupsSeries({ groups: 80 }))
const items: PackItem[] = model.groups.map((g) => ({ id: g.id, value: g.failureCount }))
const edges: ForceEdge[] = model.edges

afterEach(() => vi.restoreAllMocks())

describe('packLayout', () => {
  it('is the same picture whatever order the groups come in', () => {
    const a = packLayout(items, 480)
    const b = packLayout([...items].reverse(), 480)
    expect(fingerprint(b.circles)).toBe(fingerprint(a.circles))
  })

  it('never calls Math.random', () => {
    const random = vi.spyOn(Math, 'random')
    packLayout(items, 480)
    expect(random).not.toHaveBeenCalled()
  })

  it('every circle is a 24 px target at least, and the floor is reported', () => {
    const { circles, floorValue } = packLayout(items, 480)
    expect(circles).toHaveLength(80)
    expect(Math.min(...circles.map((c) => c.r))).toBeGreaterThanOrEqual(MIN_BUBBLE_RADIUS)
    expect(floorValue).toBeGreaterThan(0)
  })

  it('a bigger group is never a smaller circle (area follows failures)', () => {
    const { circles } = packLayout(items, 480)
    const r = new Map(circles.map((c) => [c.id, c.r]))
    for (let i = 1; i < model.groups.length; i++) {
      expect(r.get(model.groups[i - 1].id)).toBeGreaterThanOrEqual((r.get(model.groups[i].id) ?? 0) - 1e-9)
    }
    // Two groups well above the floor keep the square-root relation of their counts.
    const [g0, g1] = model.groups
    expect((r.get(g0.id) ?? 0) / (r.get(g1.id) ?? 1)).toBeCloseTo(Math.sqrt(g0.failureCount / g1.failureCount), 5)
  })

  it('no floor when every circle is already large enough; nothing to pack is no circles', () => {
    expect(packLayout(items.slice(0, 3), 480).floorValue).toBe(0)
    expect(packLayout([], 480).circles).toEqual([])
    expect(packLayout(items, 0).circles).toEqual([])
    expect(
      packLayout(
        [
          { id: 'a', value: 0 },
          { id: 'b', value: Number.NaN },
        ],
        480,
      ).circles,
    ).toEqual([])
  })

  it('stops raising the floor at the largest value (all circles equal) when the square is too small', () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ id: `g${i}`, value: i + 1 }))
    const { circles, floorValue } = packLayout(many, 60)
    expect(floorValue).toBe(50)
    expect(new Set(circles.map((c) => c.r.toFixed(6))).size).toBe(1)
  })

  it('determinism snapshot (M-504a)', () => {
    expect(fnv1a(fingerprint(packLayout(items, 480).circles))).toMatchInlineSnapshot(`"ec0ea26d"`)
  })
})

describe('forceLayout', () => {
  const packed = packLayout(items, 480).circles
  const box = { width: 640, height: 400 }

  it('lays out at most the 60 largest groups, in id order, inside the box', () => {
    const { nodes, links } = forceLayout(items, edges, packed, box)
    expect(nodes).toHaveLength(FORCE_NODE_CAP)
    const largest = new Set(model.groups.slice(0, FORCE_NODE_CAP).map((g) => g.id))
    expect(nodes.every((n) => largest.has(n.id))).toBe(true)
    expect(nodes.map((n) => n.id)).toEqual([...nodes.map((n) => n.id)].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)))
    for (const n of nodes) {
      expect(n.r).toBeGreaterThanOrEqual(MIN_BUBBLE_RADIUS)
      expect(n.x).toBeGreaterThanOrEqual(0)
      expect(n.x).toBeLessThanOrEqual(box.width)
      expect(n.y).toBeGreaterThanOrEqual(0)
      expect(n.y).toBeLessThanOrEqual(box.height)
    }
    expect(links.length).toBeGreaterThan(0)
    expect(links).toEqual(sortLinks(links))
  })

  it('never calls Math.random', () => {
    const random = vi.spyOn(Math, 'random')
    forceLayout(items, edges, packed, box)
    expect(random).not.toHaveBeenCalled()
  })

  it('the same links in ANY order give the same picture (S3: links are sorted first)', () => {
    const a = forceLayout(items, edges, packed, box)
    const b = forceLayout([...items].reverse(), [...edges].reverse(), [...packed].reverse(), box)
    expect(fingerprint(b.nodes)).toBe(fingerprint(a.nodes))
  })

  it('links pull: a linked pair ends closer than it would unlinked', () => {
    const [x, y] = model.groups.slice(10, 12)
    const distance = (r: ReturnType<typeof forceLayout>) => {
      const p = r.nodes.find((n) => n.id === x.id)
      const q = r.nodes.find((n) => n.id === y.id)
      if (!p || !q) throw new Error('a group was not laid out')
      return Math.hypot(p.x - q.x, p.y - q.y)
    }
    const unlinked = forceLayout(items, [], packed, box)
    const linked = forceLayout(items, [{ source: x.id, target: y.id, weight: 1 }], packed, box)
    expect(distance(linked)).toBeLessThan(distance(unlinked))
  })

  // R2-B F-01 / F-02: every edge was a 1-3 px stub between touching circles, and the picture used ~200 of 684 px.
  describe('a link is a visible line, and the picture fills its box', () => {
    const small = failureGroupsModel(failureGroupsSeries({ groups: 12, hostile: true }))
    const smallItems = small.groups.map((g) => ({ id: g.id, value: g.failureCount }))
    for (const [w, h] of [
      [684, 360],
      [350, 360],
      [307, 307],
    ]) {
      it(`${w} x ${h}`, () => {
        const result = forceLayout(smallItems, small.edges, packLayout(smallItems, Math.min(w, h)).circles, {
          width: w,
          height: h,
        })
        const at = new Map(result.nodes.map((n) => [n.id, n]))
        expect(result.links.length).toBeGreaterThan(5)
        // The stretch of line between the two circles' edges: none a stub, and typically a real length
        // (the link distance; collision padding alone leaves ~12-16 px, measured).
        const gaps = result.links.map((link) => {
          const a = at.get(link.source)
          const b = at.get(link.target)
          if (!a || !b) throw new Error('a link end was not laid out')
          return Math.hypot(a.x - b.x, a.y - b.y) - a.r - b.r
        })
        gaps.sort((p, q) => p - q)
        expect(gaps[0]).toBeGreaterThanOrEqual(10)
        expect(gaps[Math.floor(gaps.length / 2)]).toBeGreaterThanOrEqual(40)
        const spanX = Math.max(...result.nodes.map((n) => n.x + n.r)) - Math.min(...result.nodes.map((n) => n.x - n.r))
        const spanY = Math.max(...result.nodes.map((n) => n.y + n.r)) - Math.min(...result.nodes.map((n) => n.y - n.r))
        // Fitted to the box's limiting side (4 px margins), not left at the simulation's own size.
        expect(Math.max(spanX / (w - 8), spanY / (h - 8))).toBeGreaterThan(0.97)
      })
    }

    it('the fit never draws a circle larger than its bubble', () => {
      const one = [{ id: 'a', value: 5 }]
      const packedOne = packLayout(one, 360).circles
      const [node] = forceLayout(one, [], packedOne, { width: 684, height: 360 }).nodes
      expect(node.r).toBeLessThanOrEqual(packedOne[0].r + 1e-9)
    })
  })

  it('drops loops, unknown ends and items without a packed circle', () => {
    const { nodes, links } = forceLayout(
      [
        { id: 'a', value: 3 },
        { id: 'b', value: 2 },
        { id: 'ghost', value: 9 },
      ],
      [
        { source: 'a', target: 'a', weight: 1 },
        { source: 'a', target: 'zz', weight: 1 },
        { source: 'b', target: 'a', weight: 0.5 },
      ],
      [
        { id: 'a', x: 10, y: 10, r: 20 },
        { id: 'b', x: 60, y: 10, r: 15 },
      ],
      box,
    )
    expect(nodes.map((n) => n.id)).toEqual(['a', 'b'])
    expect(links).toEqual([{ source: 'b', target: 'a', weight: 0.5 }])
    expect(forceLayout(items, edges, packed, { width: 0, height: 10 })).toEqual({ nodes: [], links: [] })
    expect(forceLayout([], [], [], box)).toEqual({ nodes: [], links: [] })
  })

  it('determinism snapshot (M-504a)', () => {
    expect(fnv1a(fingerprint(forceLayout(items, edges, packed, box).nodes))).toMatchInlineSnapshot(`"1df1c887"`)
  })
})
