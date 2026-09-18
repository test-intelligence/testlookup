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
 *     guard (re-audit QA-B45-4, QA-B45-R2-4):
 *       - a 'no-fetch' page may not read data: SWR, a hook that fetches
 *         (imported by any path), `fetch(`, a read on api/apiClient/axios,
 *         a service call that is not a mutation, or a child component whose
 *         own source does any of those;
 *       - an 'own-error-ui' page must bind an error from a FETCHING source
 *         (a fetching hook's `error`/`isError`, or the error state its own
 *         fetch's `catch` sets) AND render it: JSX in the branch, or the
 *         error handed to a component. Logging it, an empty branch, or a
 *         branch in a comment or a string does not count.
 *   Comments are removed and string contents blanked before any of this is
 *   matched.
 *
 * What it still cannot see -- a static scan's limits, kept honest:
 *   - it proves an error from a fetching source is SHOWN, not that it is the
 *     PRIMARY fetch's error. ChatPage shows useChat's send error while its
 *     session and message fetches drop theirs, and would pass if relabelled
 *     (mutation F04 in the b45 r1 fix survives for this reason);
 *   - a fetching child is found one level down only (a child of a child is
 *     not read), and a hook that fetches through a wrapper the scan does not
 *     recognise is not a fetching hook;
 *   - "renders JSX" is not "renders something visible".
 * Behaviour is covered by outageRendersAsNoData.lists.test.tsx; relabelling
 * stays a reviewed change.
 *
 * Sources are read with import.meta.glob (not node:fs), like the route-scope
 * registry, so the guard sees exactly what Vite would bundle.
 */
import { describe, expect, it } from 'vitest'

import appSource from '../App.tsx?raw'

const pageSources = import.meta.glob<string>('./**/*.tsx', { query: '?raw', import: 'default', eager: true })
const hookSources = import.meta.glob<string>('../hooks/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true })
const componentSources = import.meta.glob<string>('../components/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
})

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
  ReviewsPage: 'data-unavailable',
  WorkflowEditorPage: 'data-unavailable',
  MyFailuresPage: 'own-error-ui',
  'settings/AIConfigPage': 'own-error-ui',
  'settings/DigestsPage': 'own-error-ui',
  'settings/SSOSettingsPage': 'own-error-ui',
  'settings/SeedDataPage': 'own-error-ui',
  'settings/FeatureFlagsPage': 'own-error-ui',
  'settings/BillingPage': 'data-unavailable',
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
  ValueMetricsPage: 'data-unavailable',
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
const KNOWN_SILENT_CEILING = 28

// Tolerant of spacing and quote style: a route written differently must not
// fall out of the guard.
const routedPages = Array.from(
  appSource.matchAll(/lazy\(\s*\(\)\s*=>\s*import\(\s*['"`]@\/pages\/([^'"`]+)['"`]\s*\)\s*\)/g),
  (m) => m[1],
)

const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// A quote opens a string only where an expression can start; after anything
// else it is JSX text ("Don't") and is left alone.
const EXPRESSION_BEFORE = /(?:[([{=,:;?&|+!\-*%<~^]|=>|\b(?:return|from|import|case|typeof|in|of|export|default|await|yield|else))$/

/**
 * ``source`` with comments removed and string/template contents blanked
 * (``keepStrings`` keeps the strings, for reading import paths).
 */
export function codeOf(source: string, { keepStrings = false }: { keepStrings?: boolean } = {}): string {
  let out = ''
  let i = 0
  while (i < source.length) {
    const c = source[i]
    if (c === '/' && source[i + 1] === '/') {
      while (i < source.length && source[i] !== '\n') i++
      continue
    }
    if (c === '/' && source[i + 1] === '*') {
      const end = source.indexOf('*/', i + 2)
      i = end < 0 ? source.length : end + 2
      out += ' '
      continue
    }
    if ((c === "'" || c === '"' || c === '`') && (out.trimEnd() === '' || EXPRESSION_BEFORE.test(out.trimEnd()))) {
      let j = i + 1
      while (j < source.length && source[j] !== c && (c === '`' || source[j] !== '\n')) j += source[j] === '\\' ? 2 : 1
      out += keepStrings ? source.slice(i, j + 1) : c + c
      i = j + 1
      continue
    }
    out += c
    i++
  }
  return out
}

// A service method that changes something is not a read.
const MUTATION_VERB =
  /^(?:create|update|delete|remove|post|put|patch|save|set|add|submit|send|trigger|run|start|stop|cancel|approve|reject|reset|upload|import|export|promote|revoke|rotate|toggle|enable|disable|mark|assign|unassign|archive|restore|register|login|logout|invite|resend|dismiss|acknowledge|ack|close|reopen|quarantine|release|test|verify|connect|disconnect|sync|generate|apply|clear|purge|seed)/i

/** Data reads a source makes itself, not through its imports. */
function directReads(code: string): string[] {
  const reads: string[] = []
  const patterns = [
    /\buseSWR(?:Infinite|Immutable)?\s*[<(]/g,
    /\bfetch\s*\(/g,
    /\b(?:api|apiClient|axios|http|httpClient)\.(?:get|head|request)\s*[<(]/g,
    /\baxios\s*\(/g,
  ]
  for (const pattern of patterns) for (const m of code.matchAll(pattern)) reads.push(m[0])
  for (const m of code.matchAll(/\b(\w+Service)\.(\w+)\s*\(/g)) {
    if (!MUTATION_VERB.test(m[2])) reads.push(`${m[1]}.${m[2]}(`)
  }
  return reads
}

/** Hooks that fetch: their own source reads data. */
export const DATA_HOOKS = new Set(
  Object.entries(hookSources)
    .filter(([path, src]) => !/\.test\.tsx?$/.test(path) && directReads(codeOf(src)).length > 0)
    .flatMap(([, src]) => Array.from(src.matchAll(/export\s+(?:default\s+)?(?:async\s+)?(?:function|const)\s+(use[A-Z]\w*)/g), (m) => m[1])),
)
const FETCHING_HOOK = (name: string) => DATA_HOOKS.has(name) || /^useSWR(?:Infinite|Immutable)?$/.test(name)

interface Import {
  names: { imported: string; local: string }[]
  from: string
}

function importsOf(source: string): Import[] {
  const imports: Import[] = []
  const withStrings = codeOf(source, { keepStrings: true })
  for (const m of withStrings.matchAll(/import\s+(?:type\s+)?(\w+)?\s*,?\s*(?:\{([^}]*)\})?\s*from\s*['"]([^'"]+)['"]/g)) {
    const names = [
      ...(m[1] ? [{ imported: 'default', local: m[1] }] : []),
      ...(m[2] ?? '')
        .split(',')
        .map((n) => n.trim().replace(/^type\s+/, ''))
        .filter(Boolean)
        .map((n) => {
          const [imported, local = imported] = n.split(/\s+as\s+/).map((x) => x.trim())
          return { imported, local }
        }),
    ]
    imports.push({ names, from: m[3] })
  }
  return imports
}

/** The source of a component a page imports, or undefined (not a component we can read). */
function componentSource(pageKey: string, spec: string): string | undefined {
  let path: string
  if (spec.startsWith('@/components/')) path = `../components/${spec.slice('@/components/'.length)}`
  else if (spec.startsWith('.')) {
    const dir = pageKey.split('/').slice(0, -1)
    for (const part of spec.split('/')) {
      if (part === '..') dir.pop()
      else if (part !== '.') dir.push(part)
    }
    path = dir.join('/')
    if (!path.startsWith('.')) path = `./${path}`
  } else return undefined
  for (const candidate of [`${path}.tsx`, `${path}/index.tsx`]) {
    const source = componentSources[candidate] ?? pageSources[candidate]
    if (source !== undefined) return source
  }
  return undefined
}

/** Every way a page reads data; empty for a page that does not fetch. */
export function dataReads(source: string, pageKey = './Page.tsx'): string[] {
  const code = codeOf(source)
  const reads = directReads(code)
  for (const imp of importsOf(source)) {
    const isHookPath = /(?:^@\/hooks\/|(?:^|\/)hooks\/)/.test(imp.from)
    for (const { imported, local } of imp.names) {
      const used = (suffix: string) => new RegExp(`${suffix}\\b${escapeRe(local)}\\b`).test(code)
      if (isHookPath && DATA_HOOKS.has(imported) && new RegExp(`\\b${escapeRe(local)}\\s*[<(]`).test(code)) {
        reads.push(local)
      } else if (/^[A-Z]/.test(local) && used('<')) {
        // A child component that fetches: one level down.
        const child = componentSource(pageKey, imp.from)
        if (child !== undefined && directReads(codeOf(child)).length > 0) reads.push(`<${local}>`)
      }
    }
  }
  return reads
}

/** Error values the page binds from a FETCHING source. */
function boundErrors(code: string): string[] {
  const names: string[] = []
  // const { data, error, isError: failed } = useThing(...)   (multi-line too)
  for (const m of code.matchAll(/(?:const|let)\s*\{([^}]*)\}\s*=\s*(use[A-Z]\w*)\s*[<(]/g)) {
    if (!FETCHING_HOOK(m[2])) continue
    for (const part of m[1].split(',')) {
      const [key, alias] = part.split(':').map((s) => s.trim())
      if (key === 'error' || key === 'isError') names.push(alias || key)
    }
  }
  // const runs = useRuns(...) ... runs.error
  for (const m of code.matchAll(/(?:const|let)\s+(\w+)\s*=\s*(use[A-Z]\w*)\s*[<(]/g)) {
    if (!FETCHING_HOOK(m[2])) continue
    for (const field of ['error', 'isError']) {
      if (new RegExp(`\\b${escapeRe(m[1])}\\.${field}\\b`).test(code)) names.push(`${m[1]}.${field}`)
    }
  }
  // const [loadError, setLoadError] = useState(...) set inside a catch
  for (const m of code.matchAll(/const\s*\[\s*(\w*[eE]rror\w*)\s*,\s*(set\w+)\s*\]\s*=\s*useState/g)) {
    // set inside a `catch {}` block, or a promise's `.catch(() => ...)`
    const setter = escapeRe(m[2])
    const caught = new RegExp(
      `(?:catch\\s*(?:\\([^)]*\\))?\\s*\\{[^}]*|\\.catch\\(\\s*(?:\\([^)]*\\)|\\w+)?\\s*=>\\s*\\{?[^}]*?)\\b${setter}\\(`,
    )
    if (caught.test(code)) names.push(m[1])
  }
  // const displayError = error ?? loadError  ·  const error = failed ? 'x' : null
  for (let pass = 0; pass < 3; pass++) {
    for (const m of code.matchAll(/(?:const|let)\s+(\w+)\s*=\s*([^;\n]+)/g)) {
      if (names.includes(m[1])) continue
      if (names.some((name) => new RegExp(`(?:^|[^\\w.])${escapeRe(name)}\\b`).test(m[2]))) names.push(m[1])
    }
  }
  return names
}

/** Whether a bound error is RENDERED: JSX in its branch, or handed to a component. */
export function hasOwnErrorBranch(source: string): boolean {
  const code = codeOf(source)
  return boundErrors(code).some((name) => {
    const n = escapeRe(name)
    return [
      // {error && <Alert />}  ·  {error && !isLoading && (<Alert />)}  ·  error ? <Alert /> : <List />
      `(?:^|[^\\w.])!?\\s*${n}\\b(?:\\s*&&\\s*!?[\\w.]+(?:\\([^()]*\\))?)*\\s*(?:&&|\\?)\\s*\\(?\\s*<`,
      // if (error) return <Alert />  ·  if (error) { ...; return (<Alert />) }
      `if\\s*\\(\\s*!?\\s*${n}\\b[^)]*\\)\\s*(?:\\{[^{}]*?)?return\\s*\\(?\\s*<`,
      // <Card failed={Boolean(error)} />  ·  <ErrorBanner error={error} />
      `<[A-Z][\\w.]*\\b[^<>]*\\s[\\w-]+=\\{\\s*(?:Boolean\\(\\s*|!!\\s*)?${n}\\b`,
      // <p>{error}</p>  ·  <p>Failed: {error.message}</p>
      `>[^<{]*\\{\\s*${n}(?:\\??\\.message)?\\s*\\}`,
    ].some((pattern) => new RegExp(pattern).test(code))
  })
}

const sourceOf = (page: string) => pageSources[`./${page}.tsx`]
const rendersDataUnavailable = (page: string) =>
  /import DataUnavailable from '@\/components\/ui\/DataUnavailable'/.test(sourceOf(page) ?? '') &&
  /<DataUnavailable\b/.test(codeOf(sourceOf(page) ?? ''))

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
      .map((p) => [p, dataReads(sourceOf(p) ?? '', `./${p}.tsx`)] as const)
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
    expect(hasOwnErrorBranch('const { error: statusError } = useRuns()\n<Card failed={Boolean(statusError)} />')).toBe(true)
    expect(hasOwnErrorBranch('const [error, setError] = useState(null)\nif (error) return <p />')).toBe(false)
    // Every lazily routed page is still found (tolerant regex).
    expect(routedPages.length).toBeGreaterThan(50)
  })

  // QA-B45-R2-4: each of these got past the guard.
  it.each([
    ['fetch(', "const r = await fetch('/api/v1/runs')"],
    ['axios.get', "const r = await axios.get('/api/v1/runs')"],
    ['apiClient.get', "const r = await apiClient.get('/api/v1/runs')"],
    ['a service verb outside get/list', 'const r = await runsService.all()'],
    ['a data hook by relative path', "import { useRuns } from '../hooks/useRuns'\nconst { data } = useRuns(1)"],
    ['a data hook by a deeper relative path', "import { useRuns } from '../../hooks/useRuns'\nconst { data } = useRuns(1)"],
  ])("'no-fetch' catches %s", (_, source) => {
    expect(dataReads(source, './settings/ExamplePage.tsx').length).toBeGreaterThan(0)
  })

  it("'no-fetch' catches a child component that fetches", () => {
    const fetching = Object.entries(componentSources).find(
      ([path, src]) => !/\.test\.tsx$/.test(path) && /export default function (\w+)/.test(src) && directReads(codeOf(src)).length > 0,
    )
    expect(fetching, 'no component fetches: the check below would be vacuous').toBeDefined()
    if (!fetching) return
    const [path, src] = fetching
    const name = src.match(/export default function (\w+)/)?.[1] ?? ''
    const spec = `@/components/${path.slice('../components/'.length).replace(/\.tsx$/, '')}`
    expect(dataReads(`import ${name} from '${spec}'\nreturn <${name} />`)).toEqual([`<${name}>`])
    expect(dataReads(`import ${name} from '${spec}'\nconst unused = 1`)).toEqual([]) // imported, not rendered
  })

  it("'no-fetch' is not fooled by mutations, comments or strings", () => {
    expect(dataReads("await runsService.deleteRun(id)\nawait api.post('/x')")).toEqual([])
    expect(dataReads("// const { data } = useSWR('/x', f)\nconst label = 'useSWR(key)'")).toEqual([])
  })

  it.each([
    ['a commented-out branch', 'const { data, error } = useRuns(1)\n// if (error) return <Alert />\nreturn <List />'],
    ['a block comment', 'const { data, error } = useRuns(1)\n/* {error && <Alert />} */\nreturn <List />'],
    ['an error from a hook that does not fetch', 'const { error } = useForm()\nif (error) return <Alert />'],
    ['an empty branch', 'const { data, error } = useRuns(1)\nif (error) {}\nreturn <List />'],
    ['a branch that only logs', 'const { data, error } = useRuns(1)\nif (error) console.warn(error)\nreturn <List />'],
    ['the pattern inside a string', "const { data, error } = useRuns(1)\nconst doc = 'if (error) return <Alert />'\nreturn <List />"],
    ['the pattern inside a template', 'const { data, error } = useRuns(1)\nconst doc = `{error && <Alert />}`\nreturn <List />'],
    ['a branch that renders nothing', 'const { data, error } = useRuns(1)\nreturn error ? null : <List />'],
  ])("'own-error-ui' refuses %s", (_, source) => {
    expect(hasOwnErrorBranch(source)).toBe(false)
  })

  it.each([
    ['a guarded block', 'const { error } = useRuns(1)\nif (error) {\n  toast.error(\'x\')\n  return (<Alert />)\n}'],
    ['a ternary', 'const { error } = useRuns(1)\nreturn error ? <Alert /> : <List />'],
    ['a negated ternary', 'const { error } = useRuns(1)\nreturn !error ? <List /> : <Alert />'],
    ['a message in JSX', 'const { error } = useRuns(1)\nreturn <p>Failed: {error.message}</p>'],
    ['a prop', 'const { error } = useRuns(1)\nreturn <ErrorBanner error={error} />'],
    ["JSX text with an apostrophe", "const { error } = useRuns(1)\nreturn <div><p>Don't panic</p>{error && <Alert />}</div>"],
    ['a further condition', 'const { error, isLoading } = useRuns(1)\nreturn <div>{error && !isLoading && (\n  <Alert />\n)}</div>'],
    ['a derived error', "const { isError: failed } = useRuns(1)\nconst error = failed ? 'Failed to load' : null\nreturn <div>{error && <p>{error}</p>}</div>"],
    ['a merged error', 'const { error: loadError } = useRuns(1)\nconst [error, setError] = useState(null)\nconst shown = error ?? loadError\nreturn <div>{shown && (<p>{shown}</p>)}</div>'],
  ])("'own-error-ui' accepts %s", (_, source) => {
    expect(hasOwnErrorBranch(source)).toBe(true)
  })
})
