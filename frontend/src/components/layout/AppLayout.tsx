import { useRef } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import DegradedBanner from './DegradedBanner'

export default function AppLayout() {
  const mainRef = useRef<HTMLElement>(null)

  return (
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
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
