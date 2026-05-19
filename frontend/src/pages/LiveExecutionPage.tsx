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
import { Fragment, useEffect, useState, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  CircleDashed,
  CircleDot,
  Clock,
  Code2,
  Copy,
  Check,
  Download,
  List,
  Package,
  Pause,
  Radio,
  RefreshCw,
  Search,
  Terminal,
  WifiOff,
  Wifi,
  XCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useLiveExecution } from '@/hooks/useLiveExecution'
import { useSuiteOptions } from '@/hooks/useSuiteOptions'
import type { LiveSessionState } from '@/types/live-stream'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Pagination from '@/components/ui/Pagination'
import SuiteBadge from '@/components/ui/SuiteBadge'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { suiteMatchesValue } from '@/utils/suiteFilters'
import { isActivelyRunning, isStaleRunning } from '@/utils/liveSessionFreshness'
import { snapToAllowed, useTimeWindowStore } from '@/store/timeWindowStore'
import { copyTextToClipboard } from '@/utils/clipboard'

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

// ── Window picker ──────────────────────────────────────────────────────────
// 1 = last 24h; the rest are day counts. Default 7 preserves the prior
// hardcoded backend cutoff. Matches the picker shape used on /runs,
// /overview, /trends, and /coverage so users have one mental model.
const LIVE_WINDOWS = [1, 7, 14, 30] as const
type LiveWindow = (typeof LIVE_WINDOWS)[number]

function LiveWindowPicker({ value, onChange }: { value: LiveWindow; onChange: (w: LiveWindow) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Completed sessions window"
      className="flex items-center gap-0 p-0.5 rounded-md"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      {LIVE_WINDOWS.map(w => {
        const active = value === w
        return (
          <button
            key={w}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(w)}
            className={clsx(
              'px-2.5 py-0.5 text-[11px] font-medium tabular-nums rounded-sm transition-colors',
              active
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text)] shadow-[var(--shadow-sm)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            {w === 1 ? '24h' : `${w}d`}
          </button>
        )
      })}
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

function getRunnerSnippet(lang: SDKLang, projectId: string, serverUrl: string): string {
  switch (lang) {
    case 'python':
      return `# Option 1: Zero-config — add testlookup.yaml to project root:
# server:
#   url: "${serverUrl}"
# auth:
#   api_key: "<api-key>"       # or token: "<jwt>"
# project:
#   id: "${projectId}"
pytest

# Option 2: CLI flags
pytest --testlookup-url ${serverUrl} \\
       --testlookup-token <jwt-or-api-key> \\
       --testlookup-project ${projectId} \\
       --testlookup-build build-42

# Option 3: Environment variables
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
pytest`
    case 'java':
      return `# Option 1: TestNG XML — zero-code setup (recommended)
# Just add suite parameters to your testng.xml:

# <suite name="My Suite">
#   <parameter name="testlookup.url" value="${serverUrl}"/>
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
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_API_KEY=<api-key>
export TESTLOOKUP_PROJECT_ID=${projectId}
mvn test

# Option 3: JVM system properties
mvn test \\
  -Dtestlookup.url=${serverUrl} \\
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
      url: "${serverUrl}",
      token: "<jwt>",
      projectId: "${projectId}",
    }],
  ],
};

// Mocha — run with reporter flag
mocha --reporter testlookup-reporter/mocha-reporter \\
  --reporter-options url=${serverUrl},token=<jwt>,projectId=${projectId}`
    case 'go':
      return `// Set environment variables
export TESTLOOKUP_URL=${serverUrl}
export TESTLOOKUP_TOKEN=<jwt>
export TESTLOOKUP_PROJECT=${projectId}

// Use the testing helper in TestMain
func TestMain(m *testing.M) {
    testlookup.RunWithReporter(m)
}`
  }
}

function getAPISnippet(lang: SDKLang, projectId: string, serverUrl: string): string {
  switch (lang) {
    case 'python':
      return `from testlookup_reporter import TestLookupReporter

# Config auto-resolved from testlookup.yaml / env vars:
reporter = TestLookupReporter()

# Or pass explicitly with API key (recommended for CI/CD):
# reporter = TestLookupReporter(
#     base_url="${serverUrl}",
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
//     .baseUrl("${serverUrl}")
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
  url: '${serverUrl}',
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
    BaseURL:   "${serverUrl}",
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
    void copyTextToClipboard(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
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

// Direct backend URL — bypasses the Vite proxy which can't stream binary responses.
// Falls back to same-origin so it Just Works behind any ingress (k8s/gcp/aws/homelab).
const SDK_API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
  (typeof window !== 'undefined' ? window.location.origin : '')

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
          <CodeBlock code={getRunnerSnippet(lang, pid, SDK_API_BASE)} />
        </div>
        <div>
          <p className="text-[var(--color-text-muted)] mb-2 font-medium">
            3. Or use the {lang === 'python' ? 'Python' : lang === 'java' ? 'Java' : lang === 'javascript' ? 'JavaScript' : 'Go'} API directly
          </p>
          <CodeBlock code={getAPISnippet(lang, pid, SDK_API_BASE)} />
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
  const [selectedSuite, setSelectedSuite] = useState('')
  // Cutoff (in days) for completed sessions shown alongside the always-current
  // active set. 1 = last 24 hours; 0 = no cutoff. Sourced from the
  // shared user-level preference so selecting "24h" here propagates to
  // Overview/Runs/Trends/Coverage/Summary/My Failures and vice versa.
  const storedDays = useTimeWindowStore(s => s.days)
  const setStoredDays = useTimeWindowStore(s => s.setDays)
  const days = snapToAllowed(storedDays, LIVE_WINDOWS) as LiveWindow
  const setDays = setStoredDays

  const {
    sessions,
    recentEvents: rawRecentEvents,
    wsStatus,
    isLoading,
  } = useLiveExecution(projectId, selectedSuite || null, days)

  const [sortField, setSortField] = useState<SortField>('started_at')
  const [sortDir,   setSortDir]   = useState<SortDir>('desc')
  const [search,    setSearch]    = useState('')
  const [filter,    setFilter]    = useState<'all' | 'running' | 'failures'>('all')
  const [selectedStageId, setSelectedStageId] = useState<string>('stream_connection')
  const [feedFilter, setFeedFilter] = useState<'all' | 'errors' | 'stage'>('all')
  const [showRawSessions, setShowRawSessions] = useState(false)
  const { options: suiteOptions } = useSuiteOptions(7)

  const recentEvents = useMemo(() => {
    if (!selectedSuite) return rawRecentEvents
    const visibleRunIds = new Set(
      sessions
        .filter(s => suiteMatchesValue(s.suite_name, selectedSuite))
        .map(s => s.run_id),
    )
    return rawRecentEvents.filter(event =>
      suiteMatchesValue(event.suite_name, selectedSuite)
      || (event.run_id ? visibleRunIds.has(event.run_id) : false),
    )
  }, [rawRecentEvents, selectedSuite, sessions])

  const suiteScopedSessions = useMemo(() => {
    if (!selectedSuite) return sessions
    return sessions.filter(s => suiteMatchesValue(s.suite_name, selectedSuite))
  }, [sessions, selectedSuite])
  // A run is "active" only when it's still emitting telemetry. A run that
  // stops sending events stays as ``status='running'`` in the DB until the
  // 10-min reaper picks it up — those sessions are kept visible (with an
  // "Idle" badge in the table) but excluded from the hero count so the
  // page doesn't claim "5 active runs" when 3 of them are silently stuck.
  // See ``utils/liveSessionFreshness.ts``.
  const suiteScopedRunningSessions = useMemo(
    () => suiteScopedSessions.filter(s => isActivelyRunning(s)),
    [suiteScopedSessions],
  )
  const suiteScopedStaleSessions = useMemo(
    () => suiteScopedSessions.filter(s => isStaleRunning(s)),
    [suiteScopedSessions],
  )

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
    let list = suiteScopedSessions
    // "Running" filter chip matches the hero definition — actually-active
    // sessions only, not stale rows awaiting the reaper.
    if (filter === 'running')  list = list.filter(s => isActivelyRunning(s))
    if (filter === 'failures') list = list.filter(s => (s.failed ?? 0) > 0)
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
  }, [suiteScopedSessions, filter, search, sortField, sortDir])

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
    // Only surface a confidence score for the stream when the socket state
    // actually implies something meaningful. For "closed"/"error"/idle we
    // leave it undefined so the UI hides the pill instead of showing a
    // fabricated percentage.
    const streamConfidence =
      wsStatus === 'open' ? 100 : wsStatus === 'connecting' ? 60 : undefined

    // Likewise, zero-valued evidence counts are meaningless and should be
    // omitted so the "0 evidence" pill doesn't render on empty pages.
    const omitIfZero = (n: number): number | undefined => (n > 0 ? n : undefined)

    const workflowStages = [
      {
        stage_name: 'stream_connection',
        status: wsStatus === 'open' ? 'completed' : wsStatus === 'connecting' ? 'running' : wsStatus === 'error' ? 'failed' : 'pending',
        label: 'Stream Connection',
        description: 'Keep the live execution channel healthy',
        confidence_score: streamConfidence,
        evidence_count: omitIfZero(recentEvents.length),
        result_data: { ws_status: wsStatus },
      },
      {
        stage_name: 'run_monitoring',
        status: suiteScopedRunningSessions.length > 0 ? 'running' : 'pending',
        label: 'Run Monitoring',
        description: 'Track active runs and current tests',
        evidence_count: omitIfZero(suiteScopedRunningSessions.length),
        result_data: { running_sessions: suiteScopedRunningSessions.length, visible_sessions: visibleSessions.length },
      },
      {
        stage_name: 'event_rollup',
        status: recentEvents.length > 0 ? 'completed' : 'pending',
        label: 'Event Rollup',
        description: 'Roll execution events into a single live pulse',
        evidence_count: omitIfZero(recentEvents.length),
        result_data: { recent_events: recentEvents.length },
      },
      {
        stage_name: 'release_readout',
        status: suiteScopedSessions.length > 0 ? 'completed' : 'pending',
        label: 'Release Readout',
        description: 'Summarize the current execution state for release and QA',
        evidence_count: omitIfZero(suiteScopedSessions.length),
        result_data: { total_sessions: suiteScopedSessions.length, visible_pass_rate: visibleStats.overallPassRate },
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
  }, [wsStatus, suiteScopedRunningSessions.length, recentEvents, suiteScopedSessions.length, visibleSessions.length, visibleStats.overallPassRate])

  // Dedup by ``run_id`` so each LiveSession gets exactly one row in the
  // table. The earlier implementation deduped by ``build_number``, which
  // existed to collapse a legacy ingestion duplicate (one logical run
  // produced two LiveSession rows — slug + UUID — sharing a single
  // build_number). That duplication is gone: the modern SDK creates one
  // LiveSession per run with a server-generated UUID, and parallel
  // TestNG / pytest runs that share a build label (e.g.
  // ``testng-<timestamp>``) are GENUINELY distinct runs that should
  // each get their own row. Incident 2026-05-18: 4 active runs showed
  // ``4 running`` in the hero but only 1 in the table because all four
  // shared the same SDK-supplied build_number.
  //
  // Keeping the dedup function (rather than dropping it entirely) so a
  // future double-emit bug would still collapse identical run_ids
  // instead of rendering ghost rows. ``run_id`` is the LiveSession PK,
  // so this is effectively a no-op for normal traffic.
  const dedupedSessions = useMemo(() => {
    const byRunId = new Map<string, LiveSessionState>()
    for (const s of visibleSessions) {
      const key = s.run_id
      const existing = byRunId.get(key)
      const ts = s.last_event_at || s.started_at || ''
      const existingTs = existing ? (existing.last_event_at || existing.started_at || '') : ''
      if (!existing || ts > existingTs) byRunId.set(key, s)
    }
    return [...byRunId.values()]
  }, [visibleSessions])

  // Client-side pagination of the sessions table. KPIs and the workflow
  // strip continue to consume visibleSessions in full so their aggregates
  // stay correct; only the on-screen table slices to 25 rows per page.
  const TABLE_PAGE_SIZE = 25
  const [tablePage, setTablePage] = useState(1)
  const sessionsForTable = showRawSessions ? visibleSessions : dedupedSessions
  // Reset to page 1 if the source set (or the raw/deduped toggle) changes,
  // otherwise the user can land on a now-empty page after a filter shift.
  useEffect(() => { setTablePage(1) }, [showRawSessions, sessionsForTable.length])
  const tableTotalPages = Math.max(1, Math.ceil(sessionsForTable.length / TABLE_PAGE_SIZE))
  const pagedSessions = useMemo(() => {
    const start = (tablePage - 1) * TABLE_PAGE_SIZE
    return sessionsForTable.slice(start, start + TABLE_PAGE_SIZE)
  }, [sessionsForTable, tablePage])

  // Currently-selected workflow stage (for the detail strip below the subway).
  const selectedStage = workflow.stages.find(stage => stage.stage_name === selectedStageId) ?? workflow.stages[0]

  // Last update timestamp for the header.
  const lastUpdateLabel = recentEvents[0]
    ? new Date(recentEvents[0].timestamp).toLocaleTimeString()
    : '—'

  // Pipeline events for the right rail. Maps recentEvents through a small
  // tone-aware mapper so the design's success/info/warning/error palette
  // surfaces correctly. Filter tabs trim by tone.
  type FeedEvent = {
    stage: string
    time: string
    ago: string
    kind: string
    tone: 'success' | 'info' | 'warning' | 'error'
    detail?: string
    runId?: string
  }
  const pipelineEvents: FeedEvent[] = useMemo(() => {
    return recentEvents.map(e => {
      const tone: FeedEvent['tone'] =
        e.type === 'live_warning' ? 'warning' :
        e.type === 'live_run_complete' ? 'success' :
        e.last_status?.toUpperCase() === 'FAILED' || e.last_status?.toUpperCase() === 'BROKEN' ? 'error' :
        e.type === 'live_test_result' ? 'success' :
        'info'
      const stage =
        e.type === 'live_run_complete' ? 'Release Readout' :
        e.type === 'live_run_started' ? 'Run Monitoring' :
        e.type === 'live_test_result' ? 'Event Rollup' :
        'Stream Connection'
      return {
        stage,
        time: new Date(e.timestamp).toLocaleTimeString(),
        ago: relativeTime(e.timestamp),
        kind: e.type,
        tone,
        detail: e.message,
        runId: e.run_id,
      }
    })
  }, [recentEvents])
  const filteredFeed = useMemo(() => {
    if (feedFilter === 'errors') return pipelineEvents.filter(e => e.tone === 'error' || e.tone === 'warning')
    if (feedFilter === 'stage') return pipelineEvents.filter(e => e.kind.startsWith('live_run_') || e.kind === 'rollup_finalized')
    return pipelineEvents
  }, [pipelineEvents, feedFilter])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  // Anchor everything on whether anything is happening RIGHT NOW. Drives the
  // hero-state copy in the status strip and the LIVE badge animation.
  const liveSummary = (() => {
    const idleCount = suiteScopedStaleSessions.length
    const idleSuffix = idleCount > 0
      ? ` · ${idleCount} idle waiting on reaper`
      : ''
    if (suiteScopedRunningSessions.length > 0) {
      const totalActive = suiteScopedRunningSessions.reduce((a, s) => a + (s.total || 0), 0)
      return {
        hero: `${suiteScopedRunningSessions.length} active run${suiteScopedRunningSessions.length > 1 ? 's' : ''}`,
        sub: `${totalActive.toLocaleString()} tests in flight${idleSuffix}`,
        isLive: true,
      }
    }
    if (suiteScopedSessions.length > 0) {
      const last = suiteScopedSessions.find(s => s.completed_at) ?? suiteScopedSessions[0]
      const lastTs = last?.completed_at || last?.last_event_at
      const ago = lastTs ? relativeTime(new Date(lastTs).getTime()) : '—'
      return {
        hero: 'No active runs',
        sub: `Last completed ${ago}${idleSuffix}`,
        isLive: false,
      }
    }
    return { hero: 'No active runs', sub: 'Stream is connected — waiting for the first run', isLive: false }
  })()

  return (
    <main className="max-w-[1480px] mx-auto p-6 space-y-5">
      {/* ════ Header ════ */}
      <header className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-[var(--color-text)]">Live Execution</h1>
            <span className={clsx(
              'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium border',
              liveSummary.isLive
                ? 'bg-emerald-900/20 text-emerald-300 border-emerald-700/30'
                : 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)] border-[var(--color-border)]',
            )}>
              <span className={clsx(
                'h-2 w-2 rounded-full',
                liveSummary.isLive ? 'bg-emerald-400 animate-pulse' : 'bg-[var(--color-text-faint)]',
              )} />
              LIVE
            </span>
            {selectedProject && (
              <span className="font-mono text-[10px] px-2 py-0.5 rounded-full border bg-[var(--color-bg-card)] border-[var(--color-border)] text-[var(--color-text-muted)]">
                {selectedProject.name}
              </span>
            )}
            {selectedSuite && (
              <span className="font-mono text-[10px] px-2 py-0.5 rounded-full border bg-[var(--color-bg-card)] border-[var(--color-border)] text-[var(--color-text-muted)]">
                {selectedSuite}
              </span>
            )}
          </div>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            Real-time test execution stream{selectedProject ? ` — ${selectedProject.name}` : ' — all projects'}
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
          <LiveWindowPicker value={days} onChange={setDays} />
          <SuiteFilterSelect
            value={selectedSuite}
            onChange={setSelectedSuite}
            options={suiteOptions}
            allLabel="All suites"
          />
          <span className="flex items-center gap-1.5">
            <RefreshCw className="w-3 h-3" />
            Auto-refresh on
          </span>
          <span className="text-[var(--color-text-faint)]">·</span>
          <span>
            Last update <span className="font-mono text-[var(--color-text-secondary)]">{lastUpdateLabel}</span>
          </span>
          <WsStatusBadge status={wsStatus} />
        </div>
      </header>

      {/* ════ Status strip (replaces 4 KPI tiles) ════ */}
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-5">
        <div className="flex flex-wrap items-center gap-x-10 gap-y-4">
          {/* Hero state */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[var(--color-bg-hover)] flex items-center justify-center">
              {liveSummary.isLive
                ? <Activity className="w-4 h-4 text-emerald-400" />
                : <Pause className="w-4 h-4 text-[var(--color-text-muted)]" />}
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Current</p>
              <p className="text-sm font-semibold text-[var(--color-text)]">{liveSummary.hero}</p>
              <p className="text-[11px] text-[var(--color-text-muted)] mt-0.5">{liveSummary.sub}</p>
            </div>
          </div>
          <div className="hidden sm:block w-px h-12 bg-[var(--color-border)]" />
          {/* Inline KPIs */}
          <div className="flex items-baseline gap-1">
            <p className="text-2xl font-semibold tabular-nums text-[var(--color-text)]">{visibleStats.totalTests.toLocaleString()}</p>
            <p className="text-[11px] text-[var(--color-text-muted)] ml-1.5 leading-tight">
              tests across<br />{dedupedSessions.length} session{dedupedSessions.length === 1 ? '' : 's'}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Pass rate</p>
            <div className="flex items-center gap-2 mt-0.5">
              <p className={clsx('text-2xl font-semibold tabular-nums', passRateColor(visibleStats.overallPassRate))}>
                {visibleStats.overallPassRate}%
              </p>
              {visibleStats.overallPassRate < 90 && visibleStats.totalTests > 0 && (
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[rgba(251,191,36,.08)] text-[#fcd34d] border-[rgba(251,191,36,.30)]">
                  below 90% target
                </span>
              )}
            </div>
          </div>
          {/* Pass/fail bar */}
          <div className="flex-1 min-w-[240px]">
            <div className="flex items-center justify-between text-[11px] text-[var(--color-text-muted)] mb-1.5">
              <span>
                {visibleStats.totalPassed} passed
                {visibleStats.totalFailed > 0 && (
                  <> · <span className="text-red-400">{visibleStats.totalFailed} failed</span></>
                )}
                {visibleStats.totalSkipped > 0 && <> · {visibleStats.totalSkipped} skipped</>}
              </span>
              <span className="font-mono">{visibleStats.totalTests} total</span>
            </div>
            <div className="bg-[var(--color-bg-hover)] rounded-full h-1.5 overflow-hidden flex">
              {visibleStats.totalTests > 0 ? (
                <>
                  <span className="bg-emerald-500 h-full" style={{ width: `${(visibleStats.totalPassed / visibleStats.totalTests) * 100}%` }} />
                  <span className="bg-red-500 h-full" style={{ width: `${(visibleStats.totalFailed / visibleStats.totalTests) * 100}%` }} />
                </>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      {/* ════ Sessions table (deduped) ════ */}
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[var(--color-border)] flex-wrap">
          <h2 className="text-sm font-semibold text-[var(--color-text)] flex-1">Sessions</h2>
          <div className="flex items-center gap-1 bg-[var(--color-bg-card)] rounded-md p-0.5">
            {(['all', 'running', 'failures'] as const).map(f => {
              const count =
                f === 'all' ? suiteScopedSessions.length :
                // Match the hero's "active" definition so the chip badge
                // doesn't disagree with the headline KPI.
                f === 'running' ? suiteScopedSessions.filter(s => isActivelyRunning(s)).length :
                suiteScopedSessions.filter(s => (s.failed ?? 0) > 0).length
              return (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={clsx(
                    'px-2.5 py-1 rounded text-xs font-medium',
                    filter === f
                      ? 'bg-[var(--color-bg-hover)] text-[var(--color-text)]'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                >
                  {f === 'failures' ? 'Has failures' : f.charAt(0).toUpperCase() + f.slice(1)}
                  <span className="ml-1 text-[var(--color-text-muted)]">{count}</span>
                </button>
              )
            })}
          </div>
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-faint)]" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search runs…"
              className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded pl-8 pr-2 py-1 text-xs w-44 text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-accent)]"
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider">
              <tr className="border-b border-[var(--color-border)]">
                <th className="px-5 py-2.5 text-left font-medium">Build</th>
                <th className="px-3 py-2.5 text-left font-medium">Suite</th>
                <th className="px-3 py-2.5 text-left font-medium">Status</th>
                <th
                  className="px-3 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                  onClick={() => handleSort('total')}
                >
                  <span className="flex items-center justify-end gap-1">Tests <SortIcon field="total" /></span>
                </th>
                <th className="px-3 py-2.5 text-right font-medium">Pass</th>
                <th
                  className="px-3 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                  onClick={() => handleSort('failed')}
                >
                  <span className="flex items-center justify-end gap-1">Failed <SortIcon field="failed" /></span>
                </th>
                <th className="px-3 py-2.5 text-left font-medium" style={{ width: 200 }}>Outcome</th>
                <th className="px-3 py-2.5 text-left font-medium">Release</th>
                <th
                  className="px-5 py-2.5 text-right font-medium cursor-pointer hover:text-[var(--color-text-secondary)] select-none"
                  onClick={() => handleSort('started_at')}
                >
                  <span className="flex items-center justify-end gap-1">Started <SortIcon field="started_at" /></span>
                </th>
                <th className="px-5 py-2.5 text-right font-medium">End</th>
              </tr>
            </thead>
            <tbody>
              {(showRawSessions ? visibleSessions : dedupedSessions).length === 0 && (
                <tr>
                  <td colSpan={10} className="px-5 py-10 text-center text-[var(--color-text-muted)]">
                    {suiteScopedSessions.length === 0
                      ? 'No active execution sessions. Start a test run with the client SDK.'
                      : 'No sessions match the current filter.'}
                  </td>
                </tr>
              )}
              {pagedSessions.map(s => {
                const passW = s.total > 0 ? (s.passed / s.total) * 100 : 0
                const failW = s.total > 0 ? (s.failed / s.total) * 100 : 0
                return (
                  <tr key={s.run_id} className="border-b border-[var(--color-border)] hover:bg-[rgba(68,147,248,.04)] last:border-b-0">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <span className={clsx('h-2 w-2 rounded-full flex-shrink-0', statusDot(s.status))} />
                        <div>
                          {/* Use the canonical TestRun.id for navigation — SDK
                              run_ids are often slugs (e.g. `local-abc12345`)
                              that 422 against the UUID-typed /runs/{id} path.
                              Fall back to the slug only if the backend hasn't
                              populated test_run_id (older response shape). */}
                          <Link
                            to={`/runs/${s.test_run_id || s.run_id}`}
                            className="font-mono text-[var(--color-text)] hover:text-[var(--color-accent-2)]"
                          >
                            {/* Prefer the per-suite incremental Run #N
                                identifier (1-based) — backed by a
                                server-side ROW_NUMBER() over the
                                (project, suite) partition. Falls back
                                to the raw SDK build_number, then to a
                                short run_id slice. */}
                            {s.run_seq != null
                              ? `Run #${s.run_seq}`
                              : s.build_number || s.run_id.slice(0, 8)}
                          </Link>
                          <div className="font-mono text-[10px] text-[var(--color-text-faint)]">
                            {s.build_number || s.run_id.slice(0, 8)}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <SuiteBadge
                        primary={(s as unknown as { suite_name?: string | null }).suite_name}
                        all={null}
                        linkTo={name => `/test-management?tab=Test+Suites&suite=${encodeURIComponent(name)}`}
                      />
                    </td>
                    <td className="px-3 py-3">
                      {(() => {
                        // A ``running`` row that hasn't emitted in ~60s is
                        // probably dead-and-waiting-for-the-reaper. Surface
                        // that explicitly so the user doesn't think it's
                        // still in flight.
                        const stale = isStaleRunning(s)
                        return (
                          <span className={clsx(
                            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border',
                            stale
                              ? 'bg-amber-900/30 text-amber-300 border-amber-700/30'
                              : s.status === 'running'
                                ? 'bg-[rgba(68,147,248,.10)] text-[#93c5fd] border-[rgba(68,147,248,.30)]'
                                : 'bg-[rgba(52,211,153,.10)] text-emerald-300 border-[rgba(52,211,153,.30)]',
                          )}
                          title={stale ? 'No telemetry for over a minute — pending reaper cleanup' : undefined}
                          >
                            {stale ? 'idle' : s.status}
                          </span>
                        )
                      })()}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-[var(--color-text)]">{s.total}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-[var(--color-text)]">{s.passed}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-red-400 font-medium">
                      {s.failed > 0 ? s.failed : <span className="text-[var(--color-text-faint)]">0</span>}
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-2">
                        <div className="bg-[var(--color-bg-hover)] rounded-full h-1.5 overflow-hidden flex w-24">
                          <span className="bg-emerald-500 h-full" style={{ width: `${passW}%` }} />
                          <span className="bg-red-500 h-full" style={{ width: `${failW}%` }} />
                        </div>
                        <span className={clsx('font-medium tabular-nums', passRateColor(s.pass_rate))}>
                          {s.pass_rate.toFixed(1)}%
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      {s.release_name ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border bg-violet-900/30 text-violet-300 border-violet-700/30">
                          <Package className="w-2.5 h-2.5" />
                          {s.release_name}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text-faint)]">—</span>
                      )}
                    </td>
                    {/* Started — kept sortable via the same column that previously
                        held the (now misleadingly labelled) "Completed" cell. */}
                    <td className="px-5 py-3 text-right text-[var(--color-text-muted)] font-mono text-[11px] whitespace-nowrap tabular-nums">
                      {s.started_at ? new Date(s.started_at).toLocaleString() : '—'}
                    </td>
                    {/* End — completed_at when the session has closed; otherwise
                        last_event_at gives the "still running, last seen" hint. */}
                    <td
                      className="px-5 py-3 text-right text-[var(--color-text-muted)] font-mono text-[11px] whitespace-nowrap tabular-nums"
                      title={!s.completed_at && s.last_event_at ? `Still running · last event ${new Date(s.last_event_at).toLocaleString()}` : undefined}
                    >
                      {s.completed_at
                        ? new Date(s.completed_at).toLocaleString()
                        : s.last_event_at
                          ? `${new Date(s.last_event_at).toLocaleString()} (live)`
                          : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <Pagination
          page={tablePage}
          pages={tableTotalPages}
          total={sessionsForTable.length}
          onChange={setTablePage}
        />
        <div className="px-5 py-2.5 border-t border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)] flex items-center justify-between flex-wrap gap-2">
          <span>
            {dedupedSessions.length} session{dedupedSessions.length === 1 ? '' : 's'}
            {visibleSessions.length > dedupedSessions.length && (
              // Reach this branch only if a future double-emit produces
              // two LiveSession rows with the SAME ``run_id`` — at which
              // point telling the user "duplicate run_id collapsed" is
              // the right copy. The legacy slug-vs-UUID pattern is gone
              // (the dedup is now keyed by run_id; see comment at
              // ``dedupedSessions`` above).
              <> · {visibleSessions.length - dedupedSessions.length} duplicate run_id row{visibleSessions.length - dedupedSessions.length === 1 ? '' : 's'} collapsed</>
            )}
          </span>
          {visibleSessions.length > dedupedSessions.length && (
            <button
              type="button"
              onClick={() => setShowRawSessions(v => !v)}
              className="text-[var(--color-accent-2)] hover:underline flex items-center gap-1"
            >
              <List className="w-3 h-3" />
              {showRawSessions ? 'Hide raw rows' : `Show ${visibleSessions.length} raw rows`}
            </button>
          )}
        </div>
      </section>

      {/* ════ Workflow + Event feed ════ */}
      <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5">
        {/* Workflow flow + selected detail */}
        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-5 space-y-5">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <p className="text-sm font-semibold text-[var(--color-text)]">Live execution workflow</p>
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                Connection health → run monitoring → event rollup → release readout
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[rgba(52,211,153,.10)] text-emerald-300 border-[rgba(52,211,153,.30)]">
                <Check className="w-3 h-3" />
                {workflow.stages.filter(s => s.status === 'completed').length} done
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border-[var(--color-border)]">
                <CircleDashed className="w-3 h-3" />
                {workflow.stages.filter(s => s.status === 'pending').length} pending
              </span>
            </div>
          </div>

          {/* Subway map of stages */}
          <div className="flex items-stretch gap-0 overflow-x-auto pb-1">
            {workflow.stages.map((stage, idx) => {
              const isLast = idx === workflow.stages.length - 1
              const status = stage.status
              const isSelected = selectedStageId === stage.stage_name
              const Icon = status === 'completed' ? Check : status === 'failed' ? XCircle : status === 'running' ? Activity : Clock
              const stageColor =
                status === 'completed' ? 'rgba(52,211,153,.12)' :
                status === 'failed' ? 'rgba(248,113,113,.12)' :
                status === 'running' ? 'var(--color-accent-muted)' :
                'var(--color-bg-hover)'
              const stageBorder =
                status === 'completed' ? 'rgba(52,211,153,.4)' :
                status === 'failed' ? 'rgba(248,113,113,.4)' :
                status === 'running' ? 'rgba(68,147,248,.4)' :
                'var(--color-border)'
              const stageIconColor =
                status === 'completed' ? 'text-emerald-400' :
                status === 'failed' ? 'text-red-400' :
                status === 'running' ? 'text-[var(--color-accent)]' :
                'text-[var(--color-text-muted)]'
              const statusLabelColor =
                status === 'completed' ? 'text-emerald-400' :
                status === 'failed' ? 'text-red-400' :
                status === 'running' ? 'text-[var(--color-accent)]' :
                'text-[var(--color-text-muted)]'
              return (
                <Fragment key={stage.stage_name}>
                  <button
                    type="button"
                    onClick={() => setSelectedStageId(stage.stage_name)}
                    className={clsx(
                      'relative bg-[var(--color-bg-card)] border rounded-2xl px-4 py-3.5 min-w-[232px] text-left flex-shrink-0 transition-colors',
                      status === 'pending' && 'opacity-60 border-dashed',
                      status === 'failed' && 'border-red-500/45',
                    )}
                    style={{
                      borderColor: isSelected ? 'var(--color-accent)' : stageBorder,
                      outline: isSelected ? '2px solid var(--color-accent)' : 'none',
                      outlineOffset: isSelected ? -2 : 0,
                      boxShadow: isSelected ? '0 0 0 4px var(--color-accent-muted)' : 'none',
                    }}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-2">
                        <div
                          className="h-7 w-7 rounded-full flex items-center justify-center border"
                          style={{ background: stageColor, borderColor: stageBorder }}
                        >
                          <Icon className={clsx('w-3.5 h-3.5', stageIconColor, status === 'running' && 'animate-spin')} />
                        </div>
                        <p className="text-sm font-semibold text-[var(--color-text)]">{stage.label}</p>
                      </div>
                      <span className={clsx('text-[10px] uppercase tracking-wider', statusLabelColor)}>
                        {status === 'completed' ? 'Done' : status === 'failed' ? 'Failed' : status === 'running' ? 'Running' : 'Pending'}
                      </span>
                    </div>
                    <p className="text-[11px] text-[var(--color-text-muted)] mb-2">{stage.description}</p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {stage.confidence_score != null && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[rgba(52,211,153,.10)] text-emerald-300 border-[rgba(52,211,153,.30)]">
                          {stage.confidence_score}% conf
                        </span>
                      )}
                      {stage.evidence_count != null && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[rgba(68,147,248,.10)] text-[#93c5fd] border-[rgba(68,147,248,.30)]">
                          {stage.evidence_count} evidence
                        </span>
                      )}
                      {stage.confidence_score == null && stage.evidence_count == null && (
                        <span className="text-[11px] text-[var(--color-text-faint)]">No data yet</span>
                      )}
                    </div>
                  </button>
                  {!isLast && (
                    <div
                      className={clsx('flex-auto self-center mx-0.5 relative', 'min-w-[24px] h-0.5')}
                      style={{
                        background: status === 'completed'
                          ? 'rgb(52 211 153)'
                          : 'repeating-linear-gradient(90deg, var(--color-text-faint) 0 4px, transparent 4px 8px)',
                      }}
                    >
                      <span
                        className="absolute -right-px top-1/2 -translate-y-1/2 w-0 h-0"
                        style={{
                          borderTop: '5px solid transparent',
                          borderBottom: '5px solid transparent',
                          borderLeft: status === 'completed'
                            ? '6px solid rgb(52 211 153)'
                            : '6px solid var(--color-text-faint)',
                        }}
                      />
                    </div>
                  )}
                </Fragment>
              )
            })}
          </div>

          {/* Selected stage detail strip */}
          {selectedStage && (
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)]/40 p-4">
              <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-[var(--color-text)]">{selectedStage.label}</p>
                  <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    selected stage
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                <div className="rounded-lg bg-[var(--color-bg-card)] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</p>
                  <p className="mt-1 font-medium text-[var(--color-text)] capitalize">{selectedStage.status}</p>
                </div>
                <div className="rounded-lg bg-[var(--color-bg-card)] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Confidence</p>
                  <p className="mt-1 font-medium text-[var(--color-text)]">
                    {selectedStage.confidence_score != null ? `${selectedStage.confidence_score}%` : '—'}
                  </p>
                </div>
                <div className="rounded-lg bg-[var(--color-bg-card)] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Evidence</p>
                  <p className="mt-1 font-medium text-[var(--color-text)]">
                    {selectedStage.evidence_count != null ? `${selectedStage.evidence_count} events` : '—'}
                  </p>
                </div>
                <div className="rounded-lg bg-[var(--color-bg-card)] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Cost</p>
                  <p className="mt-1 font-medium text-[var(--color-text-muted)]">—</p>
                </div>
              </div>
              {selectedStage.result_data && Object.keys(selectedStage.result_data).length > 0 && (
                <div className="mt-3 rounded-lg bg-[var(--color-bg-card)] p-3">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-1.5">Result data</p>
                  <div className="font-mono text-xs">
                    {Object.entries(selectedStage.result_data).map(([k, v]) => (
                      <div key={k}>
                        <span className="text-[var(--color-text-muted)]">{k}:</span>{' '}
                        <span className="text-emerald-400">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Single consolidated Pipeline events feed */}
        <aside className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] flex flex-col xl:max-h-[640px]">
          <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-[var(--color-border)]">
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold text-[var(--color-text)]">Pipeline events</p>
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border-[var(--color-border)]">
                {pipelineEvents.length}
              </span>
            </div>
            <div className="flex gap-1">
              {(['all', 'errors', 'stage'] as const).map(f => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFeedFilter(f)}
                  className={clsx(
                    'px-2 py-0.5 rounded text-[10px] uppercase tracking-wider',
                    feedFilter === f
                      ? 'bg-[var(--color-bg-hover)] text-[var(--color-text)]'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                  )}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
            {filteredFeed.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-40 text-[var(--color-text-faint)]">
                <Radio className="h-7 w-7 mb-2 opacity-40" />
                <p className="text-xs">Waiting for events…</p>
              </div>
            ) : (
              filteredFeed.map((e, i) => {
                const Icon = e.tone === 'success' ? CheckCircle2 : e.tone === 'warning' ? AlertTriangle : e.tone === 'error' ? XCircle : CircleDot
                const iconColor =
                  e.tone === 'success' ? 'text-emerald-400' :
                  e.tone === 'warning' ? 'text-amber-400' :
                  e.tone === 'error' ? 'text-red-400' :
                  'text-[var(--color-accent-2)]'
                return (
                  <div
                    key={`${e.kind}-${e.time}-${i}`}
                    className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)]/60 px-3 py-2 hover:border-[var(--color-border-light)]"
                  >
                    <div className="flex items-start gap-2">
                      <Icon className={clsx('w-3.5 h-3.5 mt-0.5 flex-shrink-0', iconColor)} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-medium text-[var(--color-text)] truncate">{e.stage}</p>
                          <span className="text-[10px] text-[var(--color-text-faint)] font-mono whitespace-nowrap">
                            {e.ago} ago
                          </span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[10px] font-mono text-[var(--color-text-muted)]">{e.kind}</span>
                          {e.detail && <span className="text-[10px] text-[var(--color-text-muted)] truncate">· {e.detail}</span>}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
          <div className="px-4 py-2 border-t border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)] flex items-center justify-between">
            <span>Streaming · 100ms batch</span>
            <span className="font-mono">{recentEvents.length} buffered</span>
          </div>
        </aside>
      </section>

      {/* ════ Connect a runner (collapsed) ════ */}
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <details>
          <summary className="cursor-pointer px-5 py-4 flex items-center gap-3 list-none [&::-webkit-details-marker]:hidden">
            <div className="w-9 h-9 rounded-lg bg-[var(--color-bg-hover)] flex items-center justify-center">
              <Terminal className="w-4 h-4 text-[var(--color-text-muted)]" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold text-[var(--color-text)]">Connect a test runner</p>
              <p className="text-[11px] text-[var(--color-text-muted)] mt-0.5">
                Stream pytest, JUnit, Jest, or Go test events into this dashboard
              </p>
            </div>
            <span className="text-[11px] text-[var(--color-text-muted)] mr-2">Python · Java · JS · Go SDKs</span>
            <ChevronDown className="w-4 h-4 text-[var(--color-text-muted)] transition-transform [details[open]_&]:rotate-180" />
          </summary>
          <div className="px-5 py-4 border-t border-[var(--color-border)]">
            <ClientSDKGuide projectId={selectedProject?.id} />
          </div>
        </details>
      </section>
    </main>
  )
}
