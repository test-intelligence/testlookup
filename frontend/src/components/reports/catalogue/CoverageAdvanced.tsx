/**
 * The Coverage page's Wave 3 sections (plan 2.5): the coverage map (VIZ-502)
 * and the suite x environment / suite x release heatmaps (VIZ-501, FK1's
 * `HeatmapSection`, mounted through its pinned contract).
 *
 * The page's whole change is ONE import of this module and ONE mount, so the
 * two chart owners never share the page file. This module is small and holds
 * no chart code: the sections are lazy chunks.
 *
 * Mounted unconditionally since Phase D, S4 (the flags are on everywhere,
 * migration 0195): neither this module nor its sections ask a flag.
 * ONE lazy chunk (`CoverageAdvancedSections`) brings the sections; each sits
 * in a `LazySection`: no hook and no request before it is near the reader.
 * The map's is in that module (no chunk before it is near either); the
 * heatmap section owns its own (FK1's contract: mount it bare), so its small
 * module chunk loads with the block and its charts when near. Placeholders,
 * and the Suspense fallbacks while a chunk loads, hold each section's height,
 * so nothing below jumps. A sections module that cannot load renders nothing;
 * the page stays. This module imports no section and no `LazySection`: the
 * page imports it EAGERLY into every first visit.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import Quiet from './QuietSectionBoundary'
import type { AdvancedSectionScope } from './sectionContracts'
import { COVERAGE_ADVANCED_HEIGHT } from './CoverageAdvanced.model'

export { COVERAGE_HEATMAP_SECTION_HEIGHT, COVERAGE_MAP_SECTION_HEIGHT } from './CoverageAdvanced.model'

const CoverageAdvancedSections = lazyWithRetry(() => import('./CoverageAdvancedSections'))

export default function CoverageAdvanced(props: AdvancedSectionScope): ReactElement {
  return (
    // A sections module that failed to load is nothing at all (logged and reported): the page is unchanged.
    <Quiet label="coverage">
      <Suspense fallback={<div aria-hidden="true" style={{ minHeight: COVERAGE_ADVANCED_HEIGHT }} />}>
        <CoverageAdvancedSections {...props} />
      </Suspense>
    </Quiet>
  )
}
