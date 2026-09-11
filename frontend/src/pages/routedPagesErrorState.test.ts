/**
 * M21 guard: every routed page has a reviewed answer to "what does a failed
 * fetch look like here?"
 *
 * The audit found DataUnavailable on 4 of ~60 routed pages; the rest could
 * render an outage as "no data" once the 4-second error toast faded. Fixing
 * all of them is a backlog, so this is a ratchet:
 *
 *   - every lazily routed page in App.tsx must be classified below (a NEW
 *     page fails here until someone decides how it shows a failed fetch);
 *   - 'data-unavailable' pages must actually render DataUnavailable;
 *   - 'known-silent' is the backlog (from the b45 enumeration). It may only
 *     shrink: a listed page that starts rendering DataUnavailable fails here
 *     until it is moved out of the list, and the list may not grow.
 *
 * Sources are read with import.meta.glob (not node:fs), like the route-scope
 * registry, so the guard sees exactly what Vite would bundle.
 */
import { describe, expect, it } from 'vitest'

import appSource from '../App.tsx?raw'

const pageSources = import.meta.glob<string>('./**/*.tsx', { query: '?raw', import: 'default', eager: true })

type ErrorState =
  /** Renders <DataUnavailable> for its primary fetch. */
  | 'data-unavailable'
  /** Reviewed: has its own visible error branch (alert, retry, message). */
  | 'own-error-ui'
  /** Reviewed: no primary data fetch (static, or mutations only). */
  | 'no-fetch'
  /** Backlog: a failed primary fetch renders as empty, blank, or "not found". */
  | 'known-silent'

const REVIEWED: Record<string, ErrorState> = {
  OverviewPage: 'data-unavailable',
  CoveragePage: 'data-unavailable',
  TrendsPage: 'data-unavailable',
  DefectsPage: 'data-unavailable',
  RunsPage: 'data-unavailable',
  IntelligenceHubPage: 'data-unavailable',
  ReleasesPage: 'data-unavailable',
  SummaryReportPage: 'data-unavailable',

  ActivityPage: 'own-error-ui',
  SuitesPage: 'own-error-ui',
  SuiteDetailPage: 'own-error-ui',
  RunIntelligencePage: 'own-error-ui',
  OwnershipEditorPage: 'own-error-ui',
  QuarantinePage: 'own-error-ui',
  MyFailuresPage: 'own-error-ui',
  'settings/AIConfigPage': 'own-error-ui',
  'settings/DigestsPage': 'own-error-ui',
  'settings/SSOSettingsPage': 'own-error-ui',
  'settings/SeedDataPage': 'own-error-ui',
  'settings/FeatureFlagsPage': 'own-error-ui',
  'settings/BillingPage': 'own-error-ui',
  'settings/GitLabIntegrationPage': 'own-error-ui',
  'settings/RetentionPage': 'own-error-ui',
  'settings/MfaPolicyPage': 'own-error-ui',
  'settings/AIAgentsPage': 'own-error-ui',
  'settings/AgentActivityPage': 'own-error-ui',

  SettingsPage: 'no-fetch',
  DocsPage: 'no-fetch',
  'settings/ProfilePage': 'no-fetch',
  'settings/ProjectDataPage': 'no-fetch',

  // Backlog. Ranked by impact in docs/reaudit_reviews/b45_m21_enumeration.md.
  FailureAnalysisPage: 'known-silent',
  TestManagementPage: 'known-silent',
  LiveExecutionPage: 'known-silent', // useLiveExecution drops the SWR error
  AgentStatusPage: 'known-silent',
  DeepInvestigationPage: 'known-silent',
  ProjectsPage: 'known-silent',
  SearchPage: 'known-silent',
  FlakyCoachPage: 'known-silent',
  ValueMetricsPage: 'known-silent',
  UserManagementPage: 'known-silent',
  OnboardingPage: 'known-silent', // useOnboardingStatus drops the SWR error
  SuiteCasesPage: 'known-silent',
  ChatPage: 'known-silent',
  RunComparePage: 'known-silent', // run picker only; the compare body is handled
  RunDetailPage: 'known-silent', // useRun drops its error; the header vanishes
  TestCasePage: 'known-silent', // failure shows "not found"
  CanonicalDetailPage: 'known-silent', // failure shows "not found"
  ReleaseGatePage: 'known-silent', // failure shows "no decision found"
  PolicyEditorPage: 'known-silent', // policy failure shows "not found"
  'settings/NotificationsPage': 'known-silent',
  'settings/IntegrationsPage': 'known-silent',
  'settings/StoragePage': 'known-silent',
  'settings/IntegrationHealthPage': 'known-silent',
  'settings/AuditDashboardPage': 'known-silent',
  'settings/AIEvalDashboardPage': 'known-silent',
  'settings/PerformancePage': 'known-silent',
  'settings/GitHubIntegrationPage': 'known-silent',
  'settings/OutboundWebhooksPage': 'known-silent',
  'settings/ApiKeysPage': 'known-silent',
}

/** The backlog's size when this guard landed. Lower it as pages are fixed. */
const KNOWN_SILENT_CEILING = 29

const routedPages = Array.from(appSource.matchAll(/lazy\(\(\) => import\('@\/pages\/([^']+)'\)\)/g), (m) => m[1])

const sourceOf = (page: string) => pageSources[`./${page}.tsx`]
const rendersDataUnavailable = (page: string) =>
  /import DataUnavailable from '@\/components\/ui\/DataUnavailable'/.test(sourceOf(page) ?? '') &&
  /<DataUnavailable\b/.test(sourceOf(page) ?? '')

describe('routed pages show a failed fetch (M21)', () => {
  it('finds the routed pages', () => {
    expect(routedPages.length).toBeGreaterThan(50)
    for (const page of routedPages) expect(sourceOf(page), page).toBeTypeOf('string')
  })

  it('every routed page has a reviewed error state', () => {
    const unreviewed = routedPages.filter((page) => !(page in REVIEWED))
    expect(
      unreviewed,
      'New routed page(s): render <DataUnavailable> for the primary fetch, then classify them in REVIEWED.',
    ).toEqual([])
  })

  it("'data-unavailable' pages really render DataUnavailable", () => {
    const missing = routedPages.filter((p) => REVIEWED[p] === 'data-unavailable' && !rendersDataUnavailable(p))
    expect(missing).toEqual([])
  })

  it('the known-silent backlog only shrinks', () => {
    const silent = routedPages.filter((p) => REVIEWED[p] === 'known-silent')
    const fixedButListed = silent.filter(rendersDataUnavailable)
    expect(fixedButListed, 'Fixed: move these to data-unavailable and lower KNOWN_SILENT_CEILING.').toEqual([])
    expect(silent.length).toBeLessThanOrEqual(KNOWN_SILENT_CEILING)
  })

  it('has no stale entries', () => {
    expect(Object.keys(REVIEWED).filter((page) => !routedPages.includes(page))).toEqual([])
  })
})
