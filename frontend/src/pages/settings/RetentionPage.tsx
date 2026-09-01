/**
 * Retention & Purge — ADMIN-only data-retention policy (PMF US-11.4 UI).
 *
 * Three sections:
 *   • Policy form — enabled toggle + four day-count windows (raw events,
 *     runs & analysis, artifacts, audit trail) with inline bounds validation
 *     mirroring the pinned contract (raw_events/artifacts 7–3650,
 *     runs 30–3650, audit 365–3650, audit ≥ runs). PUT sends only the
 *     changed fields. The backend re-validates everything.
 *   • Preview — dry-run POST that renders the cutoff timestamps and the
 *     purge-candidate counts (computed now; the nightly job may differ).
 *   • Danger zone — "Purge now" behind a typed-project-name confirmation
 *     modal (GitHub repo-delete UX, mirrors ProjectDataPage). Disabled while
 *     the saved policy is disabled — the backend 409s in that state anyway.
 *
 * The in-component ADMIN gate is a forcing function against autopilot
 * clicks, not a security check: the backend enforces ADMIN on every write.
 */
import { useState } from 'react'
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  Loader2,
  RefreshCw,
  Save,
  ScanSearch,
  Trash2,
  X,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  previewRetentionPurge,
  purgeRetentionNow,
  updateRetentionPolicy,
  useRetentionPolicy,
} from '@/hooks/useRetentionPolicy'
import {
  RETENTION_BOUNDS,
  type RetentionDayField,
  type RetentionPolicy,
  type RetentionPolicyWrite,
  type RetentionPreview,
} from '@/types/retention'
import { formatCompactDateTime } from '@/utils/formatters'

/** Copy for the four day-count fields — label + what the class covers. */
const DAY_FIELDS: Array<{ field: RetentionDayField; label: string; help: string }> = [
  {
    field: 'raw_events_days',
    label: 'Raw events',
    help: 'Raw ingestion payloads and live-stream event documents — Mongo pipeline event logs and event_archive rows. Cheapest class to keep short.',
  },
  {
    field: 'runs_days',
    label: 'Runs & analysis',
    help: 'Test runs and everything derived from them: test cases, AI analyses, failure clusters, and agent pipeline rows older than the cutoff.',
  },
  {
    field: 'artifacts_days',
    label: 'Artifacts',
    help: 'Files stored in MinIO — uploaded report files, attachments, and expired compliance packs.',
  },
  {
    field: 'audit_days',
    label: 'Audit trail',
    help: 'Audit-log and provenance rows. Compliance floor of 365 days, and it can never be shorter than Runs & analysis.',
  },
]

/** Rows of the preview candidates table, in render order. */
const CANDIDATE_ROWS: Array<{
  key: keyof Omit<RetentionPreview['candidates'], 'mongo_docs'>
  label: string
}> = [
  { key: 'runs', label: 'Test runs' },
  { key: 'test_cases', label: 'Test cases' },
  { key: 'minio_objects', label: 'MinIO objects' },
  { key: 'event_archive_rows', label: 'Event-archive rows' },
  { key: 'audit_rows', label: 'Audit rows' },
  { key: 'provenance_rows', label: 'Provenance rows' },
  { key: 'compliance_packs_expired', label: 'Expired compliance packs' },
]

/** Editable form shape — day counts as strings so typing is never fought. */
interface FormState {
  enabled: boolean
  raw_events_days: string
  runs_days: string
  artifacts_days: string
  audit_days: string
}

function toFormState(policy: RetentionPolicy): FormState {
  return {
    enabled: policy.enabled,
    raw_events_days: String(policy.raw_events_days),
    runs_days: String(policy.runs_days),
    artifacts_days: String(policy.artifacts_days),
    audit_days: String(policy.audit_days),
  }
}

/** Parse a day-count field; NaN for anything that is not a plain integer. */
function parseDays(raw: string): number {
  return /^\d+$/.test(raw.trim()) ? Number(raw.trim()) : NaN
}

/**
 * Client-side mirror of the contract bounds (inline feedback only — the
 * backend re-validates). Returns a field→message map; empty means valid.
 */
function validate(form: FormState): Partial<Record<RetentionDayField, string>> {
  const errors: Partial<Record<RetentionDayField, string>> = {}
  for (const { field, label } of DAY_FIELDS) {
    const { min, max } = RETENTION_BOUNDS[field]
    const value = parseDays(form[field])
    if (Number.isNaN(value)) errors[field] = `${label} must be a whole number of days`
    else if (value < min || value > max)
      errors[field] = `${label} must be between ${min} and ${max} days`
  }
  // Cross-rule: the audit trail can never be purged sooner than runs.
  if (!errors.audit_days && !errors.runs_days) {
    const audit = parseDays(form.audit_days)
    const runs = parseDays(form.runs_days)
    if (audit < runs)
      errors.audit_days = 'Audit trail must be kept at least as long as Runs & analysis'
  }
  return errors
}

/** Build the partial PUT body — only fields that differ from the saved policy. */
function diffPayload(form: FormState, saved: RetentionPolicy): RetentionPolicyWrite {
  const payload: RetentionPolicyWrite = {}
  if (form.enabled !== saved.enabled) payload.enabled = form.enabled
  for (const { field } of DAY_FIELDS) {
    const value = parseDays(form[field])
    if (value !== saved[field]) payload[field] = value
  }
  return payload
}

interface PurgeModalProps {
  projectId: string
  projectName: string
  onClose: () => void
  onQueued: () => void
}

/** Typed-project-name confirmation before queueing a purge (mirrors ProjectDataPage). */
function PurgeConfirmModal({ projectId, projectName, onClose, onQueued }: PurgeModalProps) {
  const [typed, setTyped] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const matches = typed === projectName

  async function handleConfirm() {
    if (!matches || submitting) return
    setSubmitting(true)
    try {
      await purgeRetentionNow(projectId, typed)
      onQueued()
    } catch (err: unknown) {
      // Axios failures (409 disabled, 422 name mismatch, …) already toast the
      // server's detail via the shared interceptor — only toast non-axios ones.
      if (!isAxiosError(err)) toast.error(`Purge failed: ${(err as Error).message}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="purge-data-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="w-full max-w-lg rounded-lg border border-[var(--status-failed-bd)]/50 bg-[var(--color-bg)] p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-[var(--status-failed)]" />
            <h2 id="purge-data-title" className="text-lg font-semibold text-[var(--color-text)]">Purge old data now</h2>
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
          Queues an immediate purge using the saved retention windows. Everything older than the
          cutoffs — runs, test cases, raw events, artifacts, and expired audit rows — is deleted
          permanently. Use “Preview purge” first to see what would go.
        </p>

        <div className="mt-4">
          <label htmlFor="purge-confirm-input" className="block text-xs text-[var(--color-text-muted)]">
            Type{' '}
            <code className="rounded bg-[var(--color-bg-secondary)] px-1 py-0.5 font-mono text-[var(--color-text)]">
              {projectName}
            </code>{' '}
            to confirm.
          </label>
          <input
            id="purge-confirm-input"
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
            className="inline-flex items-center gap-1.5 rounded bg-[var(--status-failed-bg)] px-3 py-1.5 text-sm font-medium text-white hover:bg-[var(--status-failed-bg)] disabled:cursor-not-allowed disabled:bg-[var(--status-failed-bg)]/40 disabled:text-[var(--status-failed)]/60"
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Queueing…
              </>
            ) : (
              <>
                <Trash2 className="h-3.5 w-3.5" /> Queue purge
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function RetentionPage() {
  const { isAdmin } = usePermissions()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = useProjectStore((s) => s.activeProject)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID || !activeProjectId

  // Admins only fetch for a concrete project; every other state renders
  // without touching the API.
  const projectKey = isAdmin && !isAllProjects ? activeProjectId : null
  const { data: policy, error, isLoading, mutate } = useRetentionPolicy(projectKey)

  const [form, setForm] = useState<FormState | null>(null)
  const [saving, setSaving] = useState(false)
  // Any in-progress edit sets `dirty`; it gates the render-time re-seed below
  // so a background SWR revalidation can never wipe unsaved edits.
  const [dirty, setDirty] = useState(false)
  const [seededPolicy, setSeededPolicy] = useState<RetentionPolicy | undefined>(undefined)

  const [preview, setPreview] = useState<RetentionPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [purgeOpen, setPurgeOpen] = useState(false)

  // Seed the form from the resolved policy using the React-endorsed "adjust
  // state during render" reset pattern: whenever SWR hands us a new policy
  // reference (initial load or post-save mutate), reset the form to it —
  // unless the user has unsaved edits (`dirty`). SWR returns a stable
  // reference for unchanged data, so this settles after one render.
  if (policy && policy !== seededPolicy && !dirty) {
    setSeededPolicy(policy)
    setForm(toFormState(policy))
  }

  if (!isAdmin) {
    return (
      <EmptyState
        title="Admin access required"
        description="Retention and purge configuration is restricted to platform administrators."
      />
    )
  }

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Retention & Purge"
          subtitle="Per-project data-retention windows and purge controls"
        />
        <EmptyState
          title="Select a project"
          description="Retention policies are configured per project. Switch the project selector in the top bar to continue."
        />
      </div>
    )
  }

  if (isLoading) return <LoadingSpinner size="lg" />

  // Failed GET with nothing cached: rendering a defaults-seeded form here
  // would let a single Save silently overwrite a working policy with
  // defaults — so surface the error and withhold the form entirely.
  if ((error && !policy) || !policy || !form || !seededPolicy) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Link to="/settings" className="flex items-center gap-1 hover:text-[var(--color-text)]">
            <ArrowLeft className="h-3 w-3" /> Settings
          </Link>
        </div>
        <PageHeader
          title="Retention & Purge"
          subtitle={`Data-retention windows for ${activeProject?.name || 'this project'}`}
        />
        <div className="rounded-md border border-[var(--status-failed-bd)]/40 bg-[var(--status-failed-bg)]/10 p-4 text-xs text-[var(--status-failed)] space-y-2">
          <p className="flex items-center gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <strong>Could not load the retention policy.</strong>
          </p>
          <p>
            The saved policy could not be fetched, so the form is hidden — saving now could
            overwrite a working configuration with defaults.
          </p>
          <button
            type="button"
            onClick={() => mutate()}
            className="btn-secondary text-xs flex items-center gap-1.5"
          >
            <RefreshCw className="h-3 w-3" /> Retry
          </button>
        </div>
      </div>
    )
  }

  const projectName = activeProject?.name ?? ''
  const errors = validate(form)
  const hasErrors = Object.keys(errors).length > 0
  const payload = diffPayload(form, seededPolicy)
  const hasChanges = Object.keys(payload).length > 0

  function editEnabled(value: boolean) {
    setForm((f) => (f ? { ...f, enabled: value } : f))
    setDirty(true)
  }

  function editDay(field: RetentionDayField, value: string) {
    setForm((f) => (f ? { ...f, [field]: value } : f))
    setDirty(true)
  }

  async function save() {
    if (!activeProjectId || hasErrors || !hasChanges || saving) return
    setSaving(true)
    try {
      const saved = await updateRetentionPolicy(activeProjectId, payload)
      setSeededPolicy(saved)
      setForm(toFormState(saved))
      setDirty(false)
      await mutate(saved, { revalidate: false })
      toast.success('Retention policy saved')
    } catch (err) {
      // Axios failures already toast the server's detail via the shared
      // interceptor — a second generic toast here would only bury it.
      if (!isAxiosError(err)) toast.error(`Failed to save: ${(err as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  async function runPreview() {
    if (!activeProjectId || previewing) return
    setPreviewing(true)
    setPreviewError(null)
    try {
      setPreview(await previewRetentionPurge(activeProjectId))
    } catch (err) {
      setPreview(null)
      setPreviewError(
        isAxiosError(err)
          ? ((err.response?.data as { detail?: string } | undefined)?.detail ??
              'Preview request failed')
          : (err as Error).message,
      )
    } finally {
      setPreviewing(false)
    }
  }

  const purgeDisabled = !policy.enabled
  const mongoEntries = preview ? Object.entries(preview.candidates.mongo_docs) : []

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <Link to="/settings" className="flex items-center gap-1 hover:text-[var(--color-text)]">
          <ArrowLeft className="h-3 w-3" /> Settings
        </Link>
      </div>
      <PageHeader
        title="Retention & Purge"
        subtitle={`Data-retention windows for ${projectName || 'this project'}. ADMIN only.`}
      />

      {/* ── Policy form ─────────────────────────────────────────────── */}
      <section className="card space-y-4">
        <div className="flex items-center gap-2">
          <Archive className="h-4 w-4 text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
            Retention policy
          </h2>
          <span
            className={`ml-auto text-[10px] px-2 py-0.5 rounded border ${
              policy.source === 'custom'
                ? 'border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent)]/10'
                : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
            }`}
          >
            {policy.source === 'custom' ? 'Customized' : 'Defaults'}
          </span>
        </div>

        <label className="text-xs flex items-start gap-2">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => editEnabled(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="block text-[var(--color-text)]">
              Enable scheduled purging for this project
            </span>
            <span className="mt-0.5 block text-[var(--color-text-faint)]">
              The nightly purge only runs for projects where this is enabled. Leaving it off keeps
              everything forever; you can still preview what a purge would remove.
            </span>
          </span>
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          {DAY_FIELDS.map(({ field, label, help }) => {
            const { min, max } = RETENTION_BOUNDS[field]
            const fieldError = errors[field]
            return (
              <label key={field} htmlFor={`retention-${field}`} className="text-xs block">
                <span className="text-[var(--color-text-muted)]">
                  {label}{' '}
                  <span className="text-[var(--color-text-faint)]">
                    ({min}–{max} days)
                  </span>
                </span>
                <input
                  id={`retention-${field}`}
                  type="number"
                  inputMode="numeric"
                  min={min}
                  max={max}
                  value={form[field]}
                  onChange={(e) => editDay(field, e.target.value)}
                  aria-invalid={Boolean(fieldError)}
                  className={`mt-1 w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border rounded ${
                    fieldError ? 'border-[var(--status-failed-bd)]/60' : 'border-[var(--color-border)]'
                  }`}
                />
                {fieldError ? (
                  <span className="mt-1 block text-[var(--status-failed)]">{fieldError}</span>
                ) : (
                  <span className="mt-1 block text-[var(--color-text-faint)]">{help}</span>
                )}
              </label>
            )
          })}
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={save}
            disabled={saving || hasErrors || !hasChanges}
            title={
              hasErrors
                ? 'Fix the highlighted fields first'
                : !hasChanges
                  ? 'No changes to save'
                  : undefined
            }
            className="btn-primary text-xs flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save className="h-3 w-3" /> {saving ? 'Saving…' : 'Save policy'}
          </button>
          <span className="text-[11px] text-[var(--color-text-faint)]">
            Only changed fields are sent; the backend re-validates all bounds.
          </span>
        </div>
      </section>

      {/* ── Preview ─────────────────────────────────────────────────── */}
      <section className="card space-y-3">
        <div className="flex items-center gap-2">
          <ScanSearch className="h-4 w-4 text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]">
            Purge preview
          </h2>
        </div>
        <p className="text-xs text-[var(--color-text-muted)]">
          Dry run against the <strong>saved</strong> policy — shows the cutoff dates and how many
          rows/objects are currently past them. Works even while scheduled purging is disabled.
        </p>
        <button
          type="button"
          onClick={runPreview}
          disabled={previewing}
          className="btn-secondary text-xs flex items-center gap-1.5"
        >
          {previewing ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" /> Computing…
            </>
          ) : (
            <>
              <ScanSearch className="h-3 w-3" /> Preview purge
            </>
          )}
        </button>

        {previewError && (
          <div className="rounded-md border border-[var(--status-failed-bd)]/40 bg-[var(--status-failed-bg)]/10 p-2 text-xs text-[var(--status-failed)]">
            <AlertTriangle className="h-3.5 w-3.5 inline mr-1" />
            {previewError}
          </div>
        )}

        {preview && (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-2">
              {(
                [
                  ['Raw events before', preview.cutoffs.raw_events],
                  ['Runs & analysis before', preview.cutoffs.runs],
                  ['Artifacts before', preview.cutoffs.artifacts],
                  ['Audit trail before', preview.cutoffs.audit],
                ] as const
              ).map(([label, cutoff]) => (
                <div
                  key={label}
                  className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-xs"
                >
                  <span className="text-[var(--color-text-muted)]">{label}: </span>
                  <span className="font-mono text-[var(--color-text)]">
                    {formatCompactDateTime(cutoff)}
                  </span>
                </div>
              ))}
            </div>

            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[var(--color-text-muted)]">
                  <th className="py-1 font-medium">Purge candidates</th>
                  <th className="py-1 text-right font-medium">Count</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {CANDIDATE_ROWS.map(({ key, label }) => (
                  <tr key={key}>
                    <td className="py-1 text-[var(--color-text)]">{label}</td>
                    <td className="py-1 text-right font-mono text-[var(--color-text)]">
                      {preview.candidates[key].toLocaleString()}
                    </td>
                  </tr>
                ))}
                {mongoEntries.map(([collection, count]) => (
                  <tr key={`mongo-${collection}`}>
                    <td className="py-1 text-[var(--color-text)]">
                      Mongo docs — <span className="font-mono">{collection}</span>
                    </td>
                    <td className="py-1 text-right font-mono text-[var(--color-text)]">
                      {count.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <p className="text-[11px] text-[var(--color-text-faint)]">
              Counts are computed right now; the nightly job runs later against live data, so its
              numbers may differ slightly.
            </p>
          </div>
        )}
      </section>

      {/* ── Danger zone ─────────────────────────────────────────────── */}
      <section className="card space-y-4 border-[var(--status-failed-bd)]/40" aria-labelledby="retention-danger-heading">
        <header className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-[var(--status-failed)]">
          <AlertTriangle className="h-4 w-4" />
          <h2 id="retention-danger-heading">Danger zone</h2>
        </header>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-[var(--color-text)]">Manual purge</h3>
            <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
              Queue an immediate purge with the saved retention windows instead of waiting for the
              nightly run. Deletions are permanent; every purge writes an audit record.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setPurgeOpen(true)}
            disabled={purgeDisabled}
            title={
              purgeDisabled
                ? 'Enable and save the retention policy first — purging is blocked while the policy is disabled'
                : undefined
            }
            className="inline-flex flex-shrink-0 items-center gap-1.5 rounded border border-[var(--status-failed-bd)]/60 bg-[var(--status-failed-bg)]/30 px-3 py-1.5 text-xs font-medium text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/60 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" /> Purge now
          </button>
        </div>

        {policy.last_purge && (
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs">
            <p className="text-[var(--color-text-muted)]">
              Last purge:{' '}
              <span className="text-[var(--color-text)]">
                {formatCompactDateTime(policy.last_purge.at)}
              </span>{' '}
              <span className="text-[var(--color-text-faint)]">({policy.last_purge.mode})</span>
            </p>
            {Object.keys(policy.last_purge.counts).length > 0 && (
              <ul className="mt-1 ml-4 list-disc space-y-0.5 text-[var(--color-text-muted)]">
                {Object.entries(policy.last_purge.counts).map(([what, count]) => (
                  <li key={what}>
                    <span className="font-mono">{what}</span>: {String(count)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {purgeOpen && activeProjectId && (
        <PurgeConfirmModal
          projectId={activeProjectId}
          projectName={projectName}
          onClose={() => setPurgeOpen(false)}
          onQueued={() => {
            setPurgeOpen(false)
            toast.success('Purge queued — check back for the audit record')
          }}
        />
      )}
    </div>
  )
}
