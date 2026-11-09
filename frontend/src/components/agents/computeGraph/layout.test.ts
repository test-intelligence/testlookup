/**
 * W40 (W29d F-4): the hand-placed canvas re-spaced for the 168 px card — no two
 * cards overlap, every card and the diamond sit inside the canvas, and the
 * canvas fits the Agents page's 600 px pane (`AgentStatusPage`) without a
 * vertical scroll, even under a classic 17 px horizontal scrollbar.
 */
import { describe, expect, it } from 'vitest'

import { DAG_NODE_H, DAG_ROW_PITCH } from './dagLayout'
import { CANVAS_H, CANVAS_W, DECISION_HALF, DECISION_SIZE, NODE_H, NODE_W, POS, edgeAnchors } from './layout'
import type { ComputeStageId } from './types'

const PANE_H = 600
const SCROLLBAR = 17
const MIN_GAP = 16

const CARDS = (Object.keys(POS) as (keyof typeof POS)[]).filter((id): id is ComputeStageId => id !== 'route_analysis_mode')
const box = (id: ComputeStageId) => ({ id, x: POS[id].x, y: POS[id].y, right: POS[id].x + NODE_W, bottom: POS[id].y + NODE_H })

describe('the hand-placed compute canvas (W40 F-4)', () => {
  it('sizes a card 226 x 168 and the generic rows 24 px apart', () => {
    expect([NODE_W, NODE_H]).toEqual([226, 168])
    expect(DAG_NODE_H).toBe(NODE_H)
    expect(DAG_ROW_PITCH - DAG_NODE_H).toBe(24)
  })

  it('keeps every card and the diamond inside the canvas', () => {
    for (const b of CARDS.map(box)) {
      expect(b.x, b.id).toBeGreaterThanOrEqual(0)
      expect(b.y, b.id).toBeGreaterThanOrEqual(0)
      expect(b.right, b.id).toBeLessThanOrEqual(CANVAS_W)
      expect(b.bottom, b.id).toBeLessThanOrEqual(CANVAS_H)
    }
    const d = POS.route_analysis_mode
    expect(d.y + DECISION_SIZE).toBeLessThanOrEqual(CANVAS_H)
  })

  it('never lets two cards overlap, and stacks a column at least 16 px apart', () => {
    const boxes = CARDS.map(box)
    for (const a of boxes) for (const b of boxes) {
      if (a.id >= b.id) continue
      const apart = a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y
      expect(apart, `${a.id} / ${b.id}`).toBe(true)
      if (a.x === b.x) {
        const gap = a.y < b.y ? b.y - a.bottom : a.y - b.bottom
        expect(gap, `${a.id} / ${b.id}`).toBeGreaterThanOrEqual(MIN_GAP)
      }
    }
  })

  it('fits the 600 px pane without a vertical scroll', () => {
    expect(CANVAS_H).toBe(576)
    expect(CANVAS_H + SCROLLBAR).toBeLessThanOrEqual(PANE_H)
  })

  it('centres the decision diamond on the main row, and the edges on the cards', () => {
    expect(POS.route_analysis_mode.y + DECISION_HALF).toBe(POS.anomaly.y + NODE_H / 2)
    expect(edgeAnchors('ingestion', 'anomaly')).toEqual({
      fromX: POS.ingestion.x + NODE_W, fromY: POS.ingestion.y + NODE_H / 2,
      toX: POS.anomaly.x, toY: POS.anomaly.y + NODE_H / 2,
    })
  })
})
