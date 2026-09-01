import { useParams, useNavigate } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import LogViewer from '@/components/ai/LogViewer'
import AIAnalysisPanel from '@/components/ai/AIAnalysisPanel'
import TestStepsPanel from '@/components/runs/TestStepsPanel'
import TestHistoryPanel from '@/components/runs/TestHistoryPanel'
import StepFlipPanel from '@/components/runs/StepFlipPanel'
import TestCaseDetailSummary from '@/components/runs/TestCaseDetailSummary'
import { useTestCase } from '@/hooks/useRuns'
import { formatDuration, formatDateTime } from '@/utils/formatters'
import { normalizeTestCaseDetail } from '@/utils/testCaseDetail'
import { useProjectStore } from '@/store/projectStore'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'

export default function TestCasePage() {
  const { runId, testId } = useParams<{ runId: string; testId: string }>()
  const navigate = useNavigate()
  const project = useProjectStore(s => s.activeProject)
  useProjectChangeRedirect('/runs', Boolean(runId || testId))
  const { data: tc, isLoading } = useTestCase(runId, testId)

  if (isLoading) return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  if (!tc) return <div className="text-[var(--color-text-muted)] text-center py-20">Test case not found</div>

  const detail = normalizeTestCaseDetail(tc)
  const isFailed = ['FAILED', 'BROKEN'].includes(tc.status)

  return (
    <div className="space-y-4">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
        <button onClick={() => navigate('/runs')} className="hover:text-[var(--color-text)]">Runs</button>
        <ChevronRight className="h-3 w-3" />
        <button onClick={() => navigate(`/runs/${runId}`)} className="hover:text-[var(--color-text)] font-mono">
          #{runId?.slice(0, 8)}
        </button>
        <ChevronRight className="h-3 w-3" />
        <span className="text-[var(--color-text-secondary)] truncate max-w-[200px]">{tc.test_name}</span>
      </div>

      <PageHeader
        title={tc.test_name}
        subtitle={tc.full_name ?? tc.class_name}
        actions={<StatusBadge status={tc.status} />}
      />

      {/* Test metadata */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Suite',    value: tc.suite_name   },
          { label: 'Duration', value: formatDuration(tc.duration_ms) },
          { label: 'Severity', value: tc.severity     },
          { label: 'Feature',  value: tc.feature      },
          { label: 'Owner',    value: tc.owner        },
          { label: 'Run Date', value: formatDateTime(tc.created_at) },
        ].filter(m => m.value).map(m => (
          <div key={m.label} className="bg-[var(--color-bg-hover)]/50 rounded-lg px-3 py-2">
            <p className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider">{m.label}</p>
            <p className="text-sm text-[var(--color-text-secondary)] mt-0.5 truncate">{m.value}</p>
          </div>
        ))}
      </div>

      {/* Tags */}
      {(tc.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-2">
          {tc.tags?.map((tag: string) => (
            <span key={tag} className="badge bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border border-[var(--color-border)]">{tag}</span>
          ))}
        </div>
      )}

      <TestCaseDetailSummary detail={detail} />

      {/* Split pane: log + AI */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Left: Log viewer */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">Stack Trace / Console Output</h3>
          <LogViewer
            content={tc.error_message}
            title={`${tc.class_name ?? 'Test'}.java`}
          />
          {tc.has_attachments && (
            <p className="text-xs text-[var(--color-text-muted)]">
              📎 Attachments available — open via Allure report link
            </p>
          )}
        </div>

        {/* Right: AI panel */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">
            AI Root Cause Analysis
          </h3>
          {isFailed ? (
            <AIAnalysisPanel
              testCaseId={tc.id}
              testName={tc.test_name}
              runId={runId as string}
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
      </div>

      {/* History / flakiness / metadata (cross-run, project-scoped) + steps */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">
            History &amp; Flakiness
          </h3>
          <TestHistoryPanel runId={runId} testId={testId} />
        </div>

        {/* Granular step timeline (latest-run-only snapshot) */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">Steps</h3>
          <TestStepsPanel runId={runId} testId={testId} detail={detail} />
        </div>
      </div>

      {/* Cross-run step-flip (FLK-P6): which step oscillates PASSED↔FAILED across runs */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">
          Cross-Run Step Flakiness
        </h3>
        <StepFlipPanel runId={runId} testId={testId} />
      </div>
    </div>
  )
}
