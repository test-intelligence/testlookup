/**
 * Share a run's report (GATE-06): choose the layout and how long the link
 * lives, create it, copy it, and see / revoke every link the run already has.
 *
 * It replaced a one-shot "Share report" that always minted an executive link
 * valid for 7 days and could neither list nor revoke one, although the API
 * has had all three all along:
 *
 *   POST   /reports/runs/{run}/share        layout + expiry_days (1-30)
 *   GET    /reports/runs/{run}/share-links  metadata only (tokens are hashed)
 *   DELETE /reports/share-links/{link}      creator / project member / admin
 *
 * The URL exists only in the create response (the token is not stored), so
 * it is shown once, here. A refusal (a 403/409/422 from the API, e.g. the
 * human-review gate) is shown in the server's own words.
 *
 * It renders IN THE PAGE (W27 PAN-02): an `InlineFormSection` right under the
 * release gate's header, where the page places it, not a modal side panel.
 * Its name (`Share report`), close label and `data-share-*` hooks are the
 * panel's, kept; the X and Escape inside it close it. It renders nothing while
 * `open` is false.
 */
import { useState } from 'react'
import { Copy as CopyIcon, Link2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useShareLinks } from '@/hooks/useShareLinks'
import {
  createShareLink,
  revokeShareLink,
  SHARE_EXPIRY_DAYS,
  type ShareLayout,
  type ShareLink,
} from '@/services/reportExportService'
import { extractErrorMessage } from '@/services/apiErrors'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatCompactDateTime } from '@/utils/formatCompactDateTime'

const LAYOUTS: ReadonlyArray<{ value: ShareLayout; label: string }> = [
  { value: 'executive', label: 'Executive' },
  { value: 'engineering', label: 'Engineering' },
]

const DAY_MS = 86_400_000

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** The API's refusal in its own words, else `fallback`. */
function refusal(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return extractErrorMessage(detail, fallback)
}

/** Revoked, expired, or live with the time left, as of `now`. */
function linkState(link: ShareLink, now: number = Date.now()): { status: 'active' | 'expired' | 'revoked'; expiry: string } {
  const expires = Date.parse(link.expires_at)
  if (link.is_revoked) return { status: 'revoked', expiry: `revoked · expiry ${formatCompactDateTime(link.expires_at)}` }
  if (Number.isNaN(expires)) return { status: 'active', expiry: 'expiry unknown' }
  const left = expires - now
  if (left <= 0) return { status: 'expired', expiry: `expired ${formatCompactDateTime(link.expires_at)}` }
  const days = Math.ceil(left / DAY_MS)
  return { status: 'active', expiry: days > 1 ? `expires in ${plural(days, 'day')}` : `expires in ${plural(Math.max(1, Math.ceil(left / 3_600_000)), 'hour')}` }
}

function ShareLinkRow({ link, onRevoke, revoking }: { link: ShareLink; onRevoke: () => void; revoking: boolean }) {
  const { status, expiry } = linkState(link)
  const layout = LAYOUTS.find((l) => l.value === link.report_layout)?.label ?? link.report_layout
  return (
    <li
      data-share-link={link.id}
      data-share-status={status}
      className="flex items-start justify-between gap-3 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm"
    >
      <div className="min-w-0">
        <p className="text-[var(--color-text)]">
          <span className="font-medium">{layout}</span>
          <span className="text-[var(--color-text-muted)]"> · by {link.created_by_name ?? 'Unknown'} · {formatCompactDateTime(link.created_at)}</span>
        </p>
        <p data-share-summary="" className={clsx('text-xs', status === 'active' ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text-muted)]')}>
          {expiry}, {plural(link.access_count ?? 0, 'view')}
        </p>
      </div>
      {status === 'active' ? (
        <button
          type="button"
          onClick={onRevoke}
          disabled={revoking}
          className="btn-secondary shrink-0 text-xs disabled:opacity-50"
        >
          {revoking ? 'Revoking…' : 'Revoke'}
        </button>
      ) : (
        <span className="shrink-0 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
          {status === 'revoked' ? 'Revoked' : 'Expired'}
        </span>
      )}
    </li>
  )
}

export default function ShareReportDialog({ runId, open, onClose, slot }: {
  runId: string
  open: boolean
  onClose: () => void
  /** The page's form slot, when the page has one (one open form at a time under its header). */
  slot?: InlineSlot
}) {
  const { links, isLoading, isError, refresh } = useShareLinks(open ? runId : null)
  const [layout, setLayout] = useState<ShareLayout>('executive')
  const [expiryDays, setExpiryDays] = useState<number>(7)
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<ShareLink | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const create = async () => {
    setCreating(true)
    setError(null)
    try {
      const link = await createShareLink(runId, layout, expiryDays)
      setCreated(link)
      await refresh()
    } catch (err) {
      setError(refusal(err, 'The share link could not be created.'))
    } finally {
      setCreating(false)
    }
  }

  const copy = async () => {
    if (!created?.share_url) return
    const ok = await copyTextToClipboard(created.share_url)
    if (ok) toast.success('Share link copied to clipboard')
    else toast.error('Clipboard access denied — copy manually')
  }

  const revoke = async (link: ShareLink) => {
    setRevoking(link.id)
    setError(null)
    try {
      await revokeShareLink(link.id)
      toast.success('Share link revoked')
      await refresh()
    } catch (err) {
      setError(refusal(err, 'The share link could not be revoked.'))
    } finally {
      setRevoking(null)
    }
  }

  const activeCount = links.filter((l) => linkState(l).status === 'active').length

  if (!open) return null

  return (
    <InlineFormSection title="Share report" onClose={onClose} closeLabel="Close share report" slot={slot}>
      <div className="max-w-2xl space-y-5" data-share-dialog="">
        <section className="space-y-3">
          <fieldset className="space-y-1.5">
            <legend className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Layout</legend>
            <div className="flex gap-2">
              {LAYOUTS.map((option) => (
                <label key={option.value} className="flex items-center gap-1.5 text-sm text-[var(--color-text)]">
                  <input
                    type="radio"
                    name="share-layout"
                    value={option.value}
                    checked={layout === option.value}
                    onChange={() => setLayout(option.value)}
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block space-y-1.5">
            <span className="block text-xs uppercase tracking-wider text-[var(--color-text-muted)]">Link expires after</span>
            <select
              aria-label="Link expires after"
              value={expiryDays}
              onChange={(e) => setExpiryDays(Number(e.target.value))}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-sm text-[var(--color-text)]"
            >
              {SHARE_EXPIRY_DAYS.map((days) => (
                <option key={days} value={days}>{plural(days, 'day')}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void create()}
            disabled={creating}
            className="btn-primary flex items-center gap-2 text-sm disabled:opacity-50"
          >
            <Link2 className="h-3.5 w-3.5" />
            {creating ? 'Creating…' : 'Create link'}
          </button>
          {error && <p role="alert" className="text-xs text-[var(--status-failed)]">{error}</p>}
        </section>

        {created?.share_url && (
          <section aria-label="New share link" className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)]/40 p-3">
            <p className="text-xs text-[var(--color-text-muted)]">
              Copy it now: the link is shown once (its token is not stored).
            </p>
            <div className="flex gap-2">
              <input
                readOnly
                aria-label="Share URL"
                value={created.share_url}
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1.5 font-mono text-xs text-[var(--color-text)]"
              />
              <button type="button" onClick={() => void copy()} className="btn-secondary flex items-center gap-1.5 text-xs">
                <CopyIcon className="h-3.5 w-3.5" /> Copy
              </button>
            </div>
            {created.warning && (
              <p role="status" data-share-warning="" className="text-xs text-[var(--status-broken)]">{created.warning}</p>
            )}
          </section>
        )}

        <section aria-label="Share links" className="space-y-2">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            Active links <span className="font-normal text-[var(--color-text-muted)]">({activeCount})</span>
          </h3>
          {isLoading ? (
            <p className="text-xs text-[var(--color-text-muted)]">Loading share links…</p>
          ) : isError ? (
            <p className="text-xs text-[var(--status-failed)]">The share links could not be loaded.</p>
          ) : links.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)]">No share links for this run yet.</p>
          ) : (
            <ul className="space-y-2">
              {links.map((link) => (
                <ShareLinkRow key={link.id} link={link} revoking={revoking === link.id} onRevoke={() => void revoke(link)} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </InlineFormSection>
  )
}
