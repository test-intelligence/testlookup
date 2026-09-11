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
 *     until it is moved out of the list, and the list may not grow;
 *   - the other two labels are checked too, or they were a way around the
 *     guard (re-audit QA-B45-4): a 'no-fetch' page may not call SWR, a data
 *     hook (a hook whose own source calls SWR), `api.get` or a service read;
 *     an 'own-error-ui' page must bind an error from its data source (a
 *     hook's `error`/`isError`, or the error state its own fetch's `catch`
 *     sets) AND branch on or render it.
 *
 * The 'own-error-ui' check is necessary, not sufficient: it proves an error
 * from a data hook or a catch is shown, not that it is the PRIMARY fetch's
 * error. ChatPage shows useChat's send error while its session and message
 * fetches drop theirs, and would pass if relabelled (mutation F04 in the
 * b45 r1 fix survives for this reason). A per-hook "does it fetch" test does
 * not settle it either: useChat fetches, and several hooks that fetch go
 * through wrappers a static scan misreads. Behaviour is covered by
 * outageRendersAsNoData.lists.test.tsx; relabelling stays a reviewed change.
 *
 * Sources are read with import.meta.glob (not node:fs), like the route-scope
 * registry, so the guard sees exactly what Vite would bundle.
 */
import { describe, expect, it } from 'vitest'

import appSource from '../App.tsx?raw'

const pageSources = import.meta.glob<string>('./**/*.tsx', { query: '?raw', import: 'default', eager: true })
const hookSources = import.meta.glob<string>('../hooks/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true })

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

// Tolerant of spacing and quote style: a route written differently must not
// fall out of the guard.
const routedPages = Array.from(
  appSource.matchAll(/lazy\(\s*\(\)\s*=>\s*import\(\s*['"`]@\/pages\/([^'"`]+)['"`]\s*\)\s*\)/g),
  (m) => m[1],
)

/** Hooks that fetch: their own source calls SWR. */
export const DATA_HOOKS = new Set(
  Object.entries(hookSources)
    .filter(([path, src]) => !/\.test\.tsx?$/.test(path) && /\buseSWR(?:Infinite|Immutable)?\s*[<(]/.test(src))
    .flatMap(([, src]) => Array.from(src.matchAll(/export\s+(?:default\s+)?(?:async\s+)?(?:function|const)\s+(use[A-Z]\w*)/g), (m) => m[1])),
)

const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Every way a page reads data; empty for a page that does not fetch. */
export function dataReads(source: string): string[] {
  const reads: string[] = []
  for (const m of source.matchAll(/\b(?:useSWR(?:Infinite|Immutable)?\s*[<(]|api\.get\s*[<(]|\w+Service\.(?:get|list|fetch|search|load)\w*\s*\()/g)) {
    reads.push(m[0])
  }
  for (const m of source.matchAll(/import\s+(\w+)?\s*,?\s*(?:\{([^}]*)\})?\s*from\s*['"]@\/hooks\/[^'"]+['"]/g)) {
    const names = [m[1], ...(m[2] ?? '').split(',')].filter(Boolean).map((n) => n.trim())
    for (const name of names) {
      const [imported, local = imported] = name.split(/\s+as\s+/).map((n) => n.trim())
      if (DATA_HOOKS.has(imported) && new RegExp(`\\b${escapeRe(local)}\\s*[<(]`).test(source)) reads.push(local)
    }
  }
  return reads
}

/** Error values the page binds from its data source. */
function boundErrors(source: string): string[] {
  const names: string[] = []
  // const { data, error, isError: failed } = useThing(...)   (multi-line too)
  for (const m of source.matchAll(/(?:const|let)\s*\{([^}]*)\}\s*=\s*use[A-Z]\w*\s*[<(]/g)) {
    for (const part of m[1].split(',')) {
      const [key, alias] = part.split(':').map((s) => s.trim())
      if (key === 'error' || key === 'isError') names.push(alias || key)
    }
  }
  // const runs = useRuns(...) ... runs.error
  for (const m of source.matchAll(/(?:const|let)\s+(\w+)\s*=\s*use[A-Z]\w*\s*[<(]/g)) {
    for (const field of ['error', 'isError']) {
      if (new RegExp(`\\b${escapeRe(m[1])}\\.${field}\\b`).test(source)) names.push(`${m[1]}.${field}`)
    }
  }
  // const [loadError, setLoadError] = useState(...) set inside a catch
  for (const m of source.matchAll(/const\s*\[\s*(\w*[eE]rror\w*)\s*,\s*(set\w+)\s*\]\s*=\s*useState/g)) {
    // set inside a `catch {}` block, or a promise's `.catch(() => ...)`
    const setter = escapeRe(m[2])
    const caught = new RegExp(
      `(?:catch\\s*(?:\\([^)]*\\))?\\s*\\{[^}]*|\\.catch\\(\\s*(?:\\([^)]*\\)|\\w+)?\\s*=>\\s*\\{?[^}]*?)\\b${setter}\\(`,
    )
    if (caught.test(source)) names.push(m[1])
  }
  return names
}

/** Whether a bound error is branched on or rendered, not merely bound. */
export function hasOwnErrorBranch(source: string): boolean {
  return boundErrors(source).some((name) => {
    const n = escapeRe(name)
    return new RegExp(
      `(?:\\{\\s*!?\\s*${n}\\s*(?:&&|\\?|\\|\\||\\})|\\b${n}\\s*(?:&&|\\?(?!\\.))|if\\s*\\(\\s*!?\\s*${n}\\b|${n}\\s*\\)\\s*(?:\\{|return)|Boolean\\(\\s*${n}\\s*\\)|!!\\s*${n}\\b)`,
    ).test(source)
  })
}

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

  it("'no-fetch' pages really read no data", () => {
    const fetching = routedPages
      .filter((p) => REVIEWED[p] === 'no-fetch')
      .map((p) => [p, dataReads(sourceOf(p) ?? '')] as const)
      .filter(([, reads]) => reads.length > 0)
    expect(fetching, 'These pages read data: render <DataUnavailable> or classify them honestly.').toEqual([])
  })

  it("'own-error-ui' pages really bind and show their fetch's error", () => {
    const silent = routedPages.filter((p) => REVIEWED[p] === 'own-error-ui' && !hasOwnErrorBranch(sourceOf(p) ?? ''))
    expect(silent, 'No error from the data source is shown: these pages are known-silent.').toEqual([])
  })

  it('the label checks are not vacuous', () => {
    expect(DATA_HOOKS.size).toBeGreaterThan(5)
    expect(DATA_HOOKS.has('useRuns')).toBe(true)
    expect(dataReads("import { useRuns } from '@/hooks/useRuns'\nconst { data } = useRuns(1)")).toEqual(['useRuns'])
    expect(dataReads("const { data } = useSWR('/x', f)")).toHaveLength(1)
    expect(dataReads('await projectsService.list()')).toHaveLength(1)
    expect(dataReads("await api.patch('/auth/me', body)")).toEqual([])
    expect(hasOwnErrorBranch('const { data, error } = useRuns(1)\nif (error) return <Alert />')).toBe(true)
    expect(hasOwnErrorBranch('const { data, error } = useRuns(1)\nreturn <List />')).toBe(false)
    expect(hasOwnErrorBranch('const [loadError, setLoadError] = useState(null)\ntry { x() } catch (e) { setLoadError(e) }\n{loadError && <p />}')).toBe(true)
    expect(hasOwnErrorBranch("const [error, setError] = useState(null)\nload().catch(() => setError('x'))\nif (error) return <p />")).toBe(true)
    expect(hasOwnErrorBranch('const { error: statusError } = useStatus()\n<Card failed={Boolean(statusError)} />')).toBe(true)
    expect(hasOwnErrorBranch('const [error, setError] = useState(null)\nif (error) return <p />')).toBe(false)
    // Every lazily routed page is still found (tolerant regex).
    expect(routedPages.length).toBeGreaterThan(50)
  })
})
