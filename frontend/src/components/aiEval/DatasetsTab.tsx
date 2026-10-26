/**
 * The Datasets tab (W16 AGT-09): the evaluation datasets, each with its runs (opened lazily under the row), the
 * legacy `Run label agreement` (the renamed `Run Evaluation`: the same synchronous POST, the legacy metric) and
 * `Evaluate candidate` (ADMIN: a replay through one tier's real engine, an in-page form in the row's slot — one open
 * at a time). A QA lead sees every write disabled with `Needs an administrator.` instead of a 403 toast.
 *
 * Nothing is read at load beyond the page's own datasets list: a row's runs are read when `Runs` opens, the candidate
 * options when the form opens.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { useSWRConfig } from 'swr'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import {
  type AIEvalDataset,
  createDatasetFromFeedback,
  runEvaluation,
  seedGoldenDatasets,
} from '@/services/aiEvalService'
import { formatDay } from '@/utils/formatters'
import CandidateEvaluationForm from './CandidateEvaluationForm'
import EvalRunsList from './EvalRunsList'
import { COPY } from './aiEvalModel'

export interface DatasetsTabProps {
  datasets: AIEvalDataset[]
  loading: boolean
  isAdmin: boolean
  /** The pinned project (the replay's LLM budget and release policy), null under All Projects. */
  projectId: string | null
  onDatasetsChanged: () => void
  /** A legacy evaluation finished (the dashboard re-reads its recent runs). */
  onEvaluated: () => void
  onBaselineSet: () => void
}

/** Every `['ai-eval', 'runs', …]` read (a row's runs, the Gate tab's candidate lists). */
function isRunsKey(key: unknown): boolean {
  return Array.isArray(key) && key[0] === 'ai-eval' && key[1] === 'runs'
}

export default function DatasetsTab({
  datasets, loading, isAdmin, projectId, onDatasetsChanged, onEvaluated, onBaselineSet,
}: DatasetsTabProps) {
  const slot = useInlineSlot<string>()
  const { mutate } = useSWRConfig()
  const [openRuns, setOpenRuns] = useState<ReadonlySet<string>>(() => new Set())
  const [legacyRunning, setLegacyRunning] = useState<string | null>(null)
  const rows = Array.isArray(datasets) ? datasets : []
  const adminTitle = isAdmin ? undefined : COPY.needsAdmin

  const refreshRuns = () => { void mutate(isRunsKey) }
  const showRuns = (id: string, show: boolean) =>
    setOpenRuns((current) => {
      const next = new Set(current)
      if (show) next.add(id)
      else next.delete(id)
      return next
    })

  const handleGenerateDataset = async () => {
    try {
      await createDatasetFromFeedback()
      toast.success('Dataset generated from feedback')
      onDatasetsChanged()
    } catch { toast.error('No feedback data available') }
  }

  const handleSeedGolden = async () => {
    try {
      const result = await seedGoldenDatasets()
      toast.success(result.total > 0 ? `Seeded ${result.total} golden datasets` : 'Golden datasets already exist')
      onDatasetsChanged()
    } catch { toast.error('Failed to seed golden datasets') }
  }

  const handleRunLegacy = async (datasetId: string) => {
    setLegacyRunning(datasetId)
    try {
      await runEvaluation(datasetId)
      toast.success('Evaluation complete')
      onEvaluated()
      refreshRuns()
    } catch { toast.error('Evaluation failed') }
    finally { setLegacyRunning(null) }
  }

  const queued = (datasetId: string) => {
    slot.close()
    showRuns(datasetId, true)
    refreshRuns()
  }

  return (
    <div className="space-y-4" data-testid="ai-eval-datasets-tab">
      <div className="flex gap-2">
        <button type="button" className="btn-primary" disabled={!isAdmin} title={adminTitle} onClick={() => { void handleGenerateDataset() }}>
          Generate from Feedback
        </button>
        <button type="button" className="btn-secondary" disabled={!isAdmin} title={adminTitle} onClick={() => { void handleSeedGolden() }}>
          Seed Golden Datasets
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><LoadingSpinner /></div>}
      {!loading && rows.length === 0 && (
        <div className="py-8 text-center text-[var(--color-text-muted)]">
          No evaluation datasets. Generate one from human feedback or seed golden datasets.
        </div>
      )}
      {!loading && rows.length > 0 && (
        <ul className="m-0 list-none space-y-3 p-0">
          {rows.map((ds) => {
            const runsOpen = openRuns.has(ds.id)
            return (
              <li key={ds.id} data-dataset-row={ds.id} className="card space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <span className="font-medium text-[var(--color-text)]">{ds.name}</span>
                    <p className="m-0 mt-1 text-xs text-[var(--color-text-muted)]">
                      {ds.task_type} · {ds.item_count} items · Created {formatDay(ds.created_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className="btn-secondary btn-sm" aria-expanded={runsOpen} onClick={() => showRuns(ds.id, !runsOpen)}>
                      {runsOpen ? 'Hide runs' : 'Runs'}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary btn-sm inline-flex items-center gap-1.5"
                      disabled={!isAdmin || legacyRunning !== null}
                      title={adminTitle}
                      onClick={() => { void handleRunLegacy(ds.id) }}
                    >
                      {legacyRunning === ds.id && <LoadingSpinner size="sm" />}
                      Run label agreement
                    </button>
                    <button type="button" className="btn-primary btn-sm" disabled={!isAdmin} title={adminTitle} onClick={() => slot.open(ds.id)}>
                      Evaluate candidate
                    </button>
                  </div>
                </div>
                {isAdmin && slot.openKey === ds.id && (
                  <div data-in-page-row="">
                    <CandidateEvaluationForm
                      dataset={ds}
                      projectId={projectId}
                      slot={slot}
                      onClose={slot.close}
                      onQueued={() => queued(ds.id)}
                    />
                  </div>
                )}
                {runsOpen && <EvalRunsList dataset={ds} isAdmin={isAdmin} onBaselineSet={onBaselineSet} />}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
