/**
 * Retention activation nudge (S1).
 *
 * Retention has shipped for a while, but the policy defaults to disabled and
 * nothing in the product ever asked an operator to turn it on — so the feature
 * is present, discoverable, and doing nothing. This is the ask.
 *
 * Two rules shape it:
 *
 *  • **Preview before enable.** Enabling starts an irreversible nightly delete.
 *    The operator sees the real candidate counts for THIS project and confirms
 *    them; there is no one-click enable that skips the numbers.
 *  • **Dismissal is per user, server-side.** `localStorage` would re-prompt the
 *    same person on a second machine to enable a destructive job they had
 *    already declined.
 *
 * Renders nothing once the policy is enabled or the prompt is dismissed.
 */
import { useState } from 'react'
import { Loader2, ShieldQuestion, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { previewRetentionPurge, updateRetentionPolicy } from '@/hooks/useRetentionPolicy'
import { dismissUIPrompt, useUIDismissals } from '@/hooks/useUIDismissals'
import { RETENTION_ACTIVATION_NUDGE } from '@/services/uiDismissalService'
import type { RetentionPolicy, RetentionPreview } from '@/types/retention'

interface Props {
  projectId: string
  projectName: string
  policy: RetentionPolicy
  /** Called with the saved policy so the page can update its own SWR cache. */
  onEnabled: (policy: RetentionPolicy) => void
}

export default function RetentionActivationNudge({
  projectId,
  projectName,
  policy,
  onEnabled,
}: Props) {
  const { data: dismissals, mutate: mutateDismissals } = useUIDismissals()
  const [preview, setPreview] = useState<RetentionPreview | null>(null)
  const [busy, setBusy] = useState(false)

  const dismissed = dismissals?.dismissed.includes(RETENTION_ACTIVATION_NUDGE) ?? false

  // Nothing to ask if retention already runs, or if they already said no.
  // While dismissals are still loading we render nothing rather than flashing
  // a prompt the user has already dismissed.
  if (policy.enabled || dismissed || !dismissals) return null

  const handleReview = async () => {
    setBusy(true)
    try {
      setPreview(await previewRetentionPurge(projectId))
    } catch {
      toast.error('Could not preview what retention would delete')
    } finally {
      setBusy(false)
    }
  }

  const handleEnable = async () => {
    setBusy(true)
    try {
      const saved = await updateRetentionPolicy(projectId, { enabled: true })
      onEnabled(saved)
      toast.success(`Retention enabled for ${projectName}`)
    } catch {
      toast.error('Could not enable retention')
    } finally {
      setBusy(false)
    }
  }

  const handleDismiss = async () => {
    // Optimistic: the prompt disappears immediately. If the POST fails the
    // revalidation puts it back — better than a prompt that appears to ignore
    // the dismiss button while a request is in flight.
    mutateDismissals(
      async () => dismissUIPrompt(RETENTION_ACTIVATION_NUDGE),
      {
        optimisticData: {
          dismissed: [...(dismissals?.dismissed ?? []), RETENTION_ACTIVATION_NUDGE],
        },
        rollbackOnError: true,
      },
    ).catch(() => toast.error('Could not save your preference'))
  }

  return (
    <section
      className="card space-y-3 border-[var(--color-accent)]/40"
      data-testid="retention-activation-nudge"
    >
      <div className="flex items-start gap-2">
        <ShieldQuestion className="h-4 w-4 mt-0.5 text-[var(--color-accent)] shrink-0" />
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">
            Retention is configured but not running
          </h2>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            {projectName} keeps every run, artifact and log indefinitely. Turning
            retention on lets the nightly job remove data past your windows.{' '}
            <strong className="text-[var(--color-text)]">Deletion is permanent</strong>{' '}
            — review what would be removed before enabling.
          </p>
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Dismiss retention prompt"
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {preview && (
        <div
          className="rounded border border-[var(--color-border)] p-3 text-xs"
          data-testid="retention-nudge-preview"
        >
          <p className="text-[var(--color-text-muted)]">
            At the current windows, the first run would remove:
          </p>
          <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[var(--color-text)]">
            <li>{preview.candidates.runs} test runs</li>
            <li>{preview.candidates.test_cases} test cases</li>
            <li>{preview.candidates.minio_objects} stored objects</li>
            <li>{preview.candidates.audit_rows} audit rows</li>
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2">
        {!preview ? (
          <button
            type="button"
            onClick={handleReview}
            disabled={busy}
            className="btn-primary text-xs"
          >
            {busy && <Loader2 className="h-3 w-3 animate-spin" />}
            Review what would be deleted
          </button>
        ) : (
          <button
            type="button"
            onClick={handleEnable}
            disabled={busy}
            className="btn-primary text-xs"
          >
            {busy && <Loader2 className="h-3 w-3 animate-spin" />}
            Enable retention
          </button>
        )}
        <button type="button" onClick={handleDismiss} className="btn-secondary text-xs">
          Not now
        </button>
      </div>
    </section>
  )
}
