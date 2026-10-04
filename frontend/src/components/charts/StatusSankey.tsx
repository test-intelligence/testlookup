/**
 * VIZ-507 — the status-flow Sankey (ECharts, lazy `sankey` engine). Two
 * columns, the earlier run's statuses and the later run's, joined by one flow
 * per (before, after) pair, width = tests. The regressions (passed → failed
 * or broken) are drawn in the failed colour at full strength; every other
 * flow takes its source status's colour, faint. A click on a flow (or on its
 * row in the frame's table) hands the pair to `onSelect`, which lists those
 * tests.
 *
 * No `formatter` of any kind: every label is one of this module's own words
 * (`nodeName`), never data, and still goes through `canvasSafeText`.
 */
import { useMemo } from 'react'
import { canvasSafeText } from './engines/echarts/canvasText'
import { useEChart } from './engines/useEChart'
import type { FlowStatus, StatusSankeyModel } from './statusSankeyModel'
import { useChartTokens, type ChartTokens } from './tokens'

export const SANKEY_HEIGHT = 320

function statusColor(status: FlowStatus, tokens: ChartTokens): string {
  return status === 'absent' ? tokens.textMuted : tokens.status[status]
}

/** The ECharts option (exported for tests: the colours and emphasis are the story). */
export function sankeyOption(model: StatusSankeyModel, tokens: ChartTokens): object {
  return {
    animation: false,
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'sankey',
        left: 8,
        right: 140,
        top: 8,
        bottom: 8,
        nodeWidth: 14,
        nodeGap: 10,
        layoutIterations: 0,
        draggable: false,
        emphasis: { focus: 'adjacency' },
        label: { color: tokens.text, fontSize: 12 },
        data: model.nodes.map((node) => ({
          name: canvasSafeText(node.name),
          value: node.total,
          itemStyle: { color: statusColor(node.status, tokens), borderColor: tokens.card },
          label: { position: node.side === 'before' ? 'right' : 'right' },
        })),
        links: model.links.map((link) => ({
          source: canvasSafeText(link.source),
          target: canvasSafeText(link.target),
          value: link.count,
          lineStyle: link.regression
            ? { color: tokens.status.failed, opacity: 0.85 }
            : { color: statusColor(link.before, tokens), opacity: 0.28 },
        })),
      },
    ],
  }
}

/** The pair a click landed on, from an ECharts sankey click (a link's `data`). */
export function clickedFlow(params: unknown, model: StatusSankeyModel): { before: FlowStatus; after: FlowStatus } | null {
  const p = params as { dataType?: string; dataIndex?: number } | null
  if (!p || p.dataType !== 'edge' || typeof p.dataIndex !== 'number') return null
  const link = model.links[p.dataIndex]
  return link ? { before: link.before, after: link.after } : null
}

export interface StatusSankeyProps {
  model: StatusSankeyModel
  onSelect?: (flow: { before: FlowStatus; after: FlowStatus }) => void
}

export default function StatusSankey({ model, onSelect }: StatusSankeyProps) {
  const tokens = useChartTokens()
  const option = useMemo(() => sankeyOption(model, tokens), [model, tokens])
  const events = useMemo(
    () => ({
      click: (params: unknown) => {
        const flow = clickedFlow(params, model)
        if (flow) onSelect?.(flow)
      },
    }),
    [model, onSelect],
  )
  const { containerRef, status, retry } = useEChart('sankey', option, onSelect ? events : undefined)
  return (
    <div className="relative min-w-0" style={{ height: SANKEY_HEIGHT }}>
      <div ref={containerRef} data-status-sankey="" className="h-full w-full" role="img" aria-label="Status flows between the two runs" />
      {status === 'error' ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-[var(--color-text-secondary)]">
          The chart could not load.
          <button type="button" onClick={retry} className="rounded border border-[var(--color-border)] px-2 py-1">
            Retry
          </button>
        </div>
      ) : null}
    </div>
  )
}
