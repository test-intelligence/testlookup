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
  ClipboardList,
  Plug,
  Settings,
  Network,
  Route,
  ShieldCheck,
  LifeBuoy,
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
    keywords: ['onboarding', 'first run', 'quick start', 'setup', 'upload', 'login'],
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
    keywords: ['upload', 'ingest', 'junit', 'ci', 'sdk', 'cli', 'streaming', 'parser'],
  },
  {
    id: 'failure-analysis',
    label: 'Investigating failures',
    group: 'Using TestLookup',
    icon: SearchCheck,
    summary:
      'How a failure is categorised, what evidence is attached, and how to correct it.',
    keywords: ['triage', 'root cause', 'classification', 'evidence', 'category'],
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
      'Finding runs, tests and similar failures — and what happens when semantic search is off.',
    keywords: ['search', 'similar', 'index', 'semantic', 'rag', 'discovery'],
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
    id: 'test-management',
    label: 'Test management',
    group: 'Using TestLookup',
    icon: ClipboardList,
    summary: 'Suites, ownership, assignment and triage of individual test cases.',
    keywords: ['suites', 'ownership', 'assignment', 'managed tests', 'triage'],
  },
  {
    id: 'releases',
    label: 'Releases and gates',
    group: 'Using TestLookup',
    icon: GaugeCircle,
    summary:
      'Release readiness, what a GO / CONDITIONAL_GO / NO_GO means, and who can override it.',
    keywords: ['release', 'gate', 'go', 'no-go', 'readiness', 'risk'],
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
    // NOT 'agents': `.gitignore` ignores AGENTS.md at every depth (coding-agent
    // instruction files), and a case-insensitive checkout matches `agents.md`,
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
      'Every artefact TestLookup can produce, what generates it, and where it goes.',
    keywords: ['report', 'export', 'pdf', 'csv', 'summary', 'share', 'decision report'],
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
    label: 'Administration',
    group: 'Reference',
    icon: Settings,
    summary: 'Users, roles, API keys, feature flags, AI configuration and retention.',
    keywords: ['admin', 'roles', 'permissions', 'api key', 'feature flag', 'settings'],
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
