/**
 * Old URLs of pages the UX redesign P4 merged into one (never deleted: links in
 * bookmarks, issues, chat and the docs keep working). Each redirect keeps the
 * old URL's query string and adds the tab the content now lives under.
 */
import { Navigate, useLocation, useParams } from 'react-router-dom'
import NotFoundPage from '@/pages/NotFoundPage'
import { withParams } from './withParams'

function To({ pathname, extra = {} }: { pathname: string; extra?: Record<string, string> }) {
  const { search, hash } = useLocation()
  return <Navigate to={`${pathname}${withParams(search, extra)}${hash}`} replace />
}

/** `/runs/:runId/intelligence` → the run's Analysis tab. */
export function RunIntelligenceRedirect() {
  const { runId = '' } = useParams()
  return <To pathname={`/runs/${runId}`} extra={{ tab: 'analysis' }} />
}

/** `/deep-investigate/:runId` → the run's Analysis tab (Deep Investigation's clusters live there). */
export function DeepInvestigationRunRedirect() {
  const { runId = '' } = useParams()
  return <To pathname={`/runs/${runId}`} extra={{ tab: 'analysis' }} />
}

/** `/agents/run/:runId` → the run's Evidence tab. */
export function AgentRunRedirect() {
  const { runId = '' } = useParams()
  return <To pathname={`/runs/${runId}`} extra={{ tab: 'evidence' }} />
}

/** `/intelligence` → Runs, whose table carries the AI verdict (D2). */
export function IntelligenceRedirect() {
  return <To pathname="/runs" />
}

/** `/flaky-coach` → Flaky tests (Detected is its default tab). */
export function FlakyCoachRedirect() {
  return <To pathname="/flaky" />
}

/** `/quarantine` → Flaky tests › Quarantined. */
export function QuarantineRedirect() {
  return <To pathname="/flaky" extra={{ tab: 'quarantined' }} />
}

/** `/reviews` → Inbox › Approvals (D4). */
export function ReviewsRedirect() {
  return <To pathname="/my-failures" extra={{ tab: 'approvals' }} />
}

/**
 * Paths that were LINKED but never routed, and where they now go (HON-09).
 * `/audit` was the /releases rail's "View full audit log" href: the old
 * catch-all sent it to Overview, so bookmarks of it exist. The release events
 * it meant are the Activity feed's Release category.
 *
 * Kept here rather than in App.tsx's route tables on purpose: a table entry is
 * a page every route ratchet (sweeps, route scope, report routes) would have
 * to classify, and this is only a forwarding address.
 */
const UNROUTED_LINKS: Record<string, { pathname: string; extra: Record<string, string> }> = {
  '/audit': { pathname: '/activity', extra: { category: 'release' } },
}

/**
 * The in-shell catch-all (`<Route path="*">` inside `AppLayout`): a known
 * forwarding address redirects, keeping the query string as the other
 * redirects here do; anything else is the not-found page, with the URL left
 * as typed. It used to be `<Navigate to="/overview" replace />` for every
 * unknown path.
 */
export function UnmatchedPath() {
  const { pathname } = useLocation()
  const key = pathname.length > 1 ? pathname.replace(/\/+$/, '').toLowerCase() : pathname
  const forward = UNROUTED_LINKS[key]
  if (forward) return <To pathname={forward.pathname} extra={forward.extra} />
  return <NotFoundPage />
}

// `/coverage/suite?name=X` is `SuiteByNameRedirect.tsx` (lazy: it fetches the suites).
