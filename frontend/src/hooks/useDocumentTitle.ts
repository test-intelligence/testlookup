import { createContext, useEffect, useLayoutEffect } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * The brand suffix every in-app tab title carries, and the standalone fallback
 * when a page has no title of its own. Kept short on purpose: this is the
 * per-tab label a self-hoster reads in a crowded browser window, not the
 * marketing tagline in `index.html` — a full "TestLookup — Instant Answers from
 * Your Test Results" repeated across ten open tabs is noise, not a label.
 */
export const BASE_DOCUMENT_TITLE = 'TestLookup'

/**
 * Compose the `document.title` for a page from its heading.
 *
 * Pure so the format is unit-testable without touching the DOM. A blank or
 * whitespace-only page title collapses to {@link BASE_DOCUMENT_TITLE} rather
 * than rendering a stray "· TestLookup" with nothing before the separator.
 */
export function formatDocumentTitle(pageTitle?: string): string {
  const trimmed = pageTitle?.trim()
  return trimmed ? `${trimmed} · ${BASE_DOCUMENT_TITLE}` : BASE_DOCUMENT_TITLE
}

/**
 * Set the browser-tab title for the current page.
 *
 * Every routed page in the app renders through a single static
 * `<title>TestLookup — …</title>` from `index.html`, so before this hook every
 * tab read identically: a self-hoster with Failures, the Release Gate and the
 * Docs open in three tabs could not tell them apart, and every bookmark and
 * history entry carried the same label. Driving `document.title` off the page
 * heading gives each route a distinct, shareable tab title with no per-page
 * bookkeeping to keep in sync — the heading a page already shows IS its title.
 *
 * Deliberately does not restore a previous title on unmount: during a route
 * change the outgoing page unmounts before the incoming one mounts, so a
 * restore-on-unmount would flash the base title between every navigation. The
 * next page's heading overwrites it instead; a page with no heading simply
 * keeps the last title, which is a strictly better default than the old
 * always-identical one.
 */
export const DocumentTitleRouteKeyContext = createContext('')

export function useDocumentTitle(pageTitle?: string, routeKey = ''): void {
  useEffect(() => {
    if (typeof document === 'undefined') return
    document.title = formatDocumentTitle(pageTitle)
  }, [pageTitle, routeKey])
}

const ROUTE_TITLES: Record<string, string> = {
  '/login': 'Login', '/reset-password': 'Reset Password', '/overview': 'Dashboard',
  '/getting-started': 'Getting Started', '/docs': 'Documentation', '/value-metrics': 'Value Metrics',
  '/intelligence': 'Intelligence', '/runs': 'Runs', '/coverage': 'Coverage', '/coverage/suite': 'Suite Coverage',
  '/suites': 'Suites', '/failures': 'Failure Analysis', '/trends': 'Trends', '/defects': 'Defects',
  '/search': 'Search', '/chat': 'Ask AI', '/agents': 'Agents', '/deep-investigate': 'Deep Investigation',
  '/release-gate': 'Release Gate', '/flaky-coach': 'Flaky Coach', '/quarantine': 'Quarantine', '/reviews': 'Review Queue',
  '/test-management': 'Test Management', '/live': 'Live Execution', '/my-failures': 'My Failures',
  '/reports/summary': 'Summary Report', '/settings/profile': 'Profile', '/projects': 'Projects',
  '/releases': 'Releases', '/users': 'Users', '/settings': 'Settings',
  '/settings/notifications': 'Notifications', '/settings/ai': 'AI Configuration',
  '/settings/integrations': 'Integrations', '/settings/storage': 'Storage', '/settings/sso': 'Single Sign-On',
  '/settings/digests': 'Digests', '/settings/integration-health': 'Integration Health',
  '/settings/audit': 'Audit', '/settings/ai-eval': 'AI Evaluation', '/settings/performance': 'Performance',
  '/settings/seed-data': 'Seed Data', '/settings/feature-flags': 'Feature Flags', '/settings/billing': 'Billing',
  '/settings/github': 'GitHub Integration', '/settings/gitlab': 'GitLab Integration',
  '/settings/webhooks': 'Outbound Webhooks', '/settings/api-keys': 'API Keys',
  '/settings/project-data': 'Project Data', '/settings/retention': 'Retention',
  '/settings/mfa-policy': 'MFA Policy', '/settings/ai-agents': 'AI Agents',
  '/settings/agent-activity': 'Agent Activity', '/policies': 'Policies', '/ownership': 'Ownership',
}

export function routeDocumentTitle(pathname: string): string {
  const path = pathname.length > 1 ? pathname.replace(/\/$/, '') : pathname
  if (ROUTE_TITLES[path]) return ROUTE_TITLES[path]
  if (/^\/docs\/[^/]+$/.test(path)) return 'Documentation'
  if (path === '/runs/compare') return 'Run Comparison'
  if (/^\/runs\/[^/]+\/intelligence$/.test(path)) return 'Run Intelligence'
  if (/^\/runs\/[^/]+\/tests\/[^/]+$/.test(path)) return 'Test Case'
  if (/^\/runs\/[^/]+$/.test(path)) return 'Run Details'
  if (/^\/suites\/[^/]+$/.test(path)) return 'Suite Details'
  if (/^\/canonical-test-cases\/[^/]+$/.test(path)) return 'Canonical Test Case'
  if (/^\/agents\/run\/[^/]+$/.test(path)) return 'Agent Run'
  if (/^\/deep-investigate\/[^/]+$/.test(path)) return 'Deep Investigation'
  if (/^\/release-gate\/[^/]+$/.test(path)) return 'Release Gate'
  if (path === '/policies/new') return 'New Policy'
  if (/^\/policies\/[^/]+$/.test(path)) return 'Policy Editor'
  return BASE_DOCUMENT_TITLE
}

/** Establish a title for every route before paint; page headings may refine it. */
export function useRouteDocumentTitle(): string {
  const { pathname } = useLocation()
  useLayoutEffect(() => {
    if (typeof document === 'undefined') return
    document.title = formatDocumentTitle(routeDocumentTitle(pathname))
  }, [pathname])
  return pathname
}
