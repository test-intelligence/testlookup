/**
 * My Failures — the calling user's auto-assigned failures (migration 0080).
 *
 * Reads from /api/v1/me/assigned-failures. Project scope is driven by the
 * project store (ALL_PROJECTS_ID = "all"). The page polls every 30s so new
 * ingests appear without a manual refresh.
 *
 * Layout:
 *   Header        → title + counts + filter chips (days + status hint)
 *   Empty state   → friendly "you're caught up" when total === 0
 *   Table         → severity dot, test name + suite, project · build, age,
 *                   error preview, row-click → /runs/:rid/tests/:cid
 *   Pagination    → standard Pagination component
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, CheckSquare, Clock, ExternalLink, Inbox, UserCog, X } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Pagination from '@/components/ui/Pagination'
import { formatRunWhen } from '@/utils/formatters'
import { useMyFailures, useReassignOptions } from '@/hooks/useMyFailures'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { useAuthStore } from '@/store/authStore'
import { usePermissions } from '@/hooks/usePermissions'
import {
  myFailuresService,
  type ReassignmentOption,
} from '@/services/myFailuresService'
import { mutate as swrMutate } from 'swr'
import type { MyFailureItem, TriageStatus } from '@/types/myFailures'

// Time-window chips. The backend allows 1-365; surface the most useful three.
const DAYS_OPTIONS = [1, 7, 30] as const

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return '—'
  const m = Math.floor(ms / 60_000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function severityDot(severity: string | null | undefined): string {
  const s = (severity || '').toLowerCase()
  if (s === 'blocker' || s === 'critical') return '#fca5a5'  // red
  if (s === 'major' || s === 'high')        return '#fcd34d' // amber
  if (s === 'minor' || s === 'low')         return '#9198a1' // slate
  return '#86efac'                                            // unknown → green-ish
}

export default function MyFailuresPage() {
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const { isQaLead } = usePermissions()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // Reassignment modal state. ``reassignFor`` is the failure currently
  // being reassigned (null = modal closed). Only QA_LEAD+ ever sees
  // the button that opens this modal; the backend enforces the same
  // contract independently — clients can't pivot it.
  const [reassignFor, setReassignFor] = useState<MyFailureItem | null>(null)
  // Triage-status modal state. Same single-target pattern as
  // ``reassignFor`` — only one modal open at a time.
  const [triageFor, setTriageFor] = useState<MyFailureItem | null>(null)

  // Time window comes from the shared user-level preference so a
  // selection made on any other page (Summary, Live, Coverage, …)
  // follows the user here. Snapped to this page's allowed set.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, DAYS_OPTIONS)
  const [page, setPage] = useState(1)
  const size = 25

  // Mine = caller's own assignments only (default).
  // Team = every unresolved failure on the project (QA_LEAD/ADMIN only).
  //
  // Background: failures are auto-assigned at ingest time to the suite
  // owner — or to the project's default-QA-Lead user when no explicit
  // owner exists (see backend/services/default_qa_lead_service). A
  // project admin viewing /my-failures would otherwise see almost
  // nothing because the synthetic QA-Lead user owns the bulk. The
  // toggle gives leads/admins a one-click view of the whole project's
  // open inbox without reassigning rows.
  const [scope, setScope] = useState<'mine' | 'team'>('mine')
  const canSeeTeam = isQaLead
  const effectiveScope: 'mine' | 'team' = canSeeTeam ? scope : 'mine'

  const { data, isLoading, error } = useMyFailures({ days, page, size, scope: effectiveScope })

  const scopeLabel = isAllProjects ? 'all your projects' : (project?.name ?? 'the selected project')

  return (
    <>
      <PageHeader
        title="My Failures"
        subtitle={
          user
            ? `Auto-assigned failures across ${scopeLabel}. Updated every 30 seconds.`
            : 'Sign in to see your assigned failures.'
        }
      />

      {/* Filter row */}
      <div className="flex items-center gap-2.5 flex-wrap mb-3">
        <span
          className="inline-flex items-center gap-1.5 text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]"
          style={{ letterSpacing: 'var(--tracking-wider)' }}
        >
          Window
        </span>
        <div role="radiogroup" aria-label="Time window" className="flex items-center gap-1.5">
          {DAYS_OPTIONS.map(d => {
            const active = days === d
            return (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => { setStoredDays(d); setPage(1) }}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors"
                style={{
                  background: active ? 'rgba(68,147,248,0.14)' : 'transparent',
                  borderColor: active ? 'rgba(68,147,248,0.30)' : 'var(--color-border)',
                  color: active ? '#93c5fd' : 'var(--color-text-muted)',
                }}
              >
                {d === 1 ? '24h' : `${d}d`}
              </button>
            )
          })}
        </div>
        {canSeeTeam && (
          <>
            <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
            <span
              className="inline-flex items-center gap-1.5 text-[10.5px] uppercase font-medium text-[var(--color-text-muted)]"
              style={{ letterSpacing: 'var(--tracking-wider)' }}
            >
              Scope
            </span>
            <div role="radiogroup" aria-label="Scope" className="flex items-center gap-1.5">
              {(['mine', 'team'] as const).map(opt => {
                const active = effectiveScope === opt
                return (
                  <button
                    key={opt}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => { setScope(opt); setPage(1) }}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-[12.5px] rounded-full border transition-colors capitalize"
                    style={{
                      background: active ? 'rgba(68,147,248,0.14)' : 'transparent',
                      borderColor: active ? 'rgba(68,147,248,0.30)' : 'var(--color-border)',
                      color: active ? '#93c5fd' : 'var(--color-text-muted)',
                    }}
                    title={
                      opt === 'mine'
                        ? "Only failures assigned to you"
                        : "Every unresolved failure across the project (QA_LEAD/ADMIN)"
                    }
                  >
                    {opt}
                  </button>
                )
              })}
            </div>
          </>
        )}
        <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
        <span className="text-[12px] text-[var(--color-text-muted)]">
          Status: <code className="font-mono text-[11px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">FAILED, BROKEN</code>
        </span>
        {data?.unresolved_total !== undefined && (
          <span className="ml-auto text-[12px] text-[var(--color-text-muted)]">
            {data.unresolved_total === 0
              ? 'Nothing assigned'
              : <><strong className="text-[var(--color-text)] tabular-nums">{data.unresolved_total}</strong> assigned</>}
          </span>
        )}
      </div>

      {/* Body */}
      {isLoading && !data ? (
        <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
      ) : error ? (
        <div className="flex items-center gap-2 text-sm text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded px-3 py-3">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Couldn’t load your failures. The inbox will retry automatically.
        </div>
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title={effectiveScope === 'team' ? 'No open team failures' : 'You’re caught up'}
          description={
            effectiveScope === 'team'
              ? `No unresolved failures across ${scopeLabel} in the last ${
                  days === 1 ? '24 hours' : `${days} days`
                }. New failures land here as soon as runs are ingested.`
              : `No failed or broken tests assigned to you in the last ${
                  days === 1 ? '24 hours' : `${days} days`
                }. New failures land here automatically when a test run is ingested.`
          }
          icon={<Inbox className="h-6 w-6" />}
        />
      ) : (
        <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)]/80">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider w-2">{/* severity dot */}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Test Suite</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Run</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Status</th>
                <th
                  className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider"
                  title={`Failures of this test in the last ${days === 1 ? '24 hours' : `${days} days`}`}
                >
                  Failures
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Age</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider w-4">{/* open */}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {data.items.map(item => (
                <FailureRow
                  key={item.id}
                  item={item}
                  windowDays={days}
                  canReassign={isQaLead}
                  onOpen={() => navigate(item.navigation_url)}
                  onReassign={() => setReassignFor(item)}
                  onTriage={() => setTriageFor(item)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.pages > 1 && (
        <div className="mt-3.5">
          <Pagination page={page} pages={data.pages} total={data.total} onChange={setPage} />
        </div>
      )}

      {triageFor && (
        <TriageStatusModal
          failure={triageFor}
          onClose={() => setTriageFor(null)}
          onUpdated={() => {
            setTriageFor(null)
            // Any non-PENDING_REVIEW status drops the row off the
            // inbox — refetch so the count + page reflect that.
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures',
            )
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count',
            )
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count-unscoped',
            )
          }}
        />
      )}

      {reassignFor && (
        <ReassignModal
          failure={reassignFor}
          onClose={() => setReassignFor(null)}
          onReassigned={() => {
            setReassignFor(null)
            // The reassigned row no longer belongs to the caller's
            // inbox — refetch instead of relying on optimistic
            // splice, since `failure_count` and pagination would
            // otherwise drift.
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures',
            )
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count',
            )
            void swrMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count-unscoped',
            )
          }}
        />
      )}
    </>
  )
}

function FailureRow({
  item,
  windowDays,
  canReassign,
  onOpen,
  onReassign,
  onTriage,
}: {
  item: MyFailureItem
  windowDays: number
  canReassign: boolean
  onOpen: () => void
  onReassign: () => void
  onTriage: () => void
}) {
  const dot = severityDot(item.severity)
  const count = item.failure_count ?? 1
  const windowLabel = windowDays === 1 ? '24h' : `${windowDays}d`
  // Visual emphasis scales with repetition — a single failure stays neutral,
  // 2-4 is amber, 5+ is red so repeat offenders pop without screen-real-estate cost.
  const countTone =
    count >= 5
      ? 'bg-red-900/40 text-red-300'
      : count >= 2
      ? 'bg-amber-900/30 text-amber-300'
      : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer hover:bg-[var(--color-bg-secondary)]/60 transition-colors"
    >
      <td className="px-4 py-3">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} aria-hidden />
      </td>
      <td className="px-4 py-3">
        <div className="text-[var(--color-text)] font-medium truncate max-w-[360px]" title={item.test_name}>
          {item.test_name}
        </div>
        {item.error_message && (
          <div className="text-[11px] text-[var(--color-text-muted)] truncate max-w-[360px] font-mono">
            {item.error_message}
          </div>
        )}
        {item.last_failure_step && (
          <div
            className="text-[10.5px] text-[var(--color-text-faint)] truncate max-w-[360px]"
            title={`Failed at step: ${item.last_failure_step}`}
          >
            failed at: <span className="font-mono text-[var(--color-text-muted)]">{item.last_failure_step}</span>
          </div>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-secondary)] align-middle">
        {item.suite_name ? (
          <span className="truncate max-w-[200px] inline-block align-middle" title={item.suite_name}>{item.suite_name}</span>
        ) : (
          <span className="text-[var(--color-text-faint)]">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-[var(--color-text-secondary)] font-mono text-xs">
        {/* Project name is now omitted because the user selects it in
            the global project dropdown — surfacing it again per-row is
            redundant. Run identifier is the human-readable, per-(project,
            suite) incremental ``Run #N``; falls back to the raw SDK
            build_number for pre-run_seq rows. The short test_run_id slug
            is shown underneath as a copy/correlation aid. */}
        {item.run_seq != null ? (
          <span className="text-[var(--color-text)]">Run #{item.run_seq}</span>
        ) : item.build_number ? (
          <span className="text-[var(--color-text)]">{item.build_number}</span>
        ) : (
          <span className="text-[var(--color-text-faint)]">—</span>
        )}
        {/* When the run was generated — shown INLINE (matching /live and the
            /agents live card) so same-numbered "Run #N" rows are distinguishable
            at a glance, not only on hover. */}
        {(() => {
          const when = formatRunWhen(item.created_at)
          return when ? (
            <div className="text-[10px] text-[var(--color-text-faint)] tabular-nums">{when}</div>
          ) : null
        })()}
        {item.test_run_id && (
          <div className="text-[10px] text-[var(--color-text-faint)] tabular-nums">
            {item.test_run_id.slice(0, 8)}
          </div>
        )}
      </td>
      <td className="px-4 py-3">
        <span className={clsx('text-[11px] px-2 py-0.5 rounded font-medium', statusBadgeClass(item.status))}>
          {item.status}
        </span>
      </td>
      <td className="px-4 py-3 text-right">
        <span
          className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium tabular-nums', countTone)}
          title={`Failed ${count} time${count === 1 ? '' : 's'} in the last ${windowLabel}`}
        >
          × {count}
        </span>
      </td>
      <td className="px-4 py-3 text-[var(--color-text-muted)] text-xs">
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {relativeAge(item.created_at)}
        </span>
      </td>
      <td className="px-4 py-3 text-right whitespace-nowrap">
        {/* Triage-status button — visible to anyone (the row is in their
            inbox, so they ARE the assignee). The backend independently
            enforces the assignee-or-manager rule. */}
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onTriage() }}
          className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 mr-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          title="Update triage status (drops the row off your inbox)"
        >
          <CheckSquare className="h-3 w-3" /> Update status
        </button>
        {canReassign && (
          <button
            type="button"
            // ``stopPropagation`` so the row's ``onOpen`` (which navigates
            // to the test-detail page) doesn't fire from the same click.
            onClick={e => { e.stopPropagation(); onReassign() }}
            className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 mr-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
            title="Reassign to the suite owner or a QA Engineer"
          >
            <UserCog className="h-3 w-3" /> Reassign
          </button>
        )}
        <ExternalLink className="h-3.5 w-3.5 text-[var(--color-text-faint)] inline-block" />
      </td>
    </tr>
  )
}

function statusBadgeClass(status: string): string {
  if (status === 'BROKEN') return 'bg-amber-900/40 text-amber-300'
  if (status === 'FAILED') return 'bg-red-900/40 text-red-300'
  return 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
}


function ReassignModal({
  failure,
  onClose,
  onReassigned,
}: {
  failure: MyFailureItem
  onClose: () => void
  onReassigned: () => void
}) {
  // SWR owns the fetch/loading/error state declaratively — this was a
  // load-on-mount useEffect driving three setState calls (set-state-in-effect).
  // Keyed on the failure id, so reopening the modal for a different row
  // re-fetches exactly as the old `[failure.id]` dependency did.
  const { data, error: loadError, isLoading: loading } =
    useReassignOptions(failure.id as unknown as string)
  const options = data ?? null
  const error = loadError
    ? ((loadError as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail || 'Could not load reassignment options.')
    : null

  // Default selection: suite owner if present, otherwise the first QA
  // Engineer. Empty string when neither exists — the submit button stays
  // disabled in that case so the user can't fire an empty PUT. Derived during
  // render (no setState-on-load) with an explicit pick taking precedence, so
  // the picker behaves exactly as before without driving state from an effect.
  const defaultUserId = options
    ? (options.suite_owner?.user_id ?? options.qa_engineers[0]?.user_id ?? '')
    : ''
  const [picked, setPicked] = useState('')
  const selectedUserId = picked || defaultUserId

  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit() {
    if (!selectedUserId) return
    setSubmitting(true)
    try {
      await myFailuresService.reassign(failure.id as unknown as string, selectedUserId)
      toast.success('Failure reassigned')
      onReassigned()
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      toast.error(detail || 'Failed to reassign')
    } finally {
      setSubmitting(false)
    }
  }

  // Combined list for rendering — suite owner labelled, engineers grouped.
  // Capture suite_owner once so the onSelect closure narrows it to non-null
  // without a non-null assertion.
  const suiteOwner = options?.suite_owner
  const hasAny = options
    ? Boolean(suiteOwner) || options.qa_engineers.length > 0
    : false

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl w-full max-w-md p-5"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--color-text)] m-0">Reassign failure</h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
              {failure.test_name}
            </p>
            {failure.suite_name && (
              <p className="text-[11px] text-[var(--color-text-faint)] mt-0.5 truncate">
                Suite: {failure.suite_name}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] p-1 -mt-1 -mr-1"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading ? (
          <div className="py-8 flex justify-center"><LoadingSpinner /></div>
        ) : error ? (
          <div className="text-xs text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded px-3 py-2">
            {error}
          </div>
        ) : !hasAny ? (
          <div className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded px-3 py-3">
            No suite owner is configured and this project has no QA Engineer members.
            Add one under <strong>Settings → User Management → Project Members</strong> and try again.
          </div>
        ) : (
          <>
            <div role="radiogroup" aria-label="New assignee" className="space-y-1 max-h-[280px] overflow-y-auto">
              {suiteOwner && (
                <ReassignChoice
                  option={suiteOwner}
                  badge="Suite owner"
                  checked={selectedUserId === suiteOwner.user_id}
                  onSelect={() => setPicked(suiteOwner.user_id)}
                />
              )}
              {options?.qa_engineers.map(eng => (
                <ReassignChoice
                  key={eng.user_id}
                  option={eng}
                  badge="QA Engineer"
                  checked={selectedUserId === eng.user_id}
                  onSelect={() => setPicked(eng.user_id)}
                />
              ))}
            </div>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={submitting || !selectedUserId}
                className="flex items-center gap-1.5 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] px-3 py-1.5 rounded-lg font-medium"
              >
                {submitting && <LoadingSpinner size="sm" />}
                {submitting ? 'Reassigning…' : 'Reassign'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}


const TRIAGE_CHOICES: Array<{
  value: TriageStatus
  label: string
  description: string
}> = [
  // ``PENDING_REVIEW`` is intentionally absent: this modal is for
  // resolving the row, not for rolling back. A QA Lead reassigning the
  // failure handles the "I'm not the right person" case.
  {
    value: 'REVIEWED_APPROVED',
    label: 'Reviewed & approved',
    description: "I looked at it — no action needed.",
  },
  {
    value: 'DEFECT_CREATED',
    label: 'Defect created',
    description: 'A defect/bug has been logged. Paste the link in the notes.',
  },
  {
    value: 'AUTOMATION_SCRIPT_ISSUE',
    label: 'Automation script issue',
    description: "Test code bug — not a product defect. Fix the test, not the product.",
  },
  {
    value: 'FLAKY_TEST',
    label: 'Flaky test',
    description: 'Nondeterministic — candidate for the quarantine workflow.',
  },
  {
    value: 'WONT_FIX',
    label: "Won't fix",
    description: 'Deprecated test or accepted failure. Note why.',
  },
]


function TriageStatusModal({
  failure,
  onClose,
  onUpdated,
}: {
  failure: MyFailureItem
  onClose: () => void
  onUpdated: () => void
}) {
  const [selected, setSelected] = useState<TriageStatus>('REVIEWED_APPROVED')
  const [notes, setNotes] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit() {
    setSubmitting(true)
    try {
      await myFailuresService.updateTriageStatus(failure.id as unknown as string, {
        status: selected,
        notes: notes.trim() || null,
      })
      toast.success(`Status updated to ${selected.replace(/_/g, ' ').toLowerCase()}`)
      onUpdated()
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      toast.error(detail || 'Failed to update status')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl w-full max-w-md p-5"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--color-text)] m-0">Update triage status</h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
              {failure.test_name}
            </p>
            <p className="text-[11px] text-[var(--color-text-faint)] mt-0.5">
              Anything other than &quot;Pending review&quot; removes this row from your inbox.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] p-1 -mt-1 -mr-1"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div role="radiogroup" aria-label="New triage status" className="space-y-1 mb-3">
          {TRIAGE_CHOICES.map(choice => (
            <label
              key={choice.value}
              className={clsx(
                'flex items-start gap-3 px-3 py-2 rounded border cursor-pointer transition-colors',
                selected === choice.value
                  ? 'border-[var(--color-accent)] bg-[rgba(68,147,248,0.08)]'
                  : 'border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]',
              )}
            >
              <input
                type="radio"
                name="triage-status"
                checked={selected === choice.value}
                onChange={() => setSelected(choice.value)}
                className="h-3.5 w-3.5 mt-0.5"
              />
              <div className="flex-1 min-w-0">
                <div className="text-xs text-[var(--color-text)]">{choice.label}</div>
                <div className="text-[11px] text-[var(--color-text-muted)]">{choice.description}</div>
              </div>
            </label>
          ))}
        </div>

        <label className="block text-[11px] text-[var(--color-text-muted)] mb-1">
          Notes <span className="text-[var(--color-text-faint)]">(optional — defect link, rationale)</span>
        </label>
        <textarea
          value={notes}
          onChange={e => setNotes(e.target.value.slice(0, 2000))}
          rows={3}
          placeholder={
            selected === 'DEFECT_CREATED'
              ? 'https://your-jira/BUG-1234'
              : selected === 'WONT_FIX'
              ? 'Why this won’t be fixed…'
              : 'Optional context'
          }
          className="w-full text-xs px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
        />

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-1.5 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] px-3 py-1.5 rounded-lg font-medium"
          >
            {submitting && <LoadingSpinner size="sm" />}
            {submitting ? 'Saving…' : 'Update status'}
          </button>
        </div>
      </div>
    </div>
  )
}


function ReassignChoice({
  option,
  badge,
  checked,
  onSelect,
}: {
  option: ReassignmentOption
  badge: string
  checked: boolean
  onSelect: () => void
}) {
  return (
    <label
      className={clsx(
        'flex items-center gap-3 px-3 py-2 rounded border cursor-pointer transition-colors',
        checked
          ? 'border-[var(--color-accent)] bg-[rgba(68,147,248,0.08)]'
          : 'border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]',
      )}
    >
      <input
        type="radio"
        name="reassign-target"
        checked={checked}
        onChange={onSelect}
        className="h-3.5 w-3.5"
      />
      <div className="flex-1 min-w-0">
        <div className="text-xs text-[var(--color-text)] truncate">
          {option.full_name || option.username}
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)] truncate">{option.email}</div>
      </div>
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
        {badge}
      </span>
    </label>
  )
}
