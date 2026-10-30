/**
 * Settings › AI › Review gate (W23c AGT-10): `/settings/review-gate`, admin-only.
 *
 * When unreviewed AI reports stop leaving TestLookup. Until a date is set the gate only COUNTS what it would
 * have withheld (D17: unset on upgrade = audit only); the owner picks the date from those counts.
 *
 *  - Current state: a neutral badge (Audit only / Enforced) and the server's sentence filled with the server's
 *    30-day would-refuse counts; the by-channel list is every channel the server returned (never invented,
 *    never top-N); "Not measured: …" with NO digit when the audit log could not be read.
 *  - Enforcement date: a `datetime-local` (the viewer's local time) sent as ISO 8601 WITH the browser's offset
 *    (a naive value is a 422). A future instant saves at once; a past or now instant asks inline first
 *    (`InlineConfirm`, never `confirm(`): it enforces at once. `Clear date` sends `null` (back to audit only).
 *    A refusal (403 `interactive_only`, 422) is shown under the field in the server's words.
 *  - Gated channels: the server's list, what else changes, and the 30-second propagation.
 *
 * Reads on load: ONE `GET /api/v1/settings/review-gate` (admins only — anyone else is told who can change it,
 * and nothing is read). Every name, label and date is React text.
 *
 * Widths (CI's DejaVu Sans, the ≈ 698 px settings column at 1280): the sentences wrap inside their card by design
 * (`[overflow-wrap:anywhere]`); the by-channel `<dl>` is `1fr auto` (the longest label ≈ 354 px + the count); the
 * date row is label · 224 px input · two `btn-sm` buttons ≈ 538 px, wrapping below that.
 */
import { useId, useState } from 'react'
import { helpTopicParam } from '@/components/help/helpTopics'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import { Panel } from '@/components/ui/PanelHeader'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import {
  ADMIN_ONLY,
  ALSO_CHANGES,
  BY_CHANNEL_TITLE,
  CHANNELS_TITLE,
  CLEAR_DATE_LABEL,
  DATE_REQUIRED,
  DATE_TITLE,
  ENFORCE_FROM_LABEL,
  ENFORCE_NOW_LABEL,
  LOAD_ERROR,
  NONE_IN_WINDOW,
  PAGE_SUBTITLE,
  PAGE_TITLE,
  PROPAGATION,
  SET_DATE_LABEL,
  STATE_TITLE,
  badgeLabel,
  channelRows,
  isPast,
  measuredSinceLine,
  saveRefusal,
  stateLines,
  toLocalInput,
  toOffsetIso,
  zoneLine,
} from '@/components/reviewGate/reviewGateModel'
import { usePermissions } from '@/hooks/usePermissions'
import { useReviewGate } from '@/hooks/useReviewGate'
import { GATE_SENTENCES, type ReviewGateRead } from '@/types/reviewGate'
import { formatNumber } from '@/utils/formatters'

const HELP_TOPIC = helpTopicParam('/settings/review-gate')
const BADGE = 'badge border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]'
const SENTENCE = 'm-0 text-sm text-[var(--color-text)] [overflow-wrap:anywhere]'
const HINT = 'm-0 text-xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]'

export default function ReviewGatePage() {
  const { isAdmin } = usePermissions()
  return (
    <div className="space-y-4">
      <PageHeader compact title={PAGE_TITLE} subtitle={PAGE_SUBTITLE} helpTopic={HELP_TOPIC} />
      {isAdmin ? (
        <ReviewGateForAdmin />
      ) : (
        <p data-review-gate-admin-only="" className="m-0 text-sm text-[var(--color-text-secondary)]">{ADMIN_ONLY}</p>
      )}
    </div>
  )
}

function ReviewGateForAdmin() {
  const { data, error, isLoading, mutate, save } = useReviewGate()
  if (error && !data) {
    return (
      <div className="card space-y-2" role="alert" data-review-gate-error="">
        <p className="m-0 text-sm font-medium text-[var(--color-text)]">{LOAD_ERROR}</p>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
      </div>
    )
  }
  if (isLoading || !data) {
    return (
      <div className="flex justify-center py-10">
        <LoadingSpinner size="lg" />
      </div>
    )
  }
  return (
    <div className="space-y-4" data-review-gate="">
      <StateCard gate={data} />
      <DateCard gate={data} save={save} />
      <ChannelsCard gate={data} />
    </div>
  )
}

function StateCard({ gate }: { gate: ReviewGateRead }) {
  const now = new Date()
  const rows = channelRows(gate)
  const since = measuredSinceLine(gate, now)
  const measured = Boolean(gate.would_refuse?.measured)
  return (
    <div data-review-gate-state="">
      <Panel title={STATE_TITLE} actions={<span className={BADGE} data-review-gate-badge="">{badgeLabel(gate)}</span>}>
        <div className="space-y-3 p-4">
          {stateLines(gate).map((line) => (
            <p key={line} className={SENTENCE}>{line}</p>
          ))}
          {measured && (
            <div className="space-y-1.5">
              <h4 className="m-0 text-xs font-semibold text-[var(--color-text-secondary)]">{BY_CHANNEL_TITLE}</h4>
              {rows.length === 0 ? (
                <p className={HINT}>{NONE_IN_WINDOW}</p>
              ) : (
                <dl className="m-0 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm" data-review-gate-by-channel="">
                  {rows.map((row) => (
                    <div key={row.channel} className="contents" data-channel={row.channel}>
                      <dt className="min-w-0 text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">{row.label}</dt>
                      <dd className="m-0 text-right tabular-nums text-[var(--color-text)]">{formatNumber(row.decisions)}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {since && <p className={HINT}>{since}</p>}
            </div>
          )}
        </div>
      </Panel>
    </div>
  )
}

function DateCard({ gate, save }: { gate: ReviewGateRead; save: (enforcedFrom: string | null) => Promise<unknown> }) {
  const inputId = useId()
  const [value, setValue] = useState(() => toLocalInput(gate.enforced_from))
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function send(enforcedFrom: string | null) {
    setBusy(true)
    setMessage(null)
    try {
      await save(enforcedFrom)
      setConfirming(null)
      if (enforcedFrom === null) setValue('')
    } catch (err) {
      setMessage(saveRefusal(err))
    } finally {
      setBusy(false)
    }
  }

  function setDate() {
    const iso = toOffsetIso(value)
    if (!iso) {
      setMessage(DATE_REQUIRED)
      return
    }
    setMessage(null)
    // A past (or now) instant enforces the gate at once: asked where the action lives, never with confirm().
    if (isPast(value, new Date())) setConfirming(iso)
    else void send(iso)
  }

  return (
    <div data-review-gate-date="">
      <Panel title={DATE_TITLE}>
        <div className="space-y-2 p-4">
          {gate.env_override && <p className={SENTENCE} data-review-gate-env="">{GATE_SENTENCES.env}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor={inputId} className="text-sm text-[var(--color-text-secondary)]">{ENFORCE_FROM_LABEL}</label>
            <input
              id={inputId}
              type="datetime-local"
              className="input w-56"
              value={value}
              disabled={busy}
              onChange={(e) => {
                setValue(e.target.value)
                setConfirming(null)
                setMessage(null)
              }}
            />
            <button type="button" className="btn-primary btn-sm" disabled={busy} onClick={setDate}>
              {SET_DATE_LABEL}
            </button>
            {gate.enforced_from && (
              <button type="button" className="btn-secondary btn-sm" disabled={busy} onClick={() => void send(null)}>
                {CLEAR_DATE_LABEL}
              </button>
            )}
          </div>
          <p className={HINT}>{zoneLine(new Date())}</p>
          {confirming && (
            <InlineConfirm
              testId="review-gate-past-date"
              message={GATE_SENTENCES.past_date_warning}
              confirmLabel={ENFORCE_NOW_LABEL}
              tone="danger"
              busy={busy}
              onConfirm={() => void send(confirming)}
              onCancel={() => setConfirming(null)}
            />
          )}
          {message && (
            <p role="alert" data-review-gate-refusal="" className="m-0 text-xs text-[var(--ui-danger-ink)]">{message}</p>
          )}
        </div>
      </Panel>
    </div>
  )
}

function ChannelsCard({ gate }: { gate: ReviewGateRead }) {
  return (
    <div data-review-gate-channels="">
      <Panel title={CHANNELS_TITLE}>
        <div className="space-y-2 p-4">
          <ul className="m-0 list-disc space-y-0.5 pl-5 text-sm text-[var(--color-text)]">
            {(gate.channels ?? []).map((c) => (
              <li key={c.channel} data-channel={c.channel} className="[overflow-wrap:anywhere]">{c.label}</li>
            ))}
          </ul>
          <p className={SENTENCE}>{ALSO_CHANGES}</p>
          <p className={HINT}>{PROPAGATION}</p>
        </div>
      </Panel>
    </div>
  )
}
