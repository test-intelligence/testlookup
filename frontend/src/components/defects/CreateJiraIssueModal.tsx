/**
 * Create-Jira-issue dialog — PMF US-6.1 / US-6.3.
 *
 * One-click defect creation from a failure signature (test fingerprint on
 * /failures, quarantine row on /quarantine). Follows the US-2.4 modal
 * pattern (MuteTestModal / CorrectClassificationModal on the Failure
 * Analysis page).
 *
 * The payload is assembled SERVER-side (preview endpoint) and shown here
 * read-only — the user reviews before create, never silent. Dedup is also
 * server-side: when an open defect is already linked for the signature the
 * dialog says so up front, and submitting adds a recurrence note to the
 * existing issue instead of filing a duplicate.
 *
 * Delivery targets:
 *   - "jira"    — direct Jira REST create (disabled with a tooltip when the
 *                 metadata probe reports offline mode / unconfigured Jira).
 *   - "webhook" — emits the ``defect.create_requested`` outbound-webhook
 *                 event carrying the same payload (US-6.3 fallback).
 */
import { useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { useJiraDefectMetadata, useJiraDefectPreview } from '@/hooks/useJiraDefects'
import { defectJiraService } from '@/services/defectJiraService'

const REASON_COPY: Record<string, string> = {
  offline_mode: 'AI_OFFLINE_MODE is on — outbound Jira calls are blocked.',
  disabled: 'The Jira integration is disabled in Settings → Integrations.',
  not_configured: 'Jira credentials are not configured in Settings → Integrations.',
  unreachable: 'Jira could not be reached — check the domain and credentials.',
}

export function jiraUnavailableCopy(reason: string | null | undefined): string {
  if (!reason) return 'Jira is unavailable.'
  return REASON_COPY[reason] ?? `Jira is unavailable (${reason}).`
}

export default function CreateJiraIssueModal({
  projectId,
  fingerprint,
  clusterId,
  testName,
  onClose,
  onCreated,
}: {
  projectId: string
  /** Exactly one of fingerprint / clusterId identifies the failure. */
  fingerprint?: string | null
  clusterId?: string | null
  testName: string
  onClose: () => void
  /** Invoked after a successful create/link so callers can refresh lists. */
  onCreated?: () => void
}) {
  const { metadata, isLoading: metaLoading } = useJiraDefectMetadata(projectId)
  const { preview, isLoading: previewLoading, isError: previewError } =
    useJiraDefectPreview(projectId, fingerprint ?? null, clusterId ?? null)

  const jiraAvailable = metadata?.available ?? false
  const webhookAvailable = metadata?.webhook_available ?? false

  // User selections are nullable; the effective values derive defaults from
  // metadata during render (no setState-in-effect): prefer Jira, fall back
  // to the webhook target when Jira is gated but a receiver is subscribed.
  const [targetChoice, setTargetChoice] = useState<'jira' | 'webhook' | null>(null)
  const [projectKeyChoice, setProjectKeyChoice] = useState<string | null>(null)
  const [issueTypeChoice, setIssueTypeChoice] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const target: 'jira' | 'webhook' =
    targetChoice ?? (metadata && !metadata.available && metadata.webhook_available ? 'webhook' : 'jira')
  const projectKey = projectKeyChoice ?? metadata?.default_project_key ?? ''
  const issueType =
    issueTypeChoice ??
    (metadata && metadata.issue_types.length > 0 && !metadata.issue_types.includes('Bug')
      ? metadata.issue_types[0]
      : 'Bug')

  const existing = preview?.existing_defect ?? null
  const canSubmit = useMemo(() => {
    if (submitting || !preview) return false
    if (target === 'jira') return jiraAvailable
    return webhookAvailable
  }, [submitting, preview, target, jiraAvailable, webhookAvailable])

  async function handleSubmit() {
    if (!canSubmit || !preview) return
    setSubmitting(true)
    try {
      const result = await defectJiraService.create(projectId, {
        ...(fingerprint ? { fingerprint } : {}),
        ...(clusterId && !fingerprint ? { cluster_id: clusterId } : {}),
        issue_type: issueType,
        ...(target === 'jira' && projectKey ? { jira_project_key: projectKey } : {}),
        ...(comment.trim() ? { extra_comment: comment.trim() } : {}),
        target,
      })
      if (result.target === 'webhook') {
        toast.success(
          result.subscriptions_notified
            ? `defect.create_requested sent to ${result.subscriptions_notified} webhook subscription${result.subscriptions_notified === 1 ? '' : 's'}.`
            : result.message,
          { duration: 8000 },
        )
      } else if (result.deduplicated) {
        toast.success(
          <span>
            Linked to existing{' '}
            {result.jira_url ? (
              <a href={result.jira_url} target="_blank" rel="noreferrer" className="underline font-medium">
                {result.jira_key}
              </a>
            ) : (
              <strong>{result.jira_key}</strong>
            )}{' '}
            (recurrence noted).
          </span>,
          { duration: 8000 },
        )
      } else {
        toast.success(
          <span>
            Created{' '}
            {result.jira_url ? (
              <a href={result.jira_url} target="_blank" rel="noreferrer" className="underline font-medium">
                {result.jira_key}
              </a>
            ) : (
              <strong>{result.jira_key}</strong>
            )}
            .
          </span>,
          { duration: 8000 },
        )
      }
      onCreated?.()
      onClose()
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to create the Jira issue'
      toast.error(detail, { duration: 8000 })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create Jira issue"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] p-5 shadow-xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-[var(--color-text)] m-0">
          Create Jira issue
        </h2>
        <p className="mt-1 text-[12.5px] text-[var(--color-text-muted)]">
          <code className="font-mono text-[11.5px]">{testName}</code>
        </p>

        {previewLoading || metaLoading ? (
          <p className="mt-4 text-[12.5px] text-[var(--color-text-muted)]">
            Assembling the pre-filled payload…
          </p>
        ) : previewError || !preview ? (
          <p className="mt-4 text-[12.5px]" style={{ color: 'var(--status-failed)' }}>
            Could not assemble the defect payload — the failure may not have
            per-test rows yet. Try again in a moment.
          </p>
        ) : (
          <>
            {existing && (
              <div
                className="mt-3 rounded-md border px-3 py-2 text-[12px]"
                style={{
                  background: 'color-mix(in srgb, var(--color-accent) 6%, transparent)',
                  borderColor: 'color-mix(in srgb, var(--color-accent) 25%, transparent)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                An open defect is already linked to{' '}
                {existing.jira_url ? (
                  <a href={existing.jira_url} target="_blank" rel="noreferrer" className="underline font-medium">
                    {existing.jira_key}
                  </a>
                ) : (
                  <strong>{existing.jira_key}</strong>
                )}
                {existing.external_status ? <> ({existing.external_status})</> : null}. Submitting adds a
                recurrence note to it instead of filing a duplicate.
              </div>
            )}

            {/* Delivery target */}
            <div className="mt-3" role="radiogroup" aria-label="Delivery target">
              <span className="block text-[12px] font-medium text-[var(--color-text)] mb-1.5">Deliver via</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  role="radio"
                  aria-checked={target === 'jira'}
                  disabled={!jiraAvailable || submitting}
                  title={jiraAvailable ? 'Create the issue directly in Jira' : jiraUnavailableCopy(metadata?.reason)}
                  onClick={() => setTargetChoice('jira')}
                  className={clsx(
                    'px-3 py-1.5 text-[12.5px] rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
                    target === 'jira'
                      ? 'border-[var(--color-accent)] bg-[var(--color-bg-hover)]/40 text-[var(--color-text)]'
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)]',
                  )}
                >
                  Jira
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={target === 'webhook'}
                  disabled={!webhookAvailable || submitting}
                  title={
                    webhookAvailable
                      ? 'Emit defect.create_requested to your webhook receiver instead of calling Jira'
                      : 'No enabled webhook subscription listens for defect.create_requested in this project.'
                  }
                  onClick={() => setTargetChoice('webhook')}
                  className={clsx(
                    'px-3 py-1.5 text-[12.5px] rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
                    target === 'webhook'
                      ? 'border-[var(--color-accent)] bg-[var(--color-bg-hover)]/40 text-[var(--color-text)]'
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)]',
                  )}
                >
                  Webhook event
                </button>
              </div>
              {!jiraAvailable && (
                <p className="mt-1.5 text-[11.5px] text-[var(--color-text-muted)]">
                  {jiraUnavailableCopy(metadata?.reason)}
                </p>
              )}
            </div>

            {/* Jira pickers */}
            {target === 'jira' && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className="block text-[12px] font-medium text-[var(--color-text)]">
                  Jira project
                  <select
                    value={projectKey}
                    onChange={(e) => setProjectKeyChoice(e.target.value)}
                    disabled={submitting}
                    className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[12.5px] text-[var(--color-text)]"
                  >
                    {(metadata?.projects.length ? metadata.projects : [{ key: projectKey || 'QA', name: null }]).map((p) => (
                      <option key={p.key} value={p.key}>
                        {p.key}
                        {p.name ? ` — ${p.name}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-[12px] font-medium text-[var(--color-text)]">
                  Issue type
                  <select
                    value={issueType}
                    onChange={(e) => setIssueTypeChoice(e.target.value)}
                    disabled={submitting}
                    className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[12.5px] text-[var(--color-text)]"
                  >
                    {(metadata?.issue_types.length ? metadata.issue_types : ['Bug']).map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}

            {/* Read-only preview */}
            <div className="mt-3">
              <span className="block text-[12px] font-medium text-[var(--color-text)] mb-1">
                Summary <span className="text-[var(--color-text-faint)] font-normal">(prefilled)</span>
              </span>
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] font-mono text-[var(--color-text-secondary)]">
                {preview.summary}
              </div>
            </div>
            <div className="mt-2">
              <span className="block text-[12px] font-medium text-[var(--color-text)] mb-1">
                Description <span className="text-[var(--color-text-faint)] font-normal">(prefilled — failure, history, context{preview.ai_analysis ? ', AI root cause' : ''})</span>
              </span>
              <pre
                aria-label="Prefilled description"
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[11px] font-mono text-[var(--color-text-muted)] whitespace-pre-wrap m-0 max-h-44 overflow-y-auto"
              >
                {preview.description}
              </pre>
            </div>

            <label className="block mt-3 text-[12px] font-medium text-[var(--color-text)]">
              Comment <span className="text-[var(--color-text-faint)] font-normal">(optional — appended to the ticket)</span>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                rows={2}
                disabled={submitting}
                placeholder="Anything the assignee should know?"
                className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
              />
            </label>
          </>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            title={
              !preview
                ? 'Waiting for the prefilled payload'
                : target === 'jira' && !jiraAvailable
                  ? jiraUnavailableCopy(metadata?.reason)
                  : undefined
            }
            className="text-[12.5px] font-medium rounded-md px-3 py-1.5 disabled:opacity-50"
            style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
          >
            {submitting
              ? 'Submitting…'
              : existing && target === 'jira'
                ? 'Note recurrence'
                : target === 'webhook'
                  ? 'Emit webhook event'
                  : 'Create issue'}
          </button>
        </div>
      </div>
    </div>
  )
}
