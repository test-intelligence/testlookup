import { useState } from 'react'
import { Check, AlertTriangle } from 'lucide-react'
import { useSystemHealth } from '@/hooks/useSystemHealth'
import { buildTitle, known, shortRevision } from '@/components/layout/buildInfo'
import { copyTextToClipboard } from '@/utils/clipboard'

/**
 * Compact "which build am I running" line for the sidebar footer.
 *
 * The backend already reports its version and build provenance (git revision +
 * build date) on every `/health/details` response — the poll {@link
 * useSystemHealth} already makes for the degraded banner — but nothing surfaced
 * it, so a self-host operator had no in-app way to confirm which image a
 * container is running or match a pinned digest back to its commit. This reads
 * the shared health payload (no extra request) and renders nothing until it has
 * a version, so it stays invisible when the backend is unreachable.
 *
 * Clicking it copies the FULL build identity (version + full 40-char SHA +
 * build date + env — the same multi-line text the hover tooltip spells out) to
 * the clipboard. "Which build are you running?" is the first question a
 * maintainer asks on any self-host bug report, and until now the answer lived
 * only in a hover tooltip: unselectable, invisible on a touch device, and easy
 * to mis-transcribe from a 40-char SHA. One click now yields a paste-ready
 * block instead. Copy reuses {@link copyTextToClipboard}, whose legacy
 * `execCommand` fallback keeps the button working on a plain-HTTP self-host
 * where the async Clipboard API is blocked; if both paths fail the button says
 * so rather than going silently inert.
 */
export default function AppVersionBadge() {
  const { data } = useSystemHealth()
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle')

  const version = known(data?.version)
  if (!version) return null

  const rev = known(data?.build?.revision)
  // The clipboard payload and the hover tooltip share one source of truth: the
  // full identity, full SHA and all. What you read is what you copy.
  const identity = buildTitle(data?.version, data?.build, data?.env)

  const onCopy = async () => {
    const ok = await copyTextToClipboard(identity)
    setStatus(ok ? 'copied' : 'failed')
    setTimeout(() => setStatus('idle'), ok ? 1500 : 4000)
  }

  const hoverTitle =
    status === 'copied'
      ? 'Build details copied'
      : status === 'failed'
        ? "Couldn't copy — select this line and press Ctrl/⌘-C"
        : identity
          ? `${identity}\n\nClick to copy for a bug report`
          : undefined

  return (
    <button
      type="button"
      onClick={onCopy}
      title={hoverTitle}
      aria-label={`Running build v${version}. Click to copy full build details for a bug report.`}
      data-testid="app-version-badge"
      className="flex w-full items-center gap-1 px-3 pt-1 text-left text-[10.5px] text-[var(--color-text-faint)] tabular-nums hover:text-[var(--color-text-muted)] transition-colors"
    >
      <span className="truncate select-all">
        v{version}
        {rev ? <span className="text-[var(--color-text-muted)]"> · {shortRevision(rev)}</span> : null}
      </span>
      {status === 'copied' && (
        <Check className="h-3 w-3 shrink-0 text-[var(--status-passed)]" aria-hidden />
      )}
      {status === 'failed' && (
        <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--status-broken)]" aria-hidden />
      )}
    </button>
  )
}
