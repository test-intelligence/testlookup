/**
 * The Run page's **Evidence** tab (UX redesign P4; `/agents/run/:runId`
 * redirects here): what the AI's conclusions about this run rest on.
 *
 *   1. The verified decision report — claims, their evidence and
 *      counter-evidence, terminal verification and provenance, report
 *      versions (`RunDecisionReport`, from Run Intelligence). Absent when the
 *      run has no report envelope.
 *   2. The AI report of the run's agent pipeline — what `/agents/run/:runId`
 *      opened: the newest pipeline (a picker when the run has several), its
 *      report, and its agent stages, collapsed (`PipelineDetail`, from the
 *      Agent Pipeline page).
 */
import { useState } from 'react'
import { Bot } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useAIConfig } from '@/hooks/useAIConfig'
import { usePipelines } from '@/hooks/useAgentRuns'
import { publicPipelineStatus, PUBLIC_PIPELINE_STATUS_LABEL } from '@/types/agent'
import { PipelineDetail } from '../AgentStatusPage'
import { RunDecisionReport } from '../RunIntelligencePage'

function pipelineLabel(p: { workflow_type: string; created_at: string }, status: string): string {
  const when = new Date(p.created_at)
  const at = Number.isNaN(when.getTime()) ? '' : ` · ${when.toLocaleString()}`
  return `${p.workflow_type} pipeline · ${status}${at}`
}

export default function RunEvidenceTab({ runId }: { runId: string }) {
  const { data: aiConfig } = useAIConfig()
  const analysisMode = aiConfig?.analysis_mode ?? 'auto'
  const showLLMMetrics = analysisMode !== 'rules' && analysisMode !== 'ml'

  const { data: rawPipelines = [], isLoading } = usePipelines(runId)
  // Newest first: a run carries 0, 2 or 13 pipelines; the newest is the one
  // its report describes, as on the Agent Pipeline page.
  const pipelines = [...rawPipelines].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  const [picked, setPicked] = useState<string | null>(null)
  const selected = pipelines.find((p) => p.id === picked) ?? pipelines[0] ?? null

  return (
    <div className="space-y-4">
      <RunDecisionReport runId={runId} />

      <section aria-label="AI report" data-primary="" className="space-y-3">
        {pipelines.length > 1 && (
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <span>Pipeline</span>
            <select
              value={selected?.id ?? ''}
              onChange={(e) => setPicked(e.target.value)}
              className="max-w-[420px] truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text)]"
            >
              {pipelines.map((p) => (
                <option key={p.id} value={p.id}>
                  {pipelineLabel(p, PUBLIC_PIPELINE_STATUS_LABEL[publicPipelineStatus(p)])}
                </option>
              ))}
            </select>
          </label>
        )}
        {isLoading && pipelines.length === 0 ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : !selected ? (
          <div className="card flex items-start gap-3 text-sm">
            <Bot aria-hidden className="h-5 w-5 flex-shrink-0 text-[var(--color-text-muted)]" />
            <div>
              <p className="m-0 font-medium text-[var(--color-text)]">No agent pipeline has analysed this run yet.</p>
              <p className="m-0 mt-1 text-xs text-[var(--color-text-muted)]">
                Pipelines are recorded once a run finalises; a QA engineer can start one from this page&apos;s ⋯ menu (Trigger pipeline).
              </p>
            </div>
          </div>
        ) : (
          <PipelineDetail
            key={selected.id}
            pipelineId={selected.id}
            testRunId={selected.test_run_id}
            showLLMMetrics={showLLMMetrics}
            defaultShowStages={false}
          />
        )}
      </section>
    </div>
  )
}
