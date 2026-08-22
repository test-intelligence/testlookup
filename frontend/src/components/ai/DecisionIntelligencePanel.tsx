import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck, ShieldX } from 'lucide-react'
import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type {
  DecisionIntelligence,
  DecisionReportAttempt,
  DecisionReportVerification,
  DecisionReportVersion,
  DecisionClaim,
  ProposedAction,
} from '@/services/runIntelligenceService'
import { deriveDecisionTrustState } from './decisionTrustState'
import { readAnalysisCoverage, describeAnalysisCoverage } from './analysisCoverage'
import DecisionReportFeedbackControls from './DecisionReportFeedbackControls'

interface Props {
  runId: string
  report: DecisionIntelligence | null | undefined
  latestVerification: DecisionReportVerification | null | undefined
  latestAttempt: DecisionReportAttempt | null | undefined
  reportVersion?: DecisionReportVersion | null
  reportVersions?: DecisionReportVersion[]
  selectedReportVersion?: number | null
  onSelectReportVersion?: (version: number | null) => void
}

const metricLabels: Array<[string, string]> = [
  ['total_tests', 'Total tests'],
  ['passed_tests', 'Passed'],
  ['failed_tests', 'Failed'],
  ['broken_tests', 'Broken'],
  ['pass_rate', 'Pass rate'],
  ['failure_cluster_count', 'Clusters'],
  ['flaky_finding_count', 'Flaky findings'],
  ['test_health_finding_count', 'Health findings'],
]

function text(value: unknown, fallback = 'Unavailable'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function displayMetric(key: string, value: number): string {
  return key === 'pass_rate' ? `${value.toFixed(1)}%` : value.toLocaleString()
}

function formatTime(value: string | undefined): string {
  if (!value) return 'Unknown time'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleString()
}

function ClaimsSection({ claims, actions, onInspect }: { claims: DecisionClaim[]; actions: ProposedAction[]; onInspect: (claim: DecisionClaim) => void }) {
  const groups: Array<[DecisionClaim['kind'], string]> = [
    ['fact', 'Facts'],
    ['inference', 'Inferences'],
    ['unknown', 'Unknowns'],
    ['recommendation', 'Recommendations'],
  ]
  return (
    <div className="mt-3 grid gap-3 lg:grid-cols-[2fr_1fr]">
      <div className="grid gap-3 sm:grid-cols-2">
        {groups.map(([kind, label]) => {
          const items = claims.filter(item => item.kind === kind)
          return (
            <div key={kind} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
              <h4 className="m-0 text-[12px] font-semibold">{label} · {items.length}</h4>
              {items.length === 0 ? <p className="mb-0 mt-2 text-[12px] text-[var(--color-text-muted)]">None recorded</p> : (
                <ul className="mt-2 mb-0 list-none space-y-2 p-0 text-[12px]">
                  {items.slice(0, 8).map(item => (
                    <li key={item.claim_id}>
                      <p className="m-0 text-[var(--color-text)]">{item.text}</p>
                      <span className="text-[11px] text-[var(--color-text-muted)]">Confidence {Math.round(item.confidence * 100)}% · {item.confidence_basis}</span>
                      <button type="button" className="mt-1 block text-[11px] font-semibold text-[var(--color-accent)] hover:underline" onClick={() => onInspect(item)} aria-label={`Inspect evidence for ${item.claim_id}`}>
                        Inspect evidence
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
        <h4 className="m-0 text-[12px] font-semibold">Proposed actions · {actions.length}</h4>
        {actions.length === 0 ? <p className="mb-0 mt-2 text-[12px] text-[var(--color-text-muted)]">No actions proposed</p> : (
          <ul className="mt-2 mb-0 list-none space-y-2 p-0 text-[12px]">
            {actions.slice(0, 8).map(action => (
              <li key={action.idempotency_key}>
                <p className="m-0 font-semibold">{action.title}</p>
                <p className="m-0 text-[var(--color-text-secondary)]">{action.rationale}</p>
                <span className="text-[11px] text-[var(--color-text-muted)]">Owner {action.owner} · {action.required_permission} · {action.risk} risk</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function EvidenceDrawer({ claim, reportHash, reportVersion, runId, onClose }: { claim: DecisionClaim; reportHash: string; reportVersion?: DecisionReportVersion | null; runId: string; onClose: () => void }) {
  const renderEvidence = (items: Array<Record<string, unknown>>, label: string) => (
    <section className="mt-3">
      <h4 className="m-0 text-[12px] font-semibold">{label} · {items.length}</h4>
      {items.length === 0 ? <p className="mb-0 mt-2 text-[12px] text-[var(--color-text-muted)]">None recorded</p> : (
        <ul className="mt-2 mb-0 list-none space-y-2 p-0 text-[12px]">
          {items.slice(0, 10).map((item, index) => (
            <li key={`${String(item.id ?? item.evidence_id ?? 'evidence')}-${index}`} className="rounded border border-[var(--color-border)] p-2">
              <strong>{text(item.source, text(item.type, 'Evidence'))}</strong>
              <span className="ml-2 text-[var(--color-text-muted)]">{text(item.kind, text(item.definition_version, 'reference'))}</span>
              {typeof item.excerpt === 'string' && item.excerpt && <p className="mb-0 mt-1 whitespace-pre-wrap">{item.excerpt}</p>}
              <span className="mt-1 block font-mono text-[10px] text-[var(--color-text-muted)]">{text(item.id, text(item.evidence_id, 'unidentified'))}</span>
              {reportVersion && item.type === 'artifact' && <Link className="mt-1 inline-block text-[11px] font-semibold text-[var(--color-accent)] hover:underline" to={`/deep-investigate/${runId}?report_id=${encodeURIComponent(reportVersion.report_id)}&report_version=${reportVersion.report_version}&test_case_id=${encodeURIComponent(String((item.scope as Record<string, unknown> | undefined)?.test_case_id ?? ''))}`}>Open source context</Link>}
              {reportVersion && item.type === 'metric' && <Link className="mt-1 inline-block text-[11px] font-semibold text-[var(--color-accent)] hover:underline" to={`/runs/${runId}/intelligence?report_id=${encodeURIComponent(reportVersion.report_id)}&report_version=${reportVersion.report_version}#decision-metrics`}>Open metric definition</Link>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="claim-evidence-heading" className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 shadow-xl" onClick={event => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="m-0 text-[11px] uppercase tracking-wider text-[var(--color-text-muted)]">Claim evidence</p>
            <h3 id="claim-evidence-heading" className="mt-1 mb-0 text-[16px] font-semibold">{claim.text}</h3>
            <p className="mb-0 mt-1 text-[11px] text-[var(--color-text-muted)]">Source stage: {claim.source_stage} · Confidence {Math.round(claim.confidence * 100)}% · Report {reportHash.slice(0, 12)}…</p>
          </div>
          <button type="button" aria-label="Close evidence" className="rounded border border-[var(--color-border)] px-2 py-1 text-[12px]" onClick={onClose}>Close</button>
        </div>
        <p className="mt-3 mb-0 text-[12px] text-[var(--color-text-secondary)]"><strong>Confidence basis:</strong> {claim.confidence_basis}</p>
        {renderEvidence(claim.evidence, 'Evidence')}
        {renderEvidence(claim.counter_evidence, 'Counter-evidence')}
      </div>
    </div>
  )
}
function EvidenceList({ report }: { report: DecisionIntelligence }) {
  const clusters = report.failure_clusters.slice(0, 5)
  const flaky = report.flaky_findings.slice(0, 5)
  const health = report.test_health_findings.slice(0, 5)
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <EvidenceGroup title="Failure clusters" count={report.failure_clusters.length}>
        {clusters.map((item, index) => (
          <li key={text(item.cluster_id, `cluster-${index}`)}>
            <strong>{text(item.label, text(item.cluster_id, `Cluster ${index + 1}`))}</strong>
            <span>{number(item.size) ?? (Array.isArray(item.member_test_ids) ? item.member_test_ids.length : 0)} tests</span>
          </li>
        ))}
      </EvidenceGroup>
      <EvidenceGroup title="Flaky tests" count={report.flaky_findings.length}>
        {flaky.map((item, index) => (
          <li key={text(item.test_case_id, `flaky-${index}`)}>
            <strong>{text(item.test_name, text(item.test_case_id))}</strong>
            <span>{text(item.recommendation, 'Review history')}</span>
          </li>
        ))}
      </EvidenceGroup>
      <EvidenceGroup title="Test health" count={report.test_health_findings.length}>
        {health.map((item, index) => (
          <li key={text(item.test_case_id, `health-${index}`)}>
            <strong>{text(item.test_name, text(item.test_case_id))}</strong>
            <span>Health {number(item.health_score) ?? '—'} · {text(item.recommendation, 'Review')}</span>
          </li>
        ))}
      </EvidenceGroup>
    </div>
  )
}

function EvidenceGroup({ title, count, children }: {
  title: string
  count: number
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
      <h4 className="m-0 text-[12px] font-semibold text-[var(--color-text)]">{title} · {count}</h4>
      {count === 0 ? (
        <p className="mb-0 text-[12px] text-[var(--color-text-muted)]">No findings</p>
      ) : (
        <ul className="mt-2 mb-0 space-y-2 p-0 list-none text-[12px]">
          {children}
        </ul>
      )}
    </div>
  )
}

export default function DecisionIntelligencePanel({
  runId,
  report,
  latestVerification,
  latestAttempt,
  reportVersion,
  reportVersions = [],
  selectedReportVersion,
  onSelectReportVersion,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [selectedClaim, setSelectedClaim] = useState<DecisionClaim | null>(null)
  const detailsId = useId()
  const headingId = useId()
  if (!report && !latestAttempt && !latestVerification) return null
  const { state, displayReport } = deriveDecisionTrustState(report, latestVerification, latestAttempt)
  const verified = state === 'verified' || state === 'verified_gaps'
    || state === 'verified_degraded'
  const coverage = readAnalysisCoverage(displayReport?.quality_review?.gap_report)
  const coverageNote = describeAnalysisCoverage(coverage)
  const stale = state === 'rejected_stale'
  const release = report?.release_decision
  const risk = number(release?.risk_score) ?? number(release?.composite_risk)
  const failedChecks = (latestVerification?.checks ?? []).filter(item => item.status === 'fail')
  const reportChecks = displayReport?.verification.checks ?? []
  const reportPassedChecks = reportChecks.filter(item => item.status === 'pass').length
  const reportFailedChecks = reportChecks.filter(item => item.status === 'fail').length

  return (
    <section
      aria-labelledby={headingId}
      className="mb-3.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Agentic decision report
          </p>
          <h2 id={headingId} className="mt-1 mb-0 text-[18px] font-semibold">
            Decision intelligence
          </h2>
          {reportVersions.length > 0 && onSelectReportVersion && (
            <label className="mt-2 flex items-center gap-2 text-[11px] text-[var(--color-text-muted)]">
              <span>Report version</span>
              <select
                aria-label="Decision report version"
                className="rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 text-[11px] text-[var(--color-text)]"
                value={selectedReportVersion ?? reportVersion?.report_version ?? reportVersions[0]?.report_version ?? ''}
                onChange={(event) => {
                  const value = Number(event.target.value)
                  onSelectReportVersion(Number.isInteger(value) && value > 0 ? value : null)
                }}
              >
                {reportVersions.map((version) => (
                  <option key={version.report_id} value={version.report_version}>
                    v{version.report_version} · {formatTime(version.generated_at)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <span role={verified ? 'status' : undefined} className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold">
          {verified ? <ShieldCheck aria-hidden className="h-4 w-4 text-[var(--status-passed)]" />
            : state === 'pending' ? <Clock3 aria-hidden className="h-4 w-4 text-[var(--color-text-muted)]" />
            : <ShieldX aria-hidden className="h-4 w-4 text-[var(--status-failed)]" />}
          {state === 'verified' ? 'Verified'
            : state === 'verified_gaps'
              ? (coverage && coverage.integrityOk
                  ? `Verified — ${coverage.uncoveredCount} of ${coverage.failedCount} failures not analysed`
                  : 'Verified — coverage audit did not reconcile')
            : state === 'verified_degraded' ? 'Verified with missing specialist evidence'
            : state === 'rejected_stale' ? 'Latest attempt rejected'
            : state === 'pending' ? 'Verification pending'
            : state === 'inconsistent' ? 'Verification unavailable'
            : 'No verified report'}
        </span>
      </div>

      {(state === 'rejected_stale' || state === 'rejected_empty') && (
        <div role="alert" className="mt-3 flex gap-2 rounded-md border border-[var(--alert-border-soft)] bg-[var(--alert-bg-soft)] p-3 text-[13px]">
          <AlertTriangle aria-hidden className="h-4 w-4 flex-none text-[var(--status-failed)]" />
          <div>
            <strong>{stale ? 'Showing the last verified report.' : 'No verified decision report is available.'}</strong>{' '}
            The latest attempt was rejected by terminal verification
            {latestAttempt ? ` at ${formatTime(latestAttempt.at)}` : ''}.
            {(latestVerification?.unresolved_failures?.length ?? 0) > 0 && (
              <span> Failed checks: {latestVerification?.unresolved_failures?.join(', ')}.</span>
            )}
          </div>
        </div>
      )}

      {state === 'pending' && (
        <p className="mt-3 mb-0 text-[13px] text-[var(--color-text-secondary)]">
          Terminal verification is still pending. No decision-ready recommendation is shown.
        </p>
      )}

      {state === 'inconsistent' && (
        <p className="mt-3 mb-0 text-[13px] text-[var(--status-failed)]">
          Verification metadata is missing or inconsistent. This report is not decision-ready.
        </p>
      )}

      {!displayReport ? null : (
        <>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--color-text-muted)]">
            <span>Report generated <time dateTime={displayReport.generated_at}>{formatTime(displayReport.generated_at)}</time></span>
            {latestAttempt?.at && <span>Latest attempt <time dateTime={latestAttempt.at}>{formatTime(latestAttempt.at)}</time></span>}
            <span className="font-mono">Evidence {displayReport.evidence_bundle_sha256.slice(0, 12)}…</span>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_2fr]">
            <div className="rounded-lg border border-[var(--color-border)] p-3">
              <p className="m-0 text-[11px] uppercase tracking-wider text-[var(--color-text-muted)]">Release policy</p>
              <div className="mt-1 flex items-end gap-2">
                <strong className="text-[22px]">{text(release?.recommendation, 'Unavailable')}</strong>
                <span className="pb-0.5 text-[12px] text-[var(--color-text-muted)]">{risk === null ? 'Risk unavailable' : `Risk ${Math.round(risk)}/100`}</span>
              </div>
              {release?.reasoning && <p className="mb-0 text-[12px] text-[var(--color-text-secondary)]">{release.reasoning}</p>}
            </div>
            <dl className="m-0 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {metricLabels.map(([key, label]) => {
                const value = number(displayReport.metrics[key])
                return (
                  <div key={key} className="rounded-lg border border-[var(--color-border)] p-2.5">
                    <dt className="text-[11px] text-[var(--color-text-muted)]">{label}</dt>
                    <dd className="m-0 mt-1 text-[16px] font-semibold">{value === null ? '—' : displayMetric(key, value)}</dd>
                  </div>
                )
              })}
            </dl>
          </div>

          <div className="mt-3">
            <EvidenceList report={displayReport} />
      <ClaimsSection claims={displayReport.claims ?? []} actions={displayReport.proposed_actions ?? []} onInspect={setSelectedClaim} />
            <DecisionReportFeedbackControls runId={runId} reportVersion={verified ? reportVersion : null} claims={displayReport.claims ?? []} />
            {selectedClaim && <EvidenceDrawer claim={selectedClaim} reportHash={displayReport.evidence_bundle_sha256} reportVersion={reportVersion} runId={runId} onClose={() => setSelectedClaim(null)} />}
          </div>

          {(displayReport.quality_review.requires_human_review
            || coverage?.hasAnalysisGaps
            || displayReport.quality_review.missing_or_failed_specialists.length > 0
            || displayReport.quality_review.contradictions.length > 0
            || (displayReport.quality_review.data_quality_flags?.length ?? 0) > 0) && (
            <div className="mt-3 rounded-md border border-[var(--alert-border-soft)] bg-[var(--alert-bg-soft)] p-3 text-[12px]">
              <strong>Human review required.</strong>{' '}
              {coverage?.hasAnalysisGaps && coverageNote && (
                <span>{coverageNote} </span>
              )}
              {displayReport.quality_review.missing_or_failed_specialists.length > 0 && (
                <span>Missing specialists: {displayReport.quality_review.missing_or_failed_specialists.join(', ')}. </span>
              )}
              {displayReport.quality_review.contradictions.length > 0 && (
                <span>{displayReport.quality_review.contradictions.length} evidence contradiction(s) recorded. </span>
              )}
              {(displayReport.quality_review.data_quality_flags?.length ?? 0) > 0 && (
                <span>
                  Data quality: {displayReport.quality_review.data_quality_flags
                    ?.map(item => item.code)
                    .join(', ')}.
                </span>
              )}
            </div>
          )}

          <div className="mt-3 rounded-md border border-[var(--color-border)] px-3 py-2 text-[12px]">
            <button type="button" className="font-semibold" aria-expanded={expanded} aria-controls={detailsId} onClick={() => setExpanded(value => !value)}>
              Verification and provenance
            </button>
            {expanded && <div id={detailsId} className="mt-2 grid gap-2 sm:grid-cols-2">
              <p className="m-0"><CheckCircle2 aria-hidden className="mr-1 inline h-3.5 w-3.5 text-[var(--status-passed)]" />Report checks: {reportPassedChecks} passed, {reportFailedChecks} failed</p>
              <p className="m-0">Repairs: {displayReport.verification.repairs?.length ? displayReport.verification.repairs.join(', ') : 'None'}</p>
              <p className="m-0">Source agents: {displayReport.source_stages.join(', ') || 'None'}</p>
              <p className="m-0">Latest failed checks: {failedChecks.length ? failedChecks.map(item => item.name).join(', ') : 'None'}</p>
              {displayReport.verification.report_evaluation && (
                <p className="m-0">
                  Report quality gate: {displayReport.verification.report_evaluation.status.toUpperCase()}
                  {displayReport.verification.report_evaluation.unavailable_metrics?.length
                    ? ` (${displayReport.verification.report_evaluation.unavailable_metrics.join(', ')} not evaluated)`
                    : ''}
                </p>
              )}
            </div>
            }
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-[12px]">
            <Link className="text-[var(--color-accent)] hover:underline" to={`/deep-investigate/${runId}`}>Inspect evidence</Link>
            <Link className="text-[var(--color-accent)] hover:underline" to={`/release-gate/${runId}`}>Review policy and overrides</Link>
          </div>
        </>
      )}
    </section>
  )
}
