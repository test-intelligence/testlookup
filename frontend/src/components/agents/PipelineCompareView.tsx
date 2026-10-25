/**
 * W14 AGT-05: the Compare tab — the selected pipeline (`a`) beside another
 * pipeline of the SAME run (`b`), stage by stage
 * (`GET /agents/pipelines/compare?a=&b=`).
 *
 * The picker offers only the run's other pipelines, from the list the page
 * already holds (hence one project); the server still checks both ids. A
 * changed cell is marked (`data-changed`, the info ink); an unknown checksum
 * is `unknown`, never "changed"; a missing release verdict is `—`.
 */
import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { usePipelineCompare } from '@/hooks/useAgentOps'
import { formatNumber } from '@/utils/formatters'
import {
  COMPARE_COLUMNS,
  COMPARE_LOAD_ERROR,
  COMPARE_NOT_IN_PIPELINE,
  candidateLabel,
  cellText,
  defaultCandidate,
  outputCell,
  outputLine,
  type ComparePipelineLike,
  type CompareRowLike,
} from './pipelineViewsModel'

interface SideLike {
  test_run_id: string
  workflow_type: string
  workflow_ref?: string | null
  analysis_mode_resolved?: string | null
  release_verdict?: string | null
  total_tokens?: number | null
  cost_usd?: number | null
  stages_truncated?: boolean
}

function SideFacts({ title, side }: { title: string; side: SideLike }) {
  const cost = side.cost_usd == null ? '—' : `$${side.cost_usd.toFixed(side.cost_usd !== 0 && side.cost_usd < 0.01 ? 4 : 2)}`
  return (
    <div data-compare-side="" className="min-w-0 space-y-0.5 text-xs text-[var(--color-text-muted)]">
      <div className="eyebrow">{title}</div>
      <div>workflow <span className="font-mono text-[var(--color-text)]">{side.workflow_ref ?? side.workflow_type}</span></div>
      <div>mode <span className="text-[var(--color-text)]">{side.analysis_mode_resolved ?? '—'}</span></div>
      <div>release verdict <span data-release-verdict="" className="text-[var(--color-text)]">{side.release_verdict ?? '—'}</span></div>
      <div>{formatNumber(side.total_tokens)} tokens · {cost}</div>
      {side.stages_truncated && <div>Only the first 200 stages are compared.</div>}
      <Link to={`/agents?run=${encodeURIComponent(side.test_run_id)}`} className="text-[var(--color-text-secondary)] underline">
        Open the run&apos;s pipelines
      </Link>
    </div>
  )
}

function Cell({ row, field }: { row: CompareRowLike; field: (typeof COMPARE_COLUMNS)[number]['field'] }) {
  const cell = cellText(row, field)
  return (
    <Td
      data-changed={cell.changed ? '' : undefined}
      className={cell.changed ? 'text-xs text-[var(--ui-info-ink)]' : 'text-xs'}
    >
      {cell.equal ? cell.a : (
        <span className="flex flex-col">
          <span>{cell.a}</span>
          <span>→ {cell.b}</span>
        </span>
      )}
    </Td>
  )
}

export default function PipelineCompareView<P extends ComparePipelineLike>({
  selected,
  candidates,
}: {
  selected: ComparePipelineLike
  /** The run's OTHER pipelines (`compareCandidates`), newest first. */
  candidates: readonly P[]
}) {
  const selectId = useId()
  const [picked, setPicked] = useState<string | null>(null)
  const partnerId = picked ?? defaultCandidate(candidates, selected)?.id ?? null
  const { data: compare, error, mutate } = usePipelineCompare(selected.id, partnerId)

  return (
    <div data-compare-view="" className="max-h-[600px] space-y-3 overflow-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={selectId} className="text-xs text-[var(--color-text-muted)]">Compare with</label>
        <select
          id={selectId}
          value={partnerId ?? ''}
          onChange={e => setPicked(e.target.value)}
          className="max-w-[24rem] truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs"
        >
          {candidates.map(p => <option key={p.id} value={p.id}>{candidateLabel(p)}</option>)}
        </select>
      </div>

      {!compare ? (
        error ? (
          <div data-compare-error="" className="space-y-2">
            <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{COMPARE_LOAD_ERROR}</p>
            <button type="button" className="btn-secondary text-xs" onClick={() => void mutate()}>Retry</button>
          </div>
        ) : <div className="flex justify-center py-6"><LoadingSpinner /></div>
      ) : (
        <>
          <p data-compare-output="" className="m-0 text-sm font-semibold text-[var(--color-text)]">{outputLine(compare)}</p>
          <div className="grid grid-cols-2 gap-4">
            <SideFacts title="This pipeline" side={compare.a} />
            <SideFacts title="Compared with" side={compare.b} />
          </div>
          <Table density="dense" data-compare-table="">
            <thead>
              <tr>
                <Th>Stage</Th>
                {COMPARE_COLUMNS.map(c => <Th key={c.field}>{c.label}</Th>)}
                <Th>Output</Th>
              </tr>
            </thead>
            <tbody>
              {compare.stages.map((row, index) => (
                <Tr key={`${row.stage_name}-${index}`} interactive={false} data-compare-row={row.stage_name}>
                  {/* A stage name has no spaces (cluster_investigation_dispatch): let it wrap
                      anywhere, or it widens the column and pushes Output off the frame at 1280. */}
                  <Td className="min-w-0 max-w-[11rem] [overflow-wrap:anywhere] font-mono text-xs">
                    {row.stage_name}
                    {!row.a && <span className="block font-sans text-2xs text-[var(--color-text-muted)]">{COMPARE_NOT_IN_PIPELINE}</span>}
                    {!row.b && <span className="block font-sans text-2xs text-[var(--color-text-muted)]">not in the compared pipeline</span>}
                  </Td>
                  {COMPARE_COLUMNS.map(c => <Cell key={c.field} row={row} field={c.field} />)}
                  <Td
                    data-changed={outputCell(row) === 'changed' ? '' : undefined}
                    className={outputCell(row) === 'changed' ? 'text-xs text-[var(--ui-info-ink)]' : 'text-xs'}
                  >
                    {outputCell(row)}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </div>
  )
}
