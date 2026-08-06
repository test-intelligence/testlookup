import { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Check, Copy, QrCode } from 'lucide-react'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatSecret } from '@/utils/mfaFormat'

interface Props {
  otpauthUri: string
  secret: string
  issuer: string
  accountName: string
  digits: number
  periodSeconds: number
}

/**
 * The enrollment QR, plus the manual-entry secret that always sits beside it.
 *
 * The backend deliberately returns `otpauth_uri` and no image, so rendering is
 * ours. The manual secret is NOT a progressive-enhancement nicety: plenty of
 * people set this up on a desktop with no camera pointed at the screen, and a
 * QR-only screen is how you lock those users out of their own account.
 *
 * The QR itself is drawn black-on-white in every theme. That is a scanner
 * contrast requirement, not a styling choice — a themed QR on a dark card
 * fails to decode on many phones.
 */
export default function OtpAuthQr({
  otpauthUri,
  secret,
  issuer,
  accountName,
  digits,
  periodSeconds,
}: Props) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    const ok = await copyTextToClipboard(secret)
    setCopied(ok)
    if (ok) window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
      {/* QR */}
      <div className="flex flex-col items-center gap-2">
        <div className="rounded-lg bg-white p-3">
          <QRCodeSVG
            value={otpauthUri}
            size={168}
            level="M"
            bgColor="white"
            fgColor="black"
            title={`TOTP enrollment QR code for ${accountName}`}
          />
        </div>
        <p className="flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
          <QrCode className="h-3 w-3" />
          Scan with your authenticator app
        </p>
      </div>

      {/* Manual entry — the fallback that keeps people out of lockout */}
      <div className="min-w-0 flex-1 space-y-3">
        <div>
          <p className="text-sm font-medium text-[var(--color-text)]">
            Can&apos;t scan? Enter this key by hand
          </p>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Works on any desktop authenticator, or on a phone with no camera access.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <code
            data-testid="mfa-manual-secret"
            className="min-w-0 flex-1 select-all break-all rounded border border-[var(--color-border)] bg-[var(--color-bg-input)] px-3 py-2 font-mono text-sm tracking-wider text-[var(--color-text)]"
          >
            {formatSecret(secret)}
          </code>
          <button
            type="button"
            onClick={handleCopy}
            aria-label="Copy setup key"
            className="flex items-center gap-1 rounded border border-[var(--color-border)] px-2.5 py-2 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-[var(--status-passed)]" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[var(--color-text-muted)]">
          <div className="flex gap-1">
            <dt>Account</dt>
            <dd className="truncate text-[var(--color-text-secondary)]">{accountName}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Issuer</dt>
            <dd className="truncate text-[var(--color-text-secondary)]">{issuer}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Type</dt>
            <dd className="text-[var(--color-text-secondary)]">Time-based ({digits} digits)</dd>
          </div>
          <div className="flex gap-1">
            <dt>Interval</dt>
            <dd className="text-[var(--color-text-secondary)]">{periodSeconds}s</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}
