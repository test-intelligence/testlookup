import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink, Link2Off } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PromotionAction from '@/components/testManagement/PromotionAction'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import TestHistoryTimeline from '@/components/suites/TestHistoryTimeline'
import { refreshSuites, useCanonicalCase, useCanonicalRuns, useSuite } from '@/hooks/useSuites'
import { usePermissions } from '@/hooks/usePermissions'
import { suitesService } from '@/services/suitesService'
import { formatDateTime, formatDuration, fromNow } from '@/utils/formatters'

// Status pill color map — mirrors the one in SuiteCasesPage so the two views
// render the same canonical lifecycle states identically. Keep in sync if
// the canonical state machine grows (deleted / needs_review etc.).
const STATUS_COLOR: Record<string, string> = {
  active: 'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)] ring-[var(--status-passed)]/30',
  deleted: 'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)] ring-[var(--status-failed)]/30',
  needs_review: 'bg-[var(--status-broken-bg)]/10 text-[var(--status-broken)] ring-[var(--status-broken)]/30',
}

function StatusPill({ status }: { status: string }) {
  const cls =
    STATUS_COLOR[status] ||
    'bg-[var(--color-bg-hover)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/30'
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ring-1 ring-inset',
        cls,
      )}
    >
      {status}
    </span>
  )
}

// Per-run TestCase status uses the global formatter palette. We wrap it in
// a pill so the column stays scannable at a glance even with many rows.
const RUN_STATUS_COLOR: Record<string, string> = {
  PASSED: 'bg-[var(--status-passed-bg)]/10 text-[var(--status-passed)] ring-[var(--status-passed)]/30',
  FAILED: 'bg-[var(--status-failed-bg)]/10 text-[var(--status-failed)] ring-[var(--status-failed)]/30',
  BROKEN: 'bg-[var(--status-broken-bg)]/10 text-[var(--status-broken)] ring-[var(--status-broken)]/30',
  SKIPPED: 'bg-[var(--status-skipped-bg)]/10 text-[var(--status-skipped)] ring-[var(--status-skipped)]/30',
}

function RunStatusPill({ status }: { status: string }) {
  const cls =
    RUN_STATUS_COLOR[status?.toUpperCase()] ||
    'bg-[var(--color-bg-hover)]/10 text-[var(--color-text-muted)] ring-[var(--color-border)]/30'
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ring-1 ring-inset',
        cls,
      )}
    >
      {status || 'unknown'}
    </span>
  )
}

export default function CanonicalDetailPage() {
  const { canonicalId } = useParams<{ canonicalId: string }>()
  const navigate = useNavigate()
  const { isQaEngineer } = usePermissions()
  const [showUnlink, setShowUnlink] = useState(false)
  const [unlinking, setUnlinking] = useState(false)
  const [unlinkedLocally, setUnlinkedLocally] = useState(false)
  const [unlinkRefreshWarning, setUnlinkRefreshWarning] = useState<string | null>(null)

  const {
    data: canonical,
    isLoading: canonicalLoading,
    error: canonicalError,
    mutate: mutateCanonical,
  } = useCanonicalCase(canonicalId)
  // Suite lookup is best-effort — used only to show the suite name in the
  // breadcrumb / header. We render even without it so a fresh canonical
  // (or one whose suite was just deleted) still gets a useful detail view.
  const { data: suite } = useSuite(canonical?.test_suite_id)
  const { data: runHistory, isLoading: runsLoading } = useCanonicalRuns(
    canonicalId,
  )

  if (canonicalLoading) return <LoadingSpinner size="lg" />
  if (canonicalError || !canonical) {
    return (
      <EmptyState
        title="Test case not found"
        description="It may have been deleted or you may not have access to its project."
      />
    )
  }

  const runs = runHistory?.items ?? []
  const hasManagedLink = !!canonical.managed_test_case_id && !unlinkedLocally
  // The "Open latest run" CTA jumps to the per-run test page when we have
  // both ids, falling back to the run detail page when we only know the run.
  // Mirrors the deep-link shape SuiteCasesPage uses on its rows.
  const latestRunHref =
    canonical.last_seen_run_id && canonical.last_seen_test_case_id
      ? `/runs/${canonical.last_seen_run_id}/tests/${canonical.last_seen_test_case_id}`
      : canonical.last_seen_run_id
        ? `/runs/${canonical.last_seen_run_id}`
        : null

  async function unlinkManagedCase(reason: string) {
    if (!canonicalId) return
    setUnlinking(true)
    setUnlinkRefreshWarning(null)
    let updated: typeof canonical
    try {
      updated = await suitesService.unlinkManagedCase(canonicalId, reason)
    } catch {
      toast.error('Could not remove the managed case link')
      setUnlinking(false)
      return
    }

    setUnlinkedLocally(true)
    setShowUnlink(false)
    setUnlinking(false)
    toast.success('Managed case link removed')
    try {
      await Promise.all([mutateCanonical(updated, { revalidate: false }), refreshSuites()])
    } catch {
      setUnlinkRefreshWarning('The managed link was removed, but related catalogs could not be refreshed. The unlink control remains disabled locally.')
    }
  }

  return (
    <>
      {/* Breadcrumb back to whichever suite this case lives under. When the
          suite hasn't loaded yet (or has been deleted), fall back to the
          generic suites list so the user always has a path out. */}
      <button
        type="button"
        onClick={() =>
          navigate(canonical.test_suite_id ? `/suites/${canonical.test_suite_id}` : '/suites')
        }
        className="mb-3 inline-flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-4 w-4" />
        {suite ? suite.name : 'Back to suites'}
      </button>

      <PageHeader
        title={canonical.test_name}
        subtitle={canonical.class_name ?? 'No class name'}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {isQaEngineer && !unlinkedLocally && (hasManagedLink ? (
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-2 text-sm"
                onClick={() => setShowUnlink(true)}
              >
                <Link2Off className="h-4 w-4" /> Unlink managed case
              </button>
            ) : (
              <PromotionAction
                canonicalId={canonical.id}
                onPromoted={async (result) => {
                  await mutateCanonical(result.canonical, { revalidate: false })
                }}
              />
            ))}
            {latestRunHref && (
              <Link
                to={latestRunHref}
                className="inline-flex items-center gap-1 rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white"
              >
                <ExternalLink className="h-4 w-4" /> Open latest run
              </Link>
            )}
          </div>
        }
      />

      {unlinkRefreshWarning && (
        <p role="status" className="mb-4 rounded-md border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-2 text-sm text-[var(--status-broken)]">
          {unlinkRefreshWarning}
        </p>
      )}

      {/* Identity card — the structured facts about the canonical that
          don't change run-to-run. Renders before the runs table so an
          operator scanning the page sees "what is this test" before
          "how has it behaved over time". */}
      <section className="mb-6 grid grid-cols-1 gap-4 rounded-lg p-4 ring-1 ring-[var(--color-border)] md:grid-cols-4">
        <div>
          <div className="text-xs uppercase text-[var(--color-text-muted)]">Status</div>
          <div className="mt-1">
            <StatusPill status={canonical.status} />
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-[var(--color-text-muted)]">Source</div>
          <div className="mt-1 text-sm text-[var(--color-text)]">{canonical.source}</div>
        </div>
        <div>
          <div className="text-xs uppercase text-[var(--color-text-muted)]">Suite</div>
          <div className="mt-1 text-sm text-[var(--color-text)]">
            {canonical.test_suite_name ?? suite?.name ?? '—'}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-[var(--color-text-muted)]">Fingerprint</div>
          <div className="mt-1 truncate font-mono text-xs text-[var(--color-text-muted)]" title={canonical.test_fingerprint}>
            {canonical.test_fingerprint.slice(0, 12)}…
          </div>
        </div>
      </section>

      {/* History timeline — the hero view (roadmap Phase 1). Answers "has
          this test been unstable, on which environments, and how long has it
          taken?" at a glance; the exhaustive table below stays for the
          per-run detail that a strip cannot carry. */}
      {!runsLoading && runs.length > 0 ? <TestHistoryTimeline items={runs} /> : null}

      {/* Run history — newest first. The backend already orders by
          ``TestRun.created_at DESC`` (see
          ``test_suite_service.list_runs_for_canonical``) so we render
          the response order verbatim. */}
      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase text-[var(--color-text-muted)]">
          Run history ({runHistory?.total ?? 0})
        </h2>
        {runsLoading ? (
          <LoadingSpinner />
        ) : runs.length === 0 ? (
          <EmptyState
            title="No runs yet"
            description="This canonical hasn't been observed in an executed run yet."
          />
        ) : (
          <div className="overflow-hidden rounded-lg ring-1 ring-[var(--color-border)]">
            <table className="w-full divide-y divide-[var(--color-border)] text-sm">
              <thead className="bg-[var(--color-bg-secondary)] text-left text-xs uppercase text-[var(--color-text-muted)]">
                <tr>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Run</th>
                  <th className="px-4 py-2 font-medium">Duration</th>
                  <th className="px-4 py-2 font-medium">Suite</th>
                  <th className="px-4 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {runs.map((r) => {
                  // Per-row deep link to the run's test-detail page. We
                  // navigate the whole row so a missed click on the link
                  // still works — mirrors SuiteCasesPage's row behaviour.
                  const href = `/runs/${r.test_run_id}/tests/${r.test_case_id}`
                  return (
                    <tr
                      key={r.test_case_id}
                      onClick={() => navigate(href)}
                      className="cursor-pointer hover:bg-[var(--color-bg-secondary)]"
                    >
                      <td className="px-4 py-2">
                        <RunStatusPill status={r.status} />
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-[var(--color-text)]">
                        <Link
                          to={href}
                          onClick={(e) => e.stopPropagation()}
                          className="hover:text-[var(--color-accent)] hover:underline"
                        >
                          {r.test_run_id.slice(0, 8)}
                        </Link>
                      </td>
                      <td className="px-4 py-2 text-[var(--color-text-muted)]">
                        {formatDuration(r.duration_ms)}
                      </td>
                      <td className="px-4 py-2 text-[var(--color-text-muted)]">
                        {r.suite_name ?? '—'}
                      </td>
                      <td
                        className="px-4 py-2 text-[var(--color-text-muted)]"
                        title={formatDateTime(r.created_at)}
                      >
                        {fromNow(r.created_at)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {showUnlink && (
        <TransitionReasonDialog
          title="Unlink managed case"
          description="Explain why this canonical automation identity was linked to the wrong managed case. Both records will be preserved."
          confirmLabel="Unlink"
          busy={unlinking}
          onCancel={() => setShowUnlink(false)}
          onConfirm={unlinkManagedCase}
        />
      )}
    </>
  )
}
