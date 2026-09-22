import { useRef } from 'react'
import { Outlet } from 'react-router-dom'
import { SWRConfig, type SWRConfiguration } from 'swr'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import DegradedBanner from './DegradedBanner'
import SettingsBackBar from './SettingsBackBar'
import ScopeUrlSyncGate from './ScopeUrlSyncGate'
import { useMultiFiltersRuntimeStore } from './multiFiltersRuntimeLoader'
import { scopeSupersededMiddleware } from '@/services/scopeAbortCore'

/** VIZ-303: a request aborted because the report scope moved on is never
 *  shown as an error. Nested config: SWR concatenates `use` with the root's
 *  and keeps the root's cache (no `provider` here). */
const LAYOUT_SWR_CONFIG: SWRConfiguration = { use: [scopeSupersededMiddleware] }

/**
 * The report chrome slot comes with the lazy multi-filters runtime: it renders
 * nothing unless `viz_multi_filters` is on, and in the app the flag reads 'on'
 * only once that runtime has loaded (`ScopeUrlSyncGate`).
 */
function ReportChromeHost() {
  const runtime = useMultiFiltersRuntimeStore((s) => s.runtime)
  return runtime ? <runtime.ReportChromeSlot /> : null
}

export default function AppLayout() {
  const mainRef = useRef<HTMLElement>(null)

  return (
    <SWRConfig value={LAYOUT_SWR_CONFIG}>
      {/* VIZ-306: the ONE mount of the report-scope URL sync. It resolves the
          `viz_multi_filters` flag for every other reader; with the flag off
          it does nothing else, and its implementation is never downloaded. */}
      <ScopeUrlSyncGate />
      <div className="flex h-screen overflow-hidden bg-[var(--color-bg)]">
        <a
          href="#main-content"
          className="fixed left-4 top-4 z-[100] -translate-y-20 rounded-md bg-[var(--color-btn-primary-bg)] px-4 py-2 text-sm font-semibold text-[var(--color-btn-primary-text)] shadow-lg transition-transform focus:translate-y-0"
          onClick={() => mainRef.current?.focus()}
        >
          Skip to main content
        </a>
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <TopBar />
          <DegradedBanner />
          <main id="main-content" ref={mainRef} tabIndex={-1} className="flex-1 overflow-auto">
            {/* Single source of truth for page width: a centered, capped column
                with responsive side gutters. Every routed page inherits this via
                <Outlet />, so pages stay w-full and must NOT re-cap or re-center
                (see PageShell). px scales 16→24→32→40 as the viewport grows;
                py-6 preserves the old p-6 vertical padding. */}
            <div className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 xl:px-10">
              <SettingsBackBar />
              {/* VIZ-301: the ONE mount of the report chrome (header, filter bar,
                  chips, summary, metrics strip). It renders nothing unless the
                  route is a registered report route and `viz_report_context` is
                  on. The page-level ChartAnnouncerProvider joins it here when the
                  first ChartFrame reaches a production page (Wave 2). */}
              <ReportChromeHost />
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </SWRConfig>
  )
}
