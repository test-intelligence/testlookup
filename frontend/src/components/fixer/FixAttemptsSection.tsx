/**
 * "Fix Attempts" section (Agentic plan AI-2 — Wave D), rendered on
 * Settings → Agent Activity below the governance ledger.
 *
 * Every fix the Fixer attempted — including the honest outcomes
 * (rejected_globs, skipped_budget) — with a status chip, validation reruns,
 * and the draft-PR link when one was opened. Rows expand to fetch the single
 * attempt: the candidate patch, the reason, the runner-log digest, and the
 * governance-ledger run id. The list polls every 5 s while any attempt on the
 * page is still in flight (see useFixAttempts) and stops when all are terminal.
 */
import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, ExternalLink, Wrench } from 'lucide-react'
import { clsx } from 'clsx'
import AISuggestion from '@/components/ai/AISuggestion'
import { useFixAttempt, useFixAttempts, useFixerConfig } from '@/hooks/useFixer'
import { isFixAttemptActive, type FixAttempt, type FixAttemptStatus } from '@/types/fixer'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { formatDateTime } from '@/utils/formatters'

const PAGE_SIZE = 25

/** Tooltip copy for the honest non-outcomes — shown on the chip's title. */
const STATUS_TOOLTIP: Partial<Record<FixAttemptStatus, string>> = {
  rejected_globs:
    'The candidate patch touched files outside the allowed test globs, so it was rejected without validation.',
  skipped_budget:
    'Skipped because a Fixer budget was exhausted (max tests per run, attempts per test, or concurrent open PRs).',
}

function statusTone(status: FixAttemptStatus): { color: string } {
  if (status === 'validated' || status === 'pr_opened') return { color: 'var(--status-passed)' }
  if (status === 'failed_validation' || status === 'error') return { color: 'var(--gate-no-go)' }
  if (status === 'rejected_globs' || status === 'skipped_budget') return { color: 'var(--gate-conditional)' }
  return { color: 'var(--color-accent)' } // in-flight: selected/diagnosing/generating/validating
}

export function AttemptStatusChip({ status }: { status: FixAttemptStatus }) {
  const tone = statusTone(status)
  const inFlight = isFixAttemptActive(status)
  return (
    <span
      className="inline-flex items-center gap-1.5 px-1.5 py-px rounded-full text-xs"
      title={STATUS_TOOLTIP[status]}
      data-testid={`attempt-status-${status}`}
      style={{
        color: tone.color,
        background: `color-mix(in srgb, ${tone.color} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${tone.color} 32%, transparent)`,
      }}
    >
      <i
        aria-hidden
        className={clsx(inFlight && 'animate-pulse')}
        style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }}
      />
      {status.replace(/_/g, ' ')}
    </span>
  )
}

/** Expanded detail — fetches the single-attempt endpoint on first expansion. */
function AttemptDetail({ attemptId }: { attemptId: string }) {
  const { data, isLoading, error } = useFixAttempt(attemptId)

  if (isLoading && !data) {
    return <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
  }
  if (error || !data) {
    return <p className="text-xs text-[var(--color-text-muted)] m-0 py-2">Could not load this attempt.</p>
  }
  return (
    <div className="space-y-3">
      {data.reason && (
        // US-15.1: the Fixer's reasoning is AI output and used to render bare.
        // No confidence is passed — the Fixer emits none, and the chrome must
        // not imply one exists.
        <AISuggestion bare data-testid="fix-attempt-reason" label="reasoning">
          <p className="text-xs text-[var(--color-text-secondary)] m-0">{data.reason}</p>
        </AISuggestion>
      )}
      <div>
        <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] tracking-wider mb-1">Patch</div>
        {data.patch ? (
          <pre
            className="text-xs font-mono m-0 p-3 rounded-md overflow-x-auto max-h-80"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
          >
            <code>{data.patch}</code>
          </pre>
        ) : (
          <p className="text-xs text-[var(--color-text-faint)] m-0">No patch was produced for this attempt.</p>
        )}
      </div>
      <div className="flex items-center gap-3 flex-wrap text-[11px] text-[var(--color-text-muted)]">
        {data.runner_log_digest && (
          <span>
            runner log{' '}
            <code className="font-mono px-1.5 py-px rounded" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
              {data.runner_log_digest}
            </code>
          </span>
        )}
        {data.ledger_run_id && (
          <span>
            ledger run <code className="font-mono">{data.ledger_run_id.slice(0, 8)}</code> (in the activity ledger above)
          </span>
        )}
        {data.pr_url && (
          <a
            href={data.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 font-medium hover:underline"
            style={{ color: 'var(--color-accent)' }}
            onClick={(e) => e.stopPropagation()}
          >
            Open draft PR <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>
    </div>
  )
}

function AttemptRow({ attempt }: { attempt: FixAttempt }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <Fragment>
      <tr
        className="cursor-pointer transition-colors hover:bg-[var(--color-bg-hover)]"
        style={{ borderBottom: '1px solid var(--color-border)' }}
        onClick={() => setExpanded((e) => !e)}
        data-testid={`attempt-row-${attempt.id}`}
        aria-expanded={expanded}
      >
        <td className="px-3 py-2.5">
          <span className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text)] max-w-[360px]">
            {expanded ? <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-[var(--color-text-muted)]" />}
            <span className="truncate font-mono text-xs" title={attempt.test_name}>{attempt.test_name}</span>
          </span>
        </td>
        <td className="px-3 py-2.5"><AttemptStatusChip status={attempt.status} /></td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums text-[var(--color-text-secondary)]">{attempt.attempt_no}</td>
        <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-text-secondary)]">
          {attempt.validation ? `${attempt.validation.passed}/${attempt.validation.reruns} reruns` : '—'}
        </td>
        <td className="px-3 py-2.5 text-xs">
          {attempt.pr_url ? (
            <a
              href={attempt.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 hover:underline"
              style={{ color: 'var(--color-accent)' }}
              onClick={(e) => e.stopPropagation()}
            >
              draft PR <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <span className="text-[var(--color-text-faint)]">—</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums text-[var(--color-text-muted)] whitespace-nowrap">
          {formatDateTime(attempt.created_at)}
        </td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: '1px solid var(--color-border)' }} data-testid={`attempt-detail-${attempt.id}`}>
          <td colSpan={6} className="px-4 py-3" style={{ background: 'var(--color-bg)' }}>
            <AttemptDetail attemptId={attempt.id} />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

export default function FixAttemptsSection({ projectId }: { projectId: string }) {
  const [offset, setOffset] = useState(0)
  const { data, isLoading, error } = useFixAttempts(projectId, { limit: PAGE_SIZE, offset })
  const { data: config } = useFixerConfig(projectId)

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <section className="space-y-3" data-testid="fix-attempts-section">
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text)] m-0">Fix Attempts</h2>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Every fix the Fixer attempted — including the ones it honestly rejected or skipped — with validation
          reruns and the draft PR when one was opened.
        </p>
      </div>

      {isLoading && items.length === 0 && !error ? (
        <div className="flex justify-center py-8"><LoadingSpinner size="md" /></div>
      ) : error ? (
        <p className="text-sm text-[var(--color-text-muted)]">Could not load fix attempts.</p>
      ) : items.length === 0 ? (
        config && !config.enabled ? (
          <div className="card flex flex-col items-center gap-2 py-8 text-center" data-testid="fixer-disabled-empty">
            <Wrench className="h-8 w-8 text-[var(--color-text-faint)]" />
            <h3 className="font-semibold text-[var(--color-text)] m-0">The Fixer is disabled</h3>
            <p className="text-sm text-[var(--color-text-muted)] m-0 max-w-[52ch]">
              No fixes will be attempted for this project until the Fixer is enabled. Turn it on — and pick a mode,
              runner, and budgets — under{' '}
              <Link to="/settings/ai-agents" className="font-medium hover:underline" style={{ color: 'var(--color-accent)' }}>
                Settings → AI Agents
              </Link>.
            </p>
          </div>
        ) : (
          <div className="card flex flex-col items-center gap-2 py-8 text-center" data-testid="fix-attempts-empty">
            <Wrench className="h-8 w-8 text-[var(--color-text-faint)]" />
            <h3 className="font-semibold text-[var(--color-text)] m-0">No fix attempts yet</h3>
            <p className="text-sm text-[var(--color-text-muted)] m-0 max-w-[52ch]">
              The Fixer hasn&apos;t attempted any fixes yet — attempts land here as soon as a scheduled or manual run
              selects a failing test. Kick one off with Run now on the Fixer card under{' '}
              <Link to="/settings/ai-agents" className="font-medium hover:underline" style={{ color: 'var(--color-accent)' }}>
                Settings → AI Agents
              </Link>.
            </p>
          </div>
        )
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: 760 }}>
            <thead>
              <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
                <th className="px-3 py-2 text-left text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">Test</th>
                <th className="px-3 py-2 text-left text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">Status</th>
                <th className="px-3 py-2 text-right text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">Attempt #</th>
                <th className="px-3 py-2 text-left text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">Validation</th>
                <th className="px-3 py-2 text-left text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">PR</th>
                <th className="px-3 py-2 text-right text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => <AttemptRow key={a.id} attempt={a} />)}
            </tbody>
          </table>
          <div className="flex items-center justify-between px-3 py-2.5 text-xs text-[var(--color-text-muted)]">
            <span className="tabular-nums">
              {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
            </span>
            <span className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                className={clsx('px-2.5 py-1 rounded-md border', offset === 0 ? 'opacity-50' : 'hover:bg-[var(--color-bg-hover)]')}
                style={{ borderColor: 'var(--color-border)' }}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                className={clsx('px-2.5 py-1 rounded-md border', offset + PAGE_SIZE >= total ? 'opacity-50' : 'hover:bg-[var(--color-bg-hover)]')}
                style={{ borderColor: 'var(--color-border)' }}
              >
                Next
              </button>
            </span>
          </div>
        </div>
      )}
    </section>
  )
}
