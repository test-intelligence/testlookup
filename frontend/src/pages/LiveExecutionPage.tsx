/**
 * LiveExecutionPage
 *
 * Real-time dashboard for monitoring 10 000+ concurrent test executions.
 *
 * Layout
 * ------
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │  Header: Active Runs | Tests In Progress | Pass Rate | WS Status   │
 * ├──────────────────────────────────────┬──────────────────────────────┤
 * │  Active Sessions Table               │  Recent Events Feed          │
 * │  (sortable, filterable by project)   │  (last 200 live events)      │
 * └──────────────────────────────────────┴──────────────────────────────┘
 *
 * Data sources
 * ------------
 * - SWR polling GET /api/v1/stream/active (5 s interval, 30 s when WS open)
 * - WebSocket   /ws/live/{projectId} (push updates, merges into local state)
 */
import { useState, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  Radio,
  WifiOff,
  Wifi,
  XCircle,
  Clock,
  FlaskConical,
  Cpu,
  Package,
  Copy,
  Check,
  Download,
  Terminal,
  Code2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useLiveExecution, LiveEvent } from '@/hooks/useLiveExecution'
import type { LiveSessionState } from '@/types/live-stream'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'

// ── Helpers ────────────────────────────────────────────────────────────────

function passRateColor(rate: number): string {
  if (rate >= 90) return 'text-emerald-400'
  if (rate >= 70) return 'text-yellow-400'
  return 'text-red-400'
}

function statusDot(status: string) {
  if (status === 'running') return 'bg-neutral-300 animate-pulse'
  if (status === 'completed') return 'bg-emerald-500'
  return 'bg-neutral-600'
}

function eventStatusColor(status?: string): string {
  switch (status?.toUpperCase()) {
    case 'PASSED':  return 'text-emerald-400'
    case 'FAILED':  return 'text-red-400'
    case 'BROKEN':  return 'text-orange-400'
    case 'SKIPPED': return 'text-[var(--color-text-muted)]'
    default: return 'text-[var(--color-text-secondary)]'
  }
}

function relativeTime(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000)
  if (diff < 5)  return 'just now'
  if (diff < 60) return `${diff}s ago`
  return `${Math.floor(diff / 60)}m ago`
}

// ── WS Status badge ────────────────────────────────────────────────────────

function WsStatusBadge({ status }: { status: string }) {
  const configs = {
    open:       { icon: Wifi,    label: 'Live',        cls: 'text-emerald-400' },
    connecting: { icon: Radio,   label: 'Connecting…', cls: 'text-yellow-400 animate-pulse' },
    error:      { icon: WifiOff, label: 'Error',       cls: 'text-red-400' },
    closed:     { icon: WifiOff, label: 'Reconnecting…', cls: 'text-[var(--color-text-muted)]' },
  } as const
  const cfg = configs[status as keyof typeof configs] ?? configs.closed
  const Icon = cfg.icon
  return (
    <span className={clsx('flex items-center gap-1.5 text-xs font-medium', cfg.cls)}>
      <Icon className="h-3.5 w-3.5" />
      {cfg.label}
    </span>
  )
}

// ── Metric card ────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  colorClass = 'text-[var(--color-text)]',
}: {
  icon: React.ElementType
  label: string
  value: string | number
  sub?: string
  colorClass?: string
}) {
  return (
    <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-5 py-4 flex items-start gap-4">
      <div className="h-9 w-9 rounded-md bg-[var(--color-bg-hover)] flex items-center justify-center flex-shrink-0">
        <Icon className="h-4.5 w-4.5 text-[var(--color-text-secondary)]" />
      </div>
      <div>
        <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">{label}</p>
        <p className={clsx('text-2xl font-bold leading-tight mt-0.5', colorClass)}>{value}</p>
        {sub && <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

// ── Progress bar ───────────────────────────────────────────────────────────

function MiniProgress({ passed, failed, broken, skipped, total }: {
  passed: number; failed: number; broken: number; skipped: number; total: number
}) {
  const completed = passed + failed + broken + skipped
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0
  return (
    <div className="w-full">
      <div className="flex h-1.5 rounded-full overflow-hidden bg-[var(--color-bg-hover)] w-28">
        <div style={{ width: `${(passed / (total || 1)) * 100}%` }} className="bg-emerald-500" />
        <div style={{ width: `${(failed / (total || 1)) * 100}%` }} className="bg-red-500" />
        <div style={{ width: `${(broken / (total || 1)) * 100}%` }} className="bg-orange-400" />
        <div style={{ width: `${(skipped / (total || 1)) * 100}%` }} className="bg-neutral-600" />
      </div>
      <span className="text-[10px] text-[var(--color-text-muted)] mt-0.5 block">{pct}% done</span>
    </div>
  )
}

// ── Sort helpers ───────────────────────────────────────────────────────────

type SortField = 'pass_rate' | 'total' | 'failed' | 'started_at'
type SortDir   = 'asc' | 'desc'

function sortSessions(sessions: LiveSessionState[], field: SortField, dir: SortDir) {
  return [...sessions].sort((a, b) => {
    let va: number
    let vb: number
    if (field === 'started_at') {
      va = a.started_at ? new Date(a.started_at).getTime() : 0
      vb = b.started_at ? new Date(b.started_at).getTime() : 0
    } else {
      va = (a[field] as number) ?? 0
      vb = (b[field] as number) ?? 0
    }
    return dir === 'asc' ? va - vb : vb - va
  })
}

// ── Event feed item ────────────────────────────────────────────────────────

function EventRow({ event }: { event: LiveEvent }) {
  const isResult = event.type === 'live_test_result'
  const isWarning = event.type === 'live_warning'
  const isComplete = event.type === 'live_run_complete'
  const isStarted = event.type === 'live_run_started'

  const icon = isResult
    ? event.last_status?.toUpperCase() === 'PASSED'
      ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0 mt-0.5" />
      : event.last_status?.toUpperCase() === 'FAILED' || event.last_status?.toUpperCase() === 'BROKEN'
        ? <XCircle className="h-3.5 w-3.5 text-red-400 flex-shrink-0 mt-0.5" />
        : <Clock className="h-3.5 w-3.5 text-[var(--color-text-muted)] flex-shrink-0 mt-0.5" />
    : isWarning
      ? <Activity className="h-3.5 w-3.5 text-yellow-400 flex-shrink-0 mt-0.5" />
      : isComplete
        ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 flex-shrink-0 mt-0.5" />
        : <Radio className="h-3.5 w-3.5 text-[var(--color-text)] flex-shrink-0 mt-0.5" />

  const text = isResult
    ? event.last_test ?? 'test event'
    : isWarning
      ? (event.message ?? 'Warning')
      : isComplete
        ? `Run ${event.run_id?.slice(0, 8)} completed (${event.pass_rate?.toFixed(1)}% pass)`
        : isStarted
          ? `Run ${event.run_id?.slice(0, 8)} started`
          : event.type

  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-[var(--color-border)]/60 text-xs">
      {icon}
      <div className="flex-1 min-w-0">
        <span className={clsx('font-medium truncate block', isResult && eventStatusColor(event.last_status))}>
          {text}
        </span>
        {event.run_id && (
          <span className="text-[var(--color-text-faint)] font-mono">{event.run_id.slice(0, 8)}</span>
        )}
      </div>
      <span className="text-[var(--color-text-faint)] flex-shrink-0">{relativeTime(event.timestamp)}</span>
    </div>
  )
}

// ── SDK language tabs & snippets ───────────────────────────────────────────

type SDKLang = 'python' | 'java' | 'javascript' | 'go'

const SDK_TABS: { id: SDKLang; label: string; icon: string }[] = [
  { id: 'python',     label: 'Python',     icon: 'py' },
  { id: 'java',       label: 'Java',       icon: 'java' },
  { id: 'javascript', label: 'JavaScript', icon: 'js' },
  { id: 'go',         label: 'Go',         icon: 'go' },
]

function getInstallSnippet(lang: SDKLang): string {
  switch (lang) {
    case 'python':
      return `pip install httpx pyyaml
# Copy the reporter from the SDK Downloads button above
# Then add testlookup.yaml to your project root (see Step 2)`
    case 'java':
      return `# Option 1: Download the fat JAR from the SDK Downloads button above
# Add to classpath — auto-discovery registers JUnit 5 / TestNG listeners

# Option 2: Install to local Maven repo, then add dependency:
mvn install:install-file -Dfile=testlookup-reporter-1.0.0-all.jar \\
  -DgroupId=io.testlookup -DartifactId=testlookup-reporter \\
  -Dversion=1.0.0 -Dclassifier=all -Dpackaging=jar

# Maven dependency (after local install)
<dependency>
  <groupId>io.testlookup</groupId>
  <artifactId>testlookup-reporter</artifactId>
  <version>1.0.0</version>
  <classifier>all</classifier>
  <scope>test</scope>
</dependency>

# Gradle (after local install)
testImplementation 'io.testlookup:testlookup-reporter:1.0.0:all'`
    case 'javascript':
      return 'npm install testlookup-reporter'
    case 'go':
      return 'go get github.com/testlookup/testlookup-go'
  }
}

function getRunnerSnippet(lang: SDKLang, projectId: string): string {
  switch (lang) {
    case 'python':
      return `# Option 1: Zero-config — add testlookup.yaml to project root:
# server:
#   url: "http://server:8000"
# auth:
#   api_key: "<api-key>"       # or token: "<jwt>"
# project:
#   id: "${projectId}"
pytest

# Option 2: CLI flags
pytest --testlookup-url http://server:8000 \\
       --testlookup-token <jwt-or-api-key> \\
       --testlookup-project ${projectId} \\
       --testlookup-build build-42

# Option 3: Environment variables
export TESTLOOKUP_URL=http://server:8000
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
pytest`
    case 'java':
      return `# Option 1: TestNG XML — zero-code setup (recommended)
# Just add suite parameters to your testng.xml:

# <suite name="My Suite">
#   <parameter name="testlookup.url" value="http://server:8000"/>
#   <parameter name="testlookup.apiKey" value="qai_..."/>
#   <parameter name="testlookup.projectId" value="${projectId}"/>
#   <listeners>
#     <listener class-name="io.testlookup.testng.TestLookupListener"/>
#   </listeners>
#   <test name="Regression">
#     <classes><class name="com.example.MyTest"/></classes>
#   </test>
# </suite>
mvn test -DsuiteXmlFiles=testng.xml

# Option 2: Environment variables (works with JUnit 5 too)
export TESTLOOKUP_URL=http://server:8000
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
mvn test

# Option 3: JVM system properties
mvn test \\
  -Dtestlookup.url=http://server:8000 \\
  -Dtestlookup.apiKey=<api-key> \\
  -Dtestlookup.projectId=${projectId} \\
  -Dtestlookup.build=build-42

# Option 4: testlookup.yaml in project root (same format as Python)`
    case 'javascript':
      return `// Jest — add to jest.config.js
module.exports = {
  reporters: [
    "default",
    ["testlookup-reporter/jest-reporter", {
      url: "http://server:8000",
      token: "<jwt>",
      projectId: "${projectId}",
    }],
  ],
};

// Mocha — run with reporter flag
mocha --reporter testlookup-reporter/mocha-reporter \\
  --reporter-options url=http://server:8000,token=<jwt>,projectId=${projectId}`
    case 'go':
      return `// Set environment variables
export TESTLOOKUP_URL=http://server:8000
export TESTLOOKUP_TOKEN=<jwt>
export TESTLOOKUP_PROJECT=${projectId}

// Use the testing helper in TestMain
func TestMain(m *testing.M) {
    testlookup.RunWithReporter(m)
}`
  }
}

function getAPISnippet(lang: SDKLang, projectId: string): string {
  switch (lang) {
    case 'python':
      return `from testlookup_reporter import TestLookupReporter

# Config auto-resolved from testlookup.yaml / env vars:
reporter = TestLookupReporter()

# Or pass explicitly with API key (recommended for CI/CD):
# reporter = TestLookupReporter(
#     base_url="http://server:8000",
#     api_key="qai_...",               # project-scoped API key
#     project_id="${projectId}",
# )

async with reporter.session(build_number="build-42", branch="main") as s:
    await s.record("test_login", "PASSED", 120)
    await s.record("test_cart",  "FAILED", 340,
                   error="AssertionError: expected 200",
                   suite_name="checkout_tests",
                   tags=["smoke"])
    await s.log("Environment: staging")
    await s.metric("memory_mb", 512.3, unit="MB")
await reporter.aclose()`
    case 'java':
      return `import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;
import java.util.List;

// Config auto-resolved from testlookup.yaml / env vars / system props:
TestLookupReporter reporter = new TestLookupReporter.Builder().build();

// Or pass explicitly with API key (recommended for CI/CD):
// TestLookupReporter reporter = new TestLookupReporter.Builder()
//     .baseUrl("http://server:8000")
//     .apiKey("qai_...")               // project-scoped API key
//     .projectId("${projectId}")
//     .build();

try (LiveSession session = reporter.startSession(
        SessionOptions.builder()
            .buildNumber("build-42")
            .branch("main")
            .build())) {

    session.record("test_login", TestStatus.PASSED, 120);
    session.record("test_cart",  TestStatus.FAILED, 340,
        RecordOptions.builder()
            .error("AssertionError: expected 200")
            .suiteName("CheckoutTests")
            .tags(List.of("smoke"))
            .build());
    session.log("Environment: staging", "INFO");
    session.metric("memory_mb", 512.3, "MB");
}
// session.close() called automatically — triggers AI analysis`
    case 'javascript':
      return `import { TestLookupReporter } from 'testlookup-reporter';

const reporter = new TestLookupReporter({
  url: 'http://server:8000',
  token: '<jwt>',
  projectId: '${projectId}',
});

const session = await reporter.createSession(
  'my-build',
  { releaseName: 'v2.5.0' },
);

await session.record('test_login', 'PASSED', 120);
await session.record('test_cart', 'FAILED', 340, {
  error: 'AssertionError: expected 200',
});

await session.close();`
    case 'go':
      return `package main

import "github.com/testlookup/testlookup-go/testlookup"

reporter, _ := testlookup.NewReporter(testlookup.Config{
    BaseURL:   "http://server:8000",
    Token:     "<jwt>",
    ProjectID: "${projectId}",
})

ctx := context.Background()
session, _ := reporter.CreateSession(ctx,
    "my-build",
    testlookup.WithRelease("v2.5.0"),
)
defer session.Close(ctx)

session.Record(ctx, "test_login", testlookup.Passed, 120)
session.Record(ctx, "test_cart", testlookup.Failed, 340,
    testlookup.WithError("AssertionError: expected 200"))`
  }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [text])
  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] transition-colors"
      title="Copy to clipboard"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  )
}

function CodeBlock({ code }: { code: string }) {
  return (
    <div className="relative group">
      <pre className="bg-[var(--color-bg-card)] rounded p-3 pr-8 text-[var(--color-text-secondary)] overflow-x-auto font-mono leading-relaxed">
        {code}
      </pre>
      <CopyButton text={code} />
    </div>
  )
}

// Direct backend URL — bypasses the Vite proxy which can't stream binary responses
const SDK_API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

const SDK_DOWNLOADS: { sdkLang: SDKLang; label: string; backendLang: string }[] = [
  { sdkLang: 'python',     label: 'Python (.py)',         backendLang: 'python' },
  { sdkLang: 'java',       label: 'Java (.jar)',           backendLang: 'java'   },
  { sdkLang: 'javascript', label: 'JavaScript/TS (.zip)', backendLang: 'js'     },
  { sdkLang: 'go',         label: 'Go (.zip)',             backendLang: 'go'     },
]

function ClientSDKGuide({ projectId }: { projectId?: string }) {
  const [lang, setLang] = useState<SDKLang>('python')
  const [showSdkDropdown, setShowSdkDropdown] = useState(false)
  const pid = projectId ?? '<project-id>'

  const handleSdkDownload = useCallback((backendLang: string) => {
    // Navigate directly to the backend — Content-Disposition: attachment triggers
    // download without leaving the page, and bypasses the Vite proxy which fails
    // on binary/streaming responses.
    window.location.href = `${SDK_API_BASE}/api/v1/sdk/${backendLang}`
    setShowSdkDropdown(false)
  }, [])

  return (
    <div className="theme-bg-secondary border theme-border rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-[var(--color-text-muted)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            Connect a Test Runner
          </h3>
        </div>
        <div className="relative">
          <button
            onClick={() => setShowSdkDropdown(v => !v)}
            className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            <Download className="h-3.5 w-3.5" />
            All client SDKs
            <ChevronDown className="h-3 w-3" />
          </button>
          {showSdkDropdown && (
            <div className="absolute right-0 top-full mt-1 z-10 bg-[var(--color-bg-card)] border theme-border rounded-lg shadow-lg py-1 min-w-[190px]">
              {SDK_DOWNLOADS.map(({ sdkLang, label, backendLang }) => (
                <button
                  key={sdkLang}
                  onClick={() => handleSdkDownload(backendLang)}
                  className="flex items-center gap-2 w-full px-3 py-2 text-xs text-left text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] transition-colors"
                >
                  <Download className="h-3 w-3 text-[var(--color-text-muted)]" />
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Language tabs */}
      <div className="flex items-center gap-1 mb-4 bg-[var(--color-bg-card)] rounded-lg p-1 w-fit">
        {SDK_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setLang(tab.id)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
              lang === tab.id
                ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            <Code2 className="h-3 w-3" />
            {tab.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
        <div>
          <p className="text-[var(--color-text-muted)] mb-2 font-medium">1. Install the client SDK</p>
          <CodeBlock code={getInstallSnippet(lang)} />
        </div>
        <div>
          <p className="text-[var(--color-text-muted)] mb-2 font-medium">
            2. {lang === 'python' ? 'Run pytest with the plugin' : lang === 'java' ? 'Configure TestNG XML or env vars (zero-code)' : lang === 'javascript' ? 'Use the Jest or Mocha reporter' : 'Use the testing helper'}
          </p>
          <CodeBlock code={getRunnerSnippet(lang, pid)} />
        </div>
        <div>
          <p className="text-[var(--color-text-muted)] mb-2 font-medium">
            3. Or use the {lang === 'python' ? 'Python' : lang === 'java' ? 'Java' : lang === 'javascript' ? 'JavaScript' : 'Go'} API directly
          </p>
          <CodeBlock code={getAPISnippet(lang, pid)} />
        </div>
        <div>
          <p className="text-[var(--color-text-muted)] mb-2 font-medium">4. Stream stats appear here in real-time</p>
          <ul className="space-y-1.5 text-[var(--color-text-muted)] list-disc list-inside">
            <li>Events batched every 100 ms client-side</li>
            <li>Results visible on dashboard within ~1 s</li>
            <li>Final test cases persisted to DB after run completes</li>
            <li>Supports 10 000+ concurrent executions</li>
          </ul>
        </div>
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function LiveExecutionPage() {
  const selectedProject = useProjectStore(s => s.activeProject)
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  // In All Projects mode, pass undefined so polling returns all sessions
  const projectId = isAllProjects ? undefined : selectedProject?.id?.toString()

  const {
    sessions,
    runningSessions,
    recentEvents,
    wsStatus,
    isLoading,
  } = useLiveExecution(projectId)

  const [sortField, setSortField] = useState<SortField>('started_at')
  const [sortDir,   setSortDir]   = useState<SortDir>('desc')
  const [search,    setSearch]    = useState('')
  const [filter,    setFilter]    = useState<'all' | 'running' | 'completed'>('all')

  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  const SortIcon = ({ field }: { field: SortField }) => {
    if (field !== sortField) return <ChevronsUpDown className="h-3 w-3 opacity-30" />
    return sortDir === 'asc'
      ? <ChevronUp className="h-3 w-3 text-[var(--color-text)]" />
      : <ChevronDown className="h-3 w-3 text-[var(--color-text)]" />
  }

  const visibleSessions = useMemo(() => {
    let list = sessions
    if (filter === 'running')   list = list.filter(s => s.status === 'running')
    if (filter === 'completed') list = list.filter(s => s.status === 'completed')
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        s =>
          s.run_id.toLowerCase().includes(q) ||
          s.build_number?.toLowerCase().includes(q) ||
          s.current_test?.toLowerCase().includes(q),
      )
    }
    return sortSessions(list, sortField, sortDir)
  }, [sessions, filter, search, sortField, sortDir])

  // KPIs derived from the currently visible (filtered) sessions
  const visibleStats = useMemo(() => {
    const totalTests    = visibleSessions.reduce((a, s) => a + (s.total   || 0), 0)
    const totalPassed   = visibleSessions.reduce((a, s) => a + (s.passed  || 0), 0)
    const totalFailed   = visibleSessions.reduce((a, s) => a + (s.failed  || 0), 0)
    const totalSkipped  = visibleSessions.reduce((a, s) => a + (s.skipped || 0), 0)
    const overallPassRate = (totalPassed + totalFailed) > 0
      ? Math.round((totalPassed / (totalPassed + totalFailed)) * 100)
      : 0
    return { totalTests, totalPassed, totalFailed, totalSkipped, overallPassRate }
  }, [visibleSessions])

  const workflow = useMemo(() => {
    const workflowStages = [
      {
        stage_name: 'stream_connection',
        status: wsStatus === 'open' ? 'completed' : wsStatus === 'connecting' ? 'running' : wsStatus === 'error' ? 'failed' : 'pending',
        label: 'Stream Connection',
        description: 'Keep the live execution channel healthy',
        confidence_score: wsStatus === 'open' ? 100 : wsStatus === 'connecting' ? 60 : 20,
        evidence_count: recentEvents.length,
        result_data: { ws_status: wsStatus },
      },
      {
        stage_name: 'run_monitoring',
        status: runningSessions.length > 0 ? 'running' : 'completed',
        label: 'Run Monitoring',
        description: 'Track active runs and current tests',
        evidence_count: runningSessions.length,
        result_data: { running_sessions: runningSessions.length, visible_sessions: visibleSessions.length },
      },
      {
        stage_name: 'event_rollup',
        status: recentEvents.length > 0 ? 'completed' : 'pending',
        label: 'Event Rollup',
        description: 'Roll execution events into a single live pulse',
        evidence_count: recentEvents.length,
        result_data: { recent_events: recentEvents.length },
      },
      {
        stage_name: 'release_readout',
        status: sessions.length > 0 ? 'completed' : 'pending',
        label: 'Release Readout',
        description: 'Summarize the current execution state for release and QA',
        evidence_count: sessions.length,
        result_data: { total_sessions: sessions.length, visible_pass_rate: visibleStats.overallPassRate },
      },
    ]

    const workflowEvents = recentEvents.slice(0, 8).map((event, index) => ({
      event_type: event.type === 'live_run_complete'
        ? 'stage_completed'
        : event.type === 'live_run_started'
          ? 'stage_started'
          : event.type === 'live_warning'
            ? 'stage_failed'
            : 'tool_invoked',
      stage_name: event.type === 'live_run_complete'
        ? 'release_readout'
        : event.type === 'live_run_started'
          ? 'run_monitoring'
          : 'event_rollup',
      test_case_id: event.run_id ?? null,
      timestamp: new Date(event.timestamp - index * 1000).toISOString(),
      detail: {
        type: event.type,
        status: event.last_status,
        test: event.last_test,
        message: event.message,
      },
    }))

    return {
      stages: workflowStages,
      events: workflowEvents,
      stageOrder: workflowStages.map(stage => stage.stage_name),
    }
  }, [wsStatus, runningSessions.length, recentEvents, sessions.length, visibleSessions.length, visibleStats.overallPassRate])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  return (
    <div className="space-y-5 p-6">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)]">Live Execution</h1>
          <p className="text-sm text-[var(--color-text-muted)] mt-0.5">
            Real-time test execution stream{selectedProject ? ` — ${selectedProject.name}` : ' — all projects'}
          </p>
        </div>
        <WsStatusBadge status={wsStatus} />
      </div>

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Activity}
          label="Active Runs"
          value={runningSessions.length}
          sub={`${sessions.length} total · ${sessions.filter(s => s.status === 'completed').length} completed`}
          colorClass="text-[var(--color-text)]"
        />
        <StatCard
          icon={FlaskConical}
          label="Total Tests"
          value={visibleStats.totalTests.toLocaleString()}
          sub={`${visibleStats.totalPassed.toLocaleString()} passed · ${visibleStats.totalFailed.toLocaleString()} failed`}
        />
        <StatCard
          icon={CheckCircle2}
          label="Pass Rate"
          value={`${visibleStats.overallPassRate}%`}
          sub={filter === 'all' ? 'all visible sessions' : `${filter} sessions`}
          colorClass={passRateColor(visibleStats.overallPassRate)}
        />
        <StatCard
          icon={Cpu}
          label="Skipped"
          value={visibleStats.totalSkipped.toLocaleString()}
          sub={filter === 'all' ? 'all visible sessions' : `${filter} sessions`}
          colorClass="text-[var(--color-text-muted)]"
        />
      </div>

      <WorkflowTimeline
        title="Live execution workflow"
        subtitle="Connection health, run monitoring, event rollup, and release readout"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
        showEventFeed
      />

      {recentEvents.length > 0 && (
        <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-4 py-3">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div>
              <h2 className="text-sm font-semibold text-[var(--color-text)]">Live Workflow Pulse</h2>
              <p className="text-xs text-[var(--color-text-muted)]">Recent execution events flowing through the system</p>
            </div>
            <span className="text-xs text-[var(--color-text-muted)]">{recentEvents.length} events</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {recentEvents.slice(0, 6).map((event, index) => {
              const isResult = event.type === 'live_test_result'
              const isComplete = event.type === 'live_run_complete'
              const isStarted = event.type === 'live_run_started'
              const label = isResult
                ? event.last_test ?? 'test result'
                : isComplete
                  ? `Run ${event.run_id?.slice(0, 8)} complete`
                  : isStarted
                    ? `Run ${event.run_id?.slice(0, 8)} started`
                    : event.message ?? event.type.replace(/_/g, ' ')
              const tone = isComplete
                ? 'bg-emerald-900/30 text-emerald-300 border-emerald-700/40'
                : isResult && (event.last_status?.toUpperCase() === 'FAILED' || event.last_status?.toUpperCase() === 'BROKEN')
                  ? 'bg-red-900/30 text-red-300 border-red-700/40'
                  : isResult
                    ? 'bg-emerald-900/20 text-emerald-300 border-emerald-700/30'
                    : isStarted
                      ? 'bg-white/10 text-[var(--color-text-secondary)] border-[var(--color-border-light)]'
                      : 'bg-[var(--color-bg-card)]/40 text-[var(--color-text-secondary)] border-[var(--color-border)]'
              return (
                <div key={`${event.run_id ?? 'event'}-${event.timestamp}-${index}`} className={`flex items-center gap-2 px-3 py-2 rounded-full border text-xs ${tone}`}>
                  <span className="h-2 w-2 rounded-full bg-current" />
                  <span className="max-w-[240px] truncate">{label}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Main content: sessions table + event feed ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        {/* Active Sessions Table */}
        <div className="xl:col-span-2 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-border)]">
            <h2 className="text-sm font-semibold text-[var(--color-text)] flex-1">Active Sessions</h2>
            {/* Filter tabs */}
            <div className="flex gap-1">
              {(['all', 'running', 'completed'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={clsx(
                    'px-2.5 py-1 rounded text-xs font-medium transition-colors',
                    filter === f
                      ? 'bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)]'
                      : 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                >
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
            {/* Search */}
            <input
              type="text"
              placeholder="Search runs…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="bg-[var(--color-bg-hover)] border border-[var(--color-border-light)] rounded px-2.5 py-1 text-xs text-[var(--color-text)] placeholder-[var(--color-text-faint)] w-36 focus:outline-none focus:border-neutral-500"
            />
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[var(--color-text-muted)] uppercase tracking-wider">
                  <th className="px-4 py-2.5 text-left font-medium">Run</th>
                  <th className="px-4 py-2.5 text-left font-medium">Status</th>
                  {isAllProjects && <th className="px-4 py-2.5 text-left font-medium">Project</th>}
                  <th className="px-4 py-2.5 text-left font-medium">Build</th>
                  <th
                    className="px-4 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                    onClick={() => handleSort('total')}
                  >
                    <span className="flex items-center justify-end gap-1">
                      Tests <SortIcon field="total" />
                    </span>
                  </th>
                  <th
                    className="px-4 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                    onClick={() => handleSort('pass_rate')}
                  >
                    <span className="flex items-center justify-end gap-1">
                      Pass % <SortIcon field="pass_rate" />
                    </span>
                  </th>
                  <th
                    className="px-4 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                    onClick={() => handleSort('failed')}
                  >
                    <span className="flex items-center justify-end gap-1">
                      Failed <SortIcon field="failed" />
                    </span>
                  </th>
                  <th className="px-4 py-2.5 text-left font-medium">Release</th>
                  <th className="px-4 py-2.5 text-left font-medium">Progress</th>
                  <th className="px-4 py-2.5 text-left font-medium">Current / Completed</th>
                </tr>
              </thead>
              <tbody>
                {visibleSessions.length === 0 && (
                  <tr>
                    <td colSpan={isAllProjects ? 10 : 9} className="px-4 py-10 text-center text-[var(--color-text-muted)]">
                      {sessions.length === 0
                        ? 'No active execution sessions. Start a test run with the client SDK.'
                        : 'No sessions match the current filter.'}
                    </td>
                  </tr>
                )}
                {visibleSessions.map(session => (
                  <tr
                    key={session.run_id}
                    className="border-b border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]/30 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className={clsx('h-2 w-2 rounded-full flex-shrink-0', statusDot(session.status))} />
                        <Link
                          to={`/runs/${session.run_id}`}
                          className="font-mono text-[var(--color-text)] hover:text-[var(--color-text-secondary)]"
                        >
                          {session.run_id.slice(0, 8)}
                        </Link>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx(
                        'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium',
                        session.status === 'running'
                          ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]'
                          : 'bg-emerald-900/50 text-emerald-300',
                      )}>
                        {session.status}
                      </span>
                    </td>
                    {isAllProjects && (
                      <td className="px-4 py-3 text-[var(--color-text-muted)] font-mono text-[11px]">
                        {session.project_id ? session.project_id.slice(0, 8) : '—'}
                      </td>
                    )}
                    <td className="px-4 py-3 text-[var(--color-text-secondary)] font-mono">
                      {session.build_number || '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-[var(--color-text)] tabular-nums">
                      {session.total.toLocaleString()}
                    </td>
                    <td className={clsx('px-4 py-3 text-right font-semibold tabular-nums', passRateColor(session.pass_rate))}>
                      {session.pass_rate.toFixed(1)}%
                    </td>
                    <td className="px-4 py-3 text-right text-red-400 tabular-nums font-medium">
                      {session.failed > 0 ? session.failed.toLocaleString() : (
                        <span className="text-[var(--color-text-faint)]">0</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {session.release_name ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-violet-900/40 text-violet-300">
                          <Package className="h-2.5 w-2.5" />
                          {session.release_name}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-faint)]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <MiniProgress
                        passed={session.passed}
                        failed={session.failed}
                        broken={session.broken}
                        skipped={session.skipped}
                        total={session.total}
                      />
                    </td>
                    <td className="px-4 py-3 max-w-[200px]">
                      {session.status === 'completed' && session.completed_at ? (
                        <span className="text-[var(--color-text-muted)] text-[11px]">
                          {new Date(session.completed_at).toLocaleTimeString()}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-muted)] truncate block text-[11px]">
                          {session.current_test || '—'}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {visibleSessions.length > 0 && (
            <div className="px-4 py-2 border-t border-[var(--color-border)] text-xs text-[var(--color-text-muted)]">
              {visibleSessions.length} session{visibleSessions.length !== 1 ? 's' : ''} shown
            </div>
          )}
        </div>

        {/* Event Feed */}
        <div className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--color-text)]">Live Event Feed</h2>
            <span className="text-xs text-[var(--color-text-muted)]">{recentEvents.length} events</span>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-2 max-h-[520px]">
            {recentEvents.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-40 text-[var(--color-text-faint)]">
                <Radio className="h-8 w-8 mb-2 opacity-40" />
                <p className="text-sm">Waiting for events…</p>
              </div>
            ) : (
              recentEvents.map((event, i) => (
                <EventRow key={`${event.run_id}-${event.timestamp}-${i}`} event={event} />
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── Integration guide ── */}
      <ClientSDKGuide projectId={selectedProject?.id} />
    </div>
  )
}
