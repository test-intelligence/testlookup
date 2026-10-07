/**
 * The test case, without its page shell (UX redesign P4, `02-design-spec.md`
 * §5 "Test case"): the body of `/runs/:runId/tests/:testId`, and what the Run
 * page opens in a `SidePanel` (`compact`).
 *
 * Top to bottom:
 *   Chips   → suite, class, status, duration, tags (the old metadata grid's
 *             severity / feature / owner / run date moved to Details).
 *   Answer  → the error and its stack trace beside the AI root cause, one
 *             block: the page's primary content (`data-primary`), in the first
 *             viewport at 1440 x 900 (`fold-test-case.spec.ts`).
 *   Tabs    → History (the cross-run timeline, flakiness, and the step-flip
 *             report) · Steps (this run's step tree) · Details (the ids, the
 *             fingerprint, the parser, this result's own fields). Only the
 *             open tab is mounted, so Steps asks for the step tree when opened.
 *
 * `compact` (a narrow container, e.g. the Run page's side panel): one column,
 * the full name above the chips (the panel's title carries only the short
 * name), the tab kept in local state (the host page owns `?tab=`), and no
 * `data-primary` (the host page has its own primary content).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import { Chip } from '@/components/ui/Chip'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import StatusBadge from '@/components/ui/StatusBadge'
import Tabs, { type TabItem } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import LogViewer from '@/components/ai/LogViewer'
import AIAnalysisPanel from '@/components/ai/AIAnalysisPanel'
import TestStepsPanel from '@/components/runs/TestStepsPanel'
import TestHistoryPanel from '@/components/runs/TestHistoryPanel'
import StepFlipPanel from '@/components/runs/StepFlipPanel'
import TestCaseDetailSummary from '@/components/runs/TestCaseDetailSummary'
import { useTestCase } from '@/hooks/useRuns'
import { useProjectStore } from '@/store/projectStore'
import type { TestCaseDetailResponse } from '@/types/test-case-detail'
import { formatDateTime, formatDuration } from '@/utils/formatters'
import { normalizeTestCaseDetail } from '@/utils/testCaseDetail'

type TabId = 'history' | 'steps' | 'details'
const TAB_IDS: readonly TabId[] = ['history', 'steps', 'details']

export interface TestCaseBodyProps {
  runId: string
  testId: string
  /** A narrow container (the Run page's side panel): one column, the tab in local state. */
  compact?: boolean
}

export default function TestCaseBody({ runId, testId, compact = false }: TestCaseBodyProps) {
  const { data: tc, isLoading } = useTestCase(runId, testId)
  const project = useProjectStore(s => s.activeProject)
  // Both are always called (hooks); `compact` picks one. On the full page the
  // tab is `?tab=`; in a side panel the host page's `?tab=` is not ours.
  const [urlTab, setUrlTab] = useTabParam<TabId>(TAB_IDS, 'history')
  const [localTab, setLocalTab] = useState<TabId>('history')
  const tab = compact ? localTab : urlTab
  const setTab = compact ? setLocalTab : setUrlTab

  if (isLoading) return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  if (!tc) return <div className="text-[var(--color-text-muted)] text-center py-20">Test case not found</div>

  const detail = normalizeTestCaseDetail(tc)
  const execution = detail.execution
  const isFailed = ['FAILED', 'BROKEN'].includes(String(tc.status).toUpperCase())
  const errorMessage = execution?.error?.message ?? execution?.error_message ?? tc.error_message ?? null
  const stackTrace = execution?.error?.trace ?? execution?.stack_trace ?? null
  const tags = tc.tags?.length ? tc.tags : (detail.classification?.tags ?? [])
  const tabs: TabItem<TabId>[] = [
    { id: 'history', label: 'History' },
    // The reporter's own step count, when it sent one; no count is not 0.
    { id: 'steps', label: 'Steps', count: execution?.step_count ?? undefined },
    { id: 'details', label: 'Details' },
  ]

  const answerProps = compact ? {} : { 'data-primary': '' }

  return (
    <div className="space-y-4" data-test-case-body="" data-compact={compact ? 'true' : 'false'}>
      {compact && (tc.full_name ?? tc.class_name) && (
        <p className="m-0 break-all font-mono text-[12px] text-[var(--color-text-muted)]">{tc.full_name ?? tc.class_name}</p>
      )}

      <div className="flex flex-wrap items-center gap-2" data-test-case-chips="">
        {tc.suite_name && <Chip label="Suite" value={tc.suite_name} />}
        {tc.class_name && <Chip label="Class" value={tc.class_name} />}
        <StatusBadge status={tc.status} />
        {tc.duration_ms != null && <Chip label="Duration" value={formatDuration(tc.duration_ms)} />}
        {tags.map(tag => (
          <span key={tag} className="badge bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]">
            {tag}
          </span>
        ))}
      </div>

      {/* The answer: what failed, where, and the AI's root cause, together. */}
      <section
        {...answerProps}
        aria-label="Failure and root cause"
        className={clsx('grid grid-cols-1 gap-4 items-start', !compact && 'xl:grid-cols-2')}
      >
        <div className="min-w-0 space-y-2">
          <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Error and stack trace</h2>
          {stackTrace && errorMessage && (
            <p
              data-test-case-error=""
              className="m-0 line-clamp-3 whitespace-pre-wrap break-words font-mono text-[12.5px] text-[var(--status-failed)]"
              title={errorMessage}
            >
              {errorMessage}
            </p>
          )}
          {/* The trace when the reporter sent one; else the error message,
              which is where most reporters put the trace. */}
          <LogViewer
            content={stackTrace ?? errorMessage ?? undefined}
            title={stackTrace ? 'Stack trace' : 'Error message'}
          />
          {tc.has_attachments && (
            <p className="text-xs text-[var(--color-text-muted)]">
              📎 Attachments available — open via Allure report link
            </p>
          )}
        </div>

        <div className="min-w-0 space-y-2">
          <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">AI root cause</h2>
          {isFailed ? (
            <AIAnalysisPanel
              testCaseId={tc.id}
              testName={tc.test_name}
              runId={runId}
              projectKey={project?.jira_project_key}
              ocpPodName={tc.ocp_pod_name}
              ocpNamespace={project?.ocp_namespace}
            />
          ) : (
            <div className="card text-center py-10 text-[var(--color-text-muted)]">
              <p className="text-sm">AI analysis is only available for failed or broken tests</p>
            </div>
          )}
        </div>
      </section>

      <Tabs items={tabs} value={tab} onChange={setTab} ariaLabel="Test case sections" />

      <div role="tabpanel" aria-label={tabs.find(t => t.id === tab)?.label as string} data-test-case-tab={tab} className="space-y-4">
        {tab === 'history' && (
          <>
            <TestHistoryPanel runId={runId} testId={testId} />
            {/* Cross-run step-flip (FLK-P6): which step oscillates PASSED↔FAILED across runs. */}
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">Cross-run step flakiness</h3>
              <StepFlipPanel runId={runId} testId={testId} />
            </div>
          </>
        )}
        {tab === 'steps' && <TestStepsPanel runId={runId} testId={testId} detail={detail} />}
        {tab === 'details' && (
          <>
            <ThisResult tc={tc} runId={runId} failureCategory={execution?.failure_category ?? null} />
            <TestCaseDetailSummary detail={detail} />
          </>
        )}
      </div>
    </div>
  )
}

/**
 * This execution's own fields: its ids (the "ids" of §5's Details tab) and
 * the metadata the old grid above the trace showed (severity, feature, owner,
 * run date), moved here so none of it is lost.
 */
function ThisResult({
  tc, runId, failureCategory,
}: { tc: TestCaseDetailResponse; runId: string; failureCategory: string | null }) {
  const rows: { label: string; value: React.ReactNode }[] = [
    { label: 'Result ID', value: <span className="font-mono">{tc.id}</span> },
    { label: 'Run', value: <Link to={`/runs/${runId}`} className="font-mono text-[var(--color-accent)] hover:underline">{runId}</Link> },
    { label: 'Ran at', value: tc.created_at ? formatDateTime(tc.created_at) : null },
    { label: 'Severity', value: tc.severity },
    { label: 'Feature', value: tc.feature },
    { label: 'Owner', value: tc.owner },
    { label: 'Failure category', value: failureCategory ?? tc.failure_category },
  ]
  return (
    <section className="card space-y-1 p-3" aria-label="This result">
      <h2 className="mb-1 text-sm font-semibold text-[var(--color-text-secondary)]">This result</h2>
      {rows.filter(r => r.value).map(r => (
        <div key={r.label} className="flex items-baseline justify-between gap-3 py-0.5">
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{r.label}</span>
          <span className="min-w-0 truncate text-right text-xs text-[var(--color-text-secondary)]">{r.value}</span>
        </div>
      ))}
    </section>
  )
}
