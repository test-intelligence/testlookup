/**
 * "Release gate" in a release's detail (REL-01): the CURRENT recorded
 * verdict, the scorecard it was decided on, every blocking reason, and the
 * comparison with the release's baseline.
 *
 * Nothing here is computed in the browser. The verdict is the stored snapshot
 * (`GET /releases/{id}/gate`, 404 = never evaluated, a different truth from a
 * recorded NOT_EVALUATED); the baseline is the server's comparison, or its
 * reason a comparison would mislead. A QA lead records a new verdict with the
 * same Evaluate button the card has.
 *
 * `<section id="gate">`: a blocker's "Open gate →" link lands here
 * (`/releases/{id}#gate`), and the detail scrolls it into view.
 */
import type { ReactNode } from 'react'
import { useReleaseBaseline, useReleaseGate } from '@/hooks/useReleaseGate'
import { useReleases } from '@/hooks/useReleases'
import type { ReleaseBaselineComparison, ReleaseGateDetail } from '@/types/releases'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import GateBadge from './GateBadge'
import { mapVerdict } from './mapping'
import { percent, signed } from './gateCopy'

export interface GatePanelProps {
  releaseId: string
  /** Present => QA lead: the Evaluate button renders. */
  onEvaluate?: () => void
  evaluating?: boolean
}

/** The status-count order the scorecard prints; any other key follows. */
const STATUS_ORDER = ['PASSED', 'FAILED', 'BROKEN', 'SKIPPED', 'UNKNOWN']
const STATUS_LABEL: Record<string, string> = {
  PASSED: 'Passed', FAILED: 'Failed', BROKEN: 'Broken', SKIPPED: 'Skipped', UNKNOWN: 'Unknown',
}

function statusEntries(counts: Record<string, number>): Array<[string, number]> {
  const keys = Object.keys(counts ?? {})
  const ordered = [...STATUS_ORDER.filter(k => keys.includes(k)), ...keys.filter(k => !STATUS_ORDER.includes(k)).sort()]
  return ordered.map(k => [k, counts[k]])
}

function Tile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/40 px-2.5 py-2">
      <p className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
      <p className="text-lg font-bold tabular-nums mt-0.5 text-[var(--color-text)]">{value}</p>
      {sub && <p className="text-[10.5px] text-[var(--color-text-faint)]">{sub}</p>}
    </div>
  )
}

function Scorecard({ gate }: { gate: ReleaseGateDetail }) {
  const card = gate.scorecard
  const counts = statusEntries(card.status_counts)
  return (
    <div className="space-y-2" data-gate-scorecard="">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Pass rate" value={percent(card.pass_rate)} />
        <Tile label="Evidence" value={card.evidence_count} sub="tests with results" />
        <Tile label="In scope" value={card.denominator} sub="tests" />
        <Tile label="Runs" value={card.run_count} sub="in this verdict" />
      </div>
      {counts.length > 0 && (
        <p className="text-[11.5px] text-[var(--color-text-muted)] tabular-nums" data-gate-status-counts="">
          {counts.map(([k, n]) => `${STATUS_LABEL[k] ?? k} ${n}`).join(' · ')}
        </p>
      )}
      {!card.measured && card.insufficient_reason && (
        <p className="text-[11.5px] text-[var(--color-text-muted)]" data-gate-insufficient="">{card.insufficient_reason}</p>
      )}
    </div>
  )
}

function Blocking({ gate }: { gate: ReleaseGateDetail }) {
  const shown = gate.blocking_reasons_display?.length ? gate.blocking_reasons_display : gate.blocking_reasons
  if (!shown || shown.length === 0) return null
  const total = Math.max(gate.blocking_total ?? gate.blocking_reasons.length, shown.length)
  return (
    <div>
      <h4 className="text-xs font-semibold text-[var(--color-text)] mb-1">Blocking</h4>
      <ul aria-label="Blocking" className="max-h-64 overflow-y-auto space-y-0.5 text-[12px] text-[var(--color-text-secondary)]">
        {shown.map((reason, i) => (
          <li key={`${i}-${reason}`} className="break-words">{reason}</li>
        ))}
      </ul>
      {total > shown.length && (
        <p className="mt-1 text-[11.5px] text-[var(--color-text-muted)]" data-gate-truncated="">+{total - shown.length} more</p>
      )}
    </div>
  )
}

function Baseline({ baseline, baselineName, failed }: {
  baseline: ReleaseBaselineComparison | undefined
  baselineName: string
  failed: boolean
}) {
  let body: ReactNode
  if (failed) body = <p>The baseline comparison could not be loaded.</p>
  else if (!baseline) body = <p>Comparing with the baseline…</p>
  else if (!baseline.comparable) body = <p data-baseline-reason="">{baseline.reason}</p>
  else {
    body = (
      <ul className="space-y-0.5 tabular-nums" data-baseline-deltas="">
        <li>
          Pass rate {baseline.pass_rate_delta == null ? '—' : `${signed(baseline.pass_rate_delta, 1)} pp`} vs {baselineName}
        </li>
        <li>Tests in scope {signed(baseline.denominator_delta ?? 0)}</li>
        <li>Evidence {signed(baseline.evidence_delta ?? 0)}</li>
      </ul>
    )
  }
  return (
    <div>
      <h4 className="text-xs font-semibold text-[var(--color-text)] mb-1">Against the baseline</h4>
      <div className="text-[12px] text-[var(--color-text-secondary)]">{body}</div>
    </div>
  )
}

export default function GatePanel({ releaseId, onEvaluate, evaluating = false }: GatePanelProps) {
  const { data: gate, error, isLoading, mutate } = useReleaseGate(releaseId)
  const { data: baseline, error: baselineError } = useReleaseBaseline(releaseId)
  // The baseline's name from the list the page already holds: no extra
  // request (a release outside the cached list reads "the baseline").
  const { data: list } = useReleases(undefined, { cached: true })
  const baselineName = baseline?.baseline_release_id
    ? list?.items?.find(r => r.id === baseline.baseline_release_id)?.name ?? 'the baseline'
    : 'the baseline'

  const decision = gate ? mapVerdict(gate.verdict) : 'never_evaluated'
  const decidedAt = gate?.decided_at ? new Date(gate.decided_at) : null

  return (
    <section
      id="gate"
      aria-labelledby={`release-gate-title-${releaseId}`}
      data-gate-panel=""
      className="card scroll-mt-24 space-y-3"
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 id={`release-gate-title-${releaseId}`} className="text-sm font-semibold text-[var(--color-text)]">Release gate</h3>
          {!isLoading && !error && <GateBadge decision={decision} />}
          {decidedAt && (
            <time
              dateTime={gate?.decided_at ?? undefined}
              title={gate?.decided_at ?? undefined}
              className="text-[11.5px] text-[var(--color-text-muted)]"
            >
              Evaluated {decidedAt.toLocaleString()}
            </time>
          )}
        </div>
        {onEvaluate && (
          <button
            type="button"
            data-testid="gate-panel-evaluate"
            onClick={onEvaluate}
            disabled={evaluating}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
          >
            {evaluating ? 'Evaluating…' : gate ? 'Re-evaluate' : 'Evaluate gate'}
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-4"><LoadingSpinner size="sm" /></div>
      ) : error ? (
        <p className="text-[12.5px] text-[var(--status-failed)]">
          The release gate could not be loaded.{' '}
          <button type="button" onClick={() => void mutate()} className="text-[var(--color-accent)] hover:underline">Retry</button>
        </p>
      ) : !gate ? (
        <p className="text-[12.5px] text-[var(--color-text-muted)]">This release has not been evaluated yet.</p>
      ) : (
        <div className="space-y-3">
          <Scorecard gate={gate} />
          <Blocking gate={gate} />
        </div>
      )}

      <Baseline baseline={baseline} baselineName={baselineName} failed={Boolean(baselineError)} />
    </section>
  )
}
