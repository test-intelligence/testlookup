/**
 * New defect (manual intake): a form IN THE PAGE (W27 PAN-04), opened by the
 * /defects header's "New defect" right under the page header. An
 * `InlineFormSection` named "New defect", as the dialog was: focus moves to
 * the title, Escape inside closes it (not mid-submit), the X is "Close".
 */
import { useMemo, useState } from 'react'
import { useReleases } from '@/hooks/useReleases'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { analyticsService } from '@/services/analyticsService'
import type {
  DefectIntakeCategory,
  DefectIntakePayload,
  DefectIntakeSeverity,
} from '@/types/analytics'
import type { Release } from '@/types/releases'
import { SEVERITY_STYLE, type SeverityStyle } from '@/components/ui/severity'
import { formatDay } from '@/utils/formatters'

/**
 * The four severities, each chosen chip in the ONE severity palette (W29a
 * UIX-07): P0 danger, P1 warning, P2 info, P3 neutral — a medium or low
 * severity is never orange or passed-green. The long labels are this form's copy.
 */
const SEVERITIES: { id: DefectIntakeSeverity; label: string; tone: SeverityStyle }[] = [
  { id: 'P0', label: 'P0 — Blocker', tone: SEVERITY_STYLE.P0 },
  { id: 'P1', label: 'P1 — High',    tone: SEVERITY_STYLE.P1 },
  { id: 'P2', label: 'P2 — Medium',  tone: SEVERITY_STYLE.P2 },
  { id: 'P3', label: 'P3 — Low',     tone: SEVERITY_STYLE.P3 },
]

const CATEGORIES: { id: DefectIntakeCategory; label: string }[] = [
  { id: 'PRODUCT_BUG',       label: 'Product bug' },
  { id: 'INFRASTRUCTURE',    label: 'Infrastructure' },
  { id: 'TEST_DATA',         label: 'Test data' },
  { id: 'AUTOMATION_DEFECT', label: 'Automation defect' },
  { id: 'FLAKY',             label: 'Flaky' },
  { id: 'UNKNOWN',           label: 'Unknown' },
]

/** `{name} {version}`, plus ` · shipped {date}` once the release has shipped. */
function releaseOptionLabel(release: Release): string {
  const name = [release.name, release.version].filter((part) => part && part.trim()).join(' ')
  if (!release.released_at) return name
  const shipped = new Date(release.released_at)
  const date = Number.isNaN(shipped.getTime())
    ? release.released_at
    : formatDay(shipped)
  return `${name} · shipped ${date}`
}

interface DefectIntakeModalProps {
  projectId: string
  prefillTestName?: string | null
  prefillSuiteName?: string | null
  prefillComponent?: string | null
  onClose: () => void
  onSuccess?: () => void
  /** The host section's slot: one form open there; a typed form asks before it is replaced. */
  slot?: InlineSlot
}

export default function DefectIntakeModal({
  projectId,
  prefillTestName,
  prefillSuiteName,
  prefillComponent,
  onClose,
  onSuccess,
  slot,
}: DefectIntakeModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [severity, setSeverity] = useState<DefectIntakeSeverity>('P1')
  const [category, setCategory] = useState<DefectIntakeCategory>('PRODUCT_BUG')
  const [component, setComponent] = useState(prefillComponent ?? '')
  const [testName, setTestName] = useState(prefillTestName ?? '')
  const [suiteName, setSuiteName] = useState(prefillSuiteName ?? '')
  const [jiraUrl, setJiraUrl] = useState('')
  const [affectsRelease, setAffectsRelease] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The top bar's release list, read as it is (no second request). Only this
  // project's releases are offered: the server refuses any other id.
  const { data: releaseList } = useReleases(undefined, { cached: true })
  const releases = useMemo(
    () => (releaseList?.items ?? []).filter((r) => !r.project_id || r.project_id === projectId),
    [releaseList, projectId],
  )

  const titleTrimmed = title.trim()
  const jiraTrimmed = jiraUrl.trim()
  const isJiraValid = jiraTrimmed === '' || /^https?:\/\//i.test(jiraTrimmed)
  const canSubmit = titleTrimmed.length >= 3 && isJiraValid && !submitting

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)

    // DEF-03: `affects_releases` is sent only when a release is chosen; the server
    // checks every id is a release of THIS project (422 otherwise).
    const payload: DefectIntakePayload = {
      project_id: projectId,
      title: titleTrimmed,
      severity,
      failure_category: category,
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(component.trim()   ? { component: component.trim() }     : {}),
      ...(testName.trim()    ? { test_name: testName.trim() }      : {}),
      ...(suiteName.trim()   ? { suite_name: suiteName.trim() }    : {}),
      ...(jiraTrimmed        ? { jira_ticket_url: jiraTrimmed }    : {}),
      ...(affectsRelease     ? { affects_releases: [affectsRelease] } : {}),
    }

    try {
      const created = await analyticsService.createDefect(payload)
      toast.success(`Defect ${created.id.slice(0, 8)} created`, { icon: '🐞' })
      onSuccess?.()
      onClose()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (err as { message?: string })?.message
        ?? 'Failed to create defect'
      setError(typeof msg === 'string' ? msg : 'Failed to create defect')
      setSubmitting(false)
    }
  }

  return (
    <InlineFormSection
      title="New defect"
      description={
        <span className="text-xs">Create a defect in the current project. Attach a Jira link if it already exists.</span>
      }
      onClose={onClose}
      canClose={!submitting}
      closeLabel="Close"
      busy={submitting}
      slot={slot}
      onSubmit={handleSubmit}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)] disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="btn-primary btn-sm inline-flex items-center gap-2"
          >
            {submitting && <LoadingSpinner size="sm" />}
            {submitting ? 'Creating…' : 'Create defect'}
          </button>
        </>
      }
    >
        <div className="max-w-2xl space-y-4">
          <div>
            <label htmlFor="defect-title" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
              Title <span className="text-[var(--status-failed)]">*</span>
            </label>
            <input
              id="defect-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={255}
              placeholder="Short, specific summary (min 3 chars)"
              className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
              autoFocus
              required
            />
          </div>

          <div>
            <span id="defect-intake-severity" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
              Severity
            </span>
            <div role="group" aria-labelledby="defect-intake-severity" className="grid grid-cols-4 gap-2">
              {SEVERITIES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSeverity(s.id)}
                  data-severity={s.id}
                  className={clsx(
                    'rounded-lg border px-2 py-2 text-xs font-medium transition-colors',
                    severity !== s.id
                      && 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                  style={severity === s.id ? { color: s.tone.fg, background: s.tone.bg, borderColor: s.tone.bd } : undefined}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="defect-category" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
                Failure category
              </label>
              <select
                id="defect-category"
                value={category}
                onChange={(e) => setCategory(e.target.value as DefectIntakeCategory)}
                className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
              >
                {CATEGORIES.map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="defect-component" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
                Component
              </label>
              <input
                id="defect-component"
                type="text"
                value={component}
                onChange={(e) => setComponent(e.target.value)}
                maxLength={255}
                placeholder="e.g. checkout-service"
                className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="defect-test-name" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
                Test name (optional)
              </label>
              <input
                id="defect-test-name"
                type="text"
                value={testName}
                onChange={(e) => setTestName(e.target.value)}
                maxLength={1000}
                placeholder="checkout.test_payment_flow"
                className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="defect-suite" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
                Suite name (optional)
              </label>
              <input
                id="defect-suite"
                type="text"
                value={suiteName}
                onChange={(e) => setSuiteName(e.target.value)}
                maxLength={500}
                placeholder="checkout-smoke"
                className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label htmlFor="defect-affects-release" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
              Affects release (optional)
            </label>
            <select
              id="defect-affects-release"
              aria-label="Affects release"
              value={affectsRelease}
              onChange={(e) => setAffectsRelease(e.target.value)}
              className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none"
            >
              <option value="">Not specified</option>
              {releases.map((r) => (
                <option key={r.id} value={r.id}>{releaseOptionLabel(r)}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              A defect filed against a release that has already shipped counts toward the escape rate.
            </p>
          </div>

          <div>
            <label htmlFor="defect-jira" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
              Jira ticket URL (optional)
            </label>
            <input
              id="defect-jira"
              type="url"
              value={jiraUrl}
              onChange={(e) => setJiraUrl(e.target.value)}
              maxLength={1000}
              placeholder="https://your-org.atlassian.net/browse/ABC-123"
              className={clsx(
                'w-full rounded-lg bg-[var(--color-bg-secondary)] border px-3 py-2 text-sm text-[var(--color-text)] focus:outline-none',
                isJiraValid
                  ? 'border-[var(--color-border)] focus:border-[var(--color-accent)]'
                  : 'border-[var(--status-failed-bd)]/60 focus:border-[var(--status-failed-bd)]',
              )}
            />
            {!isJiraValid && (
              <p className="mt-1 text-xs text-[var(--status-failed)]">Must be an http(s) URL.</p>
            )}
          </div>

          <div>
            <label htmlFor="defect-description" className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1.5">
              Description (optional)
            </label>
            <textarea
              id="defect-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={10_000}
              rows={4}
              placeholder="Repro steps, observed behaviour, links to logs…"
              className="w-full rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-accent)] focus:outline-none resize-y"
            />
          </div>

          {error && (
            <div className="rounded-lg bg-[var(--status-failed-bg)]/20 border border-[var(--status-failed-bd)]/40 p-3 text-xs text-[var(--status-failed)]">
              {error}
            </div>
          )}
        </div>
    </InlineFormSection>
  )
}
