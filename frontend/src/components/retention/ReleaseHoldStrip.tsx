/**
 * ReleaseHoldStrip — a release's retention holds at the top of its detail
 * (W22a ACT-05, contract §4.1).
 *
 * Renders ONLY when the release is held (a live hold of the release or of its
 * whole project, from the release detail's `holds`) OR the viewer is QA_LEAD+
 * (who may place one). Held: an `On hold` chip and, per live hold (≤ 3 shown,
 * `+n more` beyond), `Held: <reason>` with a muted `<what> · until <date>`
 * line. QA_LEAD+: `Place hold` opens an inline form inside the strip — the
 * scope is fixed to THIS release; the server's sentence shows in the form.
 *
 * No request of its own: the holds arrive as props (the detail already
 * carries them); a placed hold re-reads the detail through `onChanged`, the
 * release's pack list (its packs are pinned too) and every hold list of the
 * project through `refreshRetentionHolds`. Reasons and labels are React text.
 */
import { useId, useState, type FormEvent } from 'react'
import { ShieldAlert } from 'lucide-react'
import toast from 'react-hot-toast'
import { usePermissions } from '@/hooks/usePermissions'
import { useNow } from '@/hooks/useNow'
import { refreshRetentionHolds } from '@/hooks/useRetentionHolds'
import { placeRetentionHold } from '@/services/retentionHoldService'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { HOLD_REASON_MAX, holdsCovering, type HoldRef, type RetentionHoldCreate } from '@/types/retentionHolds'
import {
  entityLabel,
  expiryToIso,
  heldLine,
  ON_HOLD_LABEL,
  serverSentence,
  validateReason,
} from '@/components/retention/holdsModel'
import { formatDay } from '@/utils/formatters'
import { appMutate } from '@/utils/swrCacheMutate'

export const PLACE_HOLD_FAILED = 'Could not place the hold.'
export const EXPIRY_INVALID = 'Pick a valid expiry date, or leave it empty.'

export interface PlaceHoldFormProps {
  projectId: string
  entityType: 'release' | 'compliance_pack'
  entityId: string
  /** For the success toast: `Hold placed on <targetName>`. */
  targetName: string
  title: string
  onClose: () => void
  onPlaced: () => void
}

/**
 * The inline Place-hold form (reason 3-500 with a counter, optional expiry
 * date sent as the end of that local day). Shared by the release strip and
 * the compliance-pack row; the scope is fixed by the caller. The body is
 * `{entity_type, entity_id, reason}` (+ `expires_at` only when a date is set).
 */
export function PlaceHoldForm({
  projectId, entityType, entityId, targetName, title, onClose, onPlaced,
}: PlaceHoldFormProps) {
  const reasonId = useId()
  const expiresId = useId()
  const [reason, setReason] = useState('')
  const [expires, setExpires] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const invalid = validateReason(reason)
    if (invalid) return setError(invalid)
    const expiresAt = expires.trim() ? expiryToIso(expires) : null
    if (expires.trim() && !expiresAt) return setError(EXPIRY_INVALID)
    const body: RetentionHoldCreate = {
      entity_type: entityType,
      entity_id: entityId,
      reason: reason.trim(),
      ...(expiresAt ? { expires_at: expiresAt } : {}),
    }
    setBusy(true)
    setError(null)
    try {
      await placeRetentionHold(projectId, body)
      toast.success(`Hold placed on ${targetName}`)
      void refreshRetentionHolds(projectId)
      onPlaced()
    } catch (err) {
      setError(serverSentence(err, PLACE_HOLD_FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={title}
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      onSubmit={submit}
      dataAttributes={{ 'data-place-hold-form': entityType }}
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={busy} className="btn-secondary btn-sm">Cancel</button>
          <button type="submit" disabled={busy} className="btn-primary btn-sm">Place hold</button>
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
          <p className="text-right text-xs text-[var(--color-text-muted)]">{reason.trim().length}/{HOLD_REASON_MAX}</p>
        </div>
        <div>
          <label htmlFor={expiresId} className="block text-xs font-medium text-[var(--color-text)]">Expires (optional)</label>
          <input
            id={expiresId}
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className="input mt-1 w-40 text-xs"
          />
        </div>
        {error && <p role="alert" className="text-xs text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

export interface ReleaseHoldStripProps {
  projectId: string
  releaseId: string
  releaseName: string
  /** The release detail's `holds` (live holds covering the release); absent = none. */
  holds?: HoldRef[] | null
  /** Re-read the release detail. */
  onChanged: () => void
}

/** How many holds are listed before `+n more`. */
export const HOLDS_SHOWN = 3

function holdUntilLine(hold: HoldRef): string {
  const what = entityLabel(hold)
  return hold.expires_at ? `${what} · until ${formatDay(hold.expires_at)}` : `${what} · No expiry`
}

export default function ReleaseHoldStrip({ projectId, releaseId, releaseName, holds, onChanged }: ReleaseHoldStripProps) {
  const { isQaLead } = usePermissions()
  const [placing, setPlacing] = useState(false)
  const now = useNow()
  const live = holdsCovering(Array.isArray(holds) ? holds : [], { kind: 'release', id: releaseId }, new Date(now))
  if (live.length === 0 && !isQaLead) return null

  const held = live.length > 0
  const shown = live.slice(0, HOLDS_SHOWN)
  const more = live.length - shown.length

  return (
    <section
      data-release-hold={held ? 'held' : 'none'}
      aria-label="Retention hold"
      className={
        held
          ? 'space-y-1.5 rounded-md border border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] px-3 py-2'
          : 'space-y-1.5'
      }
    >
      <div data-release-hold-header="" className="flex items-center gap-2">
        {held && (
          <span
            data-release-hold-chip=""
            title={live.map(heldLine).join('\n')}
            className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded border border-[var(--ui-warning-bd)] px-1.5 text-xs font-medium text-[var(--ui-warning-ink)]"
          >
            <ShieldAlert aria-hidden="true" className="h-3 w-3" />
            {ON_HOLD_LABEL}
          </span>
        )}
        {isQaLead && !placing && (
          <button
            type="button"
            onClick={() => setPlacing(true)}
            className="btn-secondary btn-sm ml-auto shrink-0 whitespace-nowrap"
          >
            Place hold
          </button>
        )}
      </div>
      {held && (
        <ul className="space-y-1" data-release-hold-list="">
          {shown.map((hold) => (
            <li key={hold.hold_id} data-release-hold-item={hold.hold_id} className="min-w-0">
              <p className="truncate text-xs text-[var(--ui-warning-ink)]" title={heldLine(hold)}>
                {heldLine(hold)}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">{holdUntilLine(hold)}</p>
            </li>
          ))}
          {more > 0 && <li className="text-xs text-[var(--color-text-muted)]">+{more} more</li>}
        </ul>
      )}
      {isQaLead && placing && (
        <PlaceHoldForm
          projectId={projectId}
          entityType="release"
          entityId={releaseId}
          targetName={releaseName}
          title={`Place a hold on ${releaseName}`}
          onClose={() => setPlacing(false)}
          onPlaced={() => {
            setPlacing(false)
            onChanged()
            // A release hold pins the release's packs too: their rows re-read their `holds`.
            void appMutate(['compliance-packs', releaseId])
          }}
        />
      )}
    </section>
  )
}
