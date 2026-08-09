import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowUpCircle,
  Bug,
  Check,
  Clock,
  ExternalLink,
  FileText,
  RotateCcw,
  ShieldAlert,
  X,
} from 'lucide-react'
import toast from 'react-hot-toast'
import CreateJiraIssueModal from '@/components/defects/CreateJiraIssueModal'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { usePermissions } from '@/hooks/usePermissions'
import { useQuarantineList, useQuarantineStats } from '@/hooks/useFlakyQuarantine'
import {
  flakyQuarantineService,
  type FlakyQuarantineRead,
  type QuarantineStatus,
} from '@/services/flakyQuarantineService'

type Tab = 'proposals' | 'active' | 'history'

const TAB_STATUSES: Record<Tab, QuarantineStatus[]> = {
  proposals: ['PROPOSED', 'DETECTED'],
  active: ['APPROVED', 'QUARANTINED', 'RECHECK_SCHEDULED', 'RE_QUARANTINED'],
  history: ['RELEASED', 'REJECTED', 'EXPIRED'],
}

/**
 * Flaky-test quarantine review page — Tier 1 item 3.
 *
 * Three tabs mirror the state machine:
 *
 * * **Proposals** — PROPOSED/DETECTED rows awaiting QA Lead decision.
 *   Each row has Approve / Reject buttons with an optional notes field.
 *
 * * **Active** — APPROVED/QUARANTINED/RECHECK_SCHEDULED/RE_QUARANTINED.
 *   Each row shows the window end-date and a Release button for manual
 *   termination before the recheck cycle.
 *
 * * **History** — RELEASED/REJECTED/EXPIRED, read-only.
 *
 * Non-QA_LEAD users still see the tables (the underlying data is not
 * secret) but every action button is hidden.
 */
export default function QuarantinePage() {
  const { canAccessManagement: hasQaLeadAccess, isQaEngineer } = usePermissions()
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const [tab, setTab] = useState<Tab>('proposals')
  // US-6.1: quarantine row being filed to Jira via the one-click dialog.
  const [jiraTarget, setJiraTarget] = useState<FlakyQuarantineRead | null>(null)
  const { stats } = useQuarantineStats(projectId)
  const { requests, isLoading, isError, refresh } = useQuarantineList({
    projectId,
    liveOnly: tab !== 'history',
  })

  const rowsForTab = useMemo(() => {
    const allowed = new Set(TAB_STATUSES[tab])
    return requests.filter((r) => allowed.has(r.status))
  }, [requests, tab])

  return (
    <div className="space-y-4">
      <PageHeader
        title="Flaky Quarantine"
        subtitle="Review and approve flaky test quarantine proposals"
        actions={<ExperimentalBadge />}
      />

      {/* Stats tiles */}
      <div className="grid grid-cols-4 gap-3">
        <StatTile
          icon={<ShieldAlert className="h-4 w-4 text-[var(--status-broken)]" />}
          label="Awaiting review"
          value={(stats?.proposed ?? 0) + (stats?.detected ?? 0)}
        />
        <StatTile
          icon={<Clock className="h-4 w-4 text-[var(--color-accent)]" />}
          label="Active quarantines"
          value={
            (stats?.quarantined ?? 0) +
            (stats?.approved ?? 0) +
            (stats?.recheck_scheduled ?? 0) +
            (stats?.re_quarantined ?? 0)
          }
        />
        <StatTile
          icon={<Check className="h-4 w-4 text-[var(--status-passed)]" />}
          label="Released"
          value={stats?.released ?? 0}
        />
        <StatTile
          icon={<X className="h-4 w-4 text-[var(--status-failed)]" />}
          label="Rejected / expired"
          value={(stats?.rejected ?? 0) + (stats?.expired ?? 0)}
        />
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-[var(--color-border)]">
        {(['proposals', 'active', 'history'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
              tab === t
                ? 'border-[var(--color-accent)] text-[var(--color-text)]'
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {t === 'proposals' ? 'Proposals' : t === 'active' ? 'Active' : 'History'}
          </button>
        ))}
      </div>

      {isLoading && <LoadingSpinner size="lg" />}
      {isError && <EmptyState title="Failed to load quarantine requests" />}
      {!isLoading && !isError && rowsForTab.length === 0 && (
        <EmptyState
          title="Nothing here"
          description={
            tab === 'proposals'
              ? 'No pending quarantine proposals. The flaky sentinel agent creates these when it detects a test with flip rate >= 20% over 10 runs.'
              : tab === 'active'
              ? 'No tests are currently quarantined.'
              : 'No historical quarantine decisions yet.'
          }
        />
      )}
      {!isLoading && !isError && rowsForTab.length > 0 && (
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
              {rowsForTab.map((row) => (
                <QuarantineRow
                  key={row.id}
                  row={row}
                  tab={tab}
                  canAct={hasQaLeadAccess}
                  canFileJira={isQaEngineer}
                  onFileJira={(r) => setJiraTarget(r)}
                  onRefresh={() => refresh()}
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
    </div>
  )
}

// ── Tiles & rows ───────────────────────────────────────────────────────────

function StatTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: number
}) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
        {icon}
        {label}
      </div>
      <div className="text-lg font-semibold text-[var(--color-text)] mt-1">{value}</div>
    </div>
  )
}

function QuarantineRow({
  row,
  tab,
  canAct,
  canFileJira,
  onFileJira,
  onRefresh,
}: {
  row: FlakyQuarantineRead
  tab: Tab
  canAct: boolean
  /** QA_ENGINEER+ — matches the backend guard on the one-click endpoint. */
  canFileJira: boolean
  onFileJira: (row: FlakyQuarantineRead) => void
  onRefresh: () => Promise<unknown>
}) {
  const [busy, setBusy] = useState(false)

  async function doAction(fn: () => Promise<unknown>, successMsg: string) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
      await onRefresh()
      toast.success(successMsg)
    } catch (err) {
      toast.error(`Failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <tr className="border-t border-[var(--color-border)]">
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
        {canAct && tab === 'proposals' && (
          <div className="inline-flex items-center gap-1">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                doAction(
                  async () => {
                    const notes = window.prompt('Approval notes (optional)') ?? undefined
                    return flakyQuarantineService.approve(row.id, { notes })
                  },
                  'Quarantine approved',
                )
              }
              className="text-xs text-[var(--status-passed)] hover:underline flex items-center gap-0.5"
            >
              <Check className="h-3 w-3" /> Approve
            </button>
            <span className="text-[var(--color-text-faint)]">·</span>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                doAction(
                  async () => {
                    const notes = window.prompt('Rejection reason (optional)') ?? undefined
                    return flakyQuarantineService.reject(row.id, { notes })
                  },
                  'Proposal rejected',
                )
              }
              className="text-xs text-[var(--status-failed)] hover:underline flex items-center gap-0.5"
            >
              <X className="h-3 w-3" /> Reject
            </button>
          </div>
        )}
        {canAct && tab === 'active' && (
          <div className="inline-flex items-center gap-1">
            {row.ready_to_promote && (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    doAction(
                      // One-click release — the pass streak already proved
                      // stability, no notes prompt needed.
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
              onClick={() =>
                doAction(
                  async () => {
                    const notes = window.prompt('Release notes (optional)') ?? undefined
                    return flakyQuarantineService.release(row.id, { notes })
                  },
                  'Quarantine released',
                )
              }
              className="text-xs text-[var(--color-accent)] hover:underline flex items-center gap-0.5"
            >
              <RotateCcw className="h-3 w-3" /> Release
            </button>
          </div>
        )}
        {canFileJira && tab !== 'history' && !row.defect_jira_key && (
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
        {tab === 'history' && row.reviewer_notes && (
          <span
            className="text-xs text-[var(--color-text-muted)]"
            title={row.reviewer_notes}
          >
            <FileText className="h-3 w-3 inline" />
          </span>
        )}
      </td>
    </tr>
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
