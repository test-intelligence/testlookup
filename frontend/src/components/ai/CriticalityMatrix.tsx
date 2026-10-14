/**
 * CriticalityMatrix — reusable 7-dimension risk score visualisation.
 *
 * Fetches dimension descriptions from GET /api/v1/scoring-model (once,
 * cached by useScoringModel) to render a "Why this score?" expandable tooltip
 * per dimension without hardcoding any text in the component.
 *
 * GATE-05: the model is asked for the run's project, so it carries the weights
 * that project's release-gate policy applies. The stored dimension rows carry
 * the default weights (DIMENSION_METADATA) whatever the policy said; when the
 * scoped model names a policy, its weights are the ones shown and summed here,
 * which is the composite the verdict computed, and the card says which policy
 * they came from.
 *
 * Props:
 *   dimensionScores  — DimensionScore[] from release decision or cluster
 *   title            — optional section heading (default "Risk Dimension Scores")
 *   defaultExpanded  — whether the card starts expanded (default false)
 *   projectId        — the project whose policy weights apply (preferred)
 *   runId            — or the run, resolved to its project server-side; when
 *                      neither is passed, the route's `:runId` is used
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { ChevronDown, ChevronUp, HelpCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { useScoringModel } from '@/hooks/useRunIntelligence'
import type { DimensionScore, ScoringModel } from '@/services/runIntelligenceService'

interface Props {
  dimensionScores: DimensionScore[]
  title?: string
  defaultExpanded?: boolean
  projectId?: string | null
  runId?: string | null
}

/** Where the weights on this card come from, in words; null when unscoped. */
function weightSource(model: ScoringModel | undefined): string | null {
  const policy = model?.policy
  if (!policy) return null
  if (policy.level === 'hardcoded' || !policy.name) {
    return 'Weights: deployment defaults (no active release-gate policy)'
  }
  const version = policy.version != null ? ` v${policy.version}` : ''
  return policy.level === 'system'
    ? `Weights from the default policy ${policy.name}${version}`
    : `Weights from policy ${policy.name}${version}`
}

function scoreColour(score: number): string {
  if (score >= 70) return 'bg-[var(--status-failed-bg)]'
  if (score >= 40) return 'bg-[var(--status-broken-bg)]'
  return 'bg-[var(--status-passed-bg)]'
}

function scoreTextColour(score: number): string {
  if (score >= 70) return 'text-[var(--status-failed)]'
  if (score >= 40) return 'text-[var(--status-broken)]'
  return 'text-[var(--status-passed)]'
}

export default function CriticalityMatrix({
  dimensionScores,
  title = 'Risk Dimension Scores',
  defaultExpanded = false,
  projectId = null,
  runId = null,
}: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [openDim, setOpenDim] = useState<string | null>(null)
  const routeRunId = useParams<{ runId?: string }>().runId ?? null
  const { scoringModel, getDescription, isLoading: modelLoading } = useScoringModel(
    projectId ? { projectId } : { runId: runId ?? routeRunId },
  )

  if (dimensionScores.length === 0) return null

  // A policy-scoped model's weight wins over the row's stored default weight,
  // and the contributions are re-summed with it: that is the composite the
  // verdict computed (``compute_composite(dim_scores, weights=policy)``).
  const policyWeights = scoringModel?.policy
    ? new Map(scoringModel.dimensions.map((dim) => [dim.name, dim.weight]))
    : null
  const rows = dimensionScores.map((d) => {
    const weight = policyWeights?.get(d.name) ?? d.weight
    return { ...d, weight, contribution: policyWeights ? d.score * weight : d.contribution }
  })
  const source = weightSource(scoringModel)

  const composite = rows.reduce((sum, d) => sum + d.contribution, 0)

  return (
    <div className="card space-y-3">
      {/* Header — always visible */}
      <button
        className="w-full flex items-center justify-between text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <p className="text-sm font-medium text-[var(--color-text-secondary)]">{title}</p>
        <div className="flex items-center gap-2">
          {/* This is the WEIGHTED SUM OF THE DIMENSIONS BELOW, which is not
              always the verdict's risk score: a pass-rate hard floor can raise
              that score without touching any dimension. Rendered as a bare
              "17/100" beside a gauge reading 60, it read as the same number
              disagreeing with itself. Naming it says which of the two it is. */}
          <span
            className={clsx('text-sm font-bold', scoreTextColour(composite))}
            title="Weighted sum of the dimensions below. The verdict's risk score can differ when a floor is applied."
          >
            {composite.toFixed(0)}/100 weighted
          </span>
          {expanded
            ? <ChevronUp className="h-4 w-4 text-[var(--color-text-muted)]" />
            : <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)]" />}
        </div>
      </button>

      {expanded && (
        <div className="space-y-3">
          {source && (
            <p className="text-[11px] text-[var(--color-text-muted)]" data-testid="criticality-weight-source">
              {source}
            </p>
          )}
          {rows.map((d) => {
            const isOpen = openDim === d.name
            const description = getDescription(d.name)

            return (
              <div key={d.name} className="space-y-1">
                {/* Dimension row */}
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[var(--color-text-muted)]">{d.label}</span>
                    {/* "Why?" toggle — only shown once scoring model is loaded */}
                    {!modelLoading && description && (
                      <button
                        onClick={() => setOpenDim(isOpen ? null : d.name)}
                        className="text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors"
                        title="Why this score?"
                      >
                        <HelpCircle className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={clsx('font-mono font-medium', scoreTextColour(d.score))}>
                      {d.score.toFixed(0)}
                    </span>
                    <span className="text-[var(--color-text-faint)] text-[10px]" data-testid={`criticality-weight-${d.name}`}>
                      ×{(d.weight * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>

                {/* Score bar */}
                <div className="h-1.5 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                  <div
                    className={clsx('h-full rounded-full transition-all', scoreColour(d.score))}
                    style={{ width: `${d.score}%` }}
                  />
                </div>

                {/* "Why this score?" expandable description */}
                {isOpen && description && (
                  <div className="bg-[var(--color-bg-secondary)]/80 border border-[var(--color-border)] rounded-lg px-3 py-2 mt-1">
                    <p className="text-[11px] text-[var(--color-text-muted)] leading-relaxed">{description}</p>
                    {source && (
                      <p className="mt-1 text-[11px] text-[var(--color-text-faint)]" data-testid={`criticality-why-weight-${d.name}`}>
                        Weight {d.weight.toFixed(2)} · {source}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )
          })}

          {/* Composite footer */}
          <div className="flex items-center justify-between pt-2 border-t border-[var(--color-border)]">
            {/* "Composite risk score" claimed to BE the verdict's score. It is
                the weighted sum of these dimensions, which a pass-rate floor
                can override upward. */}
            <span className="text-xs text-[var(--color-text-muted)]">Weighted dimension total</span>
            <span className={clsx('text-sm font-bold', scoreTextColour(composite))}>
              {composite.toFixed(0)}/100
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
