/**
 * STRAT-03 — the "Create epic for this cluster" form, opened under the
 * cluster's row by ClusterEpicAction. Modelled on CreateJiraIssueModal:
 *
 *   preview first   the server assembles the epic (`GET .../epic/preview`) and
 *                   the form shows it before anything is filed: the summary and
 *                   Jira project key prefilled and editable, the description
 *                   and labels read-only
 *   Create          `POST .../epic` with `{jira_project_key, summary,
 *                   confirm_not_filed}`: the only write to Jira, made by the
 *                   person who clicks it
 *   409             `jira_outcome_unknown`: an earlier request may already have
 *                   filed it. Says so with the Jira label to search for, and
 *                   "I checked Jira, file it" retries with confirm_not_filed,
 *                   exactly as the defect form does
 *   errors          inline (the requests raise no global toast)
 *   success         the epic's link, in the form, and the project's status is
 *                   revalidated (`onCreated`) so the row shows "Epic <KEY>"
 *                   once the form is closed. `created: false` means a link was
 *                   already stored: nothing new was filed, and the form says so
 */
import { useState } from 'react'
import { useClusterEpicPreview } from '@/hooks/useJiraDefects'
import { extractErrorMessage } from '@/services/apiErrors'
import {
  clusterEpicService,
  type ClusterEpicCreateResult,
  type JiraOutcomeUnknownDetail,
} from '@/services/defectJiraService'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'

/** The POST body's limits (`ClusterEpicCreateRequest`): summary <= 255, Jira project key <= 50. */
export const SUMMARY_MAX = 255
export const JIRA_KEY_MAX = 50

type HttpError = { response?: { status?: number; data?: { detail?: unknown } }; message?: string }

/** The 409 an earlier request whose outcome is unknown produces, or null. */
function outcomeUnknownDetail(err: unknown): JiraOutcomeUnknownDetail | null {
  const response = (err as HttpError)?.response
  const detail = response?.data?.detail as Partial<JiraOutcomeUnknownDetail> | undefined
  if (response?.status !== 409 || !detail || detail.code !== 'jira_outcome_unknown') return null
  return detail as JiraOutcomeUnknownDetail
}

/** The server's own sentence for a refusal, else `fallback`. */
function errorText(err: unknown, fallback: string): string {
  const failure = err as HttpError
  return extractErrorMessage(failure?.response?.data?.detail, failure?.message || fallback)
}

const FIELD =
  'mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm text-[var(--color-text)]'
const CANCEL =
  'px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50'

export interface CreateClusterEpicFormProps {
  projectId: string
  membershipKey: string
  clusterLabel: string
  onClose: () => void
  /** After a successful POST (new or stored link): the host revalidates the project's epic status. */
  onCreated?: (result: ClusterEpicCreateResult) => void
}

export default function CreateClusterEpicForm({
  projectId,
  membershipKey,
  clusterLabel,
  onClose,
  onCreated,
}: CreateClusterEpicFormProps) {
  const { preview, isLoading, error: previewError } = useClusterEpicPreview(projectId, membershipKey)
  // Null = untouched: the field shows the preview's value (derived in render, no effect).
  const [keyChoice, setKeyChoice] = useState<string | null>(null)
  const [summaryChoice, setSummaryChoice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [outcomeUnknown, setOutcomeUnknown] = useState<JiraOutcomeUnknownDetail | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<ClusterEpicCreateResult | null>(null)

  const jiraProjectKey = keyChoice ?? preview?.jira_project_key ?? ''
  const summary = summaryChoice ?? preview?.summary ?? ''
  const canSubmit = Boolean(preview) && !submitting && result === null

  async function handleSubmit(confirmNotFiled = false) {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const filed = await clusterEpicService.create(projectId, membershipKey, {
        jira_project_key: jiraProjectKey.trim() || null,
        summary: summary.trim() || null,
        confirm_not_filed: confirmNotFiled,
      })
      setOutcomeUnknown(null)
      setResult(filed)
      onCreated?.(filed)
    } catch (err: unknown) {
      const unknownOutcome = outcomeUnknownDetail(err)
      if (unknownOutcome) setOutcomeUnknown(unknownOutcome)
      else setSubmitError(errorText(err, 'The epic could not be created.'))
    } finally {
      setSubmitting(false)
    }
  }

  const epicLink = (filed: ClusterEpicCreateResult) =>
    filed.jira_url ? (
      <a href={filed.jira_url} target="_blank" rel="noreferrer" className="font-medium underline">
        {filed.jira_key}
      </a>
    ) : (
      <strong>{filed.jira_key}</strong>
    )

  return (
    <InlineFormSection
      title="Create epic for this cluster"
      headingLevel={3}
      onClose={onClose}
      canClose={!submitting}
      showClose={false}
      busy={submitting}
      dataAttributes={{ 'data-cluster-epic-form': membershipKey }}
      description={<span className="[overflow-wrap:anywhere]">{clusterLabel}</span>}
      footer={
        result ? (
          <button type="button" onClick={onClose} className="btn-secondary btn-sm">
            Close
          </button>
        ) : (
          <>
            <button type="button" onClick={onClose} disabled={submitting} className={CANCEL}>
              Cancel
            </button>
            <button type="button" onClick={() => handleSubmit()} disabled={!canSubmit} className="btn-primary btn-sm">
              {submitting ? 'Creating…' : 'Create epic'}
            </button>
          </>
        )
      }
    >
      <div className="max-w-2xl text-sm">
        {result ? (
          <p role="status" data-cluster-epic-filed={result.jira_key} className="m-0 text-[var(--color-text)]">
            {result.created ? (
              <>Filed epic {epicLink(result)} in Jira.</>
            ) : (
              <>This cluster already has epic {epicLink(result)}; nothing new was filed.</>
            )}
          </p>
        ) : isLoading ? (
          <p className="m-0 text-[var(--color-text-muted)]">Preparing the epic…</p>
        ) : previewError || !preview ? (
          <p role="alert" data-cluster-epic-error="preview" className="m-0" style={{ color: 'var(--ui-danger)' }}>
            {errorText(previewError, 'The epic could not be prepared.')}
          </p>
        ) : (
          <>
            <p className="m-0 text-xs text-[var(--color-text-secondary)]">
              {preview.member_count} tests · failed together in {preview.co_failure_runs} runs. Nothing is filed until
              you click Create epic.
            </p>
            <div className="mt-3 grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-2">
              <label className="block text-xs font-medium text-[var(--color-text)]">
                Jira project key
                <input
                  type="text"
                  value={jiraProjectKey}
                  maxLength={JIRA_KEY_MAX}
                  onChange={(event) => setKeyChoice(event.target.value)}
                  disabled={submitting}
                  placeholder="Default"
                  className={FIELD}
                />
              </label>
              <label className="block text-xs font-medium text-[var(--color-text)]">
                Summary
                <input
                  type="text"
                  value={summary}
                  maxLength={SUMMARY_MAX}
                  onChange={(event) => setSummaryChoice(event.target.value)}
                  disabled={submitting}
                  className={FIELD}
                />
              </label>
            </div>
            <div className="mt-3">
              <span className="mb-1 block text-xs font-medium text-[var(--color-text)]">
                Description <span className="font-normal text-[var(--color-text-faint)]">(prefilled)</span>
              </span>
              <pre
                aria-label="Prefilled description"
                className="m-0 max-h-44 overflow-y-auto whitespace-pre-wrap rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 font-mono text-2xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]"
              >
                {preview.description_text}
              </pre>
            </div>
            {preview.labels.length > 0 && (
              <p className="mb-0 mt-2 text-xs text-[var(--color-text-secondary)]">
                Labels:{' '}
                {preview.labels.map((label, index) => (
                  <span key={label}>
                    {index > 0 && ', '}
                    <code className="font-mono">{label}</code>
                  </span>
                ))}
              </p>
            )}
          </>
        )}

        {submitError && (
          <p role="alert" data-cluster-epic-error="create" className="mb-0 mt-3" style={{ color: 'var(--ui-danger)' }}>
            {submitError}
          </p>
        )}

        {outcomeUnknown && (
          <div
            role="alert"
            className="mt-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2.5 text-[var(--color-text)]"
          >
            <p className="m-0 font-medium">Jira may already have this epic</p>
            <p className="mb-0 mt-1 text-[var(--color-text-muted)]">{outcomeUnknown.message}</p>
            <p className="mb-0 mt-1.5">
              Label: <code className="select-all font-mono text-xs">{outcomeUnknown.jira_label}</code>
            </p>
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                onClick={() => handleSubmit(true)}
                disabled={!canSubmit}
                className="rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs font-medium disabled:opacity-50"
              >
                I checked Jira, file it
              </button>
            </div>
          </div>
        )}
      </div>
    </InlineFormSection>
  )
}
