/**
 * Documentation manifest — the single registry of in-app documentation pages.
 *
 * Content lives in sibling `*.md` files, not in the page component. Before
 * this, DocsPage held five sections of hand-written JSX; adding a section meant
 * editing a React component, and the prose was invisible to every tool that
 * reads Markdown — including the repository's Mermaid validator, which walks
 * git-tracked `*.md`. Diagrams written here are checked by CI for free.
 *
 * `keywords` feeds in-page filtering. `summary` is shown under the title and in
 * the section list, so a reader can tell whether a page answers their question
 * before opening it.
 *
 * Adding a page: create `<id>.md`, add an entry here, and the nav, routing,
 * filter and tests pick it up. `docsManifest.test.ts` asserts every entry has a
 * content file and every content file has an entry, so the two cannot drift.
 */
import {
  Rocket,
  BookOpen,
  Boxes,
  Upload,
  Bot,
  SearchCheck,
  Repeat,
  Scale,
  FileText,
  GaugeCircle,
  Search,
  BarChart3,
  ChartLine,
  ClipboardList,
  Plug,
  Settings,
  Network,
  Route,
  ShieldCheck,
  LifeBuoy,
  Compass,
  GitBranch,
  Radio,
  Inbox,
  MessageSquare,
  type LucideIcon,
} from 'lucide-react'

export type DocGroup =
  | 'Start here'
  | 'Using TestLookup'
  | 'How it decides'
  | 'Reference'

export interface DocPage {
  /** URL segment: /docs/<id>. Stable — treat as a permalink. */
  id: string
  label: string
  group: DocGroup
  icon: LucideIcon
  /** One line, shown under the title and in the section list. */
  summary: string
  /** Extra terms for the filter box beyond title + body text. */
  keywords: string[]
}

export const DOC_PAGES: DocPage[] = [
  {
    id: 'introduction',
    label: 'What TestLookup is',
    group: 'Start here',
    icon: BookOpen,
    summary:
      'What the product does, who it is for, and how deterministic results differ from AI suggestions.',
    keywords: ['overview', 'intro', 'roles', 'capabilities', 'ai', 'deterministic'],
  },
  {
    id: 'getting-started',
    label: 'Getting started',
    group: 'Start here',
    icon: Rocket,
    summary:
      'Sign in, create a project, send your first test run, and confirm it arrived.',
    keywords: ['onboarding', 'first run', 'quick start', 'setup', 'upload', 'login', 'checklist', 'register', 'mfa'],
  },
  {
    id: 'navigation',
    label: 'Finding your way around',
    group: 'Start here',
    icon: Compass,
    summary:
      'The sidebar, the top bar, the Help menu, the settings index, and where old links now go.',
    keywords: ['sidebar', 'menu', 'navigation', 'top bar', 'project selector', 'time window', 'release picker', 'redirect', 'old links'],
  },
  {
    id: 'concepts',
    label: 'Concepts and terminology',
    group: 'Start here',
    icon: Boxes,
    summary:
      'Project, run, test case, fingerprint, suite, defect, release — and how they relate.',
    keywords: ['glossary', 'terminology', 'fingerprint', 'suite', 'definitions'],
  },

  {
    id: 'ingestion',
    label: 'Getting results in',
    group: 'Using TestLookup',
    icon: Upload,
    summary:
      'Every way to send test results, what happens to them, and how to verify success.',
    keywords: ['upload', 'ingest', 'junit', 'ci', 'sdk', 'cli', 'streaming', 'parser', 'api key', 'manual_upload'],
  },
  {
    id: 'runs',
    label: 'Runs and run detail',
    group: 'Using TestLookup',
    icon: GitBranch,
    summary:
      'The run list, a run\'s Tests, Analysis, Changes and Evidence tabs, comparing runs and bisecting a regression.',
    keywords: ['run', 'build', 'compare', 'bisect', 'analysis', 'evidence', 'test result', 'release tag', 'pipeline'],
  },
  {
    id: 'live',
    label: 'Watching runs live',
    group: 'Using TestLookup',
    icon: Radio,
    summary:
      'The Live page: runs streaming in test by test, sessions, the event feed, and connecting a test runner.',
    keywords: ['live', 'streaming', 'sdk', 'session', 'websocket', 'real-time', 'reporter'],
  },
  {
    id: 'inbox',
    label: 'Your inbox and approvals',
    group: 'Using TestLookup',
    icon: Inbox,
    summary:
      'Failures assigned to you, how to resolve or reassign them, and the approvals waiting for you.',
    keywords: ['inbox', 'my failures', 'assigned', 'triage', 'approvals', 'review', 'reassign', 'ai report', 'quarantine proposal'],
  },
  {
    id: 'failure-analysis',
    label: 'Investigating failures',
    group: 'Using TestLookup',
    icon: SearchCheck,
    summary:
      'The Failures, Defects and Root cause (AI) pages: categories, evidence, corrections and defects.',
    keywords: ['triage', 'root cause', 'classification', 'evidence', 'category', 'defect', 'jira', 'deep investigation', 'correct'],
  },
  {
    id: 'flaky',
    label: 'Flaky tests',
    group: 'Using TestLookup',
    icon: Repeat,
    summary:
      'What counts as flaky, the exact scoring inputs, and when the answer is "not enough data".',
    keywords: ['flaky', 'quarantine', 'intermittent', 'oscillation', 'stability'],
  },
  {
    id: 'search',
    label: 'Search and discovery',
    group: 'Using TestLookup',
    icon: Search,
    summary:
      'Finding runs, tests, defects and releases by keyword, and where meaning-based matching is used instead.',
    keywords: ['search', 'similar', 'index', 'semantic', 'keyword', 'reindex', 'discovery'],
  },
  {
    id: 'dashboards',
    label: 'Dashboards and metrics',
    group: 'Using TestLookup',
    icon: BarChart3,
    summary:
      'Every dashboard, what each metric counts, and what an empty state actually means.',
    keywords: ['analytics', 'trends', 'coverage', 'overview', 'metrics', 'charts'],
  },
  {
    id: 'charts',
    label: 'Charts and visual analysis',
    group: 'Using TestLookup',
    icon: ChartLine,
    summary:
      'Reading, zooming, filtering from and exporting any chart, and what each report page draws.',
    keywords: ['chart', 'export', 'png', 'svg', 'csv', 'zoom', 'compare', 'heatmap', 'scatter', '3d', 'sankey', 'saved views', 'cross-filter', 'presentation'],
  },
  {
    id: 'test-management',
    label: 'Test management',
    group: 'Using TestLookup',
    icon: ClipboardList,
    summary: 'Suites, observed tests, the authored test-case catalogue and its review lifecycle, and ownership.',
    keywords: ['suites', 'ownership', 'assignment', 'managed tests', 'test cases', 'lifecycle', 'review', 'plans', 'strategy', 'duplicates', 'promote', 'knowledge'],
  },
  {
    id: 'releases',
    label: 'Releases and gates',
    group: 'Using TestLookup',
    icon: GaugeCircle,
    summary:
      'Creating releases, phases and linked runs, gate policies, and what a GO / CONDITIONAL_GO / NO_GO means.',
    keywords: ['release', 'gate', 'go', 'no-go', 'readiness', 'risk', 'phase', 'policy', 'override', 'compliance'],
  },
  {
    id: 'ask-ai',
    label: 'Ask AI',
    group: 'Using TestLookup',
    icon: MessageSquare,
    summary:
      'Asking questions about your test results in plain language, and how to check the answers.',
    keywords: ['chat', 'ask ai', 'question', 'llm', 'assistant', 'conversation'],
  },

  {
    id: 'decisions',
    label: 'How decisions are made',
    group: 'How it decides',
    icon: Scale,
    summary:
      'Decision-by-decision tables separating observed evidence, rules, models and AI text.',
    keywords: ['decision', 'confidence', 'evidence', 'override', 'audit', 'policy'],
  },
  {
    // NOT 'agents': `.gitignore` ignores AGENTS.md at every depth (local-only
    // files), and a case-insensitive checkout matches `agents.md`,
    // so the file was silently never committed and CI checked out 18 of 19.
    id: 'ai-agents',
    label: 'AI agents and the pipeline',
    group: 'How it decides',
    icon: Bot,
    summary:
      'Each agent, when it runs, what it may read, and what happens when it fails.',
    keywords: ['agent', 'pipeline', 'llm', 'workflow', 'stages', 'orchestration'],
  },
  {
    id: 'reports',
    label: 'Reports and outputs',
    group: 'How it decides',
    icon: FileText,
    summary:
      'Every artefact TestLookup produces, the Summary report and Value metrics pages, exports and shared links.',
    keywords: ['report', 'export', 'pdf', 'csv', 'summary', 'share', 'decision report', 'value metrics', 'hours saved', 'roi'],
  },

  {
    id: 'architecture',
    label: 'Architecture',
    group: 'Reference',
    icon: Network,
    summary: 'Services, stores, queues and how a request travels through them.',
    keywords: ['architecture', 'components', 'deployment', 'diagram', 'workers'],
  },
  {
    id: 'workflows',
    label: 'Step-by-step workflows',
    group: 'Reference',
    icon: Route,
    summary: 'Task recipes with prerequisites, required role, and expected result.',
    keywords: ['how to', 'workflow', 'recipe', 'steps', 'guide'],
  },
  {
    id: 'integrations',
    label: 'API, CLI, SDKs and MCP',
    group: 'Reference',
    icon: Plug,
    summary: 'Programmatic access, authentication, and which interface to reach for.',
    keywords: ['api', 'cli', 'sdk', 'mcp', 'webhook', 'jira', 'integration', 'token'],
  },
  {
    id: 'administration',
    label: 'Settings and administration',
    group: 'Reference',
    icon: Settings,
    summary:
      'Every settings page — account, project, integrations, AI, security and system — who can open it, and what it changes.',
    keywords: ['admin', 'roles', 'permissions', 'api key', 'feature flag', 'settings', 'sso', 'mfa', 'webhook', 'notifications', 'retention', 'audit', 'activity log', 'billing', 'budget', 'storage', 'performance', 'team channels', 'ownership'],
  },
  {
    id: 'security',
    label: 'Security and privacy',
    group: 'Reference',
    icon: ShieldCheck,
    summary:
      'Project isolation, what leaves the deployment, and administrator responsibilities.',
    keywords: ['security', 'privacy', 'isolation', 'tenant', 'offline', 'data'],
  },
  {
    id: 'troubleshooting',
    label: 'Troubleshooting',
    group: 'Reference',
    icon: LifeBuoy,
    summary: 'Symptoms, likely causes and the check to run for each common problem.',
    keywords: ['troubleshoot', 'error', 'problem', 'empty', 'missing', 'failed', 'faq'],
  },
]

export const DOC_GROUPS: DocGroup[] = [
  'Start here',
  'Using TestLookup',
  'How it decides',
  'Reference',
]

export const DEFAULT_DOC_ID = 'introduction'

export function findDocPage(id: string | undefined): DocPage | undefined {
  return DOC_PAGES.find((p) => p.id === id)
}

/**
 * The pages immediately before and after `id` in reading order — what a
 * "Previous / Next" pager at the foot of a topic links to.
 *
 * `DOC_PAGES` is authored in the order a reader should move through the guide
 * (Start here → Using TestLookup → How it decides → Reference), so sequential
 * position in that array IS the reading sequence — no separate ordering to keep
 * in sync. The first topic has no `prev` and the last has no `next`; an unknown
 * or absent id yields `{}`, so a fallback-to-default render simply shows no
 * pager rather than a link to nowhere.
 *
 * This lets a self-hoster read the documentation straight through without
 * returning to the sidebar to hunt for the next topic — the sidebar groups by
 * area, which does not tell you what comes next.
 */
export function adjacentDocPages(id: string | undefined): {
  prev?: DocPage
  next?: DocPage
} {
  const i = DOC_PAGES.findIndex((p) => p.id === id)
  if (i === -1) return {}
  return {
    prev: i > 0 ? DOC_PAGES[i - 1] : undefined,
    next: i < DOC_PAGES.length - 1 ? DOC_PAGES[i + 1] : undefined,
  }
}
