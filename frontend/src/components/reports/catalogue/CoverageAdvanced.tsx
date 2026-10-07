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
 *
 * `sections` picks which of the two render (default both): `/coverage` (UX
 * redesign P3) mounts the block once per tab with that tab's section, so the
 * other tab's section is not rendered and asks for nothing.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import Quiet from './QuietSectionBoundary'
import type { AdvancedSectionScope } from './sectionContracts'
import { COVERAGE_ADVANCED_HEIGHT, COVERAGE_HEATMAP_SECTION_HEIGHT, COVERAGE_MAP_SECTION_HEIGHT } from './CoverageAdvanced.model'

export { COVERAGE_HEATMAP_SECTION_HEIGHT, COVERAGE_MAP_SECTION_HEIGHT } from './CoverageAdvanced.model'

const CoverageAdvancedSections = lazyWithRetry(() => import('./CoverageAdvancedSections'))

/**
 * The block's sections, by their `data-catalogue-section` / `data-lazy-section`
 * ids (the heatmap's is `HeatmapSection`'s default, `heatmap-<first kind>`), in
 * drawing order.
 */
const COVERAGE_SECTION_IDS = ['coverage-map', 'heatmap-suite_environment'] as const
export type CoverageSectionId = (typeof COVERAGE_SECTION_IDS)[number]

const SECTION_HEIGHT: Record<CoverageSectionId, number> = {
  'coverage-map': COVERAGE_MAP_SECTION_HEIGHT,
  'heatmap-suite_environment': COVERAGE_HEATMAP_SECTION_HEIGHT,
}

/**
 * What the block's placeholder holds while the sections module loads: the
 * sections it will draw, the 14 px grid gap between them and its 14 px top
 * margin. Both sections: exactly `COVERAGE_ADVANCED_HEIGHT`, as before.
 */
function coverageAdvancedHeight(sections: readonly CoverageSectionId[]): number {
  const shown = COVERAGE_SECTION_IDS.filter((id) => sections.includes(id))
  if (shown.length === COVERAGE_SECTION_IDS.length) return COVERAGE_ADVANCED_HEIGHT
  if (shown.length === 0) return 0
  return 14 + shown.reduce((sum, id) => sum + SECTION_HEIGHT[id], 0) + 14 * (shown.length - 1)
}

export interface CoverageAdvancedProps extends AdvancedSectionScope {
  /**
   * Which sections to render (a composition choice, UX redesign P3; default
   * both). A section left out is not rendered at all — no placeholder, no
   * hook, no request; one listed is drawn exactly as with both.
   */
  sections?: readonly CoverageSectionId[]
}

export default function CoverageAdvanced({ sections = COVERAGE_SECTION_IDS, ...scope }: CoverageAdvancedProps): ReactElement {
  return (
    // A sections module that failed to load is nothing at all (logged and reported): the page is unchanged.
    <Quiet label="coverage">
      <Suspense fallback={<div aria-hidden="true" style={{ minHeight: coverageAdvancedHeight(sections) }} />}>
        <CoverageAdvancedSections {...scope} sections={sections} />
      </Suspense>
    </Quiet>
  )
}
