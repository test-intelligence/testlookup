/**
 * Old URLs of pages the UX redesign P4 merged into one (never deleted: links in
 * bookmarks, issues, chat and the docs keep working). Each redirect keeps the
 * old URL's query string and adds the tab the content now lives under.
 */
import { Navigate, useLocation, useParams } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useSuites } from '@/hooks/useSuites'
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
 * `/coverage/suite?name=X` → the suite's own page, Charts tab. The old page was
 * keyed by name; the suite page by id, so the name is looked up in the
 * project's suites. An unknown (or missing) name goes to the suites list
 * rather than a blank page; any other query parameter is kept.
 */
export function SuiteByNameRedirect() {
  const { search } = useLocation()
  const name = new URLSearchParams(search).get('name')
  const { data, error, isLoading } = useSuites()
  if (name && isLoading && !data) return <LoadingSpinner size="lg" />
  const suite = name && !error ? data?.items.find((s) => s.name === name) : undefined
  const rest = new URLSearchParams(search)
  rest.delete('name')
  if (!suite) return <Navigate to={`/suites${rest.toString() ? `?${rest}` : ''}`} replace />
  rest.set('tab', 'charts')
  return <Navigate to={`/suites/${suite.id}?${rest}`} replace />
}
