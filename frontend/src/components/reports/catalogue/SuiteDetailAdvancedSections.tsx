/**
 * Suite detail's two Wave-3 sections, each in its own error boundary and
 * Suspense (the lazy half of `SuiteDetailAdvanced`).
 *
 * Why a module of its own: the page imports `SuiteDetailAdvanced` EAGERLY, so
 * everything that file imports statically is in every first visit of
 * Suite detail. Two `lazy(import())` calls and `SectionErrorBoundary` there
 * cost ~2 kB gzip of it (the boundary's chunk plus each lazy import's preload
 * list, measured, integrator I); behind ONE lazy import of this file they
 * stay off the page's eager chunk — the pattern of `FailuresAdvanced` /
 * `FailuresAdvancedSections`.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import type { HeatmapKind } from './sectionContracts'
import { SUITE_HEATMAP_MIN_HEIGHT, SUITE_SCATTER_MIN_HEIGHT } from './SuiteDetailAdvanced.model'

const HeatmapSection = lazyWithRetry(() => import('./HeatmapSection'))
const ScatterSection = lazyWithRetry(() => import('./ScatterSection'))

/** The heatmap kind Suite detail offers (plan 2.5: the "Test x run heatmap" W26 deferred). */
const SUITE_HEATMAP_KINDS: readonly HeatmapKind[] = ['test_run']

function Placeholder({ minHeight }: { minHeight: number }) {
  return <div aria-hidden="true" style={{ minHeight }} />
}

export interface SuiteDetailAdvancedSectionsProps {
  days: number
  /** The page's ONE suite (identity-stable, from the composite). */
  suiteFilter: readonly string[]
}

export default function SuiteDetailAdvancedSections({ days, suiteFilter }: SuiteDetailAdvancedSectionsProps): ReactElement {
  return (
    <div data-suite-advanced="" className="grid grid-cols-1 gap-4 min-w-0">
      <SectionErrorBoundary message="Failed to load the test x run heatmap">
        <Suspense fallback={<Placeholder minHeight={SUITE_HEATMAP_MIN_HEIGHT} />}>
          <HeatmapSection days={days} suiteFilter={suiteFilter} kinds={SUITE_HEATMAP_KINDS} />
        </Suspense>
      </SectionErrorBoundary>
      <SectionErrorBoundary message="Failed to load the test scatter">
        <Suspense fallback={<Placeholder minHeight={SUITE_SCATTER_MIN_HEIGHT} />}>
          <ScatterSection days={days} suiteFilter={suiteFilter} placement="suite" />
        </Suspense>
      </SectionErrorBoundary>
    </div>
  )
}
