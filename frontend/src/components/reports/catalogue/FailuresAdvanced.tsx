/**
 * What Failure analysis gains in Wave 3 (plan 2.5): the failure groups and
 * systemic clusters (FK3), the drill ladder (FK5) and the project-wide test
 * scatter (FK4). The page's own change is one import and one mount of this,
 * so three builders never share the page file.
 *
 * This module is STATICALLY in the page's chunk, so it holds nothing but the
 * two gates and ONE lazy import:
 *
 *   1. `useCatalogueRollout()` — the page-level read every report page makes
 *      (`viz_chart_data_api`): flag-off, the page's ONLY addition is that one
 *      status request (plan 2.4). Nothing else renders, no chunk is fetched.
 *   2. `useAdvancedRollout()` — `viz_advanced_charts` as well, looked up only
 *      once the first is on. With only one of the two on: nothing, and no
 *      section chunk (each section also reads the seam itself).
 *   3. `FailuresAdvancedSections`, a lazy chunk that composes the three
 *      sections. It is its own chunk so the three sections' preload maps (a
 *      lazy import carries its chunk's whole dependency list) are not in the
 *      page's chunk on every flag-off visit.
 *
 * If that chunk cannot load (twice: `lazyWithRetry` reloads once), the
 * boundary below renders nothing: the Wave 2.6 page stays whole.
 *
 * No section code, no chart kit, no d3 reaches the page's closure from here:
 * the d3 confinement ratchet walks every page's static imports to prove it.
 */
import { Suspense } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import Quiet from './QuietSectionBoundary'
import type { AdvancedSectionScope } from './sectionContracts'
import { useAdvancedRollout, useCatalogueRollout } from './useCatalogueRollout'

const FailuresAdvancedSections = lazyWithRetry(() => import('./FailuresAdvancedSections'))

export type FailuresAdvancedProps = AdvancedSectionScope

function AdvancedGate(props: FailuresAdvancedProps) {
  const advanced = useAdvancedRollout()
  if (!advanced) return null
  return (
    // Nothing in place of a failed composite chunk (logged and reported): the sections are additions, the page is not.
    <Quiet label="failures">
      <Suspense fallback={null}>
        <FailuresAdvancedSections {...props} />
      </Suspense>
    </Quiet>
  )
}

export default function FailuresAdvanced(props: FailuresAdvancedProps) {
  const catalogue = useCatalogueRollout()
  return catalogue ? <AdvancedGate {...props} /> : null
}
