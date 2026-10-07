/**
 * The Coverage page's two Wave-3 sections (the lazy half of
 * `CoverageAdvanced`): the coverage map behind its `LazySection`, and FK1's
 * heatmap section mounted bare (it owns its `LazySection`, as its contract
 * asks).
 *
 * Why a module of its own: the page imports `CoverageAdvanced` EAGERLY, so
 * whatever that file imports statically is in every first visit of
 * Coverage. `LazySection` (and its viewport hook) and two `lazy(import())`
 * preload lists there cost ~0.6 kB gzip of it and one more shared chunk in
 * every page's preload list (measured, integrator I); behind ONE lazy import
 * of this file they stay off the page's eager chunk — the pattern of
 * `FailuresAdvanced` / `FailuresAdvancedSections`.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import LazySection from './LazySection'
import type { AdvancedSectionScope, HeatmapKind } from './sectionContracts'
import type { CoverageSectionId } from './CoverageAdvanced'
import { COVERAGE_HEATMAP_SECTION_HEIGHT, COVERAGE_MAP_SECTION_HEIGHT } from './CoverageAdvanced.model'

const CoverageMapSection = lazyWithRetry(() => import('./CoverageMapSection'))
const HeatmapSection = lazyWithRetry(() => import('./HeatmapSection'))

/** The heatmap kinds the Coverage page offers (plan 2.5): environment and release are matrices nowhere else. */
const COVERAGE_HEATMAP_KINDS: readonly HeatmapKind[] = ['suite_environment', 'suite_release']

function Placeholder({ height }: { height: number }) {
  return <div aria-hidden="true" style={{ minHeight: height }} />
}

export interface CoverageAdvancedSectionsProps extends AdvancedSectionScope {
  /** The sections `CoverageAdvanced` was asked to render (it resolves the default: both). */
  sections: readonly CoverageSectionId[]
}

export default function CoverageAdvancedSections({ days, suiteFilter, sections }: CoverageAdvancedSectionsProps): ReactElement {
  return (
    <div data-coverage-advanced="" className="mt-3.5 grid min-w-0 grid-cols-1 gap-3.5">
      {sections.includes('coverage-map') && (
        <LazySection label="coverage-map" minHeight={COVERAGE_MAP_SECTION_HEIGHT}>
          <Suspense fallback={<Placeholder height={COVERAGE_MAP_SECTION_HEIGHT} />}>
            <CoverageMapSection days={days} suiteFilter={suiteFilter} />
          </Suspense>
        </LazySection>
      )}
      {/* FK1's section owns its LazySection: mounted bare, as its contract asks. */}
      {sections.includes('heatmap-suite_environment') && (
        <Suspense fallback={<Placeholder height={COVERAGE_HEATMAP_SECTION_HEIGHT} />}>
          <HeatmapSection days={days} suiteFilter={suiteFilter} kinds={COVERAGE_HEATMAP_KINDS} />
        </Suspense>
      )}
    </div>
  )
}
