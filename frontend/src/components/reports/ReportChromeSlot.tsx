/**
 * Where the layout mounts the report chrome — ONCE, above `<Outlet/>`.
 *
 * Renders nothing unless the current path is a registered report route
 * (`reportRoutes.ts`) AND BOTH flags are on:
 *
 *   - `viz_report_context` (the chrome itself), asked about only on a report
 *     route;
 *   - `viz_multi_filters`, as resolved by `useScopeUrlSync` into the store
 *     every data hook reads (`useMultiFiltersEnabled`).
 *
 * Why both: with `viz_multi_filters` off the PAGES keep their legacy scope —
 * a page-local suite, one release — while the chrome's bar and chips read and
 * write the global multi-value stores. Chrome and page would then filter
 * different data under one heading. With the flag on, both read the same
 * settled scope. (Owner decision, fix round B.)
 *
 * The chrome module is lazy, so non-report pages and flag-off sessions pay
 * for neither. This slot itself ships in the lazy multi-filters runtime
 * (components/layout/multiFiltersRuntime.ts): `AppLayout` mounts it once that
 * runtime has loaded, which is always before `viz_multi_filters` reads 'on'. Keyed by route pattern, not pathname: moving between two runs'
 * intelligence pages keeps the chrome (and its open popovers) mounted.
 */
import { lazy, Suspense } from 'react'
import { useLocation } from 'react-router-dom'
import { VIZ_FLAGS } from '@/config/vizFlags'
import { useFeatureEnabled } from '@/hooks/useFeatureFlags'
import { useMultiFiltersEnabled } from '@/store/multiFiltersFlag'
import { matchReportRoute, type ReportRoute } from './reportRoutes'

const ReportChrome = lazy(() => import('./ReportChrome'))

function FlaggedChrome({ route }: { route: ReportRoute }) {
  const reportContext = useFeatureEnabled(VIZ_FLAGS.reportContext)
  const multiFilters = useMultiFiltersEnabled()
  if (!reportContext || !multiFilters) return null
  return (
    <Suspense fallback={null}>
      <ReportChrome key={route} route={route} />
    </Suspense>
  )
}

export default function ReportChromeSlot() {
  const { pathname } = useLocation()
  const route = matchReportRoute(pathname)
  return route ? <FlaggedChrome route={route} /> : null
}
