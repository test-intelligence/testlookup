/**
 * Suite detail's two Wave-3 sections, each in its own error boundary and
 * Suspense (the lazy half of `SuiteDetailAdvanced`).
 *
 * Why a module of its own: the page imports `SuiteDetailAdvanced` EAGERLY, so
 * everything that file imports statically is in every first visit of
 * Suite detail. Two `lazy(import())` calls and `SectionErrorBoundary` there
 * cost ~2 kB gzip of it (the boundary's chunk plus each lazy import's preload
 * list, measured, integrator I); behind ONE lazy import of this file they
 * stay off the page's eager chunk — the pattern of `CoverageAdvanced` /
 * `CoverageAdvancedSections`.
 *
 * EXEC-04b adds a third, last: "Suite execution time" (`SuiteExecutionSection`,
 * the suite's wall clock and summed test time per run). Below the other two,
 * behind its own `LazySection`, so it asks nothing until the reader is near
 * it and the two sections above keep their place on the page.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import LazySection from './LazySection'
import type { HeatmapKind } from './sectionContracts'
import { SUITE_EXECUTION_MIN_HEIGHT, SUITE_HEATMAP_MIN_HEIGHT, SUITE_SCATTER_MIN_HEIGHT } from './SuiteDetailAdvanced.model'

const HeatmapSection = lazyWithRetry(() => import('./HeatmapSection'))
const ScatterSection = lazyWithRetry(() => import('./ScatterSection'))
const SuiteExecutionSection = lazyWithRetry(() => import('./SuiteExecutionSection'))

/** The heatmap kind Suite detail offers (plan 2.5: the "Test x run heatmap" W26 deferred). */
const SUITE_HEATMAP_KINDS: readonly HeatmapKind[] = ['test_run']

function Placeholder({ minHeight }: { minHeight: number }) {
  return <div aria-hidden="true" style={{ minHeight }} />
}

export interface SuiteDetailAdvancedSectionsProps {
  days: number
  /** The page's ONE suite (identity-stable, from the composite). */
  suiteFilter: readonly string[]
  /** The suite's own project, for the execution-time read (default: the active project). */
  projectId?: string | null
}

export default function SuiteDetailAdvancedSections({ days, suiteFilter, projectId }: SuiteDetailAdvancedSectionsProps): ReactElement {
  return (
    <div data-suite-advanced="" className="grid grid-cols-1 gap-4 min-w-0">
      <SectionErrorBoundary message="Could not load the test x run heatmap">
        <Suspense fallback={<Placeholder minHeight={SUITE_HEATMAP_MIN_HEIGHT} />}>
          <HeatmapSection days={days} suiteFilter={suiteFilter} kinds={SUITE_HEATMAP_KINDS} />
        </Suspense>
      </SectionErrorBoundary>
      <SectionErrorBoundary message="Could not load the test scatter">
        <Suspense fallback={<Placeholder minHeight={SUITE_SCATTER_MIN_HEIGHT} />}>
          <ScatterSection days={days} suiteFilter={suiteFilter} placement="suite" />
        </Suspense>
      </SectionErrorBoundary>
      <SectionErrorBoundary message="Could not load the suite execution time">
        <LazySection label="suite-execution" minHeight={SUITE_EXECUTION_MIN_HEIGHT}>
          <Suspense fallback={<Placeholder minHeight={SUITE_EXECUTION_MIN_HEIGHT} />}>
            <SuiteExecutionSection days={days} suiteName={suiteFilter[0] ?? ''} projectId={projectId} />
          </Suspense>
        </LazySection>
      </SectionErrorBoundary>
    </div>
  )
}
