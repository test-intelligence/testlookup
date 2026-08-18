import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Rocket, Copy, Check, ArrowRight, ListChecks, X } from 'lucide-react'
import { copyTextToClipboard } from '@/utils/clipboard'
import { backendUrl } from '@/services/api'
import { buildSteps } from './firstRunSteps'

/** localStorage key — once dismissed the guide stays hidden for this browser. */
export const FIRST_RUN_DISMISS_KEY = 'tl_first_run_guide_dismissed'

function CommandRow({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = async () => {
    const ok = await copyTextToClipboard(command)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }
  return (
    <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2">
      <code className="font-mono text-[12.5px] text-[var(--color-text-secondary)] truncate">{command}</code>
      <button
        type="button"
        onClick={onCopy}
        aria-label={`Copy command: ${command}`}
        className="shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-[var(--status-passed)]" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  )
}

/**
 * First-run guide shown on the dashboard when a project has no runs yet —
 * turns an empty dashboard into a clear "here's how to get value" path.
 * Presentational + dismissible (the parent owns persistence via onDismiss).
 */
export default function FirstRunGuide({
  projectName,
  projectId,
  onDismiss,
}: {
  projectName?: string
  projectId?: string
  onDismiss?: () => void
}) {
  // Resolve the ingest curl to the deployment's own backend origin (same-origin
  // behind an ingress, or VITE_API_BASE_URL) so the copy-paste command works on
  // any self-host — not just the local dev machine.
  const steps = buildSteps(projectId, backendUrl('/api/v1/ingest/file'))
  return (
    <section
      aria-label="Getting started"
      className="card border border-[var(--color-border)] relative"
    >
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss getting started guide"
          className="absolute top-3 right-3 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      )}

      <div className="flex items-center gap-3">
        <div className="p-2 rounded-xl bg-[var(--color-accent-muted)] text-[var(--color-accent)]">
          <Rocket className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold text-[var(--color-text)]">
            Welcome to TestLookup{projectName ? ` — ${projectName}` : ''}
          </h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            No test runs here yet. Three steps to your first failure-intelligence signal.
          </p>
        </div>
      </div>

      <ol className="mt-5 space-y-4">
        {steps.map((s) => (
          <li key={s.n} className="flex gap-3">
            <span className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-bg-secondary)] text-xs font-semibold text-[var(--color-text-secondary)]">
              {s.n}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-[var(--color-text)]">{s.title}</p>
              <p className="text-[13px] text-[var(--color-text-muted)]">{s.body}</p>
              {s.command && <CommandRow command={s.command} />}
              {s.apiCommand && (
                <>
                  <p className="mt-2 text-[11px] text-[var(--color-text-faint)]">
                    Or POST straight to the ingest API from any CI runner:
                  </p>
                  <CommandRow command={s.apiCommand} />
                  <p className="mt-1.5 text-[11px] text-[var(--color-text-faint)]">
                    Generate a project key under{' '}
                    <Link
                      to="/settings/api-keys"
                      className="text-[var(--color-accent)] hover:underline"
                    >
                      Settings → API Keys
                    </Link>{' '}
                    and pass it as the <code className="font-mono">X-API-Key</code> above.
                  </p>
                </>
              )}
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-5 flex flex-wrap gap-2">
        <Link to="/failures" className="btn-secondary text-sm inline-flex items-center gap-1.5">
          Failure analysis <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        <Link to="/flaky-coach" className="btn-ghost text-sm inline-flex items-center gap-1.5">
          Flaky coach <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        <Link to="/releases" className="btn-ghost text-sm inline-flex items-center gap-1.5">
          Release gate <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        <Link to="/getting-started" className="btn-ghost text-sm inline-flex items-center gap-1.5">
          <ListChecks className="h-3.5 w-3.5" /> Setup checklist
        </Link>
        <a
          href="https://github.com/anandtopu/testlookup/blob/main/GETTING_STARTED.md"
          target="_blank"
          rel="noreferrer"
          className="btn-ghost text-sm inline-flex items-center gap-1.5"
        >
          Getting started docs <ArrowRight className="h-3.5 w-3.5" />
        </a>
      </div>
    </section>
  )
}
