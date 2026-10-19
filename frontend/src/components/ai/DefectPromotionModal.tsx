/**
 * File defect: promote a failure cluster to a defect (DEF-06). It renders IN THE
 * PAGE (W27a PAN-03): the run's Analysis tab opens it inside the failure block
 * it was filed from, as an `InlineFormSection` named "Promote to Defect" (the
 * old dialog's name and heading id) — no backdrop, no focus trap. Focus moves
 * to its first field when it opens, Escape inside closes it (not mid-submit),
 * and a `pending_review` answer keeps it open with its result.
 */
import { useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Clock,
  ExternalLink,
} from 'lucide-react'
import { clsx } from 'clsx'
import { Link } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { SEVERITY_STYLE, severityLevel } from '@/components/ui/severity'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useDefectCandidate, usePromoteCluster } from '@/hooks/useDefectPromotion'
import { usePermissions } from '@/hooks/usePermissions'
import type { DefectPromotionRequest } from '@/types/defect-promotion'
import { isSafeExternalUrl } from '@/utils/safeUrl'

/** Inbox › Approvals, narrowed to the defects queue (the Inbox route is `/my-failures`). */
const DEFECT_APPROVALS_URL = '/my-failures?tab=approvals&source=defects'

const SEVERITY_OPTIONS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const
type Severity = (typeof SEVERITY_OPTIONS)[number]

/** The chosen option's look, from the one severity map (W29a UIX-07): CRITICAL P0 danger, HIGH P1 warning,
 *  MEDIUM P2 info, LOW P3 neutral (MEDIUM and LOW are no longer orange and passed-green). Constant tokens. */
function severityOptionStyle(opt: Severity): { color: string; borderColor: string; background: string } | undefined {
  const level = severityLevel(opt)
  if (!level) return undefined
  const style = SEVERITY_STYLE[level]
  return { color: style.fg, borderColor: style.bd, background: style.bg }
}

interface DefectPromotionModalProps {
  runId: string
  clusterId: string
  clusterLabel: string
  onClose: () => void
  /**
   * After a promotion is stored. `approvalStatus` is the server's: a host keeps
   * the modal open on `pending_review`, so its result (awaiting approval, and
   * where a QA lead approves it) is read, not closed away.
   */
  onSuccess?: (defectId: string, jiraUrl?: string | null, approvalStatus?: string | null) => void
  /** The host section's one-form slot (W27a): typed input asks before another form replaces it. */
  slot?: InlineSlot
}

export default function DefectPromotionModal({
  runId,
  clusterId,
  clusterLabel,
  onClose,
  onSuccess,
  slot,
}: DefectPromotionModalProps) {
  const { candidate, isLoading, isError } = useDefectCandidate(runId, clusterId)
  const { promote, isPromoting, promotionResult, promotionError, reset } =
    usePromoteCluster()
  const { isQaLead } = usePermissions()

  // Seeded from the candidate when it is already here (SWR keeps it per
  // cluster): File defect -> Cancel -> File defect again shows the candidate
  // again. It used to start empty, because the cached candidate never
  // "changed" below.
  const [title, setTitle] = useState(() => candidate?.title ?? '')
  const [severity, setSeverity] = useState<Severity>(() => (candidate?.severity as Severity | undefined) ?? 'HIGH')
  const [component, setComponent] = useState(() => candidate?.component ?? '')
  const [ownerTeam, setOwnerTeam] = useState(() => candidate?.owner_team ?? '')
  const [labelsInput, setLabelsInput] = useState(() => candidate?.labels.join(', ') ?? '')
  const [description, setDescription] = useState(() => candidate?.description ?? '')
  const [projectKey, setProjectKey] = useState('')
  const [showEvidence, setShowEvidence] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  // Populate form from candidate when it loads (or changes). Seeded during
  // render via previous-value tracking rather than a setState-in-effect;
  // re-seeds whenever the candidate object changes, matching the prior effect.
  const [prevCandidate, setPrevCandidate] = useState(candidate)
  if (candidate !== prevCandidate) {
    setPrevCandidate(candidate)
    if (candidate) {
      setTitle(candidate.title)
      setSeverity((candidate.severity as Severity) ?? 'HIGH')
      setComponent(candidate.component)
      setOwnerTeam(candidate.owner_team)
      setLabelsInput(candidate.labels.join(', '))
      setDescription(candidate.description)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return

    const request: DefectPromotionRequest = {
      title: title.trim(),
      severity,
      component: component.trim(),
      owner_team: ownerTeam.trim(),
      labels: labelsInput
        .split(',')
        .map(l => l.trim())
        .filter(Boolean),
      description: description.trim(),
      project_key: projectKey.trim() || undefined,
    }

    const result = await promote(runId, clusterId, request)
    if (result) {
      setSubmitted(true)
      // Stored: nothing typed is left to lose if another form replaces this one.
      slot?.setDirty(false)
      onSuccess?.(result.defect_id, result.jira_url, result.approval_status)
    }
  }

  const handleClose = () => {
    reset()
    onClose()
  }

  const footer = submitted ? (
    <button
      onClick={handleClose}
      className="px-5 py-2 bg-[var(--color-bg-hover)] hover:bg-[var(--color-bg-card)] text-[var(--color-text)] text-sm font-medium rounded-lg transition-colors"
    >
      Close
    </button>
  ) : (
    <>
      <button
        type="button"
        onClick={handleClose}
        className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        Cancel
      </button>
      <button
        type="submit"
        onClick={handleSubmit}
        disabled={isPromoting || !title.trim()}
        className="btn-primary flex items-center gap-2"
      >
        {isPromoting && <LoadingSpinner size="sm" />}
        {isPromoting ? 'Promoting…' : 'Promote to Defect'}
      </button>
    </>
  )

  return (
    <InlineFormSection
      title="Promote to Defect"
      titleId="defect-promotion-title"
      description={<span className="block truncate text-xs">{clusterLabel}</span>}
      onClose={handleClose}
      canClose={!isPromoting}
      closeLabel="Close"
      busy={isPromoting}
      slot={slot}
      footer={footer}
    >
      <div className="max-w-2xl">
        {isLoading && (
          <div className="flex justify-center items-center py-10">
            <LoadingSpinner size="md" />
            <span className="ml-3 text-sm text-[var(--color-text-muted)]">
              Assembling defect candidate…
            </span>
          </div>
        )}

        {isError && (
          <div className="rounded-xl bg-[var(--status-failed-bg)]/20 border border-[var(--status-failed-bd)]/40 p-4 text-sm text-[var(--status-failed)]">
            Could not load defect candidate. You can still fill in the form manually.
          </div>
        )}

        {submitted && promotionResult && (
          <div className={clsx(
            'rounded-xl p-4 space-y-2',
            promotionResult.approval_status === 'pending_review'
              ? 'bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/40'
              : 'bg-[var(--status-passed-bg)]/20 border border-[var(--status-passed-bd)]/40',
          )}>
            <p className={clsx(
              'text-sm font-semibold',
              promotionResult.approval_status === 'pending_review'
                ? 'text-[var(--status-broken)]'
                : 'text-[var(--status-passed)]',
            )}>
              {promotionResult.approval_status === 'pending_review'
                ? 'Defect created — awaiting approval'
                : 'Defect promoted successfully'}
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              Defect ID: <span className="font-mono">{promotionResult.defect_id}</span>
            </p>
            {promotionResult.approval_status === 'pending_review' && (
              <div className="flex items-start gap-2 mt-2">
                <Clock className="h-4 w-4 text-[var(--status-broken)] shrink-0 mt-0.5" />
                <div>
                  <p className="text-xs text-[var(--status-broken)]">
                    This defect requires QA Lead approval before Jira ticket creation.
                  </p>
                  {promotionResult.policy_reasons && promotionResult.policy_reasons.length > 0 && (
                    <ul className="mt-1 space-y-0.5">
                      {promotionResult.policy_reasons.map((reason, i) => (
                        <li key={i} className="text-xs text-[var(--color-text-muted)]">
                          &bull; {reason}
                        </li>
                      ))}
                    </ul>
                  )}
                  {/* W06 DEF-06: where the approval happens. Only a QA lead can open that queue. */}
                  {isQaLead ? (
                    <Link
                      to={DEFECT_APPROVALS_URL}
                      onClick={handleClose}
                      data-defect-approvals-link=""
                      className="mt-1.5 inline-block text-xs text-[var(--color-accent)] hover:underline"
                    >
                      Review it in Inbox › Approvals
                    </Link>
                  ) : (
                    <p className="mt-1.5 text-xs text-[var(--color-text-muted)]" data-defect-approvals-note="">
                      A QA lead approves it in Inbox › Approvals.
                    </p>
                  )}
                </div>
              </div>
            )}
            {promotionResult.duplicate_detected && (
              <p className="text-xs text-[var(--status-broken)]">
                Possible duplicate detected — defect ID:{' '}
                <span className="font-mono">{promotionResult.duplicate_defect_id}</span>
              </p>
            )}
            {isSafeExternalUrl(promotionResult.jira_url) && (
              <a
                href={promotionResult.jira_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
              >
                <ExternalLink className="h-3 w-3" />
                View Jira ticket
              </a>
            )}
          </div>
        )}

        {!submitted && (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Duplicate warning */}
            {candidate?.duplicate_detected && (
              <div className="rounded-xl bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/40 p-3 flex items-start gap-2">
                <AlertTriangle className="h-4 w-4 text-[var(--status-broken)] shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm text-[var(--status-broken)] font-medium">
                    Possible duplicate detected
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                    A similar open defect may already exist (ID:{' '}
                    <span className="font-mono">
                      {candidate.duplicate_defect_id?.slice(0, 8)}…
                    </span>
                    ). Review before submitting.
                  </p>
                </div>
              </div>
            )}

            {/* Title */}
            <div>
              <label htmlFor="defect-field-0" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                Title <span className="text-[var(--status-failed)]">*</span>
              </label>
              <input id="defect-field-0"
                type="text"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Concise defect title…"
                required
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors"
              />
            </div>

            {/* Severity */}
            <div>
              <span id="defect-promotion-severity" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                Severity
              </span>
              <div role="group" aria-labelledby="defect-promotion-severity" className="flex gap-2 flex-wrap">
                {SEVERITY_OPTIONS.map(opt => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setSeverity(opt)}
                    className={clsx(
                      'px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors',
                      severity !== opt && 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]',
                    )}
                    style={severity === opt ? severityOptionStyle(opt) : undefined}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            </div>

            {/* Component + Owner Team */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="defect-field-1" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                  Component
                </label>
                <input id="defect-field-1"
                  type="text"
                  value={component}
                  onChange={e => setComponent(e.target.value)}
                  placeholder="e.g. auth-service"
                  className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors"
                />
              </div>
              <div>
                <label htmlFor="defect-field-2" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                  Owner Team
                </label>
                <input id="defect-field-2"
                  type="text"
                  value={ownerTeam}
                  onChange={e => setOwnerTeam(e.target.value)}
                  placeholder="e.g. payments-backend"
                  className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors"
                />
              </div>
            </div>

            {/* Labels */}
            <div>
              <label htmlFor="defect-field-3" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                Labels{' '}
                <span className="text-[var(--color-text-faint)] font-normal">(comma-separated)</span>
              </label>
              <input id="defect-field-3"
                type="text"
                value={labelsInput}
                onChange={e => setLabelsInput(e.target.value)}
                placeholder="regression, automated-test, cluster-promoted"
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors"
              />
            </div>

            {/* Description */}
            <div>
              <label htmlFor="defect-field-4" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                Description
              </label>
              <textarea id="defect-field-4"
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={4}
                placeholder="What failed / Steps to reproduce / Expected / Actual / Environment…"
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors resize-none"
              />
            </div>

            {/* Jira project key (optional) */}
            <div>
              <label htmlFor="defect-field-5" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                Jira Project Key{' '}
                <span className="text-[var(--color-text-faint)] font-normal">(optional — leave blank for local draft)</span>
              </label>
              <input id="defect-field-5"
                type="text"
                value={projectKey}
                onChange={e => setProjectKey(e.target.value.toUpperCase())}
                placeholder="e.g. QA"
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] transition-colors"
              />
            </div>

            {/* Evidence preview (collapsible) */}
            {candidate?.evidence_bundle && (
              <div className="border border-[var(--color-border)] rounded-xl overflow-hidden">
                <button
                  type="button"
                  onClick={() => setShowEvidence(v => !v)}
                  className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors bg-[var(--color-bg-secondary)]/60"
                >
                  <span className="font-medium">
                    Evidence Preview
                  </span>
                  {showEvidence ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </button>
                {showEvidence && (
                  <div className="p-4 space-y-3 bg-[var(--color-bg-card)]/40">
                    {candidate.evidence_bundle.data_sources.length > 0 && (
                      <div>
                        <p className="text-xs text-[var(--color-text-muted)] mb-1.5">Data sources</p>
                        <div className="flex flex-wrap gap-1.5">
                          {candidate.evidence_bundle.data_sources.map(src => (
                            <span
                              key={src}
                              className="text-xs bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border border-[var(--color-border-light)] px-2 py-0.5 rounded"
                            >
                              {src}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {candidate.evidence_bundle.stack_traces.length > 0 && (
                      <div>
                        <p className="text-xs text-[var(--color-text-muted)] mb-1.5">Stack traces</p>
                        <ul className="space-y-1">
                          {candidate.evidence_bundle.stack_traces
                            .slice(0, 3)
                            .map((t, i) => (
                              <li
                                key={i}
                                className="text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]/80 rounded px-2 py-1 truncate"
                              >
                                {t}
                              </li>
                            ))}
                        </ul>
                      </div>
                    )}
                    {candidate.evidence_bundle.log_anomalies.length > 0 && (
                      <div>
                        <p className="text-xs text-[var(--color-text-muted)] mb-1.5">Log anomalies</p>
                        <ul className="space-y-1">
                          {candidate.evidence_bundle.log_anomalies
                            .slice(0, 3)
                            .map((a, i) => (
                              <li key={i} className="text-xs text-[var(--status-broken)] flex gap-1.5">
                                <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5" />
                                {a}
                              </li>
                            ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Criticality score preview */}
            {candidate && candidate.composite_score > 0 && (
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <span>Criticality score:</span>
                <span
                  className={clsx(
                    'font-semibold',
                    candidate.composite_score >= 70
                      ? 'text-[var(--status-failed)]'
                      : candidate.composite_score >= 50
                        ? 'text-[var(--status-broken)]'
                        : candidate.composite_score >= 30
                          ? 'text-[var(--status-broken)]'
                          : 'text-[var(--status-passed)]',
                  )}
                >
                  {candidate.composite_score.toFixed(1)}/100
                </span>
                <span>·</span>
                <span>{candidate.member_count} test{candidate.member_count !== 1 ? 's' : ''} in cluster</span>
              </div>
            )}

            {promotionError && (
              <div className="rounded-lg bg-[var(--status-failed-bg)]/20 border border-[var(--status-failed-bd)]/40 px-3 py-2 text-sm text-[var(--status-failed)]">
                {promotionError}
              </div>
            )}
          </form>
        )}
      </div>
    </InlineFormSection>
  )
}
