/**
 * Suite detail's Wave-3 sections (plan 2.5): the test x run heatmap (FK1's
 * `HeatmapSection`, `kinds=['test_run']`) and the test scatter (FK4's
 * `ScatterSection`, `placement="suite"`), both for the page's one suite.
 *
 * The page's whole change is one import of this file and one mount (since
 * Phase D, S3, unconditional: the page no longer asks the catalogue flag).
 * Since Phase D, S4 this composite and the heatmap ask no flag either, and
 * since S5 neither does the scatter: it renders whenever the page has a suite.
 *
 * ONE lazy chunk (`SuiteDetailAdvancedSections`) brings the two
 * sections, each a lazy chunk of its own inside its own error boundary: a
 * chunk that fails to load, or a section that throws, is one error card,
 * never the page's "Something went wrong" (and if the sections module itself
 * cannot load, this renders nothing and the page stays). Each section mounts
 * behind its OWN `LazySection` (`heatmap-test_run`, `scatter-suite`), so no
 * request and no engine chunk goes out before the reader is near; while a
 * chunk downloads, a placeholder of its height holds its place. This module is small and imports no section, no boundary and no
 * chart code: the page imports it EAGERLY into every first visit.
 */
import { Suspense, useMemo, type ReactElement } from 'react'
import { lazyWithRetry } from '@/utils/lazyWithRetry'
import Quiet from './QuietSectionBoundary'
import { SUITE_ADVANCED_MIN_HEIGHT } from './SuiteDetailAdvanced.model'

export { SUITE_HEATMAP_MIN_HEIGHT, SUITE_SCATTER_MIN_HEIGHT } from './SuiteDetailAdvanced.model'

const SuiteDetailAdvancedSections = lazyWithRetry(() => import('./SuiteDetailAdvancedSections'))

export interface SuiteDetailAdvancedProps {
  /** The page's window, days (its own `?days`). `catalogueParams` clamps it to 90 on the wire. */
  days: number
  /** The page's suite (`?name=`). Empty: nothing is rendered. */
  suiteName: string
}

export default function SuiteDetailAdvanced({ days, suiteName }: SuiteDetailAdvancedProps): ReactElement | null {
  // The page's ONE suite, as every section's suite scope (identity-stable).
  const suiteFilter = useMemo(() => [suiteName] as const, [suiteName])
  if (!suiteName) return null
  return (
    // A sections module that failed to load is nothing at all (logged and reported): the page is unchanged.
    <Quiet label="suite-detail">
      <Suspense fallback={<div aria-hidden="true" style={{ minHeight: SUITE_ADVANCED_MIN_HEIGHT }} />}>
        <SuiteDetailAdvancedSections days={days} suiteFilter={suiteFilter} />
      </Suspense>
    </Quiet>
  )
}
