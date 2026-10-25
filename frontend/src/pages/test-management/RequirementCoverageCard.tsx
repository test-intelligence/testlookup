/**
 * W13 TCL-06 — Insights' sixth card, **Requirement coverage** (only with the
 * traceability flag ON: the host mounts it when the health answer carries
 * `requirements`). Every number is the server's (`RequirementCoverageSummary`):
 * `Requirements verified` = "12 of 15" (BOTH numbers, never a lone
 * percentage), the partly-verified count, the first five gaps with why and
 * where each key is known from.
 *
 * Opening Insights sends nothing: **Show all gaps** is the only read here
 * (`useTraceabilityGaps(projectId, page, true)` once opened), paged by 25, with
 * the approved / active cases that link no requirement behind **Show**.
 *
 * Keys, titles and case titles are React TEXT.
 */
import { useState } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import MetricCard from '@/components/ui/MetricCard'
import { Panel } from '@/components/ui/PanelHeader'
import { useTraceabilityGaps } from '@/hooks/useTestLibrary'
import type { RequirementCoverageSummary, RequirementGap } from '@/types/test-library'
import {
  COVERAGE_TITLE,
  GAPS_ERROR,
  GAPS_LOADING,
  GAPS_PAGE_SIZE,
  HIDE_ALL_GAPS,
  SHOW_ALL_GAPS,
  TRUNCATED_TEXT,
  UNMEASURED_TEXT,
  VERIFIED_LABEL,
  casesWithoutLine,
  gapReason,
  gapsPageLine,
  partlyLine,
  sourcesLine,
  verifiedValue,
} from './requirementsModel'

export interface RequirementCoverageCardProps {
  projectId: string
  summary: RequirementCoverageSummary
  /** Filter the case list by this (normalised) key. */
  onFilterRequirement: (key: string) => void
  onOpenCase: (caseId: string) => void
}

const KEY_BUTTON = 'font-mono text-xs text-[var(--color-accent-ink)] hover:underline [overflow-wrap:anywhere]'
const LINK_BUTTON = 'text-xs text-[var(--color-accent-ink)] hover:underline disabled:opacity-50 disabled:no-underline'

function GapRow({ gap, onFilterRequirement }: { gap: RequirementGap; onFilterRequirement: (key: string) => void }) {
  const known = sourcesLine(gap)
  return (
    <li data-requirement-gap={gap.normalized_key} data-reason={gap.reason} className="text-sm text-[var(--color-text-secondary)]">
      <p className="m-0 [overflow-wrap:anywhere]">
        <button type="button" className={KEY_BUTTON} onClick={() => onFilterRequirement(gap.normalized_key)}>
          {gap.key}
        </button>
        {' · '}
        {gapReason(gap)}
      </p>
      {known && <p className="m-0 text-xs text-[var(--color-text-muted)]">{known}</p>}
    </li>
  )
}

function AllGaps({ projectId, onFilterRequirement, onOpenCase }: Omit<RequirementCoverageCardProps, 'summary'>) {
  const [page, setPage] = useState(1)
  const [showCases, setShowCases] = useState(false)
  const { data, error, isLoading, mutate } = useTraceabilityGaps(projectId, page, true)
  if (isLoading && !data) {
    return (
      <div role="status" className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
        <LoadingSpinner size="sm" />
        {GAPS_LOADING}
      </div>
    )
  }
  if (error && !data) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-[var(--ui-danger-ink)]">
        <span>{GAPS_ERROR}</span>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
      </div>
    )
  }
  if (!data) return null
  const lastPage = Math.max(1, Math.ceil(data.gaps_total / GAPS_PAGE_SIZE))
  return (
    <div data-requirement-gaps-all="" className="space-y-3">
      <ul className="m-0 list-none space-y-1.5 p-0">
        {data.gaps.map((gap) => (
          <GapRow key={gap.normalized_key} gap={gap} onFilterRequirement={onFilterRequirement} />
        ))}
      </ul>
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span data-requirement-gaps-page="">{gapsPageLine(page, GAPS_PAGE_SIZE, data.gaps.length, data.gaps_total)}</span>
        <button type="button" className={LINK_BUTTON} disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <button type="button" className={LINK_BUTTON} disabled={page >= lastPage} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      <div className="space-y-1">
        <p className="m-0 flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          <span data-cases-without-requirements="">{casesWithoutLine(data.cases_without_requirements_total)}</span>
          {data.cases_without_requirements_total > 0 && (
            <button type="button" className={LINK_BUTTON} aria-expanded={showCases} onClick={() => setShowCases(!showCases)}>
              {showCases ? 'Hide' : 'Show'}
            </button>
          )}
        </p>
        {showCases && (
          <ul className="m-0 list-none space-y-1 p-0" data-cases-without-requirements-list="">
            {data.cases_without_requirements.map((row) => (
              <li key={row.id} className="text-sm">
                <button type="button" className="text-left text-[var(--color-accent-ink)] hover:underline [overflow-wrap:anywhere]" onClick={() => onOpenCase(row.id)}>
                  {row.title}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function RequirementCoverageCard({ projectId, summary, onFilterRequirement, onOpenCase }: RequirementCoverageCardProps) {
  const [showAll, setShowAll] = useState(false)
  const topGaps = summary.top_gaps ?? []
  return (
    <Panel title={COVERAGE_TITLE} data-testid="tm-requirement-coverage">
      <div className="space-y-3 px-4 py-3.5">
        {!summary.measured ? (
          <p data-requirements-unmeasured="" className="m-0 text-sm text-[var(--color-text-secondary)]">{summary.reason || UNMEASURED_TEXT}</p>
        ) : (
          <>
            <MetricCard
              size="sm"
              title={VERIFIED_LABEL}
              metric={{ value: verifiedValue(summary) }}
              caption={partlyLine(summary)}
              data-requirements-verified=""
            />
            {summary.truncated && <p role="note" className="m-0 text-xs text-[var(--color-text-muted)]">{TRUNCATED_TEXT}</p>}
            {topGaps.length > 0 && !showAll && (
              <ul className="m-0 list-none space-y-1.5 p-0" data-requirement-top-gaps="">
                {topGaps.map((gap) => (
                  <GapRow key={gap.normalized_key} gap={gap} onFilterRequirement={onFilterRequirement} />
                ))}
              </ul>
            )}
            <button type="button" className={LINK_BUTTON} aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
              {showAll ? HIDE_ALL_GAPS : SHOW_ALL_GAPS}
            </button>
            {showAll && <AllGaps projectId={projectId} onFilterRequirement={onFilterRequirement} onOpenCase={onOpenCase} />}
          </>
        )}
      </div>
    </Panel>
  )
}
