/**
 * W14 AGT-07: "Recent runs" — the page's run picker — filled from the project's deep-investigation history
 * (`GET /deep-investigate/history`, one read for page 1), and an **Investigated runs · N** mode that pages
 * through every run the project investigated (Prev / Next; page 2 onwards is asked only when opened).
 *
 * A run the history does not list keeps "—" in its investigation columns (never an invented number); the
 * focused run keeps what the page knows live. A row opens its run (built from the id only). No project to ask
 * about (All Projects with no focused run) → no history read and no chip.
 */
import { useState, type CSSProperties } from 'react'
import { Panel } from '@/components/ui/PanelHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import SuiteBadge from '@/components/ui/SuiteBadge'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { useDeepHistory } from '@/hooks/useAgentOps'
import { HISTORY_LOAD_ERROR, enrichRows, historyRow, type DeepRunRow } from './deepRunModel'

export interface DeepHistoryTableProps {
  /** The recent runs (the page's picker), with what the page knows of the focused run. */
  rows: readonly DeepRunRow[]
  /** The project the history is asked for; null → no read, no chip. */
  projectId: string | null
  onOpen: (runId: string) => void
}

const STATUS_CHIP: Record<NonNullable<DeepRunRow['status']>, { label: string; style: CSSProperties }> = {
  complete: { label: 'Complete', style: { background: 'var(--ui-success-bg)', border: '1px solid var(--ui-success-bd)', color: 'var(--ui-success-ink)' } },
  running: { label: 'Running', style: { background: 'var(--ui-info-bg)', border: '1px solid var(--ui-info-bd)', color: 'var(--ui-info-ink)' } },
  failed: { label: 'Failed', style: { background: 'var(--ui-danger-bg)', border: '1px solid var(--ui-danger-bd)', color: 'var(--ui-danger-ink)' } },
}

const confInk = (v: number) => (v >= 0.8 ? 'var(--ui-success-ink)' : v >= 0.6 ? 'var(--ui-warning-ink)' : 'var(--ui-danger-ink)')
const SUITE_LINK = (name: string) => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`

function Row({ row, onOpen }: { row: DeepRunRow; onOpen: () => void }) {
  const unknown = <span className="text-[var(--color-text-faint)]">—</span>
  const chip = row.status ? STATUS_CHIP[row.status] : null
  return (
    <Tr onClick={onOpen}>
      <Td>
        <div className="flex min-w-0 flex-col">
          <SuiteBadge primary={row.suiteLabel} all={row.suiteNames} className="self-start max-w-full" linkTo={SUITE_LINK} />
          <span className="mt-1 text-2xs tabular-nums text-[var(--color-text-muted)]">{row.whenRel} · {row.whenAbs}</span>
        </div>
      </Td>
      <Td>
        <span className="font-mono text-xs text-[var(--color-text)]">{row.runSeq != null ? `Run #${row.runSeq}` : row.runIdLabel}</span>
      </Td>
      <Td numeric>{row.failures}</Td>
      <Td numeric>{row.clusters ?? unknown}</Td>
      <Td numeric>{row.findings ?? unknown}</Td>
      <Td numeric>
        {row.avgConf == null ? unknown : (
          <span className="inline-flex items-center gap-1.5" style={{ color: confInk(row.avgConf) }}>
            <i aria-hidden className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: 'currentColor' }} />
            {row.avgConf.toFixed(2)}
          </span>
        )}
      </Td>
      <Td numeric className="font-mono text-xs">{row.cost == null ? unknown : `$${row.cost.toFixed(2)}`}</Td>
      <Td>
        {chip == null ? unknown : (
          <span className="inline-flex items-center gap-1.5 rounded-full px-1.5 py-0.5 text-2xs" style={chip.style}>
            <i
              aria-hidden
              className={`inline-block h-1.5 w-1.5 rounded-full${row.status === 'running' ? ' animate-pulse' : ''}`}
              style={{ background: 'currentColor' }}
            />
            {chip.label}
          </span>
        )}
      </Td>
    </Tr>
  )
}

export default function DeepHistoryTable({ rows, projectId, onOpen }: DeepHistoryTableProps) {
  const [mode, setMode] = useState<'recent' | 'investigated'>('recent')
  const [page, setPage] = useState(1)
  const first = useDeepHistory(projectId, 1)
  // Page 2 onwards only once the reader pages there.
  const later = useDeepHistory(mode === 'investigated' && page > 1 ? projectId : null, page)
  const investigated = mode === 'investigated'
  const shown = investigated ? (page > 1 ? later : first) : null
  const total = first.data?.total ?? null
  const size = first.data?.size ?? 10
  const pages = total != null ? Math.max(1, Math.ceil(total / size)) : 1
  const tableRows = investigated
    ? (shown?.data?.items ?? []).map((item) => historyRow(item))
    : enrichRows(rows, first.data?.items)

  const failedFirst = projectId != null && !first.data && first.error != null
  const chip = failedFirst ? (
    <span data-history-error="">{HISTORY_LOAD_ERROR}</span>
  ) : projectId != null && total != null ? (
    <button
      type="button"
      aria-pressed={investigated}
      className="btn-ghost text-xs"
      onClick={() => {
        setMode(investigated ? 'recent' : 'investigated')
        setPage(1)
      }}
    >
      Investigated runs · {total}
    </button>
  ) : null

  return (
    <Panel
      title={investigated ? 'Investigated runs' : 'Recent runs'}
      actions={<>{investigated ? <span>{total ?? 0} investigated runs</span> : <span>latest {rows.length}</span>}{chip}</>}
      data-testid="deep-history-table"
    >
      <div className="overflow-x-auto">
        <Table density="dense">
          <thead>
            <tr>
              <Th>Test Suite</Th>
              <Th>Run</Th>
              <Th numeric>Failures</Th>
              <Th numeric>Clusters</Th>
              <Th numeric>Findings</Th>
              <Th numeric>Avg conf</Th>
              <Th numeric>Cost</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {tableRows.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-[var(--color-text-muted)]">
                  {investigated && shown?.error ? HISTORY_LOAD_ERROR
                    : investigated && !shown?.data ? <LoadingSpinner size="sm" />
                    : investigated ? 'No investigated runs yet.' : 'No runs yet.'}
                </td>
              </tr>
            )}
            {tableRows.map((r) => <Row key={r.runId} row={r} onOpen={() => onOpen(r.runId)} />)}
          </tbody>
        </Table>
      </div>
      {investigated && pages > 1 && (
        <div className="flex items-center justify-end gap-2 border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-muted)]">
          <span className="tabular-nums">Page {page} of {pages}</span>
          <button type="button" className="btn-secondary text-xs" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
          <button type="button" className="btn-secondary text-xs" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </Panel>
  )
}
