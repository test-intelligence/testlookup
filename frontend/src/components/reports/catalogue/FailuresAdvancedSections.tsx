/**
 * The Failures page's Wave-3 sections, in page order (plan 2.5): failure
 * groups + systemic clusters (FK3), the drill ladder (FK5), the project-wide
 * test scatter (FK4) — each through its PINNED contract (`sectionContracts.ts`).
 *
 * A lazy chunk of its own, loaded by `FailuresAdvanced` only once BOTH flags
 * are on. That is the point of the split: a `lazy(() => import(...))` puts the
 * imported chunk's whole dependency list (Vite's preload map, ~60 file names
 * for these three) into the IMPORTING chunk. Here, that is this chunk; in
 * `FailuresAdvanced` it would have been the page's, on every flag-off first
 * visit (measured: +1.4 kB gzip on the Failures page chunk).
 *
 * Each section sits in a `LazySection` (mounted, and its chunk fetched, only
 * when it scrolls near), behind `Suspense` and a `SectionErrorBoundary` (a
 * chunk that fails twice, or a section that throws, takes only itself down).
 * Each section also reads the seam itself (its contract).
 */
import { Suspense } from 'react'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import LazySection from './LazySection'
import type { AdvancedSectionScope } from './sectionContracts'

const FailureGroupsSection = lazyWithRetry(() => import('./FailureGroupsSection'))
const FailuresDrill = lazyWithRetry(() => import('./FailuresDrill'))
const ScatterSection = lazyWithRetry(() => import('./ScatterSection'))

/**
 * Placeholder heights (px) while a section is not near yet: the height it DRAWS
 * at, so nothing below it moves when it mounts (R2-B F-15: 980 / 520 / 580
 * over-reserved, and the scatter moved up 289 px while a reader scrolled).
 * Measured on Failures at 1280 x 800 with every section mounted (X3,
 * `docs/viz-work/w3/x3/groups-win.jsonl`): groups 860 (bubbles + ranked table +
 * the keyboard hint's reserved line), the drill ladder 372, the scatter 543.
 */
const GROUPS_HEIGHT = 860
const DRILL_HEIGHT = 372
// FK4's own LazySection placeholder: this outer one keeps the scatter CHUNK unfetched until near too.
const SCATTER_HEIGHT = 543

export default function FailuresAdvancedSections({ days, suiteFilter }: AdvancedSectionScope) {
  return (
    <div data-failures-advanced="" className="grid min-w-0 grid-cols-1 gap-3.5">
      <LazySection label="failures-groups" minHeight={GROUPS_HEIGHT}>
        <SectionErrorBoundary message="Failure groups failed to load">
          <Suspense fallback={null}>
            <FailureGroupsSection days={days} suiteFilter={suiteFilter} />
          </Suspense>
        </SectionErrorBoundary>
      </LazySection>
      <LazySection label="failures-drill" minHeight={DRILL_HEIGHT}>
        <SectionErrorBoundary message="Failures by suite failed to load">
          <Suspense fallback={null}>
            <FailuresDrill days={days} suiteFilter={suiteFilter} />
          </Suspense>
        </SectionErrorBoundary>
      </LazySection>
      <LazySection label="failures-scatter" minHeight={SCATTER_HEIGHT}>
        <SectionErrorBoundary message="Test scatter failed to load">
          <Suspense fallback={null}>
            <ScatterSection days={days} suiteFilter={suiteFilter} placement="project" />
          </Suspense>
        </SectionErrorBoundary>
      </LazySection>
    </div>
  )
}
