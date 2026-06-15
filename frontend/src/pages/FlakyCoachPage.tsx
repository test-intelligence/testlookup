import { useState } from 'react'
import {
  HeartPulse, ShieldAlert, AlertTriangle, Eye, RefreshCw, CheckCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useFlakyCoach } from '@/hooks/useTestHealth'
import { testHealthService, FlakyCoachEntry } from '@/services/testHealthService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'

const QUARANTINE_CONFIG: Record<string, { label: string; colour: string; bg: string; icon: React.ElementType }> = {
  QUARANTINE:  { label: 'Quarantine',  colour: 'text-red-400',     bg: 'bg-red-900/20 border-red-700/30',     icon: ShieldAlert },
  INVESTIGATE: { label: 'Investigate', colour: 'text-amber-400',   bg: 'bg-amber-900/20 border-amber-700/30', icon: AlertTriangle },
  MONITOR:     { label: 'Monitor',     colour: 'text-[var(--color-text)]',    bg: 'bg-[var(--color-bg-secondary)]/30 border-[var(--color-border-light)]',   icon: Eye },
  HEALTHY:     { label: 'Healthy',     colour: 'text-emerald-400', bg: 'bg-emerald-900/20 border-emerald-700/30', icon: CheckCircle },
}

function StatusHistoryBar({ history }: { history: string[] }) {
  if (history.length === 0) return null
  return (
    <div className="flex gap-0.5" title={history.join(' → ')}>
      {history.slice(0, 10).map((s, i) => (
        <div
          key={i}
          className={clsx(
            'w-2 h-2 rounded-full',
            s === 'PASSED' || s === 'TestStatus.PASSED' ? 'bg-emerald-500' :
            s === 'FAILED' || s === 'TestStatus.FAILED' ? 'bg-red-500' :
            s === 'BROKEN' || s === 'TestStatus.BROKEN' ? 'bg-orange-500' :
            'bg-neutral-700',
          )}
        />
      ))}
    </div>
  )
}

function FlakyTestRow({ entry }: { entry: FlakyCoachEntry }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = QUARANTINE_CONFIG[entry.quarantine_recommendation] ?? QUARANTINE_CONFIG.MONITOR
  const Icon = cfg.icon

  return (
    <div className="card border border-[var(--color-border)]/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left"
      >
        <div className="flex items-center gap-3">
          <Icon className={clsx('w-4 h-4 shrink-0', cfg.colour)} />
          <div className="flex-1 min-w-0">
            <p className="text-sm text-[var(--color-text)] truncate">{entry.test_name}</p>
            {entry.suite_name && (
              <p className="text-xs text-[var(--color-text-muted)] truncate">{entry.suite_name}</p>
            )}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <StatusHistoryBar history={entry.status_history} />
            <span className="text-sm font-mono text-red-400">
              {(entry.failure_rate * 100).toFixed(0)}%
            </span>
            <span className={clsx('text-xs px-2 py-0.5 rounded border', cfg.bg, cfg.colour)}>
              {cfg.label}
            </span>
            <span className="text-xs text-[var(--color-text-muted)] font-mono w-10 text-right">
              {entry.impact_score.toFixed(0)}
            </span>
          </div>
        </div>
      </button>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-[var(--color-border)]/50 space-y-3">
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Total Runs</p>
              <p className="text-[var(--color-text-secondary)] font-mono">{entry.total_runs}</p>
            </div>
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Failed Runs</p>
              <p className="text-red-400 font-mono">{entry.failed_runs}</p>
            </div>
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Flaky Since</p>
              <p className="text-[var(--color-text-secondary)] text-xs">
                {entry.flaky_since
                  ? new Date(entry.flaky_since).toLocaleDateString()
                  : '—'}
              </p>
            </div>
          </div>

          {entry.flaky_confidence_low != null && entry.flaky_confidence_high != null && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">
                Failure rate 95% CI{' '}
                <span className="text-[var(--color-text-faint)]">(Wilson)</span>
              </p>
              <p className="text-[var(--color-text-secondary)] font-mono text-xs">
                {(entry.flaky_confidence_low * 100).toFixed(0)}% – {(entry.flaky_confidence_high * 100).toFixed(0)}%
                <span className="text-[var(--color-text-faint)]">
                  {' '}· narrower over more runs (statistical strength)
                </span>
              </p>
            </div>
          )}

          {entry.is_flaky_confidence != null && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">
                ML flakiness confidence{' '}
                <span className="text-[var(--color-text-faint)]">(learned from quarantine decisions)</span>
              </p>
              <p
                className={clsx(
                  'font-mono text-xs',
                  entry.is_flaky_confidence >= 0.7 ? 'text-amber-400' :
                  entry.is_flaky_confidence <= 0.3 ? 'text-emerald-400' :
                  'text-[var(--color-text-secondary)]',
                )}
              >
                {(entry.is_flaky_confidence * 100).toFixed(0)}%
                <span className="text-[var(--color-text-faint)]">
                  {' '}· {entry.is_flaky_confidence >= 0.7 ? 'likely a flake' :
                    entry.is_flaky_confidence <= 0.3 ? 'likely a real failure' : 'uncertain'}
                </span>
              </p>
            </div>
          )}

          {entry.flaky_likely_cause && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Likely cause</p>
              <p className="text-[var(--color-text-secondary)] text-xs">{entry.flaky_likely_cause}</p>
            </div>
          )}

          {entry.failing_step && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">
                Failing step{' '}
                <span className="text-[var(--color-text-faint)]">(latest run)</span>
              </p>
              <p className="text-[var(--color-text-secondary)] text-xs">
                <span className="font-mono text-[11px] text-amber-400">{entry.failing_step}</span>
                {entry.failing_step_detail && (
                  <span className="block text-[var(--color-text-muted)] mt-0.5">{entry.failing_step_detail}</span>
                )}
              </p>
            </div>
          )}

          {entry.stabilization_actions.length > 0 && (
            <div>
              <p className="text-xs text-[var(--color-text-muted)] mb-1">Stabilization Actions</p>
              <ul className="space-y-1">
                {entry.stabilization_actions.map((action, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                    <span className="text-[var(--color-text)] mt-0.5 shrink-0">→</span>
                    {action}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function FlakyCoachPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const projectId = isAllProjects ? null : activeProjectId
  const { coach, isLoading, refresh } = useFlakyCoach(projectId)
  const [refreshing, setRefreshing] = useState(false)
  const [filter, setFilter] = useState<string | null>(null)

  const handleRefresh = async () => {
    if (!projectId) return
    setRefreshing(true)
    try {
      const result = await testHealthService.refreshFlakyCoach(projectId)
      toast.success(`Found ${result.flaky_tests_found} flaky tests`)
      await refresh()
    } catch {
      toast.error('Refresh failed — QA Engineer role required')
    } finally {
      setRefreshing(false)
    }
  }

  if (isAllProjects) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Flaky Coach"
          subtitle="Select a specific project to see flaky test coaching"
        />
        <EmptyState
          icon={<HeartPulse className="h-10 w-10" />}
          title="Select a project"
          description="Flaky Coach requires a specific project selection to analyze test history."
        />
      </div>
    )
  }

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  }

  const entries = coach?.entries ?? []
  const filtered = filter ? entries.filter(e => e.quarantine_recommendation === filter) : entries

  return (
    <div className="space-y-6">
      <PageHeader
        title="Flaky Coach"
        subtitle={`${coach?.total_flaky ?? 0} flaky tests · ${coach?.quarantine_candidates ?? 0} quarantine candidates`}
        actions={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="btn-secondary text-sm flex items-center gap-2"
          >
            <RefreshCw className={clsx('h-4 w-4', refreshing && 'animate-spin')} />
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4">
        {Object.entries(QUARANTINE_CONFIG).map(([key, cfg]) => {
          const count = entries.filter(e => e.quarantine_recommendation === key).length
          return (
            <button
              key={key}
              onClick={() => setFilter(filter === key ? null : key)}
              className={clsx(
                'card text-center transition-all',
                filter === key && 'ring-1 ring-neutral-500',
              )}
            >
              <p className={clsx('text-2xl font-bold', cfg.colour)}>{count}</p>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">{cfg.label}</p>
            </button>
          )
        })}
      </div>

      {/* Column headers */}
      {filtered.length > 0 && (
        <div className="flex items-center gap-3 px-4 text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider">
          <span className="w-4" />
          <span className="flex-1">Test</span>
          <span className="w-16 text-center">History</span>
          <span className="w-12 text-right">Fail %</span>
          <span className="w-20 text-center">Status</span>
          <span className="w-10 text-right">Impact</span>
        </div>
      )}

      {/* Flaky test list */}
      {filtered.length === 0 ? (
        <EmptyState
          icon={<HeartPulse className="h-10 w-10" />}
          title={filter ? `No ${QUARANTINE_CONFIG[filter]?.label ?? filter} tests` : 'No flaky tests found'}
          description={filter ? 'Try a different filter.' : 'This project has no flaky tests in the analysis window.'}
        />
      ) : (
        <div className="space-y-2">
          {filtered.map((entry) => (
            <FlakyTestRow key={entry.test_fingerprint} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}
