/**
 * "Related groups" (VIZ-504): the largest failure groups, linked when they
 * fail in the SAME tests, placed by `d3-force` (`forceLayout`). The caption
 * says, in the EPIC's words, that position means nothing else — a reader must
 * not take "top left" or "far apart" for a property of the groups.
 *
 * The frame offers this view only when the server sent at least one link
 * between the groups shown: with no links it would be the bubble chart again,
 * scattered (AC "No edges").
 */
import { useCallback, useMemo } from 'react'
import type { MarkActivationProps } from '../marks'
import { formatNumber } from '@/utils/formatters'
import type { FailureGroup, FailureGroupEdge } from './failureGroups.model'
import { FORCE_NODE_CAP, forceLayout } from './forceLayout'
import GroupPlot from './GroupPlot'
import { packLayout } from './packLayout'
import { groupPlotItems, type PlotBox, type PlotLayout } from './plot.model'

/** The caption, exactly (EPIC 504, plan 9.3 R2). */
export const RELATIONS_CAPTION = 'Linked groups fail in the same tests. Position has no other meaning.'

export interface FailureGroupRelationsProps extends MarkActivationProps {
  /** Rank order. */
  groups: readonly FailureGroup[]
  edges: readonly FailureGroupEdge[]
  height: number
  description: string
  selectedId?: string | null
  width?: number
}

export default function FailureGroupRelations({
  groups,
  edges,
  height,
  description,
  selectedId,
  width,
  onMarkActivate,
  markIntents,
}: FailureGroupRelationsProps) {
  const shown = useMemo(() => groups.slice(0, FORCE_NODE_CAP), [groups])
  const items = useMemo(() => groupPlotItems(shown), [shown])
  const layout = useCallback(
    (box: PlotBox): PlotLayout => {
      const values = shown.map((group) => ({ id: group.id, value: group.failureCount }))
      // The pack is the starting position (and the radii), in a square the height of the box.
      const packed = packLayout(values, Math.min(box.width, box.height)).circles
      return forceLayout(values, edges, packed, box)
    },
    [shown, edges],
  )
  return (
    <div data-group-relations="" className="flex min-w-0 flex-col gap-2">
      <p data-group-relations-caption="" className="m-0 text-xs text-[var(--color-text-secondary)]">
        {RELATIONS_CAPTION}
        {groups.length > FORCE_NODE_CAP ? ` Showing the ${formatNumber(FORCE_NODE_CAP)} largest groups.` : ''}
      </p>
      <GroupPlot
        kind="relations"
        items={items}
        layout={layout}
        shape="wide"
        height={height}
        description={description}
        selectedId={selectedId}
        width={width}
        onMarkActivate={onMarkActivate}
        markIntents={markIntents}
      />
    </div>
  )
}
