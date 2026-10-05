/**
 * What Failure analysis gains in Wave 3 (plan 2.5): the failure groups and
 * systemic clusters (FK3), the drill ladder (FK5) and the project-wide test
 * scatter (FK4). The page's own change is one import and one mount of this,
 * so three builders never share the page file.
 *
 * Mounted unconditionally since Phase D, S5 (the flags are on everywhere,
 * migration 0195): neither this module nor its sections ask a flag.
 * This module is STATICALLY in the page's chunk, so it holds nothing but ONE
 * lazy import: `FailuresAdvancedSections`, a lazy chunk that composes the
 * three sections. It is its own chunk so the three sections' preload maps (a
 * lazy import carries its chunk's whole dependency list) are not in the
 * page's chunk.
 *
 * If that chunk cannot load (twice: `lazyWithRetry` reloads once), the
 * boundary below renders nothing: the rest of the page stays whole.
 *
 * No section code, no chart kit, no d3 reaches the page's closure from here:
 * the d3 confinement ratchet walks every page's static imports to prove it.
 */
import { Suspense, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import Quiet from './QuietSectionBoundary'
import type { AdvancedSectionScope } from './sectionContracts'

const FailuresAdvancedSections = lazyWithRetry(() => import('./FailuresAdvancedSections'))

export type FailuresAdvancedProps = AdvancedSectionScope

export default function FailuresAdvanced(props: FailuresAdvancedProps): ReactElement {
  return (
    // Nothing in place of a failed composite chunk (logged and reported): the sections are additions, the page is not.
    <Quiet label="failures">
      <Suspense fallback={null}>
        <FailuresAdvancedSections {...props} />
      </Suspense>
    </Quiet>
  )
}
