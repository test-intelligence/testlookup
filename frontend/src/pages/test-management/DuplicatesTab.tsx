import { useCallback, useMemo, useState } from 'react'
import { AlertCircle, Copy, GitMerge, Layers, Search as SearchIcon, XCircle } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePermissions } from '@/hooks/usePermissions'
import { useDuplicateCandidates } from '@/hooks/useTestManagement'
import { testManagementService } from '@/services/testManagementService'
import type { DuplicateBand, DuplicateCandidate, DuplicateCandidateStatus } from '@/types/test-management'
import { STATUS_COLORS } from './format'
import { StatusPill } from './tmUi'

// ─── Tab: Duplicates (Phase 4; under More ▾ since UX redesign P4) ────────────

const DUP_BAND_COLORS: Record<DuplicateBand, string> = {
  exact:    'bg-[var(--status-failed-bg)] text-[var(--status-failed)] border border-[var(--status-failed-bd)]',
  strong:   'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
  possible: 'bg-[var(--status-broken-bg)] text-[var(--status-broken)] border border-[var(--status-broken-bd)]',
}

const DUP_METHOD_LABEL: Record<string, string> = {
  fingerprint: 'Fingerprint',
  structural:  'Structural',
  semantic:    'Semantic',
}

function DupBandBadge({ band }: { band: DuplicateBand }) {
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold capitalize', DUP_BAND_COLORS[band])}>
      {band}
    </span>
  )
}

function fmtScore(score: number) {
  // Service emits a 0..1 ratio; surface as a percentage.
  return `${Math.round(score * 100)}%`
}

interface DupCaseCardProps { ref_: DuplicateCandidate['case_a']; label: string }
function DupCaseCard({ ref_, label }: DupCaseCardProps) {
  return (
    <div className="flex-1 min-w-0 bg-[var(--color-bg-secondary)] rounded-lg p-3 border border-[var(--color-border)]">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1">{label}</p>
      <p className="text-sm font-medium text-[var(--color-text)] leading-snug break-words">{ref_.title}</p>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        {ref_.suite_name && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
            <Layers className="h-3 w-3" /> {ref_.suite_name}
          </span>
        )}
        {ref_.status && <StatusPill status={ref_.status} map={STATUS_COLORS} />}
      </div>
    </div>
  )
}

interface DuplicateCandidateCardProps {
  projectId: string
  candidate: DuplicateCandidate
  /** Optimistically drop this candidate from the current view, then revalidate. */
  onResolved: (candidateId: string) => Promise<unknown>
}

export function DuplicateCandidateCard({ projectId, candidate, onResolved }: DuplicateCandidateCardProps) {
  const { isQaLead } = usePermissions()
  const [busy, setBusy] = useState<'dismiss' | 'merge' | null>(null)
  const [resolvedLocally, setResolvedLocally] = useState<'dismissed' | 'merged' | null>(null)
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null)
  // Default to keeping case_a; user can flip before merging.
  const [keepCaseId, setKeepCaseId] = useState<string>(candidate.case_a.id)

  const componentScores = candidate.component_scores ?? {}
  const componentEntries = Object.entries(componentScores)

  const handleDismiss = async () => {
    setBusy('dismiss')
    setRefreshWarning(null)
    try {
      await testManagementService.dismissDuplicate(projectId, candidate.id)
    } catch {
      toast.error('Failed to dismiss pair')
      setBusy(null)
      return
    }

    setResolvedLocally('dismissed')
    setBusy(null)
    toast.success('Pair dismissed — it won’t resurface')
    try {
      await onResolved(candidate.id)
    } catch {
      setRefreshWarning('The pair was dismissed, but the duplicate queue could not be refreshed. Actions remain disabled locally.')
    }
  }

  const handleMerge = async () => {
    setBusy('merge')
    setRefreshWarning(null)
    try {
      await testManagementService.mergeDuplicate(projectId, candidate.id, {
        candidate_id: candidate.id,
        keep_case_id: keepCaseId,
        deprecate_loser: true,
      })
    } catch {
      toast.error('Failed to merge pair')
      setBusy(null)
      return
    }

    setResolvedLocally('merged')
    setBusy(null)
    toast.success('Pair merged — losing case soft-deprecated')
    try {
      await onResolved(candidate.id)
    } catch {
      setRefreshWarning('The pair was merged, but the duplicate queue could not be refreshed. Merge remains disabled locally.')
    }
  }

  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}>
      {/* Header: band + method + score */}
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <DupBandBadge band={candidate.band} />
          <span className="text-xs text-[var(--color-text-muted)]">
            {DUP_METHOD_LABEL[candidate.method] ?? candidate.method}
          </span>
        </div>
        <span className="text-sm font-semibold tabular-nums text-[var(--color-text)]">
          {fmtScore(candidate.score)} <span className="text-xs font-normal text-[var(--color-text-muted)]">match</span>
        </span>
      </div>

      {/* Side-by-side cases */}
      <div className="flex items-stretch gap-3">
        <DupCaseCard ref_={candidate.case_a} label="Case A" />
        <div className="flex items-center text-[var(--color-text-faint)]">
          <Copy className="h-4 w-4" />
        </div>
        <DupCaseCard ref_={candidate.case_b} label="Case B" />
      </div>

      {/* Reason */}
      {candidate.reason && (
        <div className="mt-3 flex items-start gap-2 bg-[var(--color-bg-secondary)]/70 rounded-lg px-3 py-2">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-[var(--color-text-muted)]" />
          <p className="text-xs text-[var(--color-text-secondary)]">{candidate.reason}</p>
        </div>
      )}

      {/* Component score breakdown */}
      {componentEntries.length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1.5">Component scores</p>
          <div className="flex flex-wrap gap-1.5">
            {componentEntries.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] text-xs px-2 py-0.5 rounded-full border border-[var(--color-border)]"
              >
                <span className="capitalize">{k.replace(/_/g, ' ')}</span>
                <span className="tabular-nums text-[var(--color-text-secondary)]">
                  {typeof v === 'number' ? fmtScore(v) : String(v)}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Actions (only on open candidates) */}
      {candidate.status === 'open' && !resolvedLocally ? (
        <div className="mt-4 flex items-center justify-between gap-3 flex-wrap border-t border-[var(--color-border)] pt-3">
          {isQaLead ? (
            <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              Keep
              <select
                className="input text-xs py-1"
                value={keepCaseId}
                onChange={e => setKeepCaseId(e.target.value)}
                disabled={busy !== null}
              >
                <option value={candidate.case_a.id}>Case A — {candidate.case_a.title}</option>
                <option value={candidate.case_b.id}>Case B — {candidate.case_b.title}</option>
              </select>
            </label>
          ) : <span />}
          <div className="flex items-center gap-2">
            <button
              onClick={handleDismiss}
              disabled={busy !== null}
              className="btn-secondary text-sm flex items-center gap-1.5"
            >
              {busy === 'dismiss' ? <LoadingSpinner size="sm" /> : <XCircle className="h-3.5 w-3.5" />}
              Dismiss
            </button>
            {isQaLead && (
              <button
                onClick={handleMerge}
                disabled={busy !== null}
                className="btn-primary text-sm flex items-center gap-1.5"
              >
                {busy === 'merge' ? <LoadingSpinner size="sm" /> : <GitMerge className="h-3.5 w-3.5" />}
                Merge
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-3 border-t border-[var(--color-border)] pt-3">
          <StatusPill status={resolvedLocally ?? candidate.status} map={STATUS_COLORS} />
        </div>
      )}
      {refreshWarning && (
        <p role="status" className="mt-3 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-xs text-[var(--status-broken)]">
          {refreshWarning}
        </p>
      )}
    </div>
  )
}

interface DuplicatesTabProps { projectId: string | null }

export default function DuplicatesTab({ projectId }: DuplicatesTabProps) {
  const [band, setBand] = useState<DuplicateBand | ''>('')
  const [statusFilter, setStatusFilter] = useState<DuplicateCandidateStatus>('open')
  const [detecting, setDetecting] = useState(false)

  // Pass undefined in All-Projects mode (projectId === null) → hook short-circuits.
  const params = useMemo(
    () => ({ ...(band ? { band } : {}), status: statusFilter, size: 200 }),
    [band, statusFilter],
  )
  const { data, isLoading, mutate } = useDuplicateCandidates(projectId ?? undefined, params)

  const candidates = data?.items ?? []

  // Optimistically remove a resolved (dismissed/merged) candidate from the
  // current view (the filter is status=open by default, so it disappears),
  // then revalidate against the server to reconcile open_count/total.
  const handleResolved = useCallback(
    (candidateId: string) =>
      mutate(
        (current) =>
          current
            ? {
                ...current,
                items: current.items.filter(c => c.id !== candidateId),
                total: Math.max(0, current.total - 1),
                open_count: Math.max(0, current.open_count - 1),
              }
            : current,
        { revalidate: true },
      ),
    [mutate],
  )

  const handleDetect = async () => {
    if (!projectId) return
    setDetecting(true)
    try {
      const res = await testManagementService.runDuplicateDetection(projectId, { enable_semantic: true })
      const msg = res.sampled
        ? `Scanned ${res.cases_scanned} cases (sampled) — ${res.candidates_created} new candidate(s)`
        : `Scanned ${res.cases_scanned} cases — ${res.candidates_created} new candidate(s)`
      toast.success(msg, { duration: 6000 })
      if (res.note) toast(res.note, { icon: 'ℹ️', duration: 6000 })
      void mutate()
    } catch {
      toast.error('Detection failed — please try again')
    } finally {
      setDetecting(false)
    }
  }

  if (!projectId) {
    return (
      <EmptyState
        icon={<Copy className="h-10 w-10" />}
        title="Select a single project"
        description="Duplicate detection runs per project. Pick a project from the top bar to review duplicate test cases."
      />
    )
  }

  return (
    <div className="space-y-4">
      {/* Header / controls */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Duplicate Test Cases</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Banded near-duplicate pairs across this project&rsquo;s authored cases.
            {data ? ` ${data.open_count} open.` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="input text-sm py-1.5"
            value={band}
            onChange={e => setBand(e.target.value as DuplicateBand | '')}
          >
            <option value="">All bands</option>
            <option value="exact">Exact</option>
            <option value="strong">Strong</option>
            <option value="possible">Possible</option>
          </select>
          <select
            className="input text-sm py-1.5"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as DuplicateCandidateStatus)}
          >
            <option value="open">Open</option>
            <option value="merged">Merged</option>
            <option value="dismissed">Dismissed</option>
          </select>
          <button
            onClick={handleDetect}
            disabled={detecting}
            className="btn-primary text-sm flex items-center gap-1.5"
          >
            {detecting ? <LoadingSpinner size="sm" /> : <SearchIcon className="h-3.5 w-3.5" />}
            {detecting ? 'Detecting…' : 'Detect duplicates'}
          </button>
        </div>
      </div>

      {/* Body */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      ) : candidates.length === 0 ? (
        <EmptyState
          icon={<Copy className="h-8 w-8" />}
          title={statusFilter === 'open' ? 'No duplicate candidates' : `No ${statusFilter} candidates`}
          description={
            statusFilter === 'open'
              ? 'Run detection to scan this project’s authored test cases for near-duplicate pairs.'
              : 'Nothing to show for this filter.'
          }
        />
      ) : (
        <div className="space-y-3">
          {candidates.map(c => (
            <DuplicateCandidateCard
              key={c.id}
              projectId={projectId}
              candidate={c}
              onResolved={handleResolved}
            />
          ))}
        </div>
      )}
    </div>
  )
}
