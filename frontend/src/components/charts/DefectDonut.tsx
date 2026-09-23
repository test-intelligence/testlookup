import { DonutPlot, type SliceStyle } from './DonutChart'
import { donutModel, type DonutSlice } from './DonutChart.model'
import { CHART_VARS, type DecalKind } from './tokens'

/**
 * Defects by priority — now one configuration of the generic `DonutPlot`
 * (VIZ-401), which owns the ring, the patterns, the legend and the empty text.
 *
 * P1 / P2 borrow the failed / broken STATUS encoding (colour AND pattern), so
 * they are drawn by `DonutPlot`'s status path; P3 / P4 get their own colour and
 * a pattern no other slice uses, so the four slices never differ by colour
 * alone (VIZ-102). P4 was a literal grey; it is the theme's muted neutral now.
 *
 * No centre total and no slice labels: this donut counts defects, not a
 * breakdown of one declared whole, and its callers give it 200 px of height.
 */
const PRIORITIES: { key: string; label: string; status?: 'failed' | 'broken'; color: string; decal: DecalKind }[] = [
  { key: 'p1', label: 'P1 Critical', status: 'failed', color: CHART_VARS.status.failed, decal: 'diagonal' },
  { key: 'p2', label: 'P2 High', status: 'broken', color: CHART_VARS.status.broken, decal: 'crosshatch' },
  { key: 'p3', label: 'P3 Medium', color: CHART_VARS.accent, decal: 'dots' },
  { key: 'p4', label: 'P4 Low', color: CHART_VARS.neutral, decal: 'solid' },
]

/** Keyed, not indexed: a zero P1 must not hand P3's colour to P2. */
function priorityStyle(slice: DonutSlice): SliceStyle {
  const priority = PRIORITIES.find((entry) => entry.key === slice.key) ?? PRIORITIES[PRIORITIES.length - 1]
  return { color: priority.color, decal: priority.decal }
}

interface Props {
  data: number[] // [p1, p2, p3, p4]
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

export default function DefectDonut({ data, animate }: Props) {
  const model = donutModel(
    PRIORITIES.map((priority, index) => ({
      key: priority.key,
      label: priority.label,
      value: data[index] ?? 0,
      ...(priority.status ? { status: priority.status } : {}),
    })),
  )

  return (
    <DonutPlot
      model={model}
      title="Defects by priority"
      height={200}
      animate={animate}
      innerRadius={50}
      outerRadius={80}
      paddingAngle={3}
      showCentreTotal={false}
      showSliceLabels={false}
      emptyText="No defect data"
      styleOf={priorityStyle}
    />
  )
}
