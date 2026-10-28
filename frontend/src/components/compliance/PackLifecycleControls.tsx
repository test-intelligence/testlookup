/**
 * PackLifecycleControls — Hold / Retire / Delete on one compliance-pack row
 * (W22a ACT-05 + the UI half of C-01, contract §4.1).
 *
 * `Hold` (QA_LEAD+) places a hold on this pack; `Retire` and `Delete` (ADMIN)
 * are DISABLED while any live hold covers the pack (title `Held: <reason>`),
 * and `Delete` also while the retention window is still open (retire first).
 * Each opens in place under the row: Hold → the shared Place-hold form;
 * Retire → a reason + the pack id typed back (the server's rule); Delete →
 * an inline confirmation. A 409's `detail.blockers` (`Held: …`, the window
 * blocker) are listed inline. Role gates mirror the server (convenience, not
 * security). No modal, no `confirm(`; every sentence is React text.
 *
 * `PackHoldChip` is the row's `On hold` chip (line 1).
 */
import { useId, useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { usePermissions } from '@/hooks/usePermissions'
import { useNow } from '@/hooks/useNow'
import { compliancePackService, type CompliancePackRead } from '@/services/compliancePackService'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { PlaceHoldForm } from '@/components/retention/ReleaseHoldStrip'
import { heldLine, ON_HOLD_LABEL, serverSentence, validateReason } from '@/components/retention/holdsModel'
import { HOLD_REASON_MAX, holdsCovering, type HoldRef } from '@/types/retentionHolds'
import { formatDay } from '@/utils/formatters'

export const DELETE_PACK_QUESTION = 'Delete this compliance pack and its ZIP? This cannot be undone.'
export const PACK_ACTION_FAILED = 'The server refused the request.'
export const CONFIRM_ID_MISMATCH = 'Type the pack id exactly to confirm.'

type Open = 'hold' | 'retire' | 'delete' | null

/** `.btn-secondary` has no disabled look of its own: a held / still-retained pack's button reads as locked. */
const LOCKABLE = 'btn-secondary btn-sm whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed'

/** The live holds covering a pack (its own, its release's, the project's); absent `holds` = none. */
function packHolds(pack: CompliancePackRead, nowMs: number): HoldRef[] {
  return holdsCovering(Array.isArray(pack.holds) ? pack.holds : [], {
    kind: 'compliance_pack', id: pack.id, releaseId: pack.release_id,
  }, new Date(nowMs))
}

/** A 409's `detail.blockers`, else the server's one sentence. */
function refusalLines(err: unknown): string[] {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  const blockers = detail && typeof detail === 'object' ? (detail as { blockers?: unknown }).blockers : undefined
  if (Array.isArray(blockers)) {
    const lines = blockers.filter((b): b is string => typeof b === 'string' && b.trim() !== '')
    if (lines.length) return lines
  }
  return [serverSentence(err, PACK_ACTION_FAILED)]
}

/** Retire: a reason (3-500) and the pack id typed back; sent ONLY when the typed id matches. */
function RetirePackForm({ pack, onClose, onRetired, onRefused }: {
  pack: CompliancePackRead
  onClose: () => void
  onRetired: () => void
  onRefused: (lines: string[]) => void
}) {
  const reasonId = useId()
  const confirmId = useId()
  const [reason, setReason] = useState('')
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const matches = typed.trim() === pack.id

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const invalid = validateReason(reason)
    if (invalid) return setError(invalid)
    if (!matches) return setError(CONFIRM_ID_MISMATCH)
    setBusy(true)
    setError(null)
    try {
      await compliancePackService.retire(pack.id, { reason: reason.trim(), confirmation_id: typed.trim() })
      onRetired()
    } catch (err) {
      const lines = refusalLines(err)
      if ((err as { response?: { status?: number } })?.response?.status === 409) onRefused(lines)
      else setError(lines.join(' '))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title="Retire this compliance pack"
      description="Its retention window ends now; the next scheduled purge removes the pack and its ZIP."
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      onSubmit={submit}
      dataAttributes={{ 'data-pack-retire-form': pack.id }}
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={busy} className="btn-secondary btn-sm">Cancel</button>
          <button type="submit" disabled={busy || !matches} className="btn-primary btn-sm">Retire pack</button>
        </>
      )}
    >
      <div className="space-y-2">
        <div>
          <label htmlFor={reasonId} className="block text-xs font-medium text-[var(--color-text)]">Reason</label>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={HOLD_REASON_MAX}
            rows={2}
            onChange={(e) => setReason(e.target.value)}
            className="input mt-1 w-full text-xs"
          />
        </div>
        <div>
          <label htmlFor={confirmId} className="block text-xs font-medium text-[var(--color-text)]">
            Type the pack id to confirm
          </label>
          <p className="font-mono text-xs text-[var(--color-text-muted)] break-all">{pack.id}</p>
          <input
            id={confirmId}
            type="text"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setTyped(e.target.value)}
            className="input mt-1 w-full font-mono text-xs"
          />
        </div>
        {error && <p role="alert" className="text-xs text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

export function PackHoldChip({ pack }: { pack: CompliancePackRead }) {
  const now = useNow()
  const held = packHolds(pack, now)
  if (held.length === 0) return null
  return (
    <span
      data-pack-hold=""
      title={held.map(heldLine).join('\n')}
      className="whitespace-nowrap rounded border border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] px-1.5 text-xs text-[var(--ui-warning-ink)]"
    >
      {ON_HOLD_LABEL}
    </span>
  )
}

export default function PackLifecycleControls({ pack, onChanged }: { pack: CompliancePackRead; onChanged: () => void }) {
  const { isAdmin, isQaLead } = usePermissions()
  const [open, setOpen] = useState<Open>(null)
  const [blockers, setBlockers] = useState<string[]>([])
  const [deleting, setDeleting] = useState(false)
  const now = useNow()
  if (!isQaLead) return null

  const held = packHolds(pack, now)
  const heldTitle = held.length ? heldLine(held[0]) : undefined
  const windowOpen = Date.parse(pack.retention_expires_at) > now
  const windowTitle = `Retire the pack first — its retention window runs to ${formatDay(pack.retention_expires_at)}`
  const packName = `the pack of ${formatDay(pack.generated_at)}`

  function toggle(next: Exclude<Open, null>) {
    setBlockers([])
    setOpen((current) => (current === next ? null : next))
  }

  function done(message: string) {
    setOpen(null)
    setBlockers([])
    if (message) toast.success(message)
    onChanged()
  }

  async function handleDelete() {
    if (deleting) return
    setDeleting(true)
    try {
      await compliancePackService.remove(pack.id)
      done('Compliance pack deleted.')
    } catch (err) {
      setOpen(null)
      setBlockers(refusalLines(err))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <button type="button" onClick={() => toggle('hold')} className="btn-secondary btn-sm whitespace-nowrap">
        Hold
      </button>
      {isAdmin && (
        <button
          type="button"
          onClick={() => toggle('retire')}
          disabled={held.length > 0}
          title={heldTitle}
          className={LOCKABLE}
        >
          Retire
        </button>
      )}
      {isAdmin && (
        <button
          type="button"
          onClick={() => toggle('delete')}
          disabled={held.length > 0 || windowOpen}
          title={heldTitle ?? (windowOpen ? windowTitle : undefined)}
          className={LOCKABLE}
        >
          Delete
        </button>
      )}
      {open === 'hold' && (
        <div className="basis-full">
          <PlaceHoldForm
            projectId={pack.project_id}
            entityType="compliance_pack"
            entityId={pack.id}
            targetName={packName}
            title="Hold this compliance pack"
            onClose={() => setOpen(null)}
            onPlaced={() => done('')}
          />
        </div>
      )}
      {open === 'retire' && isAdmin && (
        <div className="basis-full">
          <RetirePackForm
            pack={pack}
            onClose={() => setOpen(null)}
            onRetired={() => done('Compliance pack retired — the next purge removes it.')}
            onRefused={(lines) => { setOpen(null); setBlockers(lines) }}
          />
        </div>
      )}
      {open === 'delete' && isAdmin && (
        <div className="basis-full">
          <InlineConfirm
            message={DELETE_PACK_QUESTION}
            confirmLabel="Delete pack"
            tone="danger"
            busy={deleting}
            onConfirm={handleDelete}
            onCancel={() => setOpen(null)}
            testId="pack-delete-confirm"
          />
        </div>
      )}
      {blockers.length > 0 && (
        <ul role="alert" data-pack-blockers="" className="basis-full space-y-0.5">
          {blockers.map((line) => (
            <li key={line} className="text-xs text-[var(--ui-danger-ink)] break-words">{line}</li>
          ))}
        </ul>
      )}
    </>
  )
}
