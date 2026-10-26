/**
 * /users?tab=invitations (W17b ADM-01, admin only): invitations nobody has accepted yet.
 *
 * Revoke kills a link; Resend issues a new one (new token, new expiry — the old
 * link stops working) and emails it when SMTP is configured. The raw token is
 * never stored, so a link can only be copied right after it is issued: Resend
 * is also how an admin gets a copyable link again.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th } from '@/components/ui/Table'
import { refreshInvitations, useInvitations } from '@/hooks/useUserManagement'
import { userManagementService, type InvitationSummary } from '@/services/userManagementService'
import { copyTextToClipboard } from '@/utils/clipboard'
import { buildOneTimeLink, type OneTimeLinkPath } from '@/utils/oneTimeLink'
import { formatCompactDateTime } from '@/utils/formatters'

/** "Emailed to x" or "SMTP is not configured — copy this link", then the link and a Copy button. */
export function OneTimeLinkBox({
  link,
  path,
  emailSent,
  email,
  what,
}: {
  link: string
  path: OneTimeLinkPath
  emailSent: boolean
  email: string
  what: 'Invitation' | 'Reset link'
}) {
  const url = buildOneTimeLink(link, path)
  return (
    <div className="space-y-2" data-testid="one-time-link">
      <p className="text-sm text-[var(--color-text-secondary)]">
        {emailSent ? (
          <>{what} emailed to <strong>{email}</strong>. You can also copy the link:</>
        ) : (
          <>SMTP is not configured — copy this link and send it to <strong>{email}</strong>:</>
        )}
      </p>
      <div className="flex items-start gap-2">
        <code className="flex-1 text-xs text-[var(--status-passed)] break-all bg-[var(--color-bg-input)] border border-[var(--color-border)] rounded p-2">
          {url || 'Invalid link received from the server.'}
        </code>
        <button
          type="button"
          disabled={!url}
          onClick={async () => {
            const ok = await copyTextToClipboard(url)
            if (ok) toast.success('Copied!')
            else toast.error('Clipboard access denied — copy manually')
          }}
          className="btn-secondary btn-sm disabled:opacity-40"
        >
          Copy link
        </button>
      </div>
    </div>
  )
}

function roleText(role: string) {
  return role.replace(/_/g, ' ')
}

function formatDate(value: string | null) {
  return value ? formatCompactDateTime(value) : '—'
}

export function PendingInvitationsTab() {
  const { data, isLoading, error } = useInvitations(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [issued, setIssued] = useState<Record<string, { link: string; emailSent: boolean }>>({})
  const rows: InvitationSummary[] = Array.isArray(data) ? data : []

  async function revoke(inv: InvitationSummary) {
    setBusy(inv.id)
    try {
      await userManagementService.revokeInvitation(inv.id)
      toast.success(`Invitation for ${inv.email} revoked`)
      await refreshInvitations()
    } catch {
      toast.error('Failed to revoke the invitation')
    } finally {
      setBusy(null)
    }
  }

  async function resend(inv: InvitationSummary) {
    setBusy(inv.id)
    try {
      const res = await userManagementService.resendInvitation(inv.id)
      setIssued((prev) => ({ ...prev, [inv.id]: { link: res.invitation_link, emailSent: res.email_sent === true } }))
      toast.success(res.email_sent ? `New invitation emailed to ${inv.email}` : 'New invitation link created')
      await refreshInvitations()
    } catch {
      toast.error('Failed to resend the invitation')
    } finally {
      setBusy(null)
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>
  if (error) return <p className="text-sm text-[var(--ui-danger)]">Could not load invitations.</p>

  return (
    <div className="bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border)] overflow-hidden">
      <Table data-testid="pending-invitations">
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            <Th>Email</Th>
            <Th>Role</Th>
            <Th>Projects</Th>
            <Th>Invited by</Th>
            <Th>Expires</Th>
            <Th>Actions</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border)]">
          {rows.map((inv) => (
            <InvitationRow
              key={inv.id}
              inv={inv}
              busy={busy === inv.id}
              issued={issued[inv.id]}
              onRevoke={() => revoke(inv)}
              onResend={() => resend(inv)}
            />
          ))}
          {rows.length === 0 && (
            <tr>
              <Td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-muted)]">
                No pending invitations. Invite someone from the Users tab.
              </Td>
            </tr>
          )}
        </tbody>
      </Table>
    </div>
  )
}

function InvitationRow({
  inv,
  busy,
  issued,
  onRevoke,
  onResend,
}: {
  inv: InvitationSummary
  busy: boolean
  issued?: { link: string; emailSent: boolean }
  onRevoke: () => void
  onResend: () => void
}) {
  const expired = inv.status === 'expired'
  const projects = Array.isArray(inv.project_memberships) ? inv.project_memberships : []
  return (
    <>
      <tr>
        <Td className="px-4 py-3 text-[var(--color-text)]">{inv.email}</Td>
        <Td className="px-4 py-3 text-[var(--color-text-secondary)]">{roleText(inv.role)}</Td>
        <Td className="px-4 py-3 text-[var(--color-text-secondary)]">
          {projects.length ? projects.map((p) => p.project_name || 'Unknown project').join(', ') : '—'}
        </Td>
        <Td className="px-4 py-3 text-[var(--color-text-secondary)]">{inv.invited_by_name || '—'}</Td>
        <Td className="px-4 py-3">
          <span className={expired ? 'text-[var(--ui-danger)]' : 'text-[var(--color-text-secondary)]'}>
            {expired ? `Expired ${formatDate(inv.expires_at)}` : formatDate(inv.expires_at)}
          </span>
        </Td>
        <Td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <button type="button" disabled={busy} onClick={onResend} className="btn-secondary btn-sm">
              Resend
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onRevoke}
              className="text-xs px-2 py-1 rounded text-[var(--ui-danger)] hover:underline"
            >
              Revoke
            </button>
          </div>
        </Td>
      </tr>
      {issued && (
        <tr>
          <Td colSpan={6} className="px-4 pb-3">
            <OneTimeLinkBox link={issued.link} path="/accept-invite" emailSent={issued.emailSent} email={inv.email} what="Invitation" />
          </Td>
        </tr>
      )}
    </>
  )
}
