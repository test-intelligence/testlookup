import { api } from './api';

export interface LatencyBudget {
  operation: string;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  description: string;
}

export interface ThroughputBudget {
  operation: string;
  min_rps: number;
  description: string;
}

export interface ScaleScenario {
  name: string;
  description: string;
  projects: number;
  runs_per_day: number;
  tests_per_run: number;
  concurrent_users: number;
}

export interface PerformanceBudgets {
  latency_budgets: LatencyBudget[];
  throughput_budgets: ThroughputBudget[];
  scale_scenarios: ScaleScenario[];
}

export interface SearchConfig {
  index_batch_size: number;
  incremental_limit: number;
  query_timeout_ms: number;
  max_results: number;
  pg_pool_size: number;
  pg_max_overflow: number;
  pg_pool_recycle: number;
  celery_worker_concurrency: number;
}

export async function getPerformanceBudgets(): Promise<PerformanceBudgets> {
  const { data } = await api.get<PerformanceBudgets>('/api/v1/performance/budgets');
  return data;
}

export async function getSearchConfig(): Promise<SearchConfig> {
  const { data } = await api.get<SearchConfig>('/api/v1/performance/search-config');
  return data;
}

// ── W17b ADM-06: measured latency + slow queries ─────────────────────────────

export type ObservedWindow = '15m' | '1h' | '24h';

/** One budgeted route: its budget beside what was measured. Null quantiles = not measured (never 0). */
export interface ObservedBudgetRow {
  operation: string;
  http_handler: string;
  http_method: string;
  budget_p50_ms: number;
  budget_p95_ms: number;
  budget_p99_ms: number;
  observed_p50_ms: number | null;
  observed_p95_ms: number | null;
  observed_p99_ms: number | null;
  count: number;
  measured: boolean;
  breach: boolean;
}

export interface ObservedPerformance {
  window: ObservedWindow;
  /** `in_app`: measured by TestLookup on every API pod; `prometheus`: read from PROMETHEUS_URL. */
  source: 'in_app' | 'prometheus';
  measured: boolean;
  slot_seconds: number;
  budgets: ObservedBudgetRow[];
}

export interface SlowQuery {
  fingerprint: string;
  /** The statement's shape: literals and parameters are `?`, never values. */
  sql: string;
  count: number;
  total_ms: number;
  mean_ms: number | null;
  max_ms: number;
  /** Unix seconds; null from pg_stat_statements. */
  last_seen: number | null;
}

export interface SlowQueries {
  source: 'in_app' | 'pg_stat_statements';
  threshold_ms: number;
  items: SlowQuery[];
  total: number;
}

export async function getObservedPerformance(window: ObservedWindow): Promise<ObservedPerformance> {
  const { data } = await api.get<ObservedPerformance>('/api/v1/performance/observed', { params: { window } });
  return data;
}

/** ADMIN only: statement text reveals the schema. */
export async function getSlowQueries(): Promise<SlowQueries> {
  const { data } = await api.get<SlowQueries>('/api/v1/performance/slow-queries');
  return data;
}
