import { useState } from 'react'
import { AlertTriangle, Check, Copy, Download } from 'lucide-react'
import { copyTextToClipboard } from '@/utils/clipboard'

interface Props {
  codes: string[]
  /** Regeneration also invalidates the previous set — say so. */
  variant?: 'initial' | 'regenerated'
  onAcknowledge: () => void
  acknowledgeLabel?: string
}

/**
 * The one and only time these codes are ever visible.
 *
 * Everything here exists to stop someone clicking past it: the codes cannot be
 * dismissed until the checkbox is ticked, the copy says plainly that they will
 * not be shown again and that each works once, and both Copy and Download are
 * offered because people save secrets in different places.
 */
export default function RecoveryCodesPanel({
  codes,
  variant = 'initial',
  onAcknowledge,
  acknowledgeLabel = 'Done',
}: Props) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [copied, setCopied] = useState(false)

  const asText = codes.join('\n')

  const handleCopy = async () => {
    const ok = await copyTextToClipboard(asText)
    setCopied(ok)
    if (ok) window.setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = () => {
    const body = [
      'TestLookup recovery codes',
      `Generated ${new Date().toISOString()}`,
      '',
      'Each code works exactly once. Store them somewhere you can reach',
      'without your phone. They will not be shown again.',
      '',
      ...codes,
      '',
    ].join('\n')
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'testlookup-recovery-codes.txt'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div
      data-testid="recovery-codes-panel"
      className="space-y-4 rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg-soft)] p-4"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-broken)]" />
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            Save your recovery codes now
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)]">
            This is the only time these codes will be shown. Each one works{' '}
            <strong>exactly once</strong>, and they are what gets you back in if you lose
            your authenticator device. Store them somewhere you can reach without your
            phone.
          </p>
          {variant === 'regenerated' && (
            <p className="text-xs font-medium text-[var(--status-broken)]">
              Your previous recovery codes have just been invalidated — delete any old copy
              you kept.
            </p>
          )}
        </div>
      </div>

      <ul
        data-testid="recovery-codes-list"
        className="grid grid-cols-1 gap-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-input)] p-3 font-mono text-sm text-[var(--color-text)] sm:grid-cols-2"
      >
        {codes.map((code) => (
          <li key={code} className="select-all tracking-wider">
            {code}
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-[var(--status-passed)]" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {copied ? 'Copied' : 'Copy all'}
        </button>
        <button
          type="button"
          onClick={handleDownload}
          className="flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
        >
          <Download className="h-3.5 w-3.5" />
          Download .txt
        </button>
      </div>

      <label className="flex cursor-pointer items-start gap-2 text-xs text-[var(--color-text)]">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
          className="mt-0.5"
        />
        <span>I have saved these recovery codes somewhere safe.</span>
      </label>

      <button
        type="button"
        disabled={!acknowledged}
        onClick={onAcknowledge}
        className="btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
      >
        {acknowledgeLabel}
      </button>
    </div>
  )
}
