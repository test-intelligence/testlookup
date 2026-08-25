import { type ComponentType, lazy as reactLazy, Suspense, type LazyExoticComponent } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from '@/components/layout/AppLayout'
import ProtectedRoute from '@/components/auth/ProtectedRoute'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { useWebVitals } from '@/hooks/useWebVitals'
import { usePermissions } from '@/hooks/usePermissions'
import LoginPage from '@/pages/LoginPage'
import ResetPasswordPage from '@/pages/ResetPasswordPage'

/**
 * Wrap React.lazy so a stale-chunk failure auto-reloads the page.
 *
 * After a deploy, the user's open tab still has the OLD index.html in
 * memory which references chunk filenames like ``DefectsPage-urxoen35.js``.
 * Vite generates fresh content-hashed names on every build, so when the
 * user navigates to a not-yet-loaded route, the chunk 404s with
 * "Failed to fetch dynamically imported module". Without this wrapper
 * the user sees an "ErrorBoundary" page on every cross-deploy navigation.
 *
 * Strategy: on the first import failure of the session, reload
 * ``window.location`` so the browser fetches a fresh index.html with
 * the current chunk hashes. The sessionStorage flag guards against an
 * infinite reload loop in case the failure isn't deploy-related (e.g.
 * permanent network issue).
 */
const CHUNK_RELOAD_FLAG = '__testlookup_chunk_reload_attempted'

function lazyWithRetry<T extends ComponentType<unknown>>(
  importFn: () => Promise<{ default: T }>,
): LazyExoticComponent<T> {
  return reactLazy(async () => {
    try {
      const mod = await importFn()
      // Successful load — clear the flag so a future stale-chunk
      // error gets its own one-shot reload chance.
      try { sessionStorage.removeItem(CHUNK_RELOAD_FLAG) } catch { /* noop */ }
      return mod
    } catch (err) {
      const alreadyTried = (() => {
        try { return sessionStorage.getItem(CHUNK_RELOAD_FLAG) === '1' } catch { return false }
      })()
      if (!alreadyTried) {
        try { sessionStorage.setItem(CHUNK_RELOAD_FLAG, '1') } catch { /* noop */ }
        // ``location.reload()`` fetches a fresh index.html — which is
        // served with ``Cache-Control: no-cache`` so the browser gets
        // the new chunk hashes immediately.
        window.location.reload()
        // Halt the promise chain — the reload will replace the page
        // before this never-resolving promise settles.
        return await new Promise<never>(() => undefined)
      }
      throw err
    }
  })
}

// Re-export under the original name so the existing ``lazy(() => import(...))``
// call sites below pick up the retry behaviour without per-site edits.
const lazy = lazyWithRetry as typeof reactLazy

const OverviewPage = lazy(() => import('@/pages/OverviewPage'))
const RunsPage = lazy(() => import('@/pages/RunsPage'))
const RunDetailPage = lazy(() => import('@/pages/RunDetailPage'))
const TestCasePage = lazy(() => import('@/pages/TestCasePage'))
const CoveragePage = lazy(() => import('@/pages/CoveragePage'))
const SuiteDetailPage = lazy(() => import('@/pages/SuiteDetailPage'))
const SuitesPage = lazy(() => import('@/pages/SuitesPage'))
const SuiteCasesPage = lazy(() => import('@/pages/SuiteCasesPage'))
const CanonicalDetailPage = lazy(() => import('@/pages/CanonicalDetailPage'))
const FailureAnalysisPage = lazy(() => import('@/pages/FailureAnalysisPage'))
const TrendsPage = lazy(() => import('@/pages/TrendsPage'))
const DefectsPage = lazy(() => import('@/pages/DefectsPage'))
const SearchPage = lazy(() => import('@/pages/SearchPage'))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const NotificationsPage = lazy(() => import('@/pages/settings/NotificationsPage'))
const AIConfigPage = lazy(() => import('@/pages/settings/AIConfigPage'))
const IntegrationsSettingsPage = lazy(() => import('@/pages/settings/IntegrationsPage'))
const StoragePage = lazy(() => import('@/pages/settings/StoragePage'))
const DigestsPage = lazy(() => import('@/pages/settings/DigestsPage'))
const IntegrationHealthPage = lazy(() => import('@/pages/settings/IntegrationHealthPage'))
const AuditDashboardPage = lazy(() => import('@/pages/settings/AuditDashboardPage'))
const AIEvalDashboardPage = lazy(() => import('@/pages/settings/AIEvalDashboardPage'))
const PerformancePage = lazy(() => import('@/pages/settings/PerformancePage'))
const SSOSettingsPage = lazy(() => import('@/pages/settings/SSOSettingsPage'))
// /agents loads the Direction-C compute graph (see AgentStatusPage.tsx +
// components/agents/computeGraph/*). The Subway-style AgentWorkflowPage.tsx
// stays on disk as reference for Direction A; flipping this import is the
// only switch needed to swap between the two designs.
const AgentStatusPage = lazy(() => import('@/pages/AgentStatusPage'))
const DeepInvestigationPage = lazy(() => import('@/pages/DeepInvestigationPage'))
const ReleaseGatePage = lazy(() => import('@/pages/ReleaseGatePage'))
const RunIntelligencePage = lazy(() => import('@/pages/RunIntelligencePage'))
const TestManagementPage = lazy(() => import('@/pages/TestManagementPage'))
const LiveExecutionPage = lazy(() => import('@/pages/LiveExecutionPage'))
const ReleasesPage = lazy(() => import('@/pages/ReleasesPage'))
const UserManagementPage = lazy(() => import('@/pages/UserManagementPage'))
const FlakyCoachPage = lazy(() => import('@/pages/FlakyCoachPage'))
const IntelligenceHubPage = lazy(() => import('@/pages/IntelligenceHubPage'))
const OnboardingPage = lazy(() => import('@/pages/OnboardingPage'))
const DocsPage = lazy(() => import('@/pages/DocsPage'))
const ValueMetricsPage = lazy(() => import('@/pages/ValueMetricsPage'))
const PolicyEditorPage = lazy(() => import('@/pages/PolicyEditorPage'))
const OwnershipEditorPage = lazy(() => import('@/pages/OwnershipEditorPage'))
const ProfilePage = lazy(() => import('@/pages/settings/ProfilePage'))
const SeedDataPage = lazy(() => import('@/pages/settings/SeedDataPage'))
const FeatureFlagsPage = lazy(() => import('@/pages/settings/FeatureFlagsPage'))
const BillingPage = lazy(() => import('@/pages/settings/BillingPage'))
const QuarantinePage = lazy(() => import('@/pages/QuarantinePage'))
const GitHubIntegrationPage = lazy(() => import('@/pages/settings/GitHubIntegrationPage'))
const GitLabIntegrationPage = lazy(() => import('@/pages/settings/GitLabIntegrationPage'))
const OutboundWebhooksPage = lazy(() => import('@/pages/settings/OutboundWebhooksPage'))
const ApiKeysPage = lazy(() => import('@/pages/settings/ApiKeysPage'))
const ProjectDataPage = lazy(() => import('@/pages/settings/ProjectDataPage'))
const RetentionPage = lazy(() => import('@/pages/settings/RetentionPage'))
const MfaPolicyPage = lazy(() => import('@/pages/settings/MfaPolicyPage'))
const AIAgentsPage = lazy(() => import('@/pages/settings/AIAgentsPage'))
const AgentActivityPage = lazy(() => import('@/pages/settings/AgentActivityPage'))
const RunComparePage = lazy(() => import('@/pages/RunComparePage'))
const MyFailuresPage = lazy(() => import('@/pages/MyFailuresPage'))
const SummaryReportPage = lazy(() => import('@/pages/SummaryReportPage'))
const ChatPage = lazy(() => import('@/pages/ChatPage'))

type AppRoute = {
  path: string
  component: ComponentType
}

const appRoutes: AppRoute[] = [
  { path: 'overview', component: OverviewPage },
  { path: 'getting-started', component: OnboardingPage },
  { path: 'docs', component: DocsPage },
  // Deep link to one documentation topic. Without this, /docs/flaky 404s and
  // no section of the guide can be linked to from an issue, a chat message or
  // another page.
  { path: 'docs/:docId', component: DocsPage },
  { path: 'value-metrics', component: ValueMetricsPage },
  { path: 'intelligence', component: IntelligenceHubPage },
  { path: 'runs', component: RunsPage },
  { path: 'runs/compare', component: RunComparePage },
  { path: 'runs/:runId', component: RunDetailPage },
  { path: 'runs/:runId/intelligence', component: RunIntelligencePage },
  { path: 'runs/:runId/tests/:testId', component: TestCasePage },
  { path: 'coverage', component: CoveragePage },
  { path: 'coverage/suite', component: SuiteDetailPage },
  { path: 'suites', component: SuitesPage },
  { path: 'suites/:suiteId', component: SuiteCasesPage },
  { path: 'canonical-test-cases/:canonicalId', component: CanonicalDetailPage },
  { path: 'failures', component: FailureAnalysisPage },
  { path: 'trends', component: TrendsPage },
  { path: 'defects', component: DefectsPage },
  { path: 'search', component: SearchPage },
  // Ask-AI chat (US-2.1). The Sidebar entry is gated on the ask_ai_chat
  // feature flag + a non-rules AI mode; the route itself stays registered so
  // a direct URL renders the page's own "switch mode" guidance instead of 404.
  { path: 'chat', component: ChatPage },
  { path: 'agents', component: AgentStatusPage },
  { path: 'agents/run/:runId', component: AgentStatusPage },
  { path: 'deep-investigate', component: DeepInvestigationPage },
  { path: 'deep-investigate/:runId', component: DeepInvestigationPage },
  { path: 'release-gate', component: ReleaseGatePage },
  { path: 'release-gate/:runId', component: ReleaseGatePage },
  { path: 'flaky-coach', component: FlakyCoachPage },
  { path: 'quarantine', component: QuarantinePage },
  { path: 'test-management', component: TestManagementPage },
  { path: 'live', component: LiveExecutionPage },
  { path: 'my-failures', component: MyFailuresPage },
  { path: 'reports/summary', component: SummaryReportPage },
  // Profile is accessible to ALL authenticated roles
  { path: 'settings/profile', component: ProfilePage },
]

/** Routes restricted to QA_LEAD and ADMIN roles. */
const managementRoutes: AppRoute[] = [
  { path: 'projects', component: ProjectsPage },
  { path: 'releases', component: ReleasesPage },
  { path: 'users', component: UserManagementPage },
  { path: 'settings', component: SettingsPage },
  { path: 'settings/notifications', component: NotificationsPage },
  { path: 'settings/ai', component: AIConfigPage },
  { path: 'settings/integrations', component: IntegrationsSettingsPage },
  { path: 'settings/storage', component: StoragePage },
  { path: 'settings/sso', component: SSOSettingsPage },
  { path: 'settings/digests', component: DigestsPage },
  { path: 'settings/integration-health', component: IntegrationHealthPage },
  { path: 'settings/audit', component: AuditDashboardPage },
  { path: 'settings/ai-eval', component: AIEvalDashboardPage },
  { path: 'settings/performance', component: PerformancePage },
  { path: 'settings/seed-data', component: SeedDataPage },
  { path: 'settings/feature-flags', component: FeatureFlagsPage },
  { path: 'settings/billing', component: BillingPage },
  { path: 'settings/github', component: GitHubIntegrationPage },
  { path: 'settings/gitlab', component: GitLabIntegrationPage },
  { path: 'settings/webhooks', component: OutboundWebhooksPage },
  { path: 'settings/api-keys', component: ApiKeysPage },
  { path: 'settings/project-data', component: ProjectDataPage },
  { path: 'settings/retention', component: RetentionPage },
  { path: 'settings/mfa-policy', component: MfaPolicyPage },
  { path: 'settings/ai-agents', component: AIAgentsPage },
  { path: 'settings/agent-activity', component: AgentActivityPage },
  { path: 'policies', component: PolicyEditorPage },
  // NO static 'policies/new' route. React Router ranks a static segment
  // above a dynamic one, so registering it captured /policies/new with
  // NO policyId param — and PolicyEditorPage derives
  // `listMode = isNew && !policyId`, so the page rendered the LIST.
  // "New Policy" navigated to /policies/new and landed back on the list,
  // making policy creation unreachable through the UI. The component is
  // written for `policyId === 'new'`, which only this dynamic route
  // produces.
  { path: 'policies/:policyId', component: PolicyEditorPage },
  { path: 'ownership', component: OwnershipEditorPage },
]

function RouteFallback() {
  return (
    <div className="flex h-64 items-center justify-center">
      <LoadingSpinner size="lg" />
    </div>
  )
}

function RouteErrorFallback({ error }: { error: Error }) {
  // Detect the "stale chunk after deploy" case so we render a clear
  // explanation instead of the generic message. The ``lazyWithRetry``
  // wrapper above already auto-reloads on first occurrence; if we
  // reach this fallback it means the reload already fired and the
  // chunk is STILL missing — usually a network blip, not a deploy.
  const isStaleChunk = (() => {
    const msg = error.message || ''
    return (
      msg.includes('Failed to fetch dynamically imported module') ||
      msg.includes('Loading chunk') ||
      msg.includes('Loading CSS chunk') ||
      /import\(\)/i.test(msg)
    )
  })()
  return (
    <div className="mx-auto max-w-2xl p-8">
      <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">
        {isStaleChunk ? 'This page is out of date.' : 'Something went wrong loading this page.'}
      </h2>
      <p className="mb-4 text-sm text-[var(--color-text-secondary)]">
        {isStaleChunk ? (
          <>
            The site was redeployed while your tab was open and one of the
            page modules couldn’t be fetched. Reloading will pick up the
            latest version.
          </>
        ) : (
          error.message || 'An unexpected error occurred. Try navigating back or refreshing.'
        )}
      </p>
      <button
        type="button"
        onClick={() => {
          // Clear the chunk-reload guard so the reload is treated as
          // a fresh chance, not a retry against the same broken state.
          try { sessionStorage.removeItem(CHUNK_RELOAD_FLAG) } catch { /* noop */ }
          window.location.reload()
        }}
        className="rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-sm hover:bg-[var(--color-bg-hover)]"
      >
        Reload
      </button>
    </div>
  )
}

function renderLazyRoute(Component: ComponentType) {
  // ErrorBoundary wraps each lazy route so a render-phase throw in one page
  // cannot blank the whole app shell — the rest of the navigation stays live.
  return (
    <ErrorBoundary fallback={(error) => <RouteErrorFallback error={error} />}>
      <Suspense fallback={<RouteFallback />}>
        <Component />
      </Suspense>
    </ErrorBoundary>
  )
}

/**
 * Route guard for management pages (Projects, Releases, Users).
 * Redirects users without QA_LEAD or ADMIN role to the overview page.
 */
function ManagementGuard({ children }: { children: React.ReactNode }) {
  const { canAccessManagement } = usePermissions()

  if (!canAccessManagement) {
    return <Navigate to="/overview" replace />
  }

  return <>{children}</>
}

export default function App() {
  useWebVitals()

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/*" element={<AppLayout />}>
          <Route index element={<Navigate to="/overview" replace />} />
          {appRoutes.map(({ path, component }) => (
            <Route key={path} path={path} element={renderLazyRoute(component)} />
          ))}
          {managementRoutes.map(({ path, component: Component }) => (
            <Route
              key={path}
              path={path}
              element={
                <ManagementGuard>
                  {renderLazyRoute(Component)}
                </ManagementGuard>
              }
            />
          ))}
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  )
}
