/**
 * Settings › Performance › Slow queries (W17b ADM-06, admin only): the slowest statement shapes.
 *
 * Statement text only — literals and parameters read `?`, so no value ever shows. In-app capture
 * records statements over the threshold on every API pod (the 200 most recent shapes); when
 * `pg_stat_statements` is readable the database's own numbers are shown instead.
 */
import { Table, Td, Th } from '@/components/ui/Table'
import { useSlowQueries } from '@/hooks/usePerformanceSettings'
import { formatCompactDateTime, formatNumber } from '@/utils/formatters'

const ms = (v: number | null) => (v == null ? '—' : `${formatNumber(Math.round(v))} ms`)

export function SlowQueriesPanel() {
  const { data, error, isLoading } = useSlowQueries(true)
  if (isLoading) return <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
  if (error || !data) return <p className="text-sm text-[var(--ui-danger)]">Could not load slow queries.</p>
  const items = Array.isArray(data.items) ? data.items : []

  return (
    <div className="space-y-2">
      <p className="text-xs text-[var(--color-text-muted)]" data-testid="slow-queries-source">
        {data.source === 'pg_stat_statements'
          ? `From pg_stat_statements: statements whose slowest run took ${data.threshold_ms} ms or more.`
          : `Captured by TestLookup on every API pod: statements that took ${data.threshold_ms} ms or more (the 200 most recent shapes). Values are never recorded.`}
      </p>
      {items.length === 0 ? (
        <div className="card p-6 text-center text-sm text-[var(--color-text-muted)]" data-testid="slow-queries-empty">
          No query over {data.threshold_ms} ms has been recorded.
        </div>
      ) : (
        <Table data-testid="slow-queries" density="dense">
          <thead>
            <tr>
              <Th>Statement</Th>
              <Th numeric>Count</Th>
              <Th numeric>Mean</Th>
              <Th numeric>Max</Th>
              <Th>Last seen</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((q) => (
              <tr key={q.fingerprint}>
                <Td>
                  <code className="block max-w-[30rem] truncate font-mono text-xs text-[var(--color-text)]" title={q.sql}>
                    {q.sql}
                  </code>
                </Td>
                <Td numeric>{formatNumber(q.count)}</Td>
                <Td numeric className="whitespace-nowrap">{ms(q.mean_ms)}</Td>
                <Td numeric className="whitespace-nowrap font-semibold">{ms(q.max_ms)}</Td>
                <Td className="text-xs text-[var(--color-text-muted)]">
                  {q.last_seen ? formatCompactDateTime(q.last_seen * 1000) : '—'}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}
