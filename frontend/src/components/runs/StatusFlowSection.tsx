/**
 * VIZ-507 — "Status changes": the run-compare status flows (a Sankey). It
 * reads no flag (Phase D, S5: drawn on every run compare). The frame's "View
 * as table" lists every transition with its count (the story's table
 * fallback); clicking a flow filters the page's per-test table to those tests.
 */
import { useCallback, useMemo, type ReactElement } from 'react'
import ChartFrame from '@/components/charts/ChartFrame'
import { readyState, type ChartState } from '@/components/charts/chartStateCore'
import StatusSankey from '@/components/charts/StatusSankey'
import {
  buildStatusSankey,
  classificationFor,
  sankeyTakeaway,
  type FlowStatus,
  type StatusTransition,
} from '@/components/charts/statusSankeyModel'
import type { RunCompareClassification } from '@/services/runCompareService'

export const STATUS_FLOW_TITLE = 'Status changes'
export const NO_FLOWS_REASON =
  'neither run has per-test results to compare, so there are no status flows to draw'

export interface StatusFlowSectionProps {
  transitions: readonly StatusTransition[] | undefined
  /** Filter the per-test table to a flow's tests (`null` = the flow's tests did not change). */
  onSelectClassification: (classification: RunCompareClassification | null, label: string) => void
}

export default function StatusFlowSection({ transitions, onSelectClassification }: StatusFlowSectionProps): ReactElement {
  const model = useMemo(() => buildStatusSankey(transitions), [transitions])
  const select = useCallback(
    ({ before, after }: { before: FlowStatus; after: FlowStatus }) => {
      const label = `${before} → ${after}`
      onSelectClassification(classificationFor(before, after), label)
    },
    [onSelectClassification],
  )
  const state: ChartState<unknown> = model
    ? readyState(model.matrix)
    : { status: 'not-measured', reason: NO_FLOWS_REASON, meta: null }
  return (
    <section data-status-flows="" aria-label={STATUS_FLOW_TITLE} className="min-w-0">
      <ChartFrame
        title={STATUS_FLOW_TITLE}
        takeaway={model ? sankeyTakeaway(model) : undefined}
        headingLevel={2}
        state={state}
        series={model?.matrix ?? null}
        chartType="Sankey"
        scopeLabel={model ? `${model.beforeTotal} tests in the earlier run, ${model.afterTotal} in the later` : undefined}
      >
        {model ? <StatusSankey model={model} onSelect={select} /> : null}
      </ChartFrame>
    </section>
  )
}
