/**
 * Settings › Performance › Latency Budgets (W17b ADM-06): each budget beside what was measured.
 *
 * The window picker re-reads `/performance/observed`. A p95 over the budget is a red cell; a
 * route with no requests in the window says "No requests" — absence is not 0 ms. Operations with
 * no HTTP route (Celery work, harness-only paths) are listed with their budget and "Not an HTTP
 * route", because only the load harness measures them.
 */
import { useState } from 'react'

import { Table, Td, Th } from '@/components/ui/Table'
import { useObservedPerformance } from '@/hooks/usePerformanceSettings'
import type { LatencyBudget, ObservedBudgetRow, ObservedWindow } from '@/services/performanceService'
import { formatNumber } from '@/utils/formatters'

const WINDOWS: { id: ObservedWindow; label: string }[] = [
  { id: '15m', label: 'Last 15 min' },
  { id: '1h', label: 'Last hour' },
  { id: '24h', label: 'Last 24 hours' },
]

const ms = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)} ms`)

function ObservedCell({ value, budget, measured }: { value: number | null; budget: number; measured: boolean }) {
  if (!measured || value == null) return <span className="text-[var(--color-text-faint)]">—</span>
  const over = value > budget
  return (
    <span
      data-breach={over ? 'true' : 'false'}
      className={over ? 'whitespace-nowrap font-semibold text-[var(--ui-danger)]' : 'whitespace-nowrap text-[var(--ui-success)]'}
      title={over ? `Over the ${budget} ms budget` : `Within the ${budget} ms budget`}
    >
      {ms(value)}
    </span>
  )
}

export function ObservedLatencyTable({ budgets }: { budgets: LatencyBudget[] }) {
  const [window, setWindow] = useState<ObservedWindow>('1h')
  const { data, error, isLoading } = useObservedPerformance(window)
  const rows = Array.isArray(data?.budgets) ? data.budgets : []
  const byOp = new Map<string, ObservedBudgetRow>(rows.map((r) => [r.operation, r]))
  const label = WINDOWS.find((w) => w.id === window)?.label.toLowerCase() ?? window
  const breaches = rows.filter((r) => r.breach).length

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs text-[var(--color-text-muted)]" data-testid="observed-source">
          {error
            ? 'Measured latency is unavailable right now.'
            : data?.source === 'prometheus'
              ? `Observed values from Prometheus (${label}).`
              : `Observed values measured by TestLookup (${label}, all API pods).`}
          {breaches > 0 && <span className="ml-2 font-medium text-[var(--ui-danger)]">{breaches} over budget</span>}
        </p>
        <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          Window
          <select
            aria-label="Observation window"
            value={window}
            onChange={(e) => setWindow(e.target.value as ObservedWindow)}
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text)] text-xs rounded px-2 py-1"
          >
            {WINDOWS.map((w) => <option key={w.id} value={w.id}>{w.label}</option>)}
          </select>
        </label>
      </div>
      <Table data-testid="latency-budgets" density="dense">
        <thead>
          <tr>
            <Th>Operation</Th>
            <Th numeric>Budget p95</Th>
            <Th numeric>Observed p50</Th>
            <Th numeric>Observed p95</Th>
            <Th numeric>Observed p99</Th>
            <Th numeric>Requests</Th>
            <Th>Description</Th>
          </tr>
        </thead>
        <tbody>
          {budgets.map((b) => {
            const o = byOp.get(b.operation)
            return (
              <tr key={b.operation} data-operation={b.operation}>
                <Td>
                  <div className="font-mono text-xs text-[var(--color-text)]">{b.operation}</div>
                  {o && <div className="text-2xs text-[var(--color-text-faint)]">{o.http_method} {o.http_handler}</div>}
                </Td>
                <Td numeric className="whitespace-nowrap">{b.p95_ms} ms</Td>
                {o ? (
                  <>
                    <Td numeric><ObservedCell value={o.observed_p50_ms} budget={b.p50_ms} measured={o.measured} /></Td>
                    <Td numeric><ObservedCell value={o.observed_p95_ms} budget={b.p95_ms} measured={o.measured} /></Td>
                    <Td numeric><ObservedCell value={o.observed_p99_ms} budget={b.p99_ms} measured={o.measured} /></Td>
                    <Td numeric>
                      {o.measured ? formatNumber(o.count) : <span className="text-[var(--color-text-faint)]">No requests</span>}
                    </Td>
                  </>
                ) : (
                  <Td colSpan={4} className="text-xs text-[var(--color-text-faint)]">
                    {isLoading ? 'Loading…' : 'Not an HTTP route — measured by the load harness only'}
                  </Td>
                )}
                <Td className="text-xs text-[var(--color-text-muted)]">{b.description}</Td>
              </tr>
            )
          })}
        </tbody>
      </Table>
    </div>
  )
}
