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
