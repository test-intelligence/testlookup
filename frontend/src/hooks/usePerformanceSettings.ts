import useSWR from 'swr'
import {
  type PerformanceBudgets,
  type SearchConfig,
  getPerformanceBudgets,
  getSearchConfig,
} from '@/services/performanceService'

export interface PerformanceSettings {
  budgets: PerformanceBudgets
  config: SearchConfig
}

/**
 * Performance budgets + search config as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { setLoading(true);
 * Promise.all([getPerformanceBudgets(), getSearchConfig()])
 * .then(([b, c]) => { setBudgets(b); setConfig(c) }) ... }, [])` in
 * PerformancePage — the kind of effect the react-hooks `set-state-in-effect`
 * rule (correctly) discourages. SWR owns the loading/data state declaratively,
 * so the page no longer drives state from an effect.
 *
 * Both endpoints serve static, read-only configuration, so the two fetches are
 * combined under one constant SWR key and run in parallel exactly as the old
 * `Promise.all`. `budgets`/`config` are `null` while loading or on error,
 * preserving the prior `useState<… | null>(null)` semantics (the page renders
 * each tab body only when its data is present). `shouldRetryOnError` is off so a
 * failed load surfaces as empty immediately, matching the old `.catch(() => {})`
 * that swallowed the error and left the state null.
 */
export function usePerformanceSettings() {
  const { data, error, isLoading } = useSWR<PerformanceSettings>(
    'performance-settings',
    async () => {
      const [budgets, config] = await Promise.all([
        getPerformanceBudgets(),
        getSearchConfig(),
      ])
      return { budgets, config }
    },
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
    },
  )

  return {
    budgets: data ? data.budgets : null,
    config: data ? data.config : null,
    isLoading,
    isError: !!error,
  }
}
