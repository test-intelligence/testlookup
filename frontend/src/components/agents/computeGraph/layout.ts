/**
 * Layout constants for the compute graph.
 *
 * Coordinates are absolute pixel positions inside the 1750×576 canvas — every
 * node, edge endpoint, and edge label is anchored relative to these. Keep this
 * file pure-data so the renderer + edge-label math + Bézier path generator can
 * all read the same source.
 */
import type { ComputeStageId, DecisionId } from './types'

export const NODE_W = 226
/**
 * W40 (W29d F-4): 168 holds a done / pending / skipped card's content (head row,
 * a two-line description, the 2×2 metric grid: ≈ 163.4 px on CI's DejaVu); a
 * running / failed card clamps its description to one line to make room for its
 * suffix row (`ComputeNode`). Was 132, which every state overflowed by 28-56 px.
 */
export const NODE_H = 168
export const DECISION_SIZE = 88  // full square width / height (before rotation)
export const DECISION_HALF = DECISION_SIZE / 2

export const CANVAS_W = 1750
/** Three 168 px lanes 16 px apart from y 24 (24 … 560) plus 16 px: fits the 600 px pane without a scroll. */
export const CANVAS_H = 576

/** Per-node top-left positions, in pixels: a fixed, hand-placed layout, not computed. */
export const POS: Record<ComputeStageId | DecisionId, { x: number; y: number }> = {
  ingestion: { x:   32, y:  80 },
  anomaly:   { x:  290, y:  80 },
  route_analysis_mode: { x: 564, y: 120 },   // decision diamond, 88x88, centred on the y 80 row
  rca:       { x:  700, y:  24 },
  cluster:   { x:  700, y: 208 },
  triage:    { x:  970, y: 208 },  // skipped on the LLM branch
  flaky:     { x:  970, y: 392 },
  health:    { x: 1240, y: 392 },
  summary:   { x: 1240, y:  80 },
  release:   { x: 1500, y:  80 },
}

/** Returns the center point of a node (for edge-label positioning). */
export function nodeCenter(id: ComputeStageId | DecisionId): { x: number; y: number } {
  const p = POS[id]
  if (id === 'route_analysis_mode') {
    return { x: p.x + DECISION_HALF, y: p.y + DECISION_HALF }
  }
  return { x: p.x + NODE_W / 2, y: p.y + NODE_H / 2 }
}

/**
 * Edge endpoint anchors used by the cubic-Bézier path generator. The
 * decision diamond's hit box extends ~30px past its center on each side
 * (the rotated square's diagonal half) so anchors are pulled in.
 */
export function edgeAnchors(
  from: ComputeStageId | DecisionId,
  to: ComputeStageId | DecisionId,
): { fromX: number; fromY: number; toX: number; toY: number } {
  const a = POS[from]
  const b = POS[to]
  const fromCenterY = a.y + (from === 'route_analysis_mode' ? DECISION_HALF : NODE_H / 2)
  const toCenterY   = b.y + (to   === 'route_analysis_mode' ? DECISION_HALF : NODE_H / 2)
  const fromX = from === 'route_analysis_mode' ? a.x + DECISION_SIZE - 14 : a.x + NODE_W
  const toX   = to   === 'route_analysis_mode' ? b.x + 14                 : b.x
  return { fromX, fromY: fromCenterY, toX, toY: toCenterY }
}

/** Build the SVG ``d`` attribute for a cubic Bézier with horizontal control points. */
export function edgePath(
  from: ComputeStageId | DecisionId,
  to: ComputeStageId | DecisionId,
): string {
  const { fromX, fromY, toX, toY } = edgeAnchors(from, to)
  const cx = (fromX + toX) / 2
  return `M${fromX},${fromY} C ${cx},${fromY} ${cx},${toY} ${toX},${toY}`
}

/** Position for an edge label: midpoint with a small horizontal nudge to clear the diamond. */
export function edgeLabelPosition(
  from: ComputeStageId | DecisionId,
  to: ComputeStageId | DecisionId,
): { x: number; y: number } {
  const { fromX, fromY, toX, toY } = edgeAnchors(from, to)
  return { x: (fromX + toX) / 2 + 24, y: (fromY + toY) / 2 }
}
