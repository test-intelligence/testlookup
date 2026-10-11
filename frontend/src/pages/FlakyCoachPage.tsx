import { useState, type ReactNode } from 'react'
import {
  HeartPulse, ShieldAlert, AlertTriangle, Eye, RefreshCw, CheckCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import SidePanel from '@/components/ui/SidePanel'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { useFlakyCoach } from '@/hooks/useTestHealth'
import { usePermissions } from '@/hooks/usePermissions'
import { testHealthService, type FlakyCoachEntry } from '@/services/testHealthService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import ChipFilter from './inbox/ChipFilter'
import { flakySubtitle } from './flaky/flakyModel'

type Recommendation = FlakyCoachEntry['quarantine_recommendation']

const QUARANTINE_CONFIG: Record<Recommendation, { label: string; colour: string; bg: string; icon: React.ElementType }> = {
  QUARANTINE:  { label: 'Quarantine',  colour: 'text-[var(--status-failed)]',     bg: 'bg-[var(--status-failed-bg)]/20 border-[var(--status-failed-bd)]/30',     icon: ShieldAlert },
  INVESTIGATE: { label: 'Investigate', colour: 'text-[var(--status-broken)]',   bg: 'bg-[var(--status-broken-bg)]/20 border-[var(--status-broken-bd)]/30', icon: AlertTriangle },
  MONITOR:     { label: 'Monitor',     colour: 'text-[var(--color-text)]',    bg: 'bg-[var(--color-bg-secondary)]/30 border-[var(--color-border-light)]',   icon: Eye },
  HEALTHY:     { label: 'Healthy',     colour: 'text-[var(--status-passed)]', bg: 'bg-[var(--status-passed-bg)]/20 border-[var(--status-passed-bd)]/30', icon: CheckCircle },
}

const RECOMMENDATIONS = Object.keys(QUARANTINE_CONFIG) as Recommendation[]

function StatusHistoryBar({ history }: { history: string[] }) {
  if (history.length === 0) return null
  return (
    <div className="flex gap-0.5" title={history.join(' → ')}>
      {history.slice(0, 10).map((s, i) => (
        <div
          key={i}
          className={clsx(
            'w-2 h-2 rounded-full',
            // The status hues themselves: the -bg tints were near-invisible
            // dots on the dark themes once the row became a table cell.
            s === 'PASSED' || s === 'TestStatus.PASSED' ? 'bg-[var(--status-passed)]' :
            s === 'FAILED' || s === 'TestStatus.FAILED' ? 'bg-[var(--status-failed)]' :
            s === 'BROKEN' || s === 'TestStatus.BROKEN' ? 'bg-[var(--status-broken)]' :
            'bg-[var(--color-border)]',
          )}
        />
      ))}
    </div>
  )
}

export function RecommendationPill({ value }: { value: Recommendation }) {
  const cfg = QUARANTINE_CONFIG[value] ?? QUARANTINE_CONFIG.MONITOR
  const Icon = cfg.icon
  return (
    <span className={clsx('inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border whitespace-nowrap', cfg.bg, cfg.colour)}>
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      {cfg.label}
    </span>
  )
}

/** Everything the coach knows about one flaky test (the side panel's body; was the row's inline expansion). */
export function FlakyEntryDetails({ entry }: { entry: FlakyCoachEntry }) {
  return (
    <div className="space-y-4 text-sm" data-testid="flaky-entry-details">
      {/* The test's name is the panel's title. */}
      <div>
        {entry.suite_name && <p className="text-xs text-[var(--color-text-muted)]">{entry.suite_name}</p>}
        <div className="mt-2 flex items-center gap-3">
          <RecommendationPill value={entry.quarantine_recommendation} />
          <StatusHistoryBar history={entry.status_history} />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div>
          <p className="text-xs text-[var(--color-text-muted)]">Total Runs</p>
          <p className="text-[var(--color-text-secondary)] font-mono">{entry.total_runs}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--color-text-muted)]">Failed Runs</p>
          <p className="text-[var(--status-failed)] font-mono">{entry.failed_runs}</p>
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
          <p className="text-[var(--color-text-secondary)] text-xs">
            <span className="font-mono">
              {(entry.flaky_confidence_low * 100).toFixed(0)}% – {(entry.flaky_confidence_high * 100).toFixed(0)}%
            </span>
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
              entry.is_flaky_confidence >= 0.7 ? 'text-[var(--status-broken)]' :
              entry.is_flaky_confidence <= 0.3 ? 'text-[var(--status-passed)]' :
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
            <span className="font-mono text-[11px] text-[var(--status-broken)]">{entry.failing_step}</span>
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
  )
}

const TH = 'px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]'

/**
 * The flaky-test list (Flaky tests › Detected; was Flaky Coach's body): a
 * recommendation filter, then one table — test, history, failure rate,
 * recommendation, impact and, when the caller gives one, a row action. A row
 * opens its details in a side panel (they used to expand inline).
 *
 * Marked as the page's primary content (`data-primary`).
 */
export function FlakyCoachBody({
  entries,
  renderAction,
  actionLabel = 'Action',
}: {
  entries: readonly FlakyCoachEntry[]
  /** The last column's cell for a row (e.g. "Propose quarantine"); no column without it. */
  renderAction?: (entry: FlakyCoachEntry) => ReactNode
  actionLabel?: string
}) {
  const [filter, setFilter] = useState<Recommendation | 'all'>('all')
  const [openFingerprint, setOpenFingerprint] = useState<string | null>(null)
  const filtered = filter === 'all' ? entries : entries.filter((e) => e.quarantine_recommendation === filter)
  const open = openFingerprint ? entries.find((e) => e.test_fingerprint === openFingerprint) ?? null : null

  return (
    <div className="space-y-3">
      <ChipFilter
        label="Recommendation"
        value={filter}
        onChange={setFilter}
        options={[
          { id: 'all', label: 'All', count: entries.length },
          ...RECOMMENDATIONS.map((key) => ({
            id: key,
            label: QUARANTINE_CONFIG[key].label,
            count: entries.filter((e) => e.quarantine_recommendation === key).length,
          })),
        ]}
      />

      <section data-primary="" aria-label="Flaky tests">
        {filtered.length === 0 ? (
          <EmptyState
            icon={<HeartPulse className="h-10 w-10" />}
            title={filter !== 'all' ? `No ${QUARANTINE_CONFIG[filter].label} tests` : 'No flaky tests found'}
            description={filter !== 'all' ? 'Try a different filter.' : 'This project has no flaky tests in the analysis window.'}
          />
        ) : (
          <div className="overflow-hidden rounded-md border border-[var(--color-border)]">
            <table className="w-full text-sm" aria-label="Flaky tests">
              <thead className="bg-[var(--color-bg-secondary)]">
                <tr>
                  <th className={clsx(TH, 'text-left')}>Test</th>
                  <th className={clsx(TH, 'text-left')}>History</th>
                  <th className={clsx(TH, 'text-right')}>Fail %</th>
                  <th className={clsx(TH, 'text-left')}>Recommendation</th>
                  <th className={clsx(TH, 'text-right')} title="Impact score: how much this flake costs the project's runs">Impact</th>
                  {renderAction && <th className={clsx(TH, 'text-right')}>{actionLabel}</th>}
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => (
                  <tr
                    key={entry.test_fingerprint}
                    data-flaky-row={entry.test_fingerprint}
                    onClick={() => setOpenFingerprint(entry.test_fingerprint)}
                    className={clsx(
                      'cursor-pointer border-t border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]',
                      openFingerprint === entry.test_fingerprint && 'bg-[var(--color-bg-hover)]',
                    )}
                  >
                    <td className="px-3 py-2 max-w-[420px]">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpenFingerprint(entry.test_fingerprint)
                        }}
                        className="block max-w-full truncate text-left text-[var(--color-text)] hover:underline"
                        title={entry.test_name}
                      >
                        {entry.test_name}
                      </button>
                      {entry.suite_name && (
                        <p className="truncate text-xs text-[var(--color-text-muted)]">{entry.suite_name}</p>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <StatusHistoryBar history={entry.status_history} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-[var(--status-failed)]">
                      {(entry.failure_rate * 100).toFixed(0)}%
                    </td>
                    <td className="px-3 py-2">
                      <RecommendationPill value={entry.quarantine_recommendation} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs text-[var(--color-text-muted)]">
                      {entry.impact_score.toFixed(0)}
                    </td>
                    {renderAction && (
                      <td className="px-3 py-2 text-right whitespace-nowrap" onClick={(event) => event.stopPropagation()}>
                        {renderAction(entry)}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <SidePanel open={open !== null} onClose={() => setOpenFingerprint(null)} title={open?.test_name ?? 'Flaky test'}>
        {open && <FlakyEntryDetails entry={open} />}
      </SidePanel>
    </div>
  )
}

/**
 * Flaky Coach, no longer routed (UX redesign P4: `/flaky-coach` redirects to
 * Flaky tests, `/flaky`, whose Detected tab renders the same `FlakyCoachBody`).
 */
export default function FlakyCoachPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const projectId = isAllProjects ? null : activeProjectId
  const { coach, isLoading, refresh } = useFlakyCoach(projectId)
  const { isQaEngineer: canRefresh } = usePermissions()
  const [refreshing, setRefreshing] = useState(false)

  const handleRefresh = async () => {
    if (!projectId) return
    setRefreshing(true)
    try {
      const result = await testHealthService.refreshFlakyCoach(projectId)
      const found = result.flaky_tests_found
      toast.success(`Found ${found} flaky test${found === 1 ? '' : 's'}`)
      await refresh()
    } catch {
      // The API client already toasted the server's reason.
    } finally {
      setRefreshing(false)
    }
  }

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Flaky Coach"
          subtitle="Select a specific project to see flaky test coaching"
        />
        <ProjectRequiredEmptyState
          icon={<HeartPulse className="h-10 w-10" />}
          description="Flaky Coach analyses one project's test history at a time."
        />
      </div>
    )
  }

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Flaky Coach"
        subtitle={flakySubtitle(coach)}
        actions={canRefresh ? (
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="btn-secondary text-sm flex items-center gap-2"
          >
            <RefreshCw className={clsx('h-4 w-4', refreshing && 'animate-spin')} />
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        ) : undefined}
      />
      <FlakyCoachBody entries={coach?.entries ?? []} />
    </div>
  )
}
