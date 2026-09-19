/**
 * One consistent way back to Settings, for every `/settings/*` sub-page.
 *
 * BUG-009 (user-reported, 2026-09-19). The 23 settings sub-pages had **three**
 * different behaviours:
 *
 *   - 5 rendered a `btn-secondary` button labelled "Back" in `PageHeader`'s
 *     `actions` slot (AIConfig, Storage, FeatureFlags, GitHub, Integrations).
 *   - 4 rendered a small muted breadcrumb labelled "Settings" above the header
 *     (Billing, GitLab, Retention, OutboundWebhooks).
 *   - **14 rendered nothing at all** — the browser back button was the only way
 *     out of Profile, Notifications, SSO, Audit, API keys, Digests, MFA policy,
 *     Performance, Project data, Seed data, AI agents, AI eval, Agent activity
 *     and Integration health.
 *
 * This lives in `AppLayout` rather than in the pages, and that is the point. A
 * per-page control is a rule every future sub-page has to remember; 14 of 23
 * already did not. Rendering it from the layout, keyed off the route, means a
 * new `/settings/<thing>` route gets the affordance without its author doing
 * anything — the inconsistency cannot come back one page at a time.
 *
 * The breadcrumb form won over the button because `PageHeader`'s `actions` slot
 * is where real page actions live (Save, Create, Rotate). A "Back" button there
 * competes with them for position and prominence, and on the pages that had
 * both it read as though it were one of the actions.
 */
import { ArrowLeft } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'

import { SETTINGS_ROOT, isSettingsSubPage } from './settingsRoutes'

export default function SettingsBackBar() {
  const { pathname } = useLocation()
  if (!isSettingsSubPage(pathname)) return null

  return (
    <nav aria-label="Breadcrumb" className="mb-4">
      <Link
        to={SETTINGS_ROOT}
        className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 rounded"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Back to Settings
      </Link>
    </nav>
  )
}
