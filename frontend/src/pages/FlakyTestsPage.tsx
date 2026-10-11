/**
 * Flaky tests (`/flaky`, UX redesign P4 item 4): one page for the flaky-test
 * job that was split over Flaky Coach (`/flaky-coach`) and Quarantine
 * (`/quarantine`), both of which now redirect here.
 *
 * Layout (`02-design-spec.md` §5 "Flaky tests"):
 *   Header  → `PageHeader` (compact, **?** = the `flaky` help topic, which
 *             owns the definition of a flaky test; Refresh re-runs the
 *             analysis) with the tabs, counts in their labels:
 *   Tabs    → Detected (default: the flaky-test list, `FlakyCoachBody`, with
 *             a recommendation column and the row action "Propose
 *             quarantine") · Proposed · Quarantined · History (the quarantine
 *             requests in each state, `QuarantineBody`).
 * A detected test with a live request shows that state instead of the action,
 * linking to its tab. Flaky Coach's four recommendation tiles became the
 * Detected tab's filter chips; Quarantine's four stat tiles duplicated the tab
 * counts and are gone; its `window.prompt` notes are an inline form.
 *
 * The page needs one project, as Flaky Coach did: in All Projects mode it says
 * so here (the route-scope registry declares `/flaky` single-project, and its
 * ratchet reads this page for the prompt).
 */
import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { HeartPulse, RefreshCw, ShieldAlert } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import Tabs from '@/components/ui/Tabs'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import DataUnavailable from '@/components/ui/DataUnavailable'
import AllReleasesBadge from '@/components/ui/AllReleasesBadge'
import ExperimentalBadge from '@/components/ui/ExperimentalBadge'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { useTabParam } from '@/components/ui/useTabParam'
import { helpTopicParam } from '@/components/help/helpTopics'
import ProposeQuarantineModal from '@/components/quarantine/ProposeQuarantineModal'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureFlagStatus } from '@/hooks/useFeatureFlags'
import { useQuarantineList, useQuarantineStats } from '@/hooks/useFlakyQuarantine'
import { testHealthService, type FlakyCoachEntry, type FlakyCoachResponse } from '@/services/testHealthService'
import type { FlakyQuarantineRead } from '@/services/flakyQuarantineService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { FlakyCoachBody } from './FlakyCoachPage'
import { QuarantineBody } from './QuarantinePage'
import {
  QUARANTINE_VIEW_STATUSES,
  flakySubtitle,
  isLiveQuarantine,
  quarantineCounts,
  type QuarantineView,
} from './flaky/flakyModel'

const FLAKY_TABS = ['detected', 'proposed', 'quarantined', 'history'] as const
type FlakyTab = (typeof FLAKY_TABS)[number]

const VIEW_OF_TAB: Record<Exclude<FlakyTab, 'detected'>, QuarantineView> = {
  proposed: 'proposals',
  quarantined: 'active',
  history: 'history',
}

/** The analysis window and list size Flaky Coach always used (`useFlakyCoach`'s defaults, and its SWR key). */
const COACH_DAYS = 30
const COACH_LIMIT = 50

export default function FlakyTestsPage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  if (activeProjectId === ALL_PROJECTS_ID || !activeProjectId) {
    return (
      <div className="space-y-4">
        <PageHeader
          compact
          title="Flaky tests"
          subtitle="Select a specific project to see its flaky tests"
          helpTopic={helpTopicParam('/flaky')}
        />
        <ProjectRequiredEmptyState
          icon={<HeartPulse className="h-10 w-10" />}
          description="Flaky tests are analysed one project's history at a time."
        />
      </div>
    )
  }
  return <FlakyTests projectId={activeProjectId} />
}

function FlakyTests({ projectId }: { projectId: string }) {
  const [tab, setTab] = useTabParam(FLAKY_TABS, 'detected')
  const { canAccessManagement: canPropose, isQaEngineer: canRefresh } = usePermissions()
  // Every quarantine endpoint answers 503 while this flag is off, so a
  // proposal is offered only when it can be made. It was offered on every row
  // and the dialog just stayed open on "Flaky auto-quarantine is disabled"
  // (browser E2E pass, 2026-10-08). Unknown (in flight) offers nothing yet.
  const quarantineOn = useFeatureFlagStatus('flaky_auto_quarantine')

  // The same key as `useFlakyCoach` (one cache entry), read here with its
  // `error`, so a failed analysis renders as unavailable, not as "no flaky tests".
  const {
    data: coach,
    error: coachError,
    isLoading,
    mutate: refreshCoach,
  } = useSWR<FlakyCoachResponse>(
    `flaky-coach-${projectId}-${COACH_DAYS}`,
    () => testHealthService.getFlakyCoach(projectId, COACH_DAYS, COACH_LIMIT),
    { revalidateOnFocus: false },
  )
  const { stats, refresh: refreshStats } = useQuarantineStats(projectId)
  // Live requests (the Proposed and Quarantined tabs' list, one cache entry):
  // a detected test that already has one shows its state, not the action.
  const { requests: live, refresh: refreshLive } = useQuarantineList({ projectId, liveOnly: true })
  const liveByFingerprint = useMemo(() => {
    const map = new Map<string, FlakyQuarantineRead>()
    for (const row of live) if (isLiveQuarantine(row.status)) map.set(row.test_fingerprint, row)
    return map
  }, [live])

  const [refreshing, setRefreshing] = useState(false)
  const [proposeFor, setProposeFor] = useState<FlakyCoachEntry | null>(null)

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const result = await testHealthService.refreshFlakyCoach(projectId, COACH_DAYS)
      const found = result.flaky_tests_found
      toast.success(`Found ${found} flaky test${found === 1 ? '' : 's'}`)
      await refreshCoach()
    } catch {
      // The API client already toasted the server's reason; the old text here
      // blamed the role for every failure, a 500 included.
    } finally {
      setRefreshing(false)
    }
  }

  const counts = quarantineCounts(stats)
  const tabs = [
    { id: 'detected' as const, label: 'Detected', count: coach?.entries.length },
    { id: 'proposed' as const, label: 'Proposed', count: counts.proposals },
    { id: 'quarantined' as const, label: 'Quarantined', count: counts.active },
    { id: 'history' as const, label: 'History', count: counts.history },
  ]

  function rowAction(entry: FlakyCoachEntry) {
    const request = liveByFingerprint.get(entry.test_fingerprint)
    if (request) {
      const proposed = QUARANTINE_VIEW_STATUSES.proposals.includes(request.status)
      return (
        <button
          type="button"
          onClick={() => setTab(proposed ? 'proposed' : 'quarantined')}
          className="text-xs text-[var(--color-accent)] hover:underline"
          title={`A quarantine request for this test is ${request.status.replace(/_/g, ' ').toLowerCase()}`}
        >
          {proposed ? 'Proposed' : 'Quarantined'} ›
        </button>
      )
    }
    if (!canPropose || quarantineOn !== true) return <span className="text-xs text-[var(--color-text-faint)]">—</span>
    return (
      <button
        type="button"
        onClick={() => setProposeFor(entry)}
        className="btn-ghost inline-flex items-center gap-1 text-xs"
      >
        <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
        Propose quarantine
      </button>
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="Flaky tests"
        subtitle={flakySubtitle(coach)}
        // The quarantine tabs open the topic on its quarantine section.
        helpTopic={helpTopicParam(tab === 'detected' ? '/flaky' : '/quarantine')}
        actions={
          <>
            {/* Neither half is one release's: the analysis is the project's
                last 30 days, and a quarantine request is a standing decision
                about a test (the endpoints take no release). */}
            <AllReleasesBadge reason="Flaky tests are analysed over the project's last 30 days, and a quarantine request is a standing decision about a test, not about one release's runs." />
            {tab !== 'detected' && <ExperimentalBadge />}
            {canRefresh && (
              <button
                type="button"
                onClick={() => void handleRefresh()}
                disabled={refreshing}
                className="btn-secondary text-sm flex items-center gap-2"
                title="Re-run the flaky-test analysis over the last 30 days"
              >
                <RefreshCw className={clsx('h-4 w-4', refreshing && 'animate-spin')} />
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
            )}
          </>
        }
        tabs={<Tabs ariaLabel="Flaky tests" items={tabs} value={tab} onChange={setTab} />}
      />

      {tab === 'detected' ? (
        coachError ? (
          <DataUnavailable error={coachError} onRetry={() => void refreshCoach()} />
        ) : isLoading && !coach ? (
          <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
        ) : (
          <>
            {quarantineOn === false && canPropose && (
              <p data-quarantine-off="" className="text-[12px] text-[var(--color-text-muted)]">
                Quarantine is off for this project, so no test can be proposed for it. An admin turns it on with
                the <code className="font-mono">flaky_auto_quarantine</code> flag in Settings › Feature flags.
              </p>
            )}
            <FlakyCoachBody entries={coach?.entries ?? []} renderAction={rowAction} actionLabel="Quarantine" />
          </>
        )
      ) : (
        <QuarantineBody view={VIEW_OF_TAB[tab]} projectId={projectId} primary />
      )}

      {proposeFor && (
        <ProposeQuarantineModal
          prefill={{
            project_id: projectId,
            test_fingerprint: proposeFor.test_fingerprint,
            test_name: proposeFor.test_name,
            suite_name: proposeFor.suite_name,
            fail_count: proposeFor.failed_runs,
          }}
          source="flaky-tests"
          onClose={() => {
            setProposeFor(null)
            // Closed after a proposal too: the row's state and the Proposed
            // count come from these two keys.
            void refreshLive()
            void refreshStats()
          }}
        />
      )}
    </div>
  )
}
