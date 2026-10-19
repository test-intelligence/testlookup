import { Fragment, useEffect, useState, type ReactNode } from 'react'
import {
  HeartPulse, ShieldAlert, AlertTriangle, Eye, RefreshCw, CheckCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { isDetailToken } from '@/components/ui/inPage/ids'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { InlineRowSection } from '@/components/ui/inPage/InlineRowSection'
import { useDetailParam } from '@/components/ui/inPage/useDetailParam'
import { useFlakyCoach } from '@/hooks/useTestHealth'
import { usePermissions } from '@/hooks/usePermissions'
import { testHealthService, type FlakyCoachEntry } from '@/services/testHealthService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { formatDay } from '@/utils/formatters'
import ChipFilter from './inbox/ChipFilter'
import { flakySubtitle } from './flaky/flakyModel'
import {
  INSUFFICIENT_NOTE,
  SCORE_HEADER_TITLE,
  SORT_OPTIONS,
  componentLine,
  evidenceLine,
  insufficientLine,
  parseFlakyScore,
  pendingLine,
  scoreCell,
  sortEntries,
  weightsLine,
  windowLine,
  type FlakySort,
} from './flaky/flakyScoreModel'

type Recommendation = FlakyCoachEntry['quarantine_recommendation']

// W29a UIX-07: what each colour MEANS. A quarantine holds a flaky test, it is
// not a failure: an OUTLINE in the flaky colour with the shield, no red fill.
// Investigate is the warning, Healthy the success (UI aliases; their text on
// the tint reads the legible -ink, UIX-03).
const QUARANTINE_CONFIG: Record<Recommendation, { label: string; colour: string; bg: string; icon: React.ElementType }> = {
  QUARANTINE:  { label: 'Quarantine',  colour: 'text-[var(--state-quarantined)]', bg: 'bg-transparent border-[var(--state-quarantined-bd)]',                   icon: ShieldAlert },
  INVESTIGATE: { label: 'Investigate', colour: 'text-[var(--ui-warning-ink)]',    bg: 'bg-[var(--ui-warning-bg)]/20 border-[var(--ui-warning-bd)]/30',         icon: AlertTriangle },
  MONITOR:     { label: 'Monitor',     colour: 'text-[var(--color-text)]',        bg: 'bg-[var(--color-bg-secondary)]/30 border-[var(--color-border-light)]', icon: Eye },
  HEALTHY:     { label: 'Healthy',     colour: 'text-[var(--ui-success-ink)]',    bg: 'bg-[var(--ui-success-bg)]/20 border-[var(--ui-success-bd)]/30',         icon: CheckCircle },
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

/** The confidence band's chip (low / medium / high), in the existing status tokens. */
const BAND_TONE: Record<string, string> = {
  low: 'text-[var(--status-skipped)] bg-[var(--status-skipped-bg)] border-[var(--color-border)]',
  medium: 'text-[var(--status-broken)] bg-[var(--status-broken-bg)] border-[var(--status-broken-bd)]/40',
  high: 'text-[var(--status-flaky)] bg-[var(--status-flaky-bg)] border-[var(--status-flaky)]/30',
}

function BandChip({ band }: { band: string }) {
  return (
    <span
      data-score-band={band}
      className={clsx('rounded border px-1.5 text-[11px] leading-4', BAND_TONE[band] ?? 'border-[var(--color-border)] text-[var(--color-text-muted)]')}
    >
      {band}
    </span>
  )
}

/**
 * The Flakiness score cell (W09 FLK-03): ONE line, never wrapped. A scored
 * test reads `0.62` + its confidence band; below the evidence floor
 * `insufficient`; not swept yet `not scored yet`; a failed enrichment `—`.
 * Never `0.00` for a test that has no score.
 */
function ScoreCell({ entry }: { entry: FlakyCoachEntry }) {
  const brief = parseFlakyScore(entry.flaky_score)
  const cell = scoreCell(brief)
  const scored = brief?.status === 'scored' && brief.score !== null
  return (
    <span
      title={cell.title}
      data-flaky-score={brief?.status ?? 'unavailable'}
      className={clsx(
        'inline-flex items-center gap-1.5 whitespace-nowrap',
        scored ? 'text-[var(--color-text)]' : 'text-xs text-[var(--color-text-faint)]',
      )}
    >
      {scored ? <span className="font-mono text-xs">{cell.text}</span> : cell.text}
      {cell.band && <BandChip band={cell.band} />}
    </span>
  )
}

/**
 * The details panel's score section (W09 FLK-03): the score with a 0-1 bar,
 * its evidence (band · observations, window, when scored), the four signals
 * with their value × weight, and the weights' sum, from the row's stored
 * weights. Below the floor it says so (a new test is not a stable test); not
 * swept yet says when it will be. Nothing when the enrichment failed.
 */
function FlakyScoreSection({ entry }: { entry: FlakyCoachEntry }) {
  const brief = parseFlakyScore(entry.flaky_score)
  if (!brief) return null
  return (
    <section aria-label="Flakiness score (4 signals)" data-flaky-score-section="" className="space-y-2">
      <p className="text-xs text-[var(--color-text-muted)]">Flakiness score (4 signals)</p>
      {brief.status === 'scored' && brief.score !== null ? (
        <>
          <div className="flex items-center gap-3">
            <span className="font-mono text-base text-[var(--color-text)]">{brief.score.toFixed(2)}</span>
            {/* A 0-1 bar: the width is the score, the only value set inline. */}
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-secondary)]" aria-hidden>
              <div className="h-full rounded-full bg-[var(--status-flaky)]" style={{ width: `${Math.round(brief.score * 100)}%` }} />
            </div>
          </div>
          <p className="text-xs text-[var(--color-text-secondary)]">{evidenceLine(brief)}</p>
          <p className="text-xs text-[var(--color-text-faint)]">{windowLine(brief)}</p>
          <ul className="space-y-0.5">
            {brief.components.map((component) => (
              <li key={component.name} className="font-mono text-[11px] text-[var(--color-text-secondary)]">
                {componentLine(component)}
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-[var(--color-text-faint)]">{weightsLine(brief.components)}</p>
          <p className="text-[11px] text-[var(--color-text-faint)]">
            Separate from Fail %, the failure-rate detector: the score weighs result flips, retries, environment disagreement and runtime spread.
          </p>
        </>
      ) : brief.status === 'insufficient' ? (
        <>
          <p className="text-xs text-[var(--color-text-secondary)]">{insufficientLine(brief)}</p>
          <p className="text-[11px] text-[var(--color-text-faint)]">{INSUFFICIENT_NOTE}</p>
        </>
      ) : (
        <p className="text-xs text-[var(--color-text-secondary)]">{pendingLine(brief)}</p>
      )}
    </section>
  )
}

/** Everything the coach knows about one flaky test (the details under its row, W27 PAN-04). */
export function FlakyEntryDetails({ entry }: { entry: FlakyCoachEntry }) {
  return (
    <div className="space-y-4 text-sm" data-testid="flaky-entry-details">
      <FlakyScoreSection entry={entry} />
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
            {entry.flaky_since ? formatDay(entry.flaky_since) : '—'}
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
 * A two-line header (the score's) inside the row's own height: 2 x 13 px lines
 * + 2 x 2 px = 30 px, under the one-line headers' 32.2 px (measured), so the
 * header row does not grow.
 */
const TH_COMPACT = 'px-3 py-0.5 leading-[13px] text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]'

/**
 * The flaky-test list (Flaky tests › Detected; was Flaky Coach's body): a
 * recommendation filter and the Sort (Impact = the server's order, or
 * Flakiness score) in one row, then one table — test, history, failure rate,
 * the four-signal flakiness score (W09 FLK-03: one line per cell; a separate
 * detector from Fail %), recommendation, impact and, when the caller gives
 * one, a row action. A row opens its details UNDER itself (W27 PAN-04: a
 * full-width row, since the table is at its width limit at 1280 px), whose
 * first section is the score's breakdown. The open test rides the URL
 * (`?test=<fingerprint>`, pushed: Back closes it, a refresh reopens it once
 * the entries load); a fingerprint no entry has, or one that is not a plain
 * token, is dropped. `renderBelowRow` puts the caller's row form (the
 * proposal) under its row too, above the details.
 *
 * Marked as the page's primary content (`data-primary`).
 */
export function FlakyCoachBody({
  entries,
  renderAction,
  actionLabel = 'Action',
  renderBelowRow,
}: {
  entries: readonly FlakyCoachEntry[]
  /** The last column's cell for a row (e.g. "Propose quarantine"); no column without it. */
  renderAction?: (entry: FlakyCoachEntry) => ReactNode
  actionLabel?: string
  /** A row's form (e.g. the proposal), in a full-width row under that row; null for none. */
  renderBelowRow?: (entry: FlakyCoachEntry) => ReactNode
}) {
  const [filter, setFilter] = useState<Recommendation | 'all'>('all')
  // W09 FLK-03: local state, Impact (the server's order) by default (D-14).
  const [sort, setSort] = useState<FlakySort>('impact')
  // A fingerprint is matched against the loaded entries only; it never reaches a request.
  const detail = useDetailParam('test', { accept: isDetailToken })
  const openFingerprint = detail.id
  const ordered = sortEntries(entries, sort)
  const filtered = filter === 'all' ? ordered : ordered.filter((e) => e.quarantine_recommendation === filter)
  const open = openFingerprint ? entries.find((e) => e.test_fingerprint === openFingerprint) ?? null : null
  // The body renders once the analysis has loaded: a key no entry has (or not a token) is dropped.
  const stale = detail.rejected !== null || (openFingerprint !== null && open === null)
  const closeDetail = detail.close
  useEffect(() => {
    if (stale) closeDetail()
  }, [stale, closeDetail])
  const columns = renderAction ? 7 : 6

  return (
    <div className="space-y-3">
      {/* One row: the filter chips, and Sort right-aligned in the SAME row (no new row on the page). */}
      <div className="flex flex-wrap items-center gap-3">
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
        <label className="ml-auto inline-flex items-center gap-2">
          <span
            aria-hidden
            className="text-[10.5px] font-medium uppercase text-[var(--color-text-muted)]"
            style={{ letterSpacing: 'var(--tracking-wider)' }}
          >
            Sort
          </span>
          <select
            aria-label="Sort"
            value={sort}
            onChange={(event) => setSort(event.target.value as FlakySort)}
            data-flaky-sort=""
            className="h-7 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-[12.5px] text-[var(--color-text)]"
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
      </div>

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
                  {/* Two compact lines in the header row's own height (py-1, 14 px leading): the row does not grow. */}
                  <th className={clsx(TH_COMPACT, 'text-left whitespace-nowrap')} title={SCORE_HEADER_TITLE}>
                    Flakiness score
                    <span className="block font-normal normal-case tracking-normal text-[var(--color-text-faint)]">(4 signals)</span>
                  </th>
                  <th className={clsx(TH, 'text-left')}>Recommendation</th>
                  <th className={clsx(TH, 'text-right')} title="Impact score: how much this flake costs the project's runs">Impact</th>
                  {renderAction && <th className={clsx(TH, 'text-right')}>{actionLabel}</th>}
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => {
                  const below = renderBelowRow?.(entry)
                  return (
                  <Fragment key={entry.test_fingerprint}>
                  <tr
                    data-flaky-row={entry.test_fingerprint}
                    onClick={() => detail.open(entry.test_fingerprint)}
                    className={clsx(
                      'cursor-pointer border-t border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]',
                      openFingerprint === entry.test_fingerprint && 'bg-[var(--color-bg-hover)]',
                    )}
                  >
                    {/* 280 px (was 420) pays for the score column: measured, see the W09 G-C report. */}
                    <td className="px-3 py-2 max-w-[280px]">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          detail.open(entry.test_fingerprint)
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
                      <ScoreCell entry={entry} />
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
                  {/* Across every column; neither can widen the table (InlineRowSection). */}
                  {below ? <InlineRowSection colSpan={columns}>{below}</InlineRowSection> : null}
                  {open && open.test_fingerprint === entry.test_fingerprint && (
                    <InlineRowSection colSpan={columns} dataAttributes={{ 'data-flaky-details': entry.test_fingerprint }}>
                      <InlineFormSection
                        as="aside"
                        title={<span className="[overflow-wrap:anywhere]">{open.test_name || 'Flaky test'}</span>}
                        closeLabel="Close panel"
                        onClose={closeDetail}
                      >
                        <FlakyEntryDetails entry={open} />
                      </InlineFormSection>
                    </InlineRowSection>
                  )}
                  </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
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
