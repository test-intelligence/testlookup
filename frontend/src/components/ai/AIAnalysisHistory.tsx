/**
 * W03 AIH-04: the top of the Analysis tab (contract §9.6, §9.9): "vs previous run" first (live view only), then the
 * AI history control (which version is shown, compare with another), then the diff drawer when a comparison is
 * open. All state is in the URL (`aiHistoryUrl.ts`), so a link reproduces exactly what was on screen.
 *
 * A diff reads oldest -> newest whatever order the two versions were picked in; "Current data" (the live analysis,
 * human corrections included) is always the newer side.
 */
import { useSearchParams } from 'react-router-dom'
import { RefreshCw, X } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useAIHistory, useAIVersionDiff } from '@/hooks/useAIHistory'
import type { AIVersionSummary } from '@/types/aiHistory'
import AIDigestDiff from './AIDigestDiff'
import AIVersionPicker from './AIVersionPicker'
import PreviousRunAICard from './PreviousRunAICard'
import { diffSides } from './aiDigestDiff.model'
import {
  applyAiBaseline,
  applyAiCompare,
  applyAiVersion,
  isSnapshotVersion,
  readAiBaseline,
  readAiCompare,
  readAiVersion,
  readReportVersion,
  selectedDigestVersion,
} from './aiHistoryUrl'

function DiffDrawer({ runId, versions, viewed, compare, onClose }: {
  runId: string
  versions: AIVersionSummary[]
  viewed: number | null
  compare: number | 'live'
  onClose: () => void
}) {
  const sides = diffSides(versions, viewed, compare)
  const diff = useAIVersionDiff(runId, sides.fromId, sides.to)
  return (
    <div
      data-ai-diff-drawer=""
      role="region"
      aria-label="Compare AI analyses"
      className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3"
    >
      <div className="mb-2 flex items-center gap-2">
        <p className="m-0 text-[12px] font-semibold text-[var(--color-text)]">What changed between the two analyses</p>
        <button
          type="button"
          data-ai-diff-close=""
          aria-label="Close the comparison"
          onClick={onClose}
          className="ml-auto rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
        >
          <X aria-hidden className="h-3.5 w-3.5" />
        </button>
      </div>
      {sides.problem === 'missing' ? (
        <p className="m-0 text-[12px] text-[var(--color-text-muted)]">
          That version no longer exists (it may have been pruned by the retention policy).
        </p>
      ) : sides.problem === 'nothing' ? (
        <p className="m-0 text-[12px] text-[var(--color-text-muted)]">Pick another version to compare with.</p>
      ) : diff.error ? (
        <p className="m-0 flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
          The comparison could not be loaded.
          <button type="button" onClick={() => void diff.mutate()} className="inline-flex items-center gap-1 text-[var(--color-accent)] underline underline-offset-2">
            <RefreshCw aria-hidden className="h-3 w-3" /> Retry
          </button>
        </p>
      ) : !diff.data ? (
        <div className="flex justify-center py-4"><LoadingSpinner size="sm" /></div>
      ) : (
        <AIDigestDiff diff={diff.data.diff} fromLabel={sides.fromLabel} toLabel={sides.toLabel} runId={runId} mode="versions" />
      )}
    </div>
  )
}

export default function AIAnalysisHistory({ runId }: { runId: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const history = useAIHistory(runId)
  const versions = history.data?.versions ?? []
  const latest = history.data?.latest_version ?? null
  const viewed = selectedDigestVersion(versions, readAiVersion(searchParams), readReportVersion(searchParams))
  const snapshot = history.data != null && isSnapshotVersion(viewed, latest)
  const compare = readAiCompare(searchParams)
  const baselineRunId = readAiBaseline(searchParams)

  return (
    <div data-ai-analysis-history="" className="space-y-3">
      {!snapshot && (
        <PreviousRunAICard
          runId={runId}
          baselineRunId={baselineRunId}
          onChangeBaseline={(id) => setSearchParams((current) => applyAiBaseline(current, id))}
        />
      )}
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] px-4 py-2.5">
        {history.error ? (
          <p data-ai-history-unavailable="" className="m-0 flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
            The AI history of this run is unavailable right now.
            <button
              type="button"
              onClick={() => void history.mutate()}
              className="inline-flex items-center gap-1 text-[var(--color-accent)] underline underline-offset-2"
            >
              <RefreshCw aria-hidden className="h-3 w-3" /> Retry
            </button>
          </p>
        ) : (
          <AIVersionPicker
            versions={versions}
            latestVersion={latest}
            selected={viewed}
            onSelect={(version) => setSearchParams((current) => applyAiVersion(current, version, versions))}
            compare={compare}
            onCompare={(value) => setSearchParams((current) => applyAiCompare(current, value))}
            loading={!history.data}
          />
        )}
        {compare != null && history.data && (
          <DiffDrawer
            runId={runId}
            versions={versions}
            viewed={viewed}
            compare={compare}
            onClose={() => setSearchParams((current) => applyAiCompare(current, null))}
          />
        )}
      </div>
    </div>
  )
}
