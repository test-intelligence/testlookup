/**
 * The bubble view of failure groups (VIZ-504, the default view): one circle
 * per group, area by failures (`packLayout`), filled with its dominant
 * category's colour AND pattern, the five largest labelled on the circle, the
 * rest named in the readout and the ranked table. "No error message" and the
 * singletons are NOT circles: they are muted totals beside the plot (the
 * frame's), because a circle would read as one cause.
 */
import { useCallback, useMemo } from 'react'
import type { MarkActivationProps } from '../marks'
import type { FailureGroup } from './failureGroups.model'
import GroupPlot from './GroupPlot'
import { packLayout } from './packLayout'
import { floorNote, groupPlotItems, type PlotBox, type PlotLayout } from './plot.model'

export interface FailureGroupBubblesProps extends MarkActivationProps {
  /** Rank order. */
  groups: readonly FailureGroup[]
  height: number
  description: string
  selectedId?: string | null
  /** The container's width when known (tests, gallery). */
  width?: number
}

export default function FailureGroupBubbles({
  groups,
  height,
  description,
  selectedId,
  width,
  onMarkActivate,
  markIntents,
}: FailureGroupBubblesProps) {
  const items = useMemo(() => groupPlotItems(groups), [groups])
  const layout = useCallback(
    (box: PlotBox): PlotLayout => {
      const { circles, floorValue } = packLayout(
        groups.map((group) => ({ id: group.id, value: group.failureCount })),
        Math.min(box.width, box.height),
      )
      return { nodes: circles, note: floorNote(floorValue) }
    },
    [groups],
  )
  return (
    <GroupPlot
      kind="bubbles"
      items={items}
      layout={layout}
      shape="square"
      height={height}
      description={description}
      selectedId={selectedId}
      width={width}
      onMarkActivate={onMarkActivate}
      markIntents={markIntents}
    />
  )
}
