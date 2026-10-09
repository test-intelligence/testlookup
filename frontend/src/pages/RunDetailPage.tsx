/**
 * The Run page (`/runs/:runId`) — one page for one run (UX redesign P4,
 * `02-design-spec.md` §5 "Run" row). It merges what used to be four pages:
 *
 *   Tests     (default) the run's counts as status chips and the test table,
 *             failed and broken first — the page's primary content.
 *   Analysis  Run Intelligence (verdict banner → What failed → evidence) and
 *             Deep Investigation's clusters with "Analyze failures".
 *             `/runs/:id/intelligence` and `/deep-investigate/:id` redirect here.
 *   Changes   the regression diff since the last good run, and Compare.
 *   Evidence  the verified decision report (claims, verification) and the
 *             agent pipeline's AI report. `/agents/run/:id` redirects here.
 *
 * One PageHeader: build, job · branch · date, the status pill, suite and
 * release chips; every action (trigger, deep investigation, refresh, PDF,
 * evidence bundle, compare, release gate) is in its ⋯ menu. The tab is
 * `?tab=`; a tab that is not open is not rendered and asks for nothing.
 * Deleted here (§5): the duplicated header buttons and counts (now the
 * chips), the "Run Intelligence" button (now a tab), the breadcrumb (the
 * Runs section tabs lead back to the list).
 */
import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Check, FileDown, GitCompare, MessageSquare, Package, PencilLine, RefreshCw, ShieldCheck, Stethoscope, X, Zap } from 'lucide-react'
import toast from 'react-hot-toast'
import { appMutate } from '@/utils/swrCacheMutate'
import PageHeader from '@/components/ui/PageHeader'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import type { OverflowItem } from '@/components/ui/OverflowMenu'
import StatusBadge from '@/components/ui/StatusBadge'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useRun, useRuns } from '@/hooks/useRuns'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import { usePermissions } from '@/hooks/usePermissions'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { isLLMAvailable, useAIConfig } from '@/hooks/useAIConfig'
import { askAboutRun } from '@/components/chat/chatContent'
import { runsService } from '@/services/runsService'
import agentService from '@/services/agentService'
import type { TestRun } from '@/types/runs'
import { buildCompareWithPreviousHref, findPreviousRunOfSuite } from '@/utils/runComparisons'
import { formatDateTime } from '@/utils/formatters'
import { RunDeepClusters } from './DeepInvestigationPage'
import { RunIntelligenceBody } from './RunIntelligencePage'
import RunTestsTab from './run/RunTestsTab'
import RunChangesTab from './run/RunChangesTab'
import RunEvidenceTab from './run/RunEvidenceTab'
import { downloadRunEvidenceBundle, downloadRunPdf, readReportVersion } from './run/runActions'

const HELP_TOPIC = helpTopicParam('/runs')

const TAB_IDS = ['tests', 'analysis', 'changes', 'evidence'] as const
type RunTab = (typeof TAB_IDS)[number]

function ReleaseTag({ releaseName, onSet }: {
  releaseName?: string
  onSet: (name: string) => Promise<void>
}) {
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    const name = value.trim()
    if (!name) return
    setSaving(true)
    try {
      await onSet(name)
      setEditing(false)
      setValue('')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
        <input
          autoFocus
          type="text"
          placeholder="Release name (e.g. v2.5.0)"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') setEditing(false) }}
          className="bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-2 py-0.5 text-xs text-[var(--color-text)] placeholder-[var(--color-text-faint)] w-44 focus:outline-none focus:border-[var(--color-border)]"
        />
        <button onClick={handleSave} disabled={saving} aria-label="Save release" className="text-[var(--status-passed)] hover:text-[var(--status-passed)] disabled:opacity-50">
          <Check className="h-4 w-4" />
        </button>
        <button onClick={() => setEditing(false)} aria-label="Cancel" className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]">
          <X className="h-4 w-4" />
        </button>
      </div>
    )
  }

  if (releaseName) {
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={() => navigate('/releases')}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-[var(--status-flaky-bg)]/40 text-[var(--status-flaky)] hover:bg-[var(--status-flaky-bg)]/50 transition-colors"
        >
          <Package className="h-3 w-3" />
          {releaseName}
        </button>
        <button onClick={() => setEditing(true)} title="Change release" aria-label="Change release" className="text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]">
          <PencilLine className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setEditing(true)}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-dashed border-[var(--color-border-light)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:border-[var(--color-border)] transition-colors"
    >
      <Package className="h-3 w-3" />
      Set release
    </button>
  )
}

export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [tab, setTab] = useTabParam<RunTab>(TAB_IDS, 'tests')

  // P4-5: the Tests filters persist in the URL so they survive navigation.
  const [page, setPage] = useState(() => Number(searchParams.get('page')) || 1)
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get('status') || '')
  const [suiteFilter, setSuiteFilter] = useState(() => searchParams.get('suite') || '')

  // Sync the filters back to the URL (replace, no history spam). Functional
  // and key-by-key: every other key (`tab`, `report_version`, a deep link's
  // report ids) stays — writing the three filters as the whole query used
  // to drop `?tab=` on arrival.
  // NOTE: setSearchParams is intentionally excluded from deps — including it
  // causes an infinite loop because react-router returns a new reference each render.
  useEffect(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const put = (key: string, value: string) => (value ? next.set(key, value) : next.delete(key))
      put('status', statusFilter)
      put('suite', suiteFilter)
      put('page', page > 1 ? String(page) : '')
      return next
    }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, suiteFilter, page])

  // ...and the other way: a link into this page (the Analysis tab's "Open the
  // failed tests") changes the URL without remounting it, and the filters,
  // read from the URL only on mount, kept showing every test.
  // Render-phase sync: adopt the URL's filters when the URL itself changes.
  const urlStatus = searchParams.get('status') || ''
  const urlSuite = searchParams.get('suite') || ''
  const urlPage = Number(searchParams.get('page')) || 1
  const urlFilters = `${urlStatus}|${urlSuite}|${urlPage}`
  const [seenUrlFilters, setSeenUrlFilters] = useState(urlFilters)
  if (urlFilters !== seenUrlFilters) {
    setSeenUrlFilters(urlFilters)
    setStatusFilter(urlStatus)
    setSuiteFilter(urlSuite)
    setPage(urlPage)
  }

  useProjectChangeRedirect('/runs', Boolean(runId))

  const { data: run } = useRun(runId)

  // A small page of recent runs of THIS run's suite, so "Compare to previous
  // run" can pick the chronologically preceding one. Idle for a run without
  // suite attribution; 50 is plenty (the previous run is almost always one
  // or two slots away).
  const suiteForCompare = run?.primary_suite_name ?? null
  const { data: suiteRunsData } = useRuns(
    suiteForCompare
      ? { page: 1, size: 50, days: 0, suite_name: suiteForCompare }
      : undefined,
  )
  const suiteRuns = (suiteRunsData?.items ?? []) as TestRun[]

  function handleCompareWithPrevious() {
    if (!run) return
    if (!run.primary_suite_name) {
      toast('This run has no suite attribution — cannot pick a previous-of-same-suite.', { icon: '⚠️' })
      return
    }
    const previous = findPreviousRunOfSuite(run, suiteRuns)
    const href = buildCompareWithPreviousHref(run, previous)
    if (!href) {
      toast(
        `No earlier run of "${run.primary_suite_name}" found — this may be the first ingested run for the suite.`,
        { icon: '⚠️' },
      )
      return
    }
    navigate(href)
  }

  const { isQaEngineer } = usePermissions()
  // The same gate as the sidebar's Ask AI entry (both reads are the shell's,
  // so this costs no request).
  const chatFlagEnabled = useFeatureEnabled('ask_ai_chat')
  const { data: aiConfig } = useAIConfig()
  const [triggeringPipeline, setTriggeringPipeline] = useState(false)
  const [triggeringDeep, setTriggeringDeep] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  async function handleSetRelease(name: string) {
    if (!runId) return
    await runsService.setRelease(runId, name)
    appMutate(['run', runId])
  }

  async function handleTriggerPipeline() {
    if (!runId) return
    setTriggeringPipeline(true)
    try {
      await agentService.triggerPipeline(runId)
      toast.success('Pipeline queued — its report appears under Evidence.')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger pipeline'
      toast.error(detail)
    } finally {
      setTriggeringPipeline(false)
    }
  }

  async function handleTriggerDeep() {
    if (!runId) return
    setTriggeringDeep(true)
    try {
      await agentService.triggerDeepPipeline(runId)
      toast.success('Deep investigation queued — opening Analysis…')
      // The investigation's clusters live on this page's Analysis tab now
      // (it used to open `/deep-investigate/:id`, which redirects there).
      setTab('analysis')
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to trigger deep investigation'
      toast.error(detail)
    } finally {
      setTriggeringDeep(false)
    }
  }

  async function handleRefreshAnalysis() {
    if (!runId) return
    setRefreshing(true)
    try {
      const { runIntelligenceService } = await import('@/services/runIntelligenceService')
      await runIntelligenceService.refreshIntelligence(runId)
      // Every version's cached analysis of this run (`useRunIntelligence` keys).
      await appMutate((key) => typeof key === 'string' && key.startsWith(`run-intelligence-${runId}-`))
      toast.success('AI analysis refreshed')
    } catch {
      toast.error('Failed to refresh the AI analysis')
    } finally {
      setRefreshing(false)
    }
  }

  const busy = triggeringPipeline || triggeringDeep
  const chatAvailable = chatFlagEnabled && !!aiConfig && isLLMAvailable(aiConfig)
  const overflow: OverflowItem[] = [
    ...(isQaEngineer
      ? [
          {
            label: triggeringPipeline ? 'Queuing pipeline…' : 'Trigger pipeline',
            icon: <Zap className="h-3.5 w-3.5" />,
            onClick: () => void handleTriggerPipeline(),
            disabled: busy,
          },
          {
            label: triggeringDeep ? 'Queuing investigation…' : 'Deep investigation',
            icon: <Stethoscope className="h-3.5 w-3.5" />,
            onClick: () => void handleTriggerDeep(),
            disabled: busy,
          },
        ]
      : []),
    {
      label: refreshing ? 'Refreshing AI analysis…' : 'Refresh AI analysis',
      icon: <RefreshCw className="h-3.5 w-3.5" />,
      onClick: () => void handleRefreshAnalysis(),
      disabled: refreshing || !runId,
    },
    {
      label: 'Export PDF',
      icon: <FileDown className="h-3.5 w-3.5" />,
      onClick: () => { if (runId) void downloadRunPdf(runId) },
      disabled: !runId,
    },
    {
      label: 'Download evidence bundle',
      icon: <Package className="h-3.5 w-3.5" />,
      onClick: () => { if (runId) void downloadRunEvidenceBundle(runId) },
      disabled: !runId,
    },
    {
      label: 'Compare to previous run',
      icon: <GitCompare className="h-3.5 w-3.5" />,
      onClick: handleCompareWithPrevious,
      disabled: !run?.primary_suite_name,
    },
    ...(runId
      ? [{ label: 'Release gate', icon: <ShieldCheck className="h-3.5 w-3.5" />, href: `/release-gate/${runId}` }]
      : []),
    // Ask AI, with the question about this run already written (the reader
    // sends it): what failed, why, and whether it is new.
    ...(run && chatAvailable
      ? [{
          label: 'Ask AI about this run',
          icon: <MessageSquare className="h-3.5 w-3.5" />,
          href: `/chat?prompt=${encodeURIComponent(askAboutRun(run.build_number))}`,
        }]
      : []),
  ]

  const tabs: TabItem<RunTab>[] = [
    { id: 'tests', label: 'Tests', count: run?.total_tests },
    { id: 'analysis', label: 'Analysis' },
    { id: 'changes', label: 'Changes' },
    { id: 'evidence', label: 'Evidence' },
  ]

  const subtitle = run
    ? [run.jenkins_job ?? 'Jenkins', run.branch, formatDateTime(run.created_at)].filter(Boolean).join(' · ')
    : undefined

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title={run ? `Run #${run.build_number}` : 'Run'}
        subtitle={subtitle}
        helpTopic={HELP_TOPIC}
        actions={
          run && (
            <div className="flex items-center gap-2 flex-wrap text-sm">
              <StatusBadge status={run.status} />
              <SuiteBadge primary={run.primary_suite_name} all={run.suite_names} />
              <ReleaseTag releaseName={run.release_name} onSet={handleSetRelease} />
            </div>
          )
        }
        overflow={overflow}
        tabs={<Tabs items={tabs} value={tab} onChange={setTab} ariaLabel="Run sections" />}
      />

      {runId && tab === 'tests' && (
        <RunTestsTab
          runId={runId}
          run={run}
          statusFilter={statusFilter}
          suiteFilter={suiteFilter}
          page={page}
          onStatusFilter={(status) => { setStatusFilter(status); setPage(1) }}
          onSuiteFilter={(suite) => { setSuiteFilter(suite); setPage(1) }}
          onPage={setPage}
        />
      )}
      {/* Each hosted body behind its own boundary: one section's bad answer
          (a clusters payload that is not a list) must not take the header,
          the tabs and the other sections down with it. */}
      {runId && tab === 'analysis' && (
        <SectionErrorBoundary message="The run's AI analysis failed to load">
          <RunIntelligenceBody
            runId={runId}
            reportVersion={readReportVersion(searchParams)}
            afterPrimary={
              <SectionErrorBoundary message="The deep investigation failed to load">
                <RunDeepClusters runId={runId} run={run ?? null} />
              </SectionErrorBoundary>
            }
          />
        </SectionErrorBoundary>
      )}
      {runId && tab === 'changes' && (
        <SectionErrorBoundary message="The run's changes failed to load">
          <RunChangesTab runId={runId} run={run} onCompareWithPrevious={handleCompareWithPrevious} />
        </SectionErrorBoundary>
      )}
      {runId && tab === 'evidence' && (
        <SectionErrorBoundary message="The run's evidence failed to load">
          <RunEvidenceTab runId={runId} />
        </SectionErrorBoundary>
      )}
    </div>
  )
}
