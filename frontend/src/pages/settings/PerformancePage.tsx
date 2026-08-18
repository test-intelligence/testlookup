import { useState } from 'react';
import { clsx } from 'clsx';
import { usePerformanceSettings } from '@/hooks/usePerformanceSettings';

type Tab = 'budgets' | 'config' | 'scenarios';

export default function PerformancePage() {
  const [tab, setTab] = useState<Tab>('budgets');
  const { budgets, config, isLoading } = usePerformanceSettings();

  const tabs: { key: Tab; label: string }[] = [
    { key: 'budgets', label: 'Latency Budgets' },
    { key: 'config', label: 'Search Config' },
    { key: 'scenarios', label: 'Scale Scenarios' },
  ];

  if (isLoading) return <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text)]">Performance Budgets</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">Codified latency targets, search configuration, and scale scenarios.</p>
      </div>

      <div className="flex gap-1 border-b border-[var(--color-border)]">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx('px-4 py-2 text-sm font-medium rounded-t-lg', tab === t.key ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text)] border-b-2 border-[var(--color-border)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]')}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Latency Budgets */}
      {tab === 'budgets' && budgets && (
        <div className="space-y-4">
          <div className="bg-[var(--color-bg-hover)]/50 rounded-lg px-4 py-2 grid grid-cols-5 gap-2 text-xs text-[var(--color-text-muted)] font-medium">
            <span>Operation</span><span>p50</span><span>p95</span><span>p99</span><span>Description</span>
          </div>
          {budgets.latency_budgets.map(b => (
            <div key={b.operation} className="bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 grid grid-cols-5 gap-2 items-center text-sm">
              <span className="text-[var(--color-text)] font-mono text-xs">{b.operation}</span>
              <span className="text-[var(--status-passed)] text-xs">{b.p50_ms}ms</span>
              <span className="text-[var(--status-skipped)] text-xs">{b.p95_ms}ms</span>
              <span className="text-[var(--status-failed)] text-xs">{b.p99_ms}ms</span>
              <span className="text-[var(--color-text-muted)] text-xs">{b.description}</span>
            </div>
          ))}

          {budgets.throughput_budgets.length > 0 && (
            <>
              <h2 className="text-sm font-semibold text-[var(--color-text)] mt-6">Throughput Budgets</h2>
              {budgets.throughput_budgets.map(b => (
                <div key={b.operation} className="bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 flex justify-between items-center text-sm">
                  <span className="text-[var(--color-text)] font-mono text-xs">{b.operation}</span>
                  <span className="text-[var(--color-text)] text-xs">{b.min_rps} rps</span>
                  <span className="text-[var(--color-text-muted)] text-xs">{b.description}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {/* Search Config */}
      {tab === 'config' && config && (
        <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Current Search & Indexing Configuration</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {([
              ['Index Batch Size', config.index_batch_size],
              ['Incremental Limit', config.incremental_limit],
              ['Query Timeout', `${config.query_timeout_ms}ms`],
              ['Max Results', config.max_results],
              ['PG Pool Size', config.pg_pool_size],
              ['PG Max Overflow', config.pg_max_overflow],
              ['PG Pool Recycle', `${config.pg_pool_recycle}s`],
              ['Worker Concurrency', config.celery_worker_concurrency],
            ] as [string, string | number][]).map(([label, value]) => (
              <div key={label} className="bg-[var(--color-bg)]/50 rounded-lg p-3">
                <div className="text-lg font-bold text-[var(--color-text)]">{value}</div>
                <div className="text-[10px] text-[var(--color-text-muted)]">{label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Scale Scenarios */}
      {tab === 'scenarios' && budgets && (
        <div className="space-y-3">
          {budgets.scale_scenarios.map(s => (
            <div key={s.name} className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
              <h3 className="text-[var(--color-text)] font-medium">{s.name}</h3>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">{s.description}</p>
              <div className="grid grid-cols-4 gap-3 mt-3">
                <div className="text-center">
                  <div className="text-lg font-bold text-[var(--color-text)]">{s.projects}</div>
                  <div className="text-[10px] text-[var(--color-text-muted)]">Projects</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-[var(--status-passed)]">{s.runs_per_day}</div>
                  <div className="text-[10px] text-[var(--color-text-muted)]">Runs/Day</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-[var(--status-flaky)]">{s.tests_per_run}</div>
                  <div className="text-[10px] text-[var(--color-text-muted)]">Tests/Run</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-[var(--status-broken)]">{s.concurrent_users}</div>
                  <div className="text-[10px] text-[var(--color-text-muted)]">Concurrent Users</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
