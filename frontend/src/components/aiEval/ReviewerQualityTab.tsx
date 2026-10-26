/**
 * The Reviewer tab (W16 AGT-09 a): the project's persisted G3 reviewer-quality history, READ-ONLY.
 *
 * The page never POSTs `/ai-eval/reviewer-quality`: that route takes observation batches with `auto_disable=True` by
 * default and can switch a project's second-model reviewer check OFF (§0.23) — an empty hand-built batch must not
 * be able to retire it. The history comes from the nightly measurement (`GET …/reviewer-quality/runs`, no
 * observation arrays). All Projects → the shared project prompt; no per-project read at all.
 */
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import MetricCard from '@/components/ui/MetricCard'
import { Panel } from '@/components/ui/PanelHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { useReviewerQualityRuns } from '@/hooks/useAiEval'
import type { ReviewerQualityRun } from '@/types/aiEval'
import { formatDay } from '@/utils/formatters'
import { CHIP_CLASS, COPY, REVIEWER_CARDS, TONE_CLASS, percent, pointsText, verdictChip } from './aiEvalModel'

const SOURCE_LABEL: Record<string, { label: string; title: string }> = {
  scheduled: { label: 'Nightly', title: 'Scheduled (nightly)' },
  observation_batch: { label: 'Batch', title: 'Observation batch' },
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function semanticTotal(row: ReviewerQualityRun): number {
  const counts = row.semantic_sample_counts && typeof row.semantic_sample_counts === 'object' ? row.semantic_sample_counts : {}
  return Object.values(counts).reduce<number>((sum, n) => sum + (num(n) ?? 0), 0)
}

/** The newest row's metric with its 95 % Wilson bounds (the row's direct field when the list has none). */
function Card({ row, spec }: { row: ReviewerQualityRun; spec: (typeof REVIEWER_CARDS)[number] }) {
  const metrics = Array.isArray(row.metrics) ? row.metrics : []
  const metric = metrics.find((m) => m && m.name === spec.name) as Record<string, unknown> | undefined
  const value = num(metric?.value) ?? num(row[spec.field])
  const low = num(metric?.ci_low)
  const high = num(metric?.ci_high)
  const n = num(metric?.n)
  const caption =
    value === null
      ? COPY.notMeasured
      : low !== null && high !== null
        ? `95%: ${percent(low)}–${percent(high)}${n !== null ? ` · n ${n}` : ''}`
        : n !== null ? `n ${n}` : undefined
  return <MetricCard size="sm" title={spec.title} metric={{ value: percent(value) }} caption={caption} data-reviewer-metric={spec.name} />
}

function HistoryTable({ rows }: { rows: ReviewerQualityRun[] }) {
  return (
    <div className="overflow-x-auto" data-table-scroller="reviewer-history">
      <Table density="compact" className="table-fixed text-xs" aria-label="Reviewer-quality history">
        <colgroup>
          <col style={{ width: 104 }} />
          <col style={{ width: 66 }} />
          <col style={{ width: 156 }} />
          <col style={{ width: 82 }} />
          <col style={{ width: 106 }} />
          <col style={{ width: 74 }} />
          <col style={{ width: 90 }} />
        </colgroup>
        <thead>
          <tr>
            <Th>Evaluated</Th>
            <Th>Source</Th>
            <Th>Verdict</Th>
            <Th numeric>Recall Δ</Th>
            <Th numeric>False flags</Th>
            <Th numeric>Samples</Th>
            <Th>2nd model</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const source = SOURCE_LABEL[row.source] ?? { label: row.source, title: row.source }
            const verdict = verdictChip(row.status)
            const semantic = semanticTotal(row)
            return (
              <Tr key={row.id} interactive={false} data-reviewer-row={row.id}>
                <Td className="truncate">{formatDay(row.evaluated_at)}</Td>
                <Td className="truncate" title={source.title}>{source.label}</Td>
                <Td><span data-verdict={row.status} className={clsx(CHIP_CLASS, TONE_CLASS[verdict.tone])}>{verdict.label}</span></Td>
                <Td numeric>{pointsText(row.recall_delta)}</Td>
                <Td numeric>{percent(row.false_flag_rate)}</Td>
                <Td numeric title={`${semantic} semantic · ${row.clean_sample_count} clean · ${row.human_outcome_count} human outcomes`}>
                  {semantic}
                </Td>
                <Td title={row.auto_disable_eligible ? 'Eligible for retirement' : undefined}>
                  {row.auto_disable_applied ? 'Retired' : 'Kept'}
                </Td>
              </Tr>
            )
          })}
        </tbody>
      </Table>
    </div>
  )
}

export default function ReviewerQualityTab({ projectId }: { projectId: string | null }) {
  const { data, error, isLoading, mutate } = useReviewerQualityRuns(projectId)
  if (!projectId) return <ProjectRequiredEmptyState description={COPY.reviewerProjectRequired} />
  const rows = Array.isArray(data?.items) ? data.items : []
  const newest = rows[0]
  return (
    <div className="space-y-4" data-testid="ai-eval-reviewer-tab">
      <Panel title="Reviewer quality (G3)" subtitle={COPY.reviewerReadOnly}>
        {isLoading ? (
          <div className="flex justify-center p-4"><LoadingSpinner size="sm" /></div>
        ) : error ? (
          <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">
            {COPY.loadError.reviewer}{' '}
            <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
          </p>
        ) : !newest ? (
          <p className="m-0 p-4 text-xs text-[var(--color-text-muted)]">{COPY.reviewerEmpty}</p>
        ) : (
          <>
            {/* 2 × 2, not 4 across: in the 732 px settings column the four ~160 px cards cut their titles
                ("DETERMINISTIC RE…") and their 95 % lines on CI's DejaVu. */}
            <div className="grid grid-cols-2 gap-3 p-4" data-reviewer-cards="">
              {REVIEWER_CARDS.map((spec) => <Card key={spec.name} row={newest} spec={spec} />)}
            </div>
            <div className="border-t border-[var(--color-border)]">
              <HistoryTable rows={rows} />
            </div>
          </>
        )}
      </Panel>
    </div>
  )
}
