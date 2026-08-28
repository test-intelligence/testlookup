import { useEffect, useState } from 'react'
import { Bug, X } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { analyticsService } from '@/services/analyticsService'
import type {
  DefectIntakeCategory,
  DefectIntakePayload,
  DefectIntakeSeverity,
} from '@/types/analytics'

const SEVERITIES: { id: DefectIntakeSeverity; label: string; tone: string }[] = [
  { id: 'P0', label: 'P0 — Blocker', tone: 'text-[var(--status-failed)] border-[var(--status-failed-bd)]/60 bg-[var(--status-failed-bg)]/30' },
  { id: 'P1', label: 'P1 — High',    tone: 'text-[var(--status-broken)] border-[var(--status-broken-bd)]/60 bg-[var(--status-broken-bg)]/30' },
  { id: 'P2', label: 'P2 — Medium',  tone: 'text-[var(--status-broken)] border-[var(--status-broken-bd)]/60 bg-[var(--status-broken-bg)]/30' },
  { id: 'P3', label: 'P3 — Low',     tone: 'text-[var(--status-passed)] border-[var(--status-passed-bd)]/60 bg-[var(--status-passed-bg)]/30' },
]

const CATEGORIES: { id: DefectIntakeCategory; label: string }[] = [
  { id: 'PRODUCT_BUG',       label: 'Product bug' },
  { id: 'INFRASTRUCTURE',    label: 'Infrastructure' },
  { id: 'TEST_DATA',         label: 'Test data' },
  { id: 'AUTOMATION_DEFECT', label: 'Automation defect' },
  { id: 'FLAKY',             label: 'Flaky' },
  { id: 'UNKNOWN',           label: 'Unknown' },
]

interface DefectIntakeModalProps {
  projectId: string
  prefillTestName?: string | null
  prefillSuiteName?: string | null
  prefillComponent?: string | null
  onClose: () => void
  onSuccess?: () => void
}

export default function DefectIntakeModal({
  projectId,
  prefillTestName,
  prefillSuiteName,
  prefillComponent,
  onClose,
  onSuccess,
}: DefectIntakeModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [severity, setSeverity] = useState<DefectIntakeSeverity>('P1')
  const [category, setCategory] = useState<DefectIntakeCategory>('PRODUCT_BUG')
  const [component, setComponent] = useState(prefillComponent ?? '')
  const [testName, setTestName] = useState(prefillTestName ?? '')
  const [suiteName, setSuiteName] = useState(prefillSuiteName ?? '')
  const [jiraUrl, setJiraUrl] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, submitting])

  const titleTrimmed = title.trim()
  const jiraTrimmed = jiraUrl.trim()
  const isJiraValid = jiraTrimmed === '' || /^https?:\/\//i.test(jiraTrimmed)
  const canSubmit = titleTrimmed.length >= 3 && isJiraValid && !submitting

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)

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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="New defect"
    >
      <div className="relative w-full max-w-2xl mx-4 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-2xl shadow-2xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-3">
            <Bug className="h-5 w-5 text-[var(--color-text)]" />
            <div>
              <h2 className="text-base font-semibold text-[var(--color-text)]">New defect</h2>
              <p className="text-xs text-[var(--color-text-muted)]">
                Create a defect in the current project. Attach a Jira link if it already exists.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors disabled:opacity-40"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="overflow-y-auto flex-1 px-6 py-5 space-y-4">
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
                  className={clsx(
                    'rounded-lg border px-2 py-2 text-xs font-medium transition-colors',
                    severity === s.id
                      ? s.tone
                      : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
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

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-[var(--color-border)]">
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
              className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting && <LoadingSpinner size="sm" />}
              {submitting ? 'Creating…' : 'Create defect'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
