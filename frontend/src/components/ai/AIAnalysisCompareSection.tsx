/**
 * W03 AIH-03/04: Run Compare's "AI analysis" section (contract §9.7). The baseline (left) and target (right) runs'
 * AI analyses side by side: verdict and risk, the engine and model that produced each (a `deterministic` chip when
 * no model did), the failure categories and the top suggested root causes; then what changed between them
 * (`AIDigestDiff`, cross-run mode) and the narrative's label ("AI narrative" or "deterministic summary").
 *
 * Two runs of different projects are not compared (and nothing is fetched). A run without an AI analysis, or a
 * failed read, is a short note, never an error toast.
 */
import { Sparkles } from 'lucide-react'
import { clsx } from 'clsx'
import { useAICompare } from '@/hooks/useAIHistory'
import type { DigestSide } from '@/types/aiHistory'
import { categoryColor } from '@/utils/formatters'
import AIDigestDiff from './AIDigestDiff'
import { engineWord, previousRunEmptyText, runLabel } from './aiDigestDiff.model'

const VERDICT_TONE: Record<string, string> = {
  GO: 'text-[var(--status-passed)]',
  CONDITIONAL_GO: 'text-[var(--status-broken)]',
  NO_GO: 'text-[var(--status-failed)]',
}

function Note({ children, kind }: { children: React.ReactNode; kind: string }) {
  return (
    <p data-ai-compare-note={kind} className="m-0 text-[12px] text-[var(--color-text-muted)]">{children}</p>
  )
}

function SideColumn({ side, title, runName, data }: { side: 'baseline' | 'current'; title: string; runName: string; data: DigestSide | null }) {
  const categories = Object.entries(data?.categories ?? {}).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  const model = data?.llm_model && data.llm_model.toLowerCase() !== 'none' ? data.llm_model : null
  return (
    <div data-ai-compare-side={side} className="min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">{title}</div>
      <div className="text-[13px] font-semibold text-[var(--color-text)]">{runName}</div>
      {!data ? (
        <p className="m-0 mt-1 text-[12px] text-[var(--color-text-muted)]">No AI analysis for this run.</p>
      ) : (
        <div className="mt-1.5 space-y-1.5 text-[12px]">
          <p className="m-0">
            <span data-ai-compare-verdict="" className={clsx('font-semibold', VERDICT_TONE[data.verdict ?? ''] ?? 'text-[var(--color-text)]')}>
              {data.verdict ?? 'No verdict'}
            </span>
            {data.risk_score != null && <span className="ml-2 text-[var(--color-text-muted)]">risk {data.risk_score}/100</span>}
          </p>
          <p className="m-0 text-[var(--color-text-secondary)]">
            {engineWord(data.engine)}{model ? ` ${model}` : ''}
            {data.deterministic && (
              <span className="ml-1.5 rounded-full border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
                deterministic
              </span>
            )}
            {data.fallback_used && <span className="ml-1.5 text-[11px] text-[var(--status-broken)]">fallback used</span>}
            <span className="ml-1.5 text-[var(--color-text-muted)]">· {data.analysed_tests} analysed</span>
          </p>
          {categories.length > 0 && (
            <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
              {categories.map(([category, n]) => (
                <li key={category} className={clsx('rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[11px]', categoryColor(category))}>
                  {category} {n}
                </li>
              ))}
            </ul>
          )}
          {(data.top_clusters ?? []).length > 0 && (
            <div>
              <p className="m-0 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Top suggested root causes</p>
              <ol className="m-0 list-decimal pl-5">
                {data.top_clusters.slice(0, 5).map((c) => (
                  <li key={c.signature} className="text-[var(--color-text-secondary)]">
                    <span className="break-words">{c.label}</span>
                    <span className="ml-1 text-[var(--color-text-muted)]">({c.size})</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function AIAnalysisCompareSection({
  leftRunId,
  rightRunId,
  suiteName,
  sameProject,
  leftLabel,
  rightLabel,
}: {
  leftRunId: string
  rightRunId: string
  suiteName?: string | null
  sameProject: boolean
  /** How the page names the two runs (`Build #239`); else read from the answer. */
  leftLabel?: string
  rightLabel?: string
}) {
  const compare = useAICompare(sameProject ? rightRunId : null, { baselineRunId: leftRunId, suiteName: suiteName ?? null })
  const data = compare.data
  const baselineName = leftLabel ?? (data?.baseline ? runLabel(data.baseline) : 'Baseline run')
  const targetName = rightLabel ?? (data?.current_ref?.build_number ? `#${data.current_ref.build_number}` : 'Target run')

  let body: React.ReactNode
  if (!sameProject) {
    body = <Note kind="different_projects">AI analysis comparison needs two runs of the same project.</Note>
  } else if (compare.error) {
    body = (
      <Note kind="unavailable">
        The AI analysis comparison is unavailable right now.{' '}
        <button type="button" onClick={() => void compare.mutate()} className="text-[var(--color-accent)] underline underline-offset-2">Retry</button>
      </Note>
    )
  } else if (!data) {
    body = <Note kind="loading">Comparing the two runs' AI analyses…</Note>
  } else {
    body = (
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <SideColumn side="baseline" title="Baseline" runName={baselineName} data={data.sides?.baseline ?? null} />
          <SideColumn side="current" title="Target" runName={targetName} data={data.sides?.current ?? null} />
        </div>
        {data.reason || !data.diff ? (
          <Note kind={data.reason ?? 'none'}>
            {previousRunEmptyText(data.reason, { baseline: data.baseline, message: data.message })}
          </Note>
        ) : (
          <AIDigestDiff
            diff={data.diff}
            mode="cross_run"
            fromLabel={`Baseline: ${baselineName}`}
            toLabel={`Target: ${targetName}`}
            runId={rightRunId}
            fromRunId={leftRunId}
            maxRows={15}
          />
        )}
        {data.narrative && (
          <p className="m-0 text-[11px] text-[var(--color-text-muted)]">
            Narrative: <span data-ai-compare-narrative-label="" className="font-medium text-[var(--color-text-secondary)]">{data.narrative.label}</span>
            {data.narrative.report_status === 'queued' && ' (the AI narrative is being written)'}
          </p>
        )}
      </div>
    )
  }

  return (
    <section
      data-ai-compare-section=""
      aria-label="AI analysis"
      className="space-y-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3"
    >
      <div className="flex items-center gap-2">
        <Sparkles aria-hidden className="h-4 w-4 text-[var(--color-accent)]" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text)]">AI analysis</div>
          <p className="m-0 text-xs text-[var(--color-text-muted)]">
            What the AI concluded about each run, and what changed between the two analyses.
          </p>
        </div>
      </div>
      {body}
    </section>
  )
}
