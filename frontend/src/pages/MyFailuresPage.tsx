/**
 * Inbox (`/my-failures`, UX redesign P4 item 5, owner decision D4): what is
 * waiting on the reader, in two tabs (`?tab=`):
 *
 *   Assigned to me (default) → the auto-assigned failures (migration 0080),
 *       from /api/v1/me/assigned-failures, polled every 30 s. Toolbar: the
 *       global `WindowPicker` (24h · 7d · 30d) and, for QA leads, the
 *       Mine/Team scope (`?scope=team` on the same endpoint). One table, the
 *       page's primary content (`data-primary`), two lines a row: test name
 *       over its error · failing step · assignment reason; suite; run number
 *       over its time and id; repeat count; age; Update status / Reassign.
 *       Row click → the backend-provided `navigation_url`. (The Status column
 *       and the static "Status: FAILED, BROKEN" chip are gone, §5.)
 *   Approvals → AI reports (the old `/reviews`, which redirects here),
 *       quarantine proposals and test-case approvals, filtered by source
 *       (`inbox/ApprovalsTab.tsx`). It needs one project; in All Projects
 *       mode it shows the project prompt. Its count is in the tab label.
 *
 * Project scope is driven by the project store (ALL_PROJECTS_ID = "all").
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, CheckSquare, Clock, ExternalLink, Inbox, UserCog, X } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import Tabs from '@/components/ui/Tabs'
import WindowPicker from '@/components/ui/WindowPicker'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import Pagination from '@/components/ui/Pagination'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
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
import { appMutate } from '@/utils/swrCacheMutate'
import type { MyFailureItem, TriageStatus } from '@/types/myFailures'
import ApprovalsTab, { ApprovalsCountBadge } from './inbox/ApprovalsTab'
import ChipFilter from './inbox/ChipFilter'

// Time-window chips. The backend allows 1-365; surface the most useful three.
const DAYS_OPTIONS = [1, 7, 30] as const

const INBOX_TABS = ['assigned', 'approvals'] as const

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

/** `null` color = severity not known: drawn as a hollow, muted ring. It used
 *  to fall through to the passed green, which read as "this failure is fine". */
function severityDot(severity: string | null | undefined): string | null {
  const s = (severity || '').toLowerCase()
  if (s === 'blocker' || s === 'critical') return 'var(--status-failed)'  // red
  if (s === 'major' || s === 'high')        return 'var(--status-broken)' // amber
  if (s === 'minor' || s === 'low')         return '#9198a1' // slate
  return null
}

const TH = 'px-4 py-2.5 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider whitespace-nowrap'

export default function MyFailuresPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useTabParam(INBOX_TABS, 'assigned')
  const user = useAuthStore(s => s.user)
  const { isQaLead } = usePermissions()
  const project = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // The Approvals queues belong to one project: their count is only asked for
  // (and only shown) while one is active.
  const singleProjectId = !isAllProjects && activeProjectId ? activeProjectId : null
  // Reassignment modal state. ``reassignFor`` is the failure currently
  // being reassigned (null = modal closed). Only QA_LEAD+ ever sees
  // the button that opens this modal; the backend enforces the same
  // contract independently — clients can't pivot it.
  const [reassignFor, setReassignFor] = useState<MyFailureItem | null>(null)
  // Triage-status modal state. Same single-target pattern as
  // ``reassignFor`` — only one modal open at a time.
  const [triageFor, setTriageFor] = useState<MyFailureItem | null>(null)

  // Time window comes from the shared user-level preference (the toolbar's
  // `WindowPicker` writes it), so a selection made on any other page
  // (Summary, Live, Coverage, …) follows the user here. Snapped to this
  // page's allowed set.
  const storedDays = useTimeWindowStore(s => s.days)
  const days = snapToAllowed(storedDays, DAYS_OPTIONS)
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
  //
  // Team is the DEFAULT for anyone allowed to see it: the synthetic QA-Lead
  // owns the bulk of auto-assignments, so "Mine" opened almost empty for a
  // real lead or admin and looked like the page was broken.
  //
  // `null` means "hasn't chosen yet" rather than defaulting the state itself.
  // `isQaLead` reads from the auth store, which reports VIEWER until `user`
  // hydrates — a useState initialiser would latch that early `false` and strand
  // a lead on "Mine" depending on load timing. Deriving the scope on every
  // render instead means the default follows the permission as soon as it
  // arrives, while still respecting an explicit click.
  const [scopeChoice, setScopeChoice] = useState<'mine' | 'team' | null>(null)
  const canSeeTeam = isQaLead
  const effectiveScope: 'mine' | 'team' = canSeeTeam ? (scopeChoice ?? 'team') : 'mine'

  // The page number belongs to one (window, scope): changing either starts
  // again at page 1. Derived rather than reset in an effect.
  const [paging, setPaging] = useState({ days, scope: effectiveScope, page: 1 })
  const page = paging.days === days && paging.scope === effectiveScope ? paging.page : 1
  const setPage = (next: number) => setPaging({ days, scope: effectiveScope, page: next })

  const { data, isLoading, error } = useMyFailures({ days, page, size, scope: effectiveScope })

  const scopeLabel = isAllProjects ? 'all your projects' : (project?.name ?? 'the selected project')

  const subtitle =
    tab === 'approvals'
      ? singleProjectId
        ? `AI reports, quarantine proposals and test-case reviews waiting for a decision in ${project?.name ?? 'the selected project'}.`
        : 'Approvals belong to one project: pick it to see what is waiting for a decision.'
      : user
        ? effectiveScope === 'team'
          // The tab reads "Assigned to me" but opens on the team inbox for
          // leads, so the subtitle has to say whose failures these are.
          ? `Every unresolved failure across ${scopeLabel}. Updated every 30 seconds.`
          : `Failures assigned to you across ${scopeLabel}. Updated every 30 seconds.`
        : 'Sign in to see your assigned failures.'

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Inbox"
        subtitle={subtitle}
        // Approvals open the AI-report review section of the reports topic.
        helpTopic={helpTopicParam(tab === 'approvals' ? '/reviews' : '/my-failures')}
        tabs={
          <Tabs
            ariaLabel="Inbox"
            value={tab}
            onChange={setTab}
            items={[
              { id: 'assigned', label: 'Assigned to me', count: data?.unresolved_total },
              {
                id: 'approvals',
                label: 'Approvals',
                badge: singleProjectId ? (
                  <ApprovalsCountBadge projectId={singleProjectId} active={tab === 'approvals'} />
                ) : undefined,
              },
            ]}
          />
        }
      />

      {tab === 'approvals' ? (
        <ApprovalsTab />
      ) : (
        <>
          {/* Toolbar: one row */}
          <div className="flex items-center gap-3 flex-wrap">
            <WindowPicker options={DAYS_OPTIONS} />
            {canSeeTeam && (
              <>
                <span className="w-px h-4" style={{ background: 'var(--color-border)' }} aria-hidden />
                <ChipFilter
                  label="Scope"
                  value={effectiveScope}
                  onChange={setScopeChoice}
                  optionClassName="capitalize"
                  options={[
                    { id: 'mine', label: 'mine', title: 'Only failures assigned to you' },
                    { id: 'team', label: 'team', title: 'Every unresolved failure across the project (QA_LEAD/ADMIN)' },
                  ]}
                />
              </>
            )}
            {data?.unresolved_total !== undefined && (
              <span className="ml-auto text-[12px] text-[var(--color-text-muted)]">
                {data.unresolved_total === 0
                  ? 'Nothing assigned'
                  : <><strong className="text-[var(--color-text)] tabular-nums">{data.unresolved_total}</strong> assigned</>}
              </span>
            )}
          </div>

          {/* Body: the page's primary content */}
          <section data-primary="" aria-label="Assigned failures" className="space-y-3">
            {isLoading && !data ? (
              <div className="flex items-center justify-center py-16"><LoadingSpinner size="lg" /></div>
            ) : error ? (
              <div className="flex items-center gap-2 text-sm text-[var(--status-broken)] bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/30 rounded px-3 py-3">
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
                <table className="w-full text-sm" aria-label="Assigned failures">
                  <thead className="bg-[var(--color-bg-secondary)]/80">
                    <tr>
                      <th className={clsx(TH, 'w-2')}>{/* severity dot */}</th>
                      <th className={TH}>Test</th>
                      <th className={TH}>Test Suite</th>
                      <th className={TH}>Run</th>
                      <th
                        className={clsx(TH, '!text-right')}
                        title={`Failures of this test in the last ${days === 1 ? '24 hours' : `${days} days`}`}
                      >
                        Failures
                      </th>
                      <th className={TH}>Age</th>
                      <th className={clsx(TH, 'w-4')}>{/* open */}</th>
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
              <Pagination page={page} pages={data.pages} total={data.total} onChange={setPage} />
            )}
          </section>
        </>
      )}

      {triageFor && (
        <TriageStatusModal
          failure={triageFor}
          onClose={() => setTriageFor(null)}
          onUpdated={() => {
            setTriageFor(null)
            // Any non-PENDING_REVIEW status drops the row off the
            // inbox — refetch so the count + page reflect that.
            void appMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures',
            )
            void appMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count',
            )
            void appMutate(
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
            void appMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures',
            )
            void appMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count',
            )
            void appMutate(
              (key: unknown) => Array.isArray(key) && key[0] === 'my-failures-count-unscoped',
            )
          }}
        />
      )}
    </div>
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
      ? 'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]'
      : count >= 2
      ? 'bg-[var(--status-broken-bg)]/30 text-[var(--status-broken)]'
      : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
  // The second line: what failed, where, and why it is the reader's. Each
  // part keeps its own element (and the whole line its title) so a long
  // error cuts off at the cell's edge instead of growing the row.
  const detail = [
    item.error_message,
    item.last_failure_step ? `failed at: ${item.last_failure_step}` : null,
    item.assignment_reason,
  ].filter(Boolean).join(' · ')
  const when = formatRunWhen(item.created_at)
  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer hover:bg-[var(--color-bg-secondary)]/60 transition-colors"
    >
      <td className="px-4 py-2">
        <span
          data-testid="severity-dot"
          data-severity={dot ? (item.severity || '').toLowerCase() : 'unknown'}
          className="inline-block h-2 w-2 rounded-full"
          style={dot
            ? { background: dot }
            : { background: 'transparent', border: '1px solid var(--color-text-faint)' }}
          title={dot ? `Severity: ${item.severity}` : 'Severity unknown'}
          aria-hidden
        />
      </td>
      {/* The Test cell takes the room the other columns leave (`w-full`) and
          never asks for more (`max-w-0`), so its two lines cut off at the
          cell's edge. With a fixed 460 px cap the table was 1,168 px wide in a
          1,134 px card at 1440 (974 at 1280): the card's `overflow-hidden`
          cut off the row actions (P6 fold budget, `fold-budget.spec.ts`). */}
      <td className="px-4 py-2 w-full max-w-0" data-col="test">
        <div className="text-[var(--color-text)] font-medium truncate" title={item.test_name}>
          {item.test_name}
        </div>
        {detail && (
          <div className="text-[11px] text-[var(--color-text-muted)] truncate" title={detail} data-row-detail="">
            {item.error_message && <span className="font-mono">{item.error_message}</span>}
            {item.last_failure_step && (
              <span className="text-[var(--color-text-faint)]">
                {item.error_message ? ' · ' : ''}failed at:{' '}
                <span className="font-mono text-[var(--color-text-muted)]">{item.last_failure_step}</span>
              </span>
            )}
            {item.assignment_reason && (
              <span className="text-[var(--color-text-faint)]">
                {item.error_message || item.last_failure_step ? ' · ' : ''}
                <span className="text-[var(--color-text-muted)]">{item.assignment_reason}</span>
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-2 text-xs text-[var(--color-text-secondary)] align-middle">
        {item.suite_name ? (
          <span className="truncate max-w-[200px] inline-block align-middle" title={item.suite_name}>{item.suite_name}</span>
        ) : (
          <span className="text-[var(--color-text-faint)]">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-[var(--color-text-secondary)] font-mono text-xs whitespace-nowrap">
        {/* Project name is omitted because the user selects it in the global
            project dropdown — surfacing it again per-row is redundant. Run
            identifier is the human-readable, per-(project, suite) incremental
            ``Run #N``; falls back to the raw SDK build_number for pre-run_seq
            rows. Under it, on the same line: when the run was generated (so
            same-numbered "Run #N" rows are distinguishable at a glance, as on
            /live) and the short test_run_id slug as a copy/correlation aid. */}
        {item.run_seq != null ? (
          <span className="text-[var(--color-text)]">Run #{item.run_seq}</span>
        ) : item.build_number ? (
          <span className="text-[var(--color-text)]">{item.build_number}</span>
        ) : (
          <span className="text-[var(--color-text-faint)]">—</span>
        )}
        {(when || item.test_run_id) && (
          <div className="text-[10px] text-[var(--color-text-faint)] tabular-nums">
            {when && <span>{when}</span>}
            {item.test_run_id && <span>{when ? ' · ' : ''}{item.test_run_id.slice(0, 8)}</span>}
          </div>
        )}
      </td>
      <td className="px-4 py-2 text-right">
        <span
          className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium tabular-nums', countTone)}
          title={`Failed ${count} time${count === 1 ? '' : 's'} in the last ${windowLabel}`}
        >
          × {count}
        </span>
      </td>
      <td className="px-4 py-2 text-[var(--color-text-muted)] text-xs whitespace-nowrap">
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {relativeAge(item.created_at)}
        </span>
      </td>
      <td className="px-4 py-2 text-right whitespace-nowrap">
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
    useReassignOptions(failure.id)
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
      await myFailuresService.reassign(failure.id, selectedUserId)
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
          <div className="text-xs text-[var(--status-broken)] bg-[var(--status-broken-bg)]/20 border border-[var(--status-broken-bd)]/30 rounded px-3 py-2">
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
      await myFailuresService.updateTriageStatus(failure.id, {
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
                  ? 'border-[var(--color-accent)] bg-[color-mix(in srgb, var(--color-accent) 8%, transparent)]'
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

        <label htmlFor="myfail-field-0" className="block text-[11px] text-[var(--color-text-muted)] mb-1">
          Notes <span className="text-[var(--color-text-faint)]">(optional — defect link, rationale)</span>
        </label>
        <textarea id="myfail-field-0"
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
          ? 'border-[var(--color-accent)] bg-[color-mix(in srgb, var(--color-accent) 8%, transparent)]'
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
