/**
 * W14 AGT-07: "Cost & time" — the MEASURED estimate of a deep run (`GET /deep-investigate/{run}/estimate`): the
 * p50 of the project's latest completed deep investigations, priced only when a price is known.
 *
 * Absence is not a zero: with no completed deep investigation there are no minutes (no `~0.5 min` from a
 * formula), and an unpriced cloud model is "—" with the server's sentence, never `$0.00`. A local model's
 * `$0.00` IS a measured answer ("local model — no charge"). Then the four slowest stages, the last analysis's
 * actual cost (the decision trail) and the project's real spend against its budget.
 */
import MetricCard from '@/components/ui/MetricCard'
import type { DeepEstimate } from '@/types/agentOps'
import {
  ESTIMATE_LOAD_ERROR, costLine, minutesText, sampleCaption, slowestStages, stageLine,
} from './deepRunModel'

export interface DeepEstimateCardProps {
  estimate: DeepEstimate | undefined
  error: unknown
  onRetry: () => void
  /** The focused run's last analysis's actual cost (decision trail); null when none was costed. */
  lastRunCostDollars: number | null
  spendMtdDollars: number
  /** The monthly hard cap; null when no budget is set. */
  spendBudgetDollars: number | null
  spendSoftWarnPct: number | null
}

const PILL = {
  background: 'var(--color-bg-secondary)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text-secondary)',
}

export default function DeepEstimateCard({
  estimate, error, onRetry, lastRunCostDollars, spendMtdDollars, spendBudgetDollars, spendSoftWarnPct,
}: DeepEstimateCardProps) {
  const cost = estimate ? costLine(estimate.cost) : null
  const minutes = estimate ? minutesText(estimate) : null
  const caption = !estimate
    ? (error ? ESTIMATE_LOAD_ERROR : 'estimating…')
    : [cost?.unknown ? 'cost unknown' : null, sampleCaption(estimate)].filter(Boolean).join(' · ')
  const stages = estimate?.measured ? slowestStages(estimate.stages) : []
  const budget = spendBudgetDollars
  const usedPct = budget && budget > 0 ? Math.min(100, (spendMtdDollars / budget) * 100) : 0
  return (
    <div className="flex min-w-0 flex-col gap-2.5" data-deep-estimate="">
      <MetricCard
        title="Cost & time"
        metric={{ value: cost?.value ?? '—' }}
        caption={caption}
        sparkline={minutes ? (
          <span data-estimate-minutes="" className="inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-semibold tabular-nums" style={PILL}>
            {minutes}
          </span>
        ) : undefined}
      />
      {!estimate && error != null && (
        <button type="button" className="btn-secondary self-start text-xs" onClick={onRetry}>Retry</button>
      )}
      {cost?.note && <p data-cost-note="" className="m-0 text-xs text-[var(--color-text-secondary)]">{cost.note}</p>}

      {stages.length > 0 && (
        <div className="flex flex-col gap-1.5 text-xs" data-estimate-stages="">
          {stages.map((stage) => {
            const line = stageLine(stage)
            return (
              <div
                key={stage.stage_name}
                className="flex items-baseline justify-between gap-3"
                title={stage.per_failure ? 'p50 per failure, scaled by the failures this run would analyse' : undefined}
              >
                <span className="min-w-0 truncate text-[var(--color-text-secondary)]">{line.name}</span>
                <span className="whitespace-nowrap tabular-nums text-[var(--color-text-muted)]">{line.detail}</span>
              </div>
            )
          })}
        </div>
      )}
      {lastRunCostDollars != null && (
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span className="text-[var(--color-text-secondary)]">last run</span>
          <span className="tabular-nums text-[var(--color-text-muted)]">${lastRunCostDollars.toFixed(2)}</span>
        </div>
      )}

      {/* Budget bar — the real spend against the configured monthly quota. */}
      {budget != null && budget > 0 ? (
        <div className="mt-1">
          <div className="relative h-1.5 overflow-hidden rounded-full" style={{ background: 'var(--color-bg-secondary)' }}>
            <i className="block h-full" style={{ width: `${usedPct}%`, background: 'var(--color-accent)' }} />
            {spendSoftWarnPct != null && spendSoftWarnPct > 0 && spendSoftWarnPct < 100 && (
              <span
                aria-hidden
                className="absolute top-0 bottom-0"
                style={{ left: `${spendSoftWarnPct}%`, width: 1, background: 'var(--ui-warning)' }}
                title={`Soft warn at ${spendSoftWarnPct}%`}
              />
            )}
          </div>
          <div className="mt-1.5 flex justify-between text-2xs text-[var(--color-text-muted)]">
            <span>${spendMtdDollars.toFixed(2)} of ${budget} monthly</span>
            {spendSoftWarnPct != null && <span>warn at {spendSoftWarnPct}%</span>}
          </div>
        </div>
      ) : (
        <div className="mt-1 text-2xs text-[var(--color-text-muted)]">
          ${spendMtdDollars.toFixed(2)} spent this month · no budget set — configure in Settings → Billing
        </div>
      )}
    </div>
  )
}
