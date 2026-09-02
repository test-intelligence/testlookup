/**
 * S5 — build a deletion criteria set, review what it selects, then execute it.
 *
 * The two-step shape is the safety property, not a UX flourish. Preview
 * FREEZES a candidate set server-side and returns a `job_id`; execute replays
 * that frozen set. Re-resolving at execute time would delete a different set
 * from the one shown here, because `TestRun.status` and `primary_suite_name`
 * are rewritten by ingestion and live-session close while the job waits.
 *
 * So this component never sends criteria to execute — only the `job_id` it
 * was handed. Changing any field after a preview discards it, because the
 * numbers on screen would otherwise describe a set that is no longer the one
 * that would run.
 */
import { useState } from 'react'
import { AlertTriangle, Loader2, Search, Trash2, X } from 'lucide-react'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import { criteriaDeletionService } from '@/services/retentionService'
import { RUN_STATUSES, type DeletionPreview, type RunStatus } from '@/types/retention'
import {
  buildCriteria,
  EMPTY_CRITERIA_FORM,
  narrowsNothing,
  toList,
  type CriteriaFormState,
} from './criteriaForm'

interface Props {
  projectId: string
  projectName: string
  /** Called after a successful execute so the caller can refresh its job list. */
  onExecuted?: () => void
}

export default function CriteriaDeletionPanel({ projectId, projectName, onExecuted }: Props) {
  const [form, setForm] = useState<CriteriaFormState>(EMPTY_CRITERIA_FORM)
  const [preview, setPreview] = useState<DeletionPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const criteria = buildCriteria(form)
  const empty = narrowsNothing(criteria)

  /**
   * Any edit invalidates a preview. The frozen set on screen was resolved from
   * the OLD criteria, so leaving it visible would show counts for a set the
   * execute button would not run.
   */
  function update<K extends keyof CriteriaFormState>(key: K, value: CriteriaFormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setPreview(null)
  }

  function toggleStatus(status: RunStatus) {
    update(
      'statuses',
      form.statuses.includes(status)
        ? form.statuses.filter((s) => s !== status)
        : [...form.statuses, status],
    )
  }

  async function handlePreview() {
    if (empty || previewing) return
    setPreviewing(true)
    try {
      setPreview(await criteriaDeletionService.preview(projectId, criteria))
    } catch (err: unknown) {
      // 403 (foreign run ids), 422 (nothing narrowed) already toast the
      // server's detail through the shared interceptor.
      if (!isAxiosError(err)) toast.error(`Preview failed: ${(err as Error).message}`)
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <section className="card space-y-4" aria-labelledby="criteria-deletion-heading">
      <header>
        <h2
          id="criteria-deletion-heading"
          className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
        >
          Delete by criteria
        </h2>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Narrow to the runs you want gone, review exactly what was selected, then execute.
          Criteria combine with <strong>AND</strong> across fields and <strong>OR</strong> within a
          field — “Failed” plus branch <code>main</code> means failed <em>and</em> on main.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-xs">
          <span className="text-[var(--color-text-muted)]">Older than (days)</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={form.olderThanDays}
            onChange={(e) => update('olderThanDays', e.target.value)}
            placeholder="e.g. 90"
            aria-label="Older than days"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </label>

        <fieldset className="block text-xs">
          <legend className="text-[var(--color-text-muted)]">Status</legend>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {RUN_STATUSES.map((status) => {
              const on = form.statuses.includes(status)
              return (
                <button
                  key={status}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggleStatus(status)}
                  className={`rounded border px-2 py-1 text-xs ${
                    on
                      ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-text)]'
                      : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]'
                  }`}
                >
                  {status}
                </button>
              )
            })}
          </div>
        </fieldset>

        <label className="block text-xs">
          <span className="text-[var(--color-text-muted)]">From</span>
          <input
            type="date"
            value={form.dateFrom}
            onChange={(e) => update('dateFrom', e.target.value)}
            aria-label="Created from"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </label>

        <label className="block text-xs">
          <span className="text-[var(--color-text-muted)]">To</span>
          <input
            type="date"
            value={form.dateTo}
            onChange={(e) => update('dateTo', e.target.value)}
            aria-label="Created to"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </label>

        <label className="block text-xs">
          <span className="text-[var(--color-text-muted)]">Branches</span>
          <input
            value={form.branches}
            onChange={(e) => update('branches', e.target.value)}
            placeholder="main, release/*"
            aria-label="Branches"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </label>

        <label className="block text-xs">
          <span className="text-[var(--color-text-muted)]">Environments</span>
          <input
            value={form.environments}
            onChange={(e) => update('environments', e.target.value)}
            placeholder="staging, qa"
            aria-label="Environments"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </label>

        <label className="block text-xs sm:col-span-2">
          <span className="text-[var(--color-text-muted)]">Suites</span>
          <input
            value={form.suiteNames}
            onChange={(e) => update('suiteNames', e.target.value)}
            placeholder="Auth, Checkout"
            aria-label="Suites"
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
          {toList(form.suiteNames).length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {(['only', 'any'] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="suite-match"
                    value={mode}
                    checked={form.suiteMatch === mode}
                    onChange={() => update('suiteMatch', mode)}
                  />
                  <span className="text-[var(--color-text-muted)]">
                    {mode === 'only'
                      ? 'Only runs whose own suite matches'
                      : 'Any run containing a test in these suites'}
                  </span>
                </label>
              ))}
              {form.suiteMatch === 'any' && (
                <p className="w-full text-[var(--color-text-faint)]">
                  This deletes multi-suite runs where only one suite matched.
                </p>
              )}
            </div>
          )}
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handlePreview}
          disabled={empty || previewing}
          title={empty ? 'Add at least one criterion — otherwise this is a full project purge' : undefined}
          className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {previewing ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Resolving…
            </>
          ) : (
            <>
              <Search className="h-3.5 w-3.5" /> Preview
            </>
          )}
        </button>
        {empty && (
          <p className="text-xs text-[var(--color-text-faint)]">
            Add at least one criterion. Deleting with none selected would purge the entire project.
          </p>
        )}
      </div>

      {preview && (
        <div
          data-testid="criteria-preview"
          className="space-y-3 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs"
        >
          <p className="text-[var(--color-text)]">
            <span data-testid="preview-run-count" className="text-base font-semibold">
              {preview.run_count}
            </span>{' '}
            run{preview.run_count === 1 ? '' : 's'} selected.
          </p>

          {preview.truncated && (
            <p className="text-[var(--status-failed)]">
              More runs matched than can be reviewed at once — only the newest are in this set.
              Narrow the criteria and preview again.
            </p>
          )}

          {preview.blocked.length > 0 && (
            <div data-testid="preview-blocked">
              <p className="font-medium text-[var(--color-text)]">
                {preview.blocked.length} run{preview.blocked.length === 1 ? '' : 's'} cannot be
                deleted and {preview.blocked.length === 1 ? 'is' : 'are'} excluded:
              </p>
              <ul className="mt-1 ml-4 list-disc space-y-0.5 text-[var(--color-text-muted)]">
                {preview.blocked.slice(0, 10).map((b) => (
                  <li key={b.run_id}>
                    <span className="font-mono">{b.run_id.slice(0, 8)}</span> — {b.reasons.join('; ')}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.refused_prefixes.length > 0 && (
            <div data-testid="preview-refused-prefixes">
              <p className="font-medium text-[var(--status-failed)]">
                Object storage outside this project's scope will NOT be deleted:
              </p>
              <ul className="mt-1 ml-4 list-disc space-y-0.5 font-mono text-[var(--color-text-muted)]">
                {preview.refused_prefixes.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={preview.run_count === 0}
            title={preview.run_count === 0 ? 'Nothing to delete' : undefined}
            className="inline-flex items-center gap-1.5 rounded border border-[var(--status-failed-bd)]/60 bg-[var(--status-failed-bg)]/30 px-3 py-1.5 text-xs font-medium text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/60 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete {preview.run_count} run
            {preview.run_count === 1 ? '' : 's'}
          </button>
        </div>
      )}

      {confirmOpen && preview && (
        <ExecuteConfirmModal
          projectId={projectId}
          projectName={projectName}
          preview={preview}
          onClose={() => setConfirmOpen(false)}
          onExecuted={() => {
            setConfirmOpen(false)
            // The frozen set has been consumed — a second execute would 409.
            setPreview(null)
            onExecuted?.()
          }}
        />
      )}
    </section>
  )
}

interface ModalProps {
  projectId: string
  projectName: string
  preview: DeletionPreview
  onClose: () => void
  onExecuted: () => void
}

function ExecuteConfirmModal({
  projectId,
  projectName,
  preview,
  onClose,
  onExecuted,
}: ModalProps) {
  const [typed, setTyped] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const matches = typed === projectName

  async function handleConfirm() {
    if (!matches || submitting) return
    setSubmitting(true)
    try {
      // The JOB ID, never the criteria — execute replays the frozen set.
      await criteriaDeletionService.execute(projectId, preview.job_id, typed)
      toast.success(`Deleting ${preview.run_count} runs — track it in the job list`)
      onExecuted()
    } catch (err: unknown) {
      if (!isAxiosError(err)) toast.error(`Delete failed: ${(err as Error).message}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="criteria-delete-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
    >
      <div className="w-full max-w-lg rounded-lg border border-[var(--status-failed-bd)]/50 bg-[var(--color-bg)] p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-[var(--status-failed)]" />
            <h2
              id="criteria-delete-title"
              className="text-lg font-semibold text-[var(--color-text)]"
            >
              Delete {preview.run_count} run{preview.run_count === 1 ? '' : 's'}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
          This deletes the {preview.run_count} run{preview.run_count === 1 ? '' : 's'} shown in the
          preview across every store — results, raw events, artifacts and analysis. It cannot be
          undone. Only the set that was previewed will go; nothing is re-selected at delete time.
        </p>

        <div className="mt-4">
          <label
            htmlFor="criteria-delete-confirm"
            className="block text-xs text-[var(--color-text-muted)]"
          >
            Type{' '}
            <code className="rounded bg-[var(--color-bg-secondary)] px-1 py-0.5 font-mono text-[var(--color-text)]">
              {projectName}
            </code>{' '}
            to confirm.
          </label>
          <input
            id="criteria-delete-confirm"
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={submitting}
            placeholder={projectName}
            className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text)]"
          />
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!matches || submitting}
            aria-disabled={!matches || submitting}
            className="inline-flex items-center gap-1.5 rounded bg-[var(--status-failed-bg)] px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[var(--status-failed-bg)]/40 disabled:text-[var(--status-failed)]/60"
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Deleting…
              </>
            ) : (
              <>
                <Trash2 className="h-3.5 w-3.5" /> Delete permanently
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
