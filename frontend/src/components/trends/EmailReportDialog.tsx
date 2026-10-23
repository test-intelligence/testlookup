/**
 * TRND-01: "Email report…" from the Trends header's ⋯ (QA lead and above).
 *
 * It states what will be sent (`Trends · last 14 days · suite payments ·
 * release 2.4`), lists the project's members — service accounts, synthetic
 * and inactive users left out — as checkboxes with name and email, and takes
 * up to 5 typed addresses. Whether the relay saved in Settings › Notifications
 * can deliver is asked when the dialog OPENS (`useEmailAvailability`), so a
 * reader is told why it cannot before any click, and Send stays disabled.
 *
 * The server is the authority on every rule (members of THIS project only, the
 * egress allow-list, the 10-recipient cap); its refusal is shown as TEXT under
 * the form. Names and addresses are user data: React text only.
 *
 * W27b PAN-05b: the form renders IN THE PAGE (an `InlineFormSection`, region
 * `Email report`) right under the Trends page header; no backdrop, no trap.
 * It reads the relay and the members when it mounts (= opens), as before.
 */
import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { X } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useEmailAvailability } from '@/hooks/useEmailAvailability'
import { usePermissions } from '@/hooks/usePermissions'
import { useReleases } from '@/hooks/useReleases'
import { useProjectMembers } from '@/hooks/useUserManagement'
import { scopeArg, type ScopeValue } from '@/lib/scopeParams'
import { emailTrends, refusalText } from '@/services/trendsReportService'
import type { ProjectMember } from '@/services/userManagementService'
import type { EmailTrendsBody } from '@/types/trends'

export interface EmailReportDialogProps {
  projectId: string
  /** The page's window, days. */
  days: number
  /** The page's suite scope and release scope, sent as the page reads them. */
  suiteFilter: ScopeValue
  release: ScopeValue
  /** What the summary line names: the suite, as the page shows it. */
  suiteLabel: string | null
  /**
   * The release, as the summary line names it. Absent: read from the
   * project's release list (the cached SWR entry the hero's markers already
   * read, so opening the dialog asks nothing new).
   */
  releaseLabel?: string | null
  onClose: () => void
  /** The slot of the area under the page header (asks before typed input is replaced). */
  slot?: InlineSlot
}

/** The server's caps (`EmailTrendsRequest`): 5 typed addresses, 10 recipients in all. */
export const MAX_TYPED_ADDRESSES = 5
export const MAX_RECIPIENTS = 10
export const NOT_CONFIGURED_TEXT = 'Email is not configured — Settings › Notifications.'

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const FIELD =
  'min-h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm text-[var(--color-text)]'

/** The members a report can go to: real, active people (`ProjectMemberResponse`'s flags). */
export function mailableMembers(members: readonly ProjectMember[] | null | undefined): ProjectMember[] {
  if (!Array.isArray(members)) return []
  return members.filter(
    (m) => m && typeof m.user_id === 'string' && !m.is_service_account && !m.is_synthetic && m.is_active !== false && Boolean(m.email),
  )
}

/** "Trends · last 14 days · suite payments · release 2.4". */
export function reportSummaryLine(days: number, suite: string | null, release: string | null): string {
  return [
    'Trends',
    days === 1 ? 'last 24 hours' : `last ${days} days`,
    suite ? `suite ${suite}` : null,
    release ? `release ${release}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

const plural = (n: number) => `${n} recipient${n === 1 ? '' : 's'}`

/** The page's release scope as names ("2.4, 2.5"); an id the list does not hold is shown short. */
export function releaseText(
  release: ScopeValue,
  items: readonly { id: string; name: string }[] | null | undefined,
): string | null {
  const ids = Array.isArray(release) ? release : typeof release === 'string' && release ? [release] : []
  if (ids.length === 0) return null
  const names = new Map((Array.isArray(items) ? items : []).map((r) => [r.id, r.name]))
  return ids.map((id) => names.get(id) ?? id.slice(0, 8)).join(', ')
}

export default function EmailReportDialog({
  projectId,
  days,
  suiteFilter,
  release,
  suiteLabel,
  releaseLabel,
  onClose,
  slot,
}: EmailReportDialogProps) {
  const id = useId()
  const { isAdmin } = usePermissions()
  const { availability, isLoading: checking, error: availabilityError } = useEmailAvailability(true)
  const { data: memberData, isLoading: membersLoading, error: membersError } = useProjectMembers(projectId)
  const members = useMemo(() => mailableMembers(memberData), [memberData])
  const { data: releaseList } = useReleases(undefined, { cached: true })
  const shownRelease = releaseLabel !== undefined ? releaseLabel : releaseText(release, releaseList?.items)
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set())
  const [typed, setTyped] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [draftProblem, setDraftProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const available = availability?.available === true
  const blockedReason = availability && !availability.available ? (availability.reason ?? NOT_CONFIGURED_TEXT) : null
  const notConfigured = availability?.code === 'not_configured'
  const recipients = chosen.size + typed.length
  const canSend = available && recipients >= 1 && recipients <= MAX_RECIPIENTS && !busy

  const toggle = (userId: string) =>
    setChosen((current) => {
      const next = new Set(current)
      if (next.has(userId)) next.delete(userId)
      else next.add(userId)
      return next
    })

  const addTyped = () => {
    const address = draft.trim().toLowerCase()
    if (!address) return
    if (!EMAIL.test(address)) {
      setDraftProblem('That is not an email address.')
      return
    }
    if (typed.includes(address)) {
      setDraft('')
      setDraftProblem(null)
      return
    }
    if (typed.length >= MAX_TYPED_ADDRESSES) {
      setDraftProblem(`At most ${MAX_TYPED_ADDRESSES} typed addresses.`)
      return
    }
    setTyped((current) => [...current, address])
    setDraft('')
    setDraftProblem(null)
  }

  const send = async () => {
    if (!canSend) return
    setBusy(true)
    setRefusal(null)
    const suiteArg = scopeArg(suiteFilter)
    const releaseArg = scopeArg(release)
    const body: EmailTrendsBody = {
      project_id: projectId,
      days,
      ...(releaseArg !== null ? { release_id: releaseArg } : {}),
      ...(suiteArg !== null ? { suite_name: Array.isArray(suiteArg) ? suiteArg : [suiteArg] } : {}),
      recipient_user_ids: [...chosen],
      recipient_emails: typed,
    }
    try {
      const result = await emailTrends(body)
      const sent = typeof result?.sent_to === 'number' ? result.sent_to : recipients
      toast.success(`Sent to ${plural(sent)}`)
      onClose()
    } catch (error) {
      setRefusal(await refusalText(error, 'The report could not be sent.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title="Email report"
      titleId={`${id}-title`}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      closeLabel="Close email dialog"
      slot={slot}
      dataAttributes={{ 'data-email-report-dialog': '' }}
      description={
        <p data-email-report-summary="" className="m-0 text-sm text-[var(--color-text-secondary)] break-words">
          {reportSummaryLine(days, suiteLabel, shownRelease)}
        </p>
      }
      footer={
        <>
          <span className="mr-auto self-center text-xs tabular-nums text-[var(--color-text-secondary)]">{plural(recipients)}</span>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            data-email-send=""
            onClick={() => void send()}
            disabled={!canSend}
            className="btn-primary flex items-center gap-2"
          >
            {busy && <LoadingSpinner size="sm" />}
            Send
          </button>
        </>
      }
    >
      {/* The recipients keep a readable width; the section spans its column. */}
      <div className="max-w-2xl">
        {checking ? (
          <p data-email-availability="checking" className="mt-3 text-xs text-[var(--color-text-secondary)]">
            Checking the mail relay…
          </p>
        ) : blockedReason ? (
          <p role="status" data-email-availability="blocked" className="mt-3 rounded-md border border-[var(--gate-conditional-border)] bg-[var(--gate-conditional-bg-soft)] px-3 py-2 text-sm text-[var(--color-text)] break-words">
            {blockedReason}
            {isAdmin && notConfigured ? (
              <>
                {' '}
                <Link to="/settings/notifications" className="font-medium underline" onClick={onClose}>
                  Open Settings › Notifications
                </Link>
              </>
            ) : null}
          </p>
        ) : !availability ? (
          <p role="status" data-email-availability="unknown" className="mt-3 text-xs text-[var(--status-broken)]">
            {availabilityError ? 'The mail relay could not be checked, so the report cannot be sent right now.' : 'Not available'}
          </p>
        ) : null}

        <fieldset className="mt-4 border-0 p-0">
          <legend className="mb-1 text-xs font-semibold text-[var(--color-text-secondary)]">Project members</legend>
          {membersLoading ? (
            <p className="text-xs text-[var(--color-text-secondary)]">Loading members…</p>
          ) : membersError ? (
            <p className="text-xs text-[var(--status-broken)]">The member list is not available.</p>
          ) : members.length === 0 ? (
            <p className="text-xs text-[var(--color-text-secondary)]">No members can receive email.</p>
          ) : (
            <ul data-email-members="" className="m-0 flex max-h-56 list-none flex-col gap-1 overflow-y-auto p-0">
              {members.map((member) => (
                <li key={member.user_id}>
                  <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                    <input
                      type="checkbox"
                      data-email-member={member.user_id}
                      checked={chosen.has(member.user_id)}
                      onChange={() => toggle(member.user_id)}
                    />
                    <span className="min-w-0 truncate">
                      {member.full_name || member.username}
                      <span className="text-[var(--color-text-secondary)]"> · {member.email}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>

        <div className="mt-3 flex flex-col gap-1">
          <label htmlFor={`${id}-address`} className="text-xs font-semibold text-[var(--color-text-secondary)]">
            Add an address
          </label>
          <div className="flex gap-2">
            <input
              id={`${id}-address`}
              data-email-address=""
              type="email"
              value={draft}
              placeholder="name@example.com"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addTyped()
                }
              }}
              className={FIELD}
            />
            <button
              type="button"
              data-email-address-add=""
              onClick={addTyped}
              disabled={typed.length >= MAX_TYPED_ADDRESSES}
              className="rounded-md border border-[var(--color-border)] px-3 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
            >
              Add
            </button>
          </div>
          {draftProblem ? <p className="m-0 text-xs text-[var(--status-broken)]">{draftProblem}</p> : null}
          {typed.length > 0 ? (
            <ul data-email-typed="" className="m-0 mt-1 flex list-none flex-wrap gap-1 p-0">
              {typed.map((address) => (
                <li key={address} className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text)]">
                  {address}
                  <button
                    type="button"
                    aria-label={`Remove ${address}`}
                    onClick={() => setTyped((current) => current.filter((a) => a !== address))}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {recipients > MAX_RECIPIENTS ? (
          <p className="mt-2 text-xs text-[var(--status-broken)]">Choose at most {MAX_RECIPIENTS} recipients.</p>
        ) : null}
        {refusal ? (
          <p role="alert" data-email-refusal="" className="mt-2 text-xs text-[var(--status-failed)] break-words">
            {refusal}
          </p>
        ) : null}
      </div>
    </InlineFormSection>
  )
}
