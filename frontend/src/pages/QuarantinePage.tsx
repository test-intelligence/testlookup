import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowUpCircle,
  Bug,
  Check,
  ExternalLink,
  FileText,
  RotateCcw,
  X,
} from 'lucide-react'
import toast from 'react-hot-toast'
import CreateJiraIssueModal from '@/components/defects/CreateJiraIssueModal'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Tabs from '@/components/ui/Tabs'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { openHelp } from '@/store/helpStore'
import { usePermissions } from '@/hooks/usePermissions'
import { useQuarantineList, useQuarantineStats } from '@/hooks/useFlakyQuarantine'
import {
  flakyQuarantineService,
  type FlakyQuarantineRead,
  type QuarantineStatus,
} from '@/services/flakyQuarantineService'
import { QUARANTINE_VIEW_STATUSES, quarantineCounts, type QuarantineView } from './flaky/flakyModel'

export type { QuarantineView } from './flaky/flakyModel'

/** The help topic's section on quarantine: the one place the workflow is explained. */
function HowQuarantineWorks() {
  return (
    <button
      type="button"
      onClick={() => openHelp('flaky', 'quarantine-is-a-recommendation-not-an-action')}
      className="text-xs text-[var(--color-accent)] hover:underline"
    >
      How flaky tests are detected and quarantined
    </button>
  )
}

/**
 * One view of the quarantine requests — a table with the row actions of that
 * state (Approve / Reject, Promote out / Release, File Jira). Used by Flaky
 * tests (Proposed · Quarantined · History) and by Inbox › Approvals (the
 * proposals). `primary` marks it as the page's primary content.
 *
 * Non-QA_LEAD users still see the tables (the underlying data is not secret)
 * but every decision button is hidden.
 */
export function QuarantineBody({
  view,
  projectId,
  primary = false,
}: {
  view: QuarantineView
  projectId: string | null
  primary?: boolean
}) {
  const { canAccessManagement: hasQaLeadAccess, isQaEngineer } = usePermissions()
  // US-6.1: quarantine row being filed to Jira via the one-click dialog.
  const [jiraTarget, setJiraTarget] = useState<FlakyQuarantineRead | null>(null)
  // The view counts (Flaky tests' tab labels, the Inbox's Approvals count)
  // come from the stats key and sit beside this table, so every row action
  // refreshes them too.
  const { refresh: refreshStats } = useQuarantineStats(projectId)
  const { requests, isLoading, isError, refresh } = useQuarantineList({
    projectId,
    liveOnly: view !== 'history',
  })

  const rows = useMemo(() => {
    const allowed = new Set(QUARANTINE_VIEW_STATUSES[view])
    return requests.filter((r) => allowed.has(r.status))
  }, [requests, view])

  return (
    <section
      data-primary={primary ? '' : undefined}
      // Named (a region) as a page's primary content; inside Inbox › Approvals
      // the source section around it carries the name.
      aria-label={
        !primary
          ? undefined
          : view === 'proposals' ? 'Quarantine proposals' : view === 'active' ? 'Quarantined tests' : 'Quarantine history'
      }
      className="space-y-3"
    >
      {isLoading && <LoadingSpinner size="lg" />}
      {isError && <EmptyState title="Failed to load quarantine requests" />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState
          title="Nothing here"
          description={
            view === 'proposals'
              ? 'No pending quarantine proposals.'
              : view === 'active'
              ? 'No tests are currently quarantined.'
              : 'No historical quarantine decisions yet.'
          }
          action={view === 'proposals' ? <HowQuarantineWorks /> : undefined}
        />
      )}
      {!isLoading && !isError && rows.length > 0 && (
        <div className="overflow-hidden rounded-md border border-[var(--color-border)]">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
              <tr>
                <th className="text-left px-3 py-2">Test</th>
                <th className="text-left px-3 py-2">Suite</th>
                <th className="text-left px-3 py-2">Owner</th>
                <th className="text-right px-3 py-2">Flip rate</th>
                <th className="text-right px-3 py-2">Window</th>
                <th className="text-center px-3 py-2">Status</th>
                <th className="text-right px-3 py-2">Updated</th>
                <th className="text-right px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <QuarantineRow
                  key={row.id}
                  row={row}
                  view={view}
                  canAct={hasQaLeadAccess}
                  canFileJira={isQaEngineer}
                  onFileJira={(r) => setJiraTarget(r)}
                  // The counts beside the table are a SEPARATE SWR key and are
                  // on screen at the same time, so approving a row updated the
                  // row and left the counts beside it disagreeing until their
                  // own 30s poll landed.
                  onRefresh={() => Promise.all([refresh(), refreshStats()])}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {jiraTarget && (
        <CreateJiraIssueModal
          projectId={jiraTarget.project_id}
          fingerprint={jiraTarget.test_fingerprint}
          testName={jiraTarget.test_name || jiraTarget.test_fingerprint.slice(0, 16)}
          onClose={() => setJiraTarget(null)}
          onCreated={() => void refresh()}
        />
      )}
    </section>
  )
}

const VIEW_TABS: { id: QuarantineView; label: string }[] = [
  { id: 'proposals', label: 'Proposals' },
  { id: 'active', label: 'Active' },
  { id: 'history', label: 'History' },
]

/**
 * Flaky-test quarantine review page — Tier 1 item 3. No longer routed (UX
 * redesign P4: `/quarantine` redirects to Flaky tests › Quarantined, whose
 * Proposed · Quarantined · History tabs render the same `QuarantineBody`).
 */
export default function QuarantinePage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const [view, setView] = useState<QuarantineView>('proposals')
  const { stats } = useQuarantineStats(projectId)
  const counts = quarantineCounts(stats)

  return (
    <div className="space-y-4">
      <PageHeader
        title="Flaky Quarantine"
        subtitle="Review and approve flaky test quarantine proposals"
        actions={
          <span className="inline-flex items-center gap-2">
            {/* A quarantine request is a standing decision about a TEST, with
                no release dimension — the endpoint takes none. Without this
                the counts below read as one release's proposals. */}
            <AllReleasesBadge reason="A quarantine request is a standing decision about a test, not about one release's runs — a test is quarantined for the project or it is not." />
            <ExperimentalBadge />
          </span>
        }
        tabs={
          <Tabs
            ariaLabel="Quarantine requests"
            items={VIEW_TABS.map((t) => ({ ...t, count: counts[t.id] }))}
            value={view}
            onChange={setView}
          />
        }
      />
      <QuarantineBody view={view} projectId={projectId} />
    </div>
  )
}

// ── Rows ───────────────────────────────────────────────────────────────────

type Decision = 'approve' | 'reject' | 'release'

const DECISION: Record<Decision, { notesLabel: string; confirm: string; success: string }> = {
  approve: { notesLabel: 'Approval notes (optional)', confirm: 'Confirm approve', success: 'Quarantine approved' },
  reject: { notesLabel: 'Rejection reason (optional)', confirm: 'Confirm reject', success: 'Proposal rejected' },
  release: { notesLabel: 'Release notes (optional)', confirm: 'Confirm release', success: 'Quarantine released' },
}

function QuarantineRow({
  row,
  view,
  canAct,
  canFileJira,
  onFileJira,
  onRefresh,
}: {
  row: FlakyQuarantineRead
  view: QuarantineView
  canAct: boolean
  /** QA_ENGINEER+ — matches the backend guard on the one-click endpoint. */
  canFileJira: boolean
  onFileJira: (row: FlakyQuarantineRead) => void
  onRefresh: () => Promise<unknown>
}) {
  const [busy, setBusy] = useState(false)
  // The decision being recorded, with its notes (an inline form under the row;
  // it was a `window.prompt`).
  const [decision, setDecision] = useState<Decision | null>(null)
  const [notes, setNotes] = useState('')

  async function doAction(fn: () => Promise<unknown>, successMsg: string) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
      await onRefresh()
      toast.success(successMsg)
      setDecision(null)
      setNotes('')
    } catch (err) {
      toast.error(`Failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  function confirmDecision() {
    if (!decision) return
    const payload = { notes: notes.trim() || undefined }
    const call =
      decision === 'approve'
        ? () => flakyQuarantineService.approve(row.id, payload)
        : decision === 'reject'
        ? () => flakyQuarantineService.reject(row.id, payload)
        : () => flakyQuarantineService.release(row.id, payload)
    void doAction(call, DECISION[decision].success)
  }

  return (
    <>
      <tr className="border-t border-[var(--color-border)]" data-testid="quarantine-row" data-quarantine-id={row.id}>
        <td className="px-3 py-2 text-[var(--color-text)] font-mono text-xs truncate max-w-[240px]">
          {row.test_name || row.test_fingerprint.slice(0, 16)}
        </td>
        <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] truncate max-w-[200px]">
          {row.suite_name || '—'}
        </td>
        <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] whitespace-nowrap">
          <span className="inline-flex items-center gap-1.5">
            {row.owner_name || '—'}
            {row.defect_id && !row.defect_jira_key && (
              <Link
                to="/defects"
                title="Internal defect record auto-created for this quarantine"
                className="text-[var(--color-accent)] hover:opacity-80"
              >
                <Bug className="h-3 w-3" />
              </Link>
            )}
            {row.defect_jira_key && (
              // US-6.1/US-6.2: Jira link + mirrored status badge. The
              // conflict badge fires when Jira reports Done-category while
              // the signature still failed recently.
              <span className="inline-flex items-center gap-1">
                <a
                  href={row.defect_jira_url ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  title={`Linked Jira issue ${row.defect_jira_key}`}
                  className="inline-flex items-center gap-0.5 text-[var(--color-accent)] hover:underline"
                >
                  {row.defect_jira_key}
                  <ExternalLink className="h-2.5 w-2.5" />
                </a>
                {row.defect_external_status && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]"
                    title="Jira status (mirrored every ~15 min)"
                  >
                    {row.defect_external_status}
                  </span>
                )}
                {row.defect_external_status_conflict && (
                  <span
                    className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded border border-[var(--status-broken-bd)]/40 text-[var(--status-broken)] bg-[var(--status-broken-bg)]/10"
                    title="Jira reports this issue as done, but the test still failed within the last 7 days."
                  >
                    <AlertTriangle className="h-2.5 w-2.5" />
                    closed in Jira but still failing
                  </span>
                )}
              </span>
            )}
          </span>
        </td>
        <td className="px-3 py-2 text-right text-xs text-[var(--color-text)]">
          {row.flip_rate != null ? `${(row.flip_rate * 100).toFixed(0)}%` : '—'}
        </td>
        <td className="px-3 py-2 text-right text-xs text-[var(--color-text-muted)]">
          {row.flip_window_size ?? '—'}
        </td>
        <td className="px-3 py-2 text-center">
          <div className="inline-flex flex-col items-center gap-1">
            <StatusPill status={row.status} />
            {row.stale && (
              <span
                className="text-[10px] px-2 py-0.5 rounded border border-[var(--status-failed-bd)]/40 text-[var(--status-failed)] bg-[var(--status-failed-bg)]/10"
                title={row.sla_days != null ? `SLA: ${row.sla_days} days` : undefined}
              >
                stale — {daysOverSla(row.stale_at)}d over SLA
              </span>
            )}
            {row.ready_to_promote && (
              <span
                className="text-[10px] px-2 py-0.5 rounded border border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10"
                title={`${row.consecutive_passes} consecutive passing runs since quarantine`}
              >
                Ready to promote
              </span>
            )}
          </div>
        </td>
        <td className="px-3 py-2 text-right text-[11px] text-[var(--color-text-faint)]">
          {new Date(row.updated_at).toLocaleDateString()}
        </td>
        <td className="px-3 py-2 text-right whitespace-nowrap">
          {canAct && view === 'proposals' && decision === null && (
            <div className="inline-flex items-center gap-1">
              <button
                type="button"
                disabled={busy}
                data-testid="quarantine-approve"
                onClick={() => setDecision('approve')}
                className="text-xs text-[var(--status-passed)] hover:underline flex items-center gap-0.5"
              >
                <Check className="h-3 w-3" /> Approve
              </button>
              <span className="text-[var(--color-text-faint)]">·</span>
              <button
                type="button"
                disabled={busy}
                data-testid="quarantine-reject"
                onClick={() => setDecision('reject')}
                className="text-xs text-[var(--status-failed)] hover:underline flex items-center gap-0.5"
              >
                <X className="h-3 w-3" /> Reject
              </button>
            </div>
          )}
          {canAct && view === 'active' && decision === null && (
            <div className="inline-flex items-center gap-1">
              {row.ready_to_promote && (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void doAction(
                        // One-click release — the pass streak already proved
                        // stability, no notes needed.
                        () =>
                          flakyQuarantineService.release(row.id, {
                            notes: `Promoted out of quarantine after ${row.consecutive_passes} consecutive passing runs`,
                          }),
                        'Quarantine released',
                      )
                    }
                    className="text-xs text-[var(--status-passed)] hover:underline flex items-center gap-0.5 font-medium"
                  >
                    <ArrowUpCircle className="h-3 w-3" /> Promote out
                  </button>
                  <span className="text-[var(--color-text-faint)]">·</span>
                </>
              )}
              <button
                type="button"
                disabled={busy}
                data-testid="quarantine-release"
                onClick={() => setDecision('release')}
                className="text-xs text-[var(--color-accent)] hover:underline flex items-center gap-0.5"
              >
                <RotateCcw className="h-3 w-3" /> Release
              </button>
            </div>
          )}
          {canFileJira && view !== 'history' && !row.defect_jira_key && (
            // US-6.1: one-click Jira issue for quarantined tests that don't
            // have a linked defect yet. Opens the prefilled review dialog —
            // the backend dedups against open defects for the fingerprint.
            <button
              type="button"
              disabled={busy}
              onClick={() => onFileJira(row)}
              title="File a pre-filled Jira issue for this flaky test"
              className="text-xs text-[var(--color-accent)] hover:underline inline-flex items-center gap-0.5 ml-2"
            >
              <Bug className="h-3 w-3" /> File Jira
            </button>
          )}
          {view === 'history' && row.reviewer_notes && (
            <span
              className="text-xs text-[var(--color-text-muted)]"
              title={row.reviewer_notes}
            >
              <FileText className="h-3 w-3 inline" />
            </span>
          )}
        </td>
      </tr>
      {decision !== null && (
        <tr data-testid="quarantine-decision-form">
          <td colSpan={8} className="px-3 pb-3">
            <div className="flex flex-wrap items-end gap-2 rounded-md bg-[var(--color-bg-secondary)] p-3">
              <label className="flex min-w-[260px] flex-1 flex-col gap-1 text-xs text-[var(--color-text-muted)]">
                {DECISION[decision].notesLabel}
                <input
                  data-testid="quarantine-notes"
                  value={notes}
                  maxLength={2000}
                  onChange={(e) => setNotes(e.target.value)}
                  className="input text-sm"
                />
              </label>
              <button
                type="button"
                data-testid="quarantine-confirm"
                disabled={busy}
                onClick={confirmDecision}
                className="btn-primary text-xs disabled:opacity-40"
              >
                {DECISION[decision].confirm}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setDecision(null)
                  setNotes('')
                }}
                className="btn-ghost text-xs"
              >
                Cancel
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** Whole days elapsed since the SLA deadline (US-5.4 "stale — N days over SLA"). */
function daysOverSla(staleAt: string | null): number {
  if (!staleAt) return 0
  const over = Date.now() - new Date(staleAt).getTime()
  return Math.max(0, Math.floor(over / 86_400_000))
}

function StatusPill({ status }: { status: QuarantineStatus }) {
  const toneClass =
    status === 'QUARANTINED' || status === 'RE_QUARANTINED'
      ? 'border-[var(--status-broken-bd)]/40 text-[var(--status-broken)] bg-[var(--status-broken-bg)]/10'
      : status === 'RECHECK_SCHEDULED' || status === 'APPROVED'
      ? 'border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent)]/10'
      : status === 'PROPOSED' || status === 'DETECTED'
      ? 'border-[var(--status-broken-bd)]/40 text-[var(--status-broken)]'
      : status === 'RELEASED'
      ? 'border-[var(--status-passed-bd)]/40 text-[var(--status-passed)] bg-[var(--status-passed-bg)]/10'
      : status === 'REJECTED' || status === 'EXPIRED'
      ? 'border-[var(--status-failed-bd)]/40 text-[var(--status-failed)] bg-[var(--status-failed-bg)]/10'
      : 'border-[var(--color-border)] text-[var(--color-text-muted)]'
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded border ${toneClass}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}
