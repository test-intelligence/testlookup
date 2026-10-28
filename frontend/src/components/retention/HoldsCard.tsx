/**
 * Retention holds (W22a ACT-05) — the card on /settings/retention, between
 * the policy form and the purge preview.
 *
 * Lists the project's holds by state (`Live n` · `Expired n` · `Released n`,
 * the counts of the server's answer — never the length of a page), places a
 * PROJECT hold (release and pack holds are placed from their own rows, run
 * holds through the API) and releases a live hold with a reason. Every flow
 * opens inline (InlineFormSection / an in-row section): no modal, no
 * `confirm(`. The page is ADMIN-only, so Release is shown on every live row;
 * the server re-checks the role on every write anyway.
 *
 * A failed read says `Could not load the retention holds.` — "No holds" is
 * only ever answered from a successful read. Every reason, label and name is
 * React text; an entity link is built from its id and type (`entityHref`).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, ShieldAlert } from 'lucide-react'
import toast from 'react-hot-toast'
import ChipFilter from '@/pages/inbox/ChipFilter'
import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { refreshRetentionHolds, useRetentionHolds } from '@/hooks/useRetentionHolds'
import { placeRetentionHold, releaseRetentionHold } from '@/services/retentionHoldService'
import type { HoldState, RetentionHold } from '@/types/retentionHolds'
import { formatDay } from '@/utils/formatters'
import {
  HOLD_REASON_MAX,
  HOLDS_LOAD_ERROR,
  STATE_LABELS,
  entityHref,
  entityLabel,
  entityTypeLabel,
  errorStatus,
  expiryToIso,
  heldLine,
  placedLine,
  serverSentence,
  validateReason,
} from './holdsModel'

const STATES: readonly HoldState[] = ['live', 'expired', 'released']

const FIELD =
  'w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 text-sm text-[var(--color-text)]'

function InlineError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="text-xs text-[var(--ui-danger-ink)]">
      {message}
    </p>
  )
}

interface PlaceHoldFormProps {
  projectId: string
  projectName: string
  onClose: () => void
  onPlaced: () => void
}

/** Place a PROJECT hold: reason (3-500) + an optional expiry day. */
function PlaceHoldForm({ projectId, projectName, onClose, onPlaced }: PlaceHoldFormProps) {
  const [reason, setReason] = useState('')
  const [expires, setExpires] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reasonError = validateReason(reason)
  const length = reason.trim().length

  async function submit() {
    if (submitting) return
    if (reasonError) {
      setError(reasonError)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await placeRetentionHold(projectId, {
        entity_type: 'project',
        reason: reason.trim(),
        expires_at: expiryToIso(expires),
      })
      toast.success('Hold placed on the whole project')
      onPlaced()
    } catch (err) {
      setError(serverSentence(err, 'Could not place the hold.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <InlineFormSection
      title="Place a retention hold"
      headingLevel={3}
      onClose={onClose}
      canClose={!submitting}
      busy={submitting}
      dataAttributes={{ 'data-retention-hold-form': '' }}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={submitting} className="btn-secondary btn-sm">
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={submitting} className="btn-primary btn-sm">
            {submitting ? 'Placing…' : 'Place hold'}
          </button>
        </>
      }
    >
      <div className="space-y-3 text-xs">
        <p className="text-[var(--color-text-muted)]">
          Scope: <span className="text-[var(--color-text)]">Whole project ({projectName || 'this project'})</span>
        </p>
        <label htmlFor="retention-hold-reason" className="block">
          <span className="flex items-baseline justify-between text-[var(--color-text-muted)]">
            <span>Reason (3–500 characters)</span>
            <span data-reason-counter="">
              {length}/{HOLD_REASON_MAX}
            </span>
          </span>
          <textarea
            id="retention-hold-reason"
            rows={2}
            maxLength={2000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={`mt-1 ${FIELD}`}
          />
        </label>
        <label htmlFor="retention-hold-expires" className="block w-44">
          <span className="text-[var(--color-text-muted)]">Expires (optional)</span>
          <input
            id="retention-hold-expires"
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className={`mt-1 ${FIELD}`}
          />
        </label>
        <InlineError message={error} />
      </div>
    </InlineFormSection>
  )
}

interface ReleaseFormProps {
  projectId: string
  hold: RetentionHold
  onClose: () => void
  onReleased: () => void
  /** A 409 (released or expired meanwhile): the list is re-read. */
  onStale: () => void
}

/** The in-row release form: one reason input + Release hold / Cancel. */
function ReleaseForm({ projectId, hold, onClose, onReleased, onStale }: ReleaseFormProps) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputId = `retention-hold-release-${hold.id}`

  async function submit() {
    if (submitting) return
    const invalid = validateReason(reason)
    if (invalid) {
      setError(invalid)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await releaseRetentionHold(projectId, hold.id, reason.trim())
      toast.success('Hold released')
      onReleased()
    } catch (err) {
      setError(serverSentence(err, 'Could not release the hold.'))
      if (errorStatus(err) === 409) onStale()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div data-retention-hold-release="" className="mt-2 space-y-1.5 border-t border-[var(--color-border)] pt-2">
      <div className="flex flex-wrap items-end gap-2">
        <label htmlFor={inputId} className="block min-w-0 flex-1 text-xs">
          <span className="text-[var(--color-text-muted)]">Release reason</span>
          <input
            id={inputId}
            autoFocus
            value={reason}
            maxLength={2000}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && !submitting) onClose()
            }}
            className={`mt-1 ${FIELD}`}
          />
        </label>
        <button type="button" onClick={submit} disabled={submitting} className="btn-primary btn-sm">
          {submitting ? 'Releasing…' : 'Release hold'}
        </button>
        <button type="button" onClick={onClose} disabled={submitting} className="btn-secondary btn-sm">
          Cancel
        </button>
      </div>
      <InlineError message={error} />
    </div>
  )
}

interface HoldRowProps {
  projectId: string
  hold: RetentionHold
  releasing: boolean
  onRelease: () => void
  onCloseRelease: () => void
  onChanged: () => void
}

/** Line 1: type chip + entity (a link when it has one) + `Held: <reason>`; line 2: who / when. */
function HoldRow({ projectId, hold, releasing, onRelease, onCloseRelease, onChanged }: HoldRowProps) {
  const href = entityHref(hold)
  const label = entityLabel(hold)
  const held = heldLine(hold)
  const live = hold.state === 'live'
  return (
    <li data-retention-hold={hold.id} className="py-2">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 items-center gap-2 text-sm">
            <span className="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]">
              {entityTypeLabel(hold.entity_type)}
            </span>
            {href ? (
              <Link to={href} className="max-w-[40%] shrink-0 truncate text-[var(--color-accent)] hover:underline" title={label}>
                {label}
              </Link>
            ) : (
              <span className="max-w-[40%] shrink-0 truncate text-[var(--color-text)]" title={label}>
                {label}
              </span>
            )}
            <span className="min-w-0 truncate text-[var(--color-text)]" title={held}>
              {held}
            </span>
          </p>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">{placedLine(hold, formatDay)}</p>
        </div>
        {live && !releasing && (
          <button type="button" onClick={onRelease} className="btn-secondary btn-sm shrink-0">
            Release
          </button>
        )}
      </div>
      {live && releasing && (
        <ReleaseForm
          projectId={projectId}
          hold={hold}
          onClose={onCloseRelease}
          onReleased={() => {
            onCloseRelease()
            onChanged()
          }}
          onStale={onChanged}
        />
      )}
    </li>
  )
}

export interface HoldsCardProps {
  projectId: string
  projectName: string
}

export default function HoldsCard({ projectId, projectName }: HoldsCardProps) {
  const [state, setState] = useState<HoldState>('live')
  const [placing, setPlacing] = useState(false)
  const [releasingId, setReleasingId] = useState<string | null>(null)
  const { holds, total, counts, isLoading, error, mutate } = useRetentionHolds(projectId, state)
  const answered = !error && total !== null

  // Every state's list (and its counts) moves after a write.
  function refresh() {
    void refreshRetentionHolds(projectId)
  }

  const options = STATES.map((id) => ({
    id,
    label: STATE_LABELS[id],
    count: counts && typeof counts[id] === 'number' ? counts[id] : undefined,
  }))

  return (
    <section data-retention-holds-card="" aria-labelledby="retention-holds-heading" className="card space-y-3">
      <div data-retention-holds-header="" className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert aria-hidden="true" className="h-4 w-4 text-[var(--color-accent)]" />
          <h2
            id="retention-holds-heading"
            className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text)]"
          >
            Retention holds
          </h2>
        </div>
        <ChipFilter
          ariaLabel="Hold state"
          options={options}
          value={state}
          onChange={(next) => {
            setState(next)
            setReleasingId(null)
          }}
        />
        <button
          type="button"
          onClick={() => setPlacing(true)}
          disabled={placing}
          className="btn-secondary btn-sm ml-auto"
        >
          Place hold
        </button>
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">
        While a hold is live, nothing it covers is purged or deleted — scheduled purge, criteria
        deletion, single-run delete, compliance-pack retire / delete, release delete and project reset
        all refuse. Only an admin can release a hold.
      </p>

      {placing && (
        <PlaceHoldForm
          projectId={projectId}
          projectName={projectName}
          onClose={() => setPlacing(false)}
          onPlaced={() => {
            setPlacing(false)
            if (state !== 'live') setState('live')
            refresh()
          }}
        />
      )}

      {isLoading && !answered && (
        <div className="flex justify-center py-3">
          <LoadingSpinner size="sm" />
        </div>
      )}

      {error && (
        <div role="alert" data-retention-holds-error="" className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[var(--ui-danger-ink)]">{HOLDS_LOAD_ERROR}</span>
          <button type="button" onClick={() => void mutate()} className="btn-secondary btn-sm inline-flex items-center gap-1.5">
            <RefreshCw aria-hidden="true" className="h-3 w-3" /> Retry
          </button>
        </div>
      )}

      {answered && holds.length === 0 && (
        <p data-retention-holds-empty="" className="text-xs text-[var(--color-text-muted)]">
          No {STATE_LABELS[state].toLowerCase()} holds.
        </p>
      )}

      {answered && holds.length > 0 && (
        <ul data-retention-holds="" className="divide-y divide-[var(--color-border)]">
          {holds.map((hold) => (
            <HoldRow
              key={hold.id}
              projectId={projectId}
              hold={hold}
              releasing={releasingId === hold.id}
              onRelease={() => setReleasingId(hold.id)}
              onCloseRelease={() => setReleasingId(null)}
              onChanged={refresh}
            />
          ))}
        </ul>
      )}

      {answered && total !== null && total > holds.length && (
        <p data-retention-holds-total="" className="text-xs text-[var(--color-text-muted)]">
          Showing the newest {holds.length} of {total} {STATE_LABELS[state].toLowerCase()} holds.
        </p>
      )}
    </section>
  )
}
