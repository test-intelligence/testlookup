import { useCallback, useRef, useState, useSyncExternalStore } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { SWRConfig, type SWRConfiguration } from 'swr'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import DegradedBanner from './DegradedBanner'
import SettingsBackBar from './SettingsBackBar'
import ScopeUrlSyncGate from './ScopeUrlSyncGate'
import { useMultiFiltersRuntimeStore } from './multiFiltersRuntimeLoader'
import { scopeSupersededMiddleware } from '@/services/scopeAbortCore'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'

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

/**
 * VIZ-106 (Wave 2.6, OD-9): below 1024 px the sidebar is a drawer. This is
 * Tailwind v4's own `max-lg:` query, so the elements that exist only in the
 * narrow shell (the menu button, the drawer's dialog role, its backdrop) come
 * and go at exactly the width where the `max-lg:` styling does. At 1024 px
 * and above the shell renders what it rendered before VIZ-106
 * (`AppLayout.desktop.test.tsx` holds that against origin/main's DOM).
 */
export const NARROW_SHELL_QUERY = '(width < 64rem)'

function subscribeNarrow(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const query = window.matchMedia(NARROW_SHELL_QUERY)
  query.addEventListener?.('change', onChange)
  return () => query.removeEventListener?.('change', onChange)
}

/** No matchMedia (jsdom, SSR): the desktop shell, the pre-VIZ-106 behaviour. */
function isNarrow(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(NARROW_SHELL_QUERY).matches
}

/** The drawer's id, for the menu button's `aria-controls`. */
export const SHELL_NAV_ID = 'app-shell-navigation'

export default function AppLayout() {
  const mainRef = useRef<HTMLElement>(null)
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, () => false)
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()

  // The drawer closes on every navigation (a link inside it, the browser's
  // back button, a redirect) and when the window widens past 1024 px, where
  // the sidebar is the permanent column again. Adjusted while rendering, so
  // no frame shows the drawer over the page it navigated to.
  const [seenLocation, setSeenLocation] = useState(location.key)
  if (seenLocation !== location.key) {
    setSeenLocation(location.key)
    if (navOpen) setNavOpen(false)
  }
  if (!narrow && navOpen) setNavOpen(false)

  const closeNav = useCallback(() => setNavOpen(false), [])
  const toggleNav = useCallback(() => setNavOpen((open) => !open), [])
  const drawerOpen = narrow && navOpen

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
        {/* Below 1024 px only, while the drawer is open: a click beside the
            drawer closes it. Not a control of its own (Escape and the
            drawer's close button are), so hidden from assistive tech. */}
        {drawerOpen && (
          <div
            /* not-a-dialog: the drawer panel (Sidebar) carries role="dialog" and aria-modal. */
            data-shell-backdrop=""
            aria-hidden="true"
            className="fixed inset-0 z-[45] bg-black/50 lg:hidden"
            onClick={closeNav}
          />
        )}
        <Sidebar drawer={narrow ? { open: navOpen, onClose: closeNav, id: SHELL_NAV_ID } : null} />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <TopBar navToggle={narrow ? { open: drawerOpen, onToggle: toggleNav, controls: SHELL_NAV_ID } : null} />
          <DegradedBanner />
          {/* `relative`: the scroll box is the containing block of the page's
              absolutely positioned descendants (every `sr-only` label, the chart
              announcer's live regions). Without it they are positioned against
              the document, which then scrolls past the shell into blank space. */}
          <main id="main-content" ref={mainRef} tabIndex={-1} className="relative flex-1 overflow-auto">
            {/* Single source of truth for page width: a centered, capped column
                with responsive side gutters. Every routed page inherits this via
                <Outlet />, so pages stay w-full and must NOT re-cap or re-center
                (see PageShell). px scales 16→24→32→40 as the viewport grows;
                py-6 preserves the old p-6 vertical padding. */}
            <div className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 xl:px-10">
              <SettingsBackBar />
              {/* VIZ-104 (Wave 2.5): the ONE page-level chart announcer, for
                  every routed page. Each ChartFrame reports its changes to it,
                  and the report chrome's filtered-dataset line (FilteredSummary)
                  coalesces with them into one polite message — so it wraps the
                  chrome AND the page. Without it a frame renders but never
                  announces. No routed page mounts a provider of its own (a
                  second one would be a second pair of live regions): the dev
                  gallery and report-context pages that do sit outside this
                  layout. `AppLayout.announcer.test.tsx` holds both facts. */}
              <ChartAnnouncerProvider>
                {/* VIZ-301: the ONE mount of the report chrome (header, filter bar,
                    chips, summary, metrics strip). It renders nothing unless the
                    route is a registered report route and `viz_report_context` is
                    on. */}
                <ReportChromeHost />
                <Outlet />
              </ChartAnnouncerProvider>
            </div>
          </main>
        </div>
      </div>
    </SWRConfig>
  )
}
