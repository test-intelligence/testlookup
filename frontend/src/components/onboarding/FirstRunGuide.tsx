import { ReactNode, useState } from 'react'
import { Link } from 'react-router-dom'
import { Rocket, Copy, Check, ArrowRight, X } from 'lucide-react'
import { copyTextToClipboard } from '@/utils/clipboard'

/** localStorage key — once dismissed the guide stays hidden for this browser. */
export const FIRST_RUN_DISMISS_KEY = 'tl_first_run_guide_dismissed'

interface Step {
  n: number
  title: string
  body: ReactNode
  command?: string
}

const STEPS: Step[] = [
  {
    n: 1,
    title: 'Load the demo dataset',
    body: 'Spin up a fully populated instance — sample runs, failures, flaky tests, trends, and a release gate — in a few minutes.',
    command: 'make quickstart',
  },
  {
    n: 2,
    title: 'Or ingest your own test results',
    body: 'Point your CI at the ingest API (JUnit / TestNG / Allure / Cypress / Playwright / pytest), or upload a file from the CLI.',
    command: 'testlookup upload results.xml',
  },
  {
    n: 3,
    title: 'Then explore the intelligence',
    body: 'Once a run lands, dig into clustered failures, the flaky coach, and the release-risk gate.',
  },
]

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
  onDismiss,
}: {
  projectName?: string
  onDismiss?: () => void
}) {
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
        {STEPS.map((s) => (
          <li key={s.n} className="flex gap-3">
            <span className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-bg-secondary)] text-xs font-semibold text-[var(--color-text-secondary)]">
              {s.n}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-[var(--color-text)]">{s.title}</p>
              <p className="text-[13px] text-[var(--color-text-muted)]">{s.body}</p>
              {s.command && <CommandRow command={s.command} />}
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
