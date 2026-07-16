/**
 * Settings → Agent Activity (Agentic plan AI-3 — governance ledger).
 *
 * Immutable ledger of every agent run: agent, mode chip, trigger, status,
 * one-line summary, tokens, cost, duration, timestamp. Rows expand to show
 * actions proposed / actions taken, the prompt-registry digest, and a details
 * link that navigates into the source view (the Investigator cockpit) when
 * the entry carries a details_path. Agent filter + limit/offset pagination.
 *
 * Route-level access is QA_LEAD+ (managementRoutes in App.tsx).
 */
import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, ScrollText, ShieldAlert } from 'lucide-react'
import { clsx } from 'clsx'
import { useAgentRuns } from '@/hooks/useAgentGovernance'
import { useActiveProjectId } from '@/hooks/useProjectScopedSWR'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { AgentRunEntry } from '@/types/investigator'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import FixAttemptsSection from '@/components/fixer/FixAttemptsSection'
import { formatDateTime, formatDuration } from '@/utils/formatters'

const PAGE_SIZE = 50

/** Known governable agents; the dropdown also absorbs ids seen in the data. */
const KNOWN_AGENTS = ['investigator', 'fixer']

function StatusChip({ status }: { status: string }) {
  const tone =
    status === 'completed' ? { color: 'var(--status-passed)' }
    : status === 'failed' ? { color: 'var(--gate-no-go)' }
    : status === 'cancelled' ? { color: 'var(--color-text-faint)' }
    : { color: 'var(--color-accent)' }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs capitalize" style={tone}>
      <i aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
      {status}
    </span>
  )
}

function ModeChip({ mode }: { mode: string }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-semibold uppercase"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text-secondary)',
        letterSpacing: 'var(--tracking-wide)',
      }}
    >
      {mode}
    </span>
  )
}

function ActionList({ title, actions }: { title: string; actions: string[] }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase font-medium text-[var(--color-text-muted)] tracking-wider mb-1">{title}</div>
      {actions.length === 0 ? (
        <p className="text-xs text-[var(--color-text-faint)] m-0">none</p>
      ) : (
        <ul className="m-0 pl-4 space-y-0.5">
          {actions.map((a, i) => (
            <li key={i} className="text-xs text-[var(--color-text-secondary)]">{a}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function LedgerRow({ entry }: { entry: AgentRunEntry }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <Fragment>
      <tr
        className="cursor-pointer transition-colors hover:bg-[var(--color-bg-hover)]"
        style={{ borderBottom: '1px solid var(--color-border)' }}
        onClick={() => setExpanded((e) => !e)}
        data-testid={`ledger-row-${entry.id}`}
        aria-expanded={expanded}
      >
        <td className="px-3 py-2.5">
          <span className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text)]">
            {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
            <span className="capitalize">{entry.agent_id}</span>
          </span>
        </td>
        <td className="px-3 py-2.5"><ModeChip mode={entry.mode} /></td>
        <td className="px-3 py-2.5 text-xs text-[var(--color-text-secondary)]">{entry.trigger}</td>
        <td className="px-3 py-2.5"><StatusChip status={entry.status} /></td>
        <td className="px-3 py-2.5 text-xs text-[var(--color-text-secondary)] max-w-[320px]">
          <span className="block truncate" title={entry.summary}>{entry.summary}</span>
        </td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums text-[var(--color-text-secondary)]">{entry.tokens.toLocaleString()}</td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums font-mono text-[var(--color-text-secondary)]">${entry.cost_usd.toFixed(2)}</td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums text-[var(--color-text-secondary)]">{formatDuration(entry.duration_ms)}</td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums text-[var(--color-text-muted)] whitespace-nowrap">{formatDateTime(entry.created_at)}</td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: '1px solid var(--color-border)' }} data-testid={`ledger-detail-${entry.id}`}>
          <td colSpan={9} className="px-4 py-3" style={{ background: 'var(--color-bg)' }}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <ActionList title="Actions proposed" actions={entry.actions_proposed} />
              <ActionList title="Actions taken" actions={entry.actions_taken} />
            </div>
            <div className="flex items-center gap-2 flex-wrap mt-3 text-[11px] text-[var(--color-text-muted)]">
              {entry.prompt_registry_digest && (
                <span>
                  prompt digest{' '}
                  <code className="font-mono px-1.5 py-px rounded" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
                    {entry.prompt_registry_digest}
                  </code>
                </span>
              )}
              {entry.run_id && (
                <span>
                  run <code className="font-mono">{entry.run_id.slice(0, 8)}</code>
                </span>
              )}
              {entry.details_path && (
                <Link
                  to={entry.details_path}
                  className="ml-auto font-medium hover:underline"
                  style={{ color: 'var(--color-accent)' }}
                  onClick={(e) => e.stopPropagation()}
                >
                  Open details →
                </Link>
              )}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

export default function AgentActivityPage() {
  const projectId = useActiveProjectId()
  const scopedProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  const [agentFilter, setAgentFilter] = useState<string>('')
  const [offset, setOffset] = useState(0)

  const { data, isLoading, error } = useAgentRuns(scopedProjectId, {
    agentId: agentFilter || undefined,
    limit: PAGE_SIZE,
    offset,
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const agentOptions = Array.from(new Set([...KNOWN_AGENTS, ...items.map((i) => i.agent_id)]))

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">Agent Activity</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            The governance ledger — every agent run with its trigger, spend, and the actions it proposed or took.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          Agent
          <select
            value={agentFilter}
            onChange={(e) => { setAgentFilter(e.target.value); setOffset(0) }}
            aria-label="Filter by agent"
            className="rounded-md px-2 py-1.5 text-sm bg-[var(--color-bg)] text-[var(--color-text)]"
            style={{ border: '1px solid var(--color-border)' }}
          >
            <option value="">All agents</option>
            {agentOptions.map((a) => (
              <option key={a} value={a} className="capitalize">{a}</option>
            ))}
          </select>
        </label>
      </div>

      {!scopedProjectId ? (
        <div
          className="card flex items-center gap-3 py-3 px-4"
          style={{ borderColor: 'var(--gate-conditional-border)', background: 'var(--gate-conditional-bg)' }}
        >
          <ShieldAlert className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--gate-conditional)' }} />
          <p className="text-sm m-0" style={{ color: 'var(--gate-conditional)' }}>
            The activity ledger is per-project — select a specific project from the top bar.
          </p>
        </div>
      ) : isLoading && items.length === 0 ? (
        <div className="flex justify-center py-10"><LoadingSpinner size="lg" /></div>
      ) : error ? (
        <p className="text-sm text-[var(--color-text-muted)]">Could not load agent activity.</p>
      ) : items.length === 0 ? (
        <div className="card flex flex-col items-center gap-2 py-10 text-center">
          <ScrollText className="h-8 w-8 text-[var(--color-text-faint)]" />
          <h3 className="font-semibold text-[var(--color-text)] m-0">No agent activity yet</h3>
          <p className="text-sm text-[var(--color-text-muted)] m-0 max-w-[52ch]">
            Every run by a governed AI agent — like the Investigator — lands here with its trigger, mode, token/cost
            spend, and the actions it proposed or took. Trigger an investigation from the Deep Investigation page, or
            enable auto-triggers under Settings → AI Agents.
          </p>
        </div>
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: 900 }}>
            <thead>
              <tr style={{ background: 'var(--color-bg)', borderBottom: '1px solid var(--color-border)' }}>
                {['Agent', 'Mode', 'Trigger', 'Status', 'Summary'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">{h}</th>
                ))}
                {['Tokens', 'Cost', 'Duration', 'When'].map((h) => (
                  <th key={h} className="px-3 py-2 text-right text-[10.5px] uppercase tracking-wider font-medium text-[var(--color-text-muted)]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => <LedgerRow key={entry.id} entry={entry} />)}
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

      {/* Fix Attempts (AI-2) — the Fixer's per-test attempt ledger. */}
      {scopedProjectId && <FixAttemptsSection projectId={scopedProjectId} />}
    </div>
  )
}
