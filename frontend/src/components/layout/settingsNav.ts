/**
 * Settings and admin, as data (UX redesign P5, `02-design-spec.md` §4).
 *
 * One list is the single source for three things:
 *  - the grouped sub-nav beside every settings and admin page (`SettingsLayout`);
 *  - the `/settings` index, the same groups as compact lists (it was 22 cards);
 *  (Which pages get that layout is `settingsRoutes.ts`, eager and data-free.)
 *
 * Role filtering mirrors the routes, never more strictly: an item a role can
 * open (App.tsx's route lists) is an item that role sees, so the sub-nav never
 * hides a page the old card grid offered. `admin` is for the pages whose
 * route or whole content is admin-only (Users' actions are, its list is not:
 * it stays `management`).
 */
/** The settings index — "All settings", and where a page not for this build sends you. */
export const SETTINGS_ROOT = '/settings'

export type SettingsAccess = 'all' | 'management'

export interface SettingsItem {
  /** Stable id: what tests select on (`data-settings-item`), never the label. */
  id: string
  label: string
  /** A line for the index page: what the page is for. */
  description: string
  /** The route. */
  to: string
  /** When the page has tabs and the item is one of them (`?tab=`). */
  tab?: string
  requires: SettingsAccess
  /** Development builds only (the route is guarded too). */
  devOnly?: boolean
}

export interface SettingsGroup {
  id: string
  label: string
  items: readonly SettingsItem[]
}

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  {
    id: 'account',
    label: 'Account',
    items: [
      { id: 'profile', label: 'Profile', description: 'Your name, password and personal API keys', to: '/settings/profile', requires: 'all' },
      { id: 'my-notifications', label: 'My notifications', description: 'What reaches you, and how', to: '/settings/my-notifications', requires: 'all' },
    ],
  },
  {
    id: 'project',
    label: 'Project',
    items: [
      { id: 'projects', label: 'Projects', description: 'Create, rename and archive projects', to: '/projects', requires: 'management' },
      { id: 'members', label: 'Members & access', description: "Who is on each project, and their role in it", to: '/users', tab: 'project-members', requires: 'management' },
      { id: 'ownership', label: 'Ownership', description: 'Which team owns which suites and tests', to: '/ownership', requires: 'management' },
      { id: 'api-keys', label: 'Streaming API keys', description: "The project's keys for CI result streaming", to: '/settings/api-keys', requires: 'management' },
      { id: 'retention', label: 'Retention & purge', description: 'How long runs and artifacts are kept', to: '/settings/retention', requires: 'management' },
      { id: 'project-data', label: 'Project data', description: 'Export, import and reset a project\'s data', to: '/settings/project-data', requires: 'management' },
    ],
  },
  {
    id: 'release-governance',
    label: 'Release governance',
    items: [
      { id: 'policies', label: 'Gate policies', description: 'The rules a release must pass (also under Releases)', to: '/policies', requires: 'management' },
    ],
  },
  {
    id: 'integrations',
    label: 'Integrations',
    items: [
      { id: 'integrations', label: 'Jira, Splunk, OCP, Slack, Teams', description: 'Connections to trackers, logs and chat', to: '/settings/integrations', requires: 'management' },
      { id: 'github', label: 'GitHub', description: 'Repository, checks and pull-request comments', to: '/settings/github', requires: 'management' },
      { id: 'gitlab', label: 'GitLab', description: 'Repository and merge-request comments', to: '/settings/gitlab', requires: 'management' },
      { id: 'webhooks', label: 'Outbound webhooks', description: 'Events sent to your own endpoints', to: '/settings/webhooks', requires: 'management' },
      { id: 'email', label: 'Email (SMTP) & channels', description: 'The mail server and the shared notification channels', to: '/settings/notifications', requires: 'management' },
      { id: 'digests', label: 'Digests & views', description: 'Scheduled summaries and saved views', to: '/settings/digests', requires: 'management' },
      { id: 'integration-health', label: 'Integration health', description: 'Whether each connection is working', to: '/settings/integration-health', requires: 'management' },
    ],
  },
  {
    id: 'ai',
    label: 'AI',
    items: [
      { id: 'ai-config', label: 'AI configuration', description: 'Analysis mode, models and providers', to: '/settings/ai', requires: 'management' },
      { id: 'ai-agents', label: 'AI agents', description: 'Which agents run, and how', to: '/settings/ai-agents', requires: 'management' },
      { id: 'workflows', label: 'Workflow editor', description: 'The stages an AI pipeline runs', to: '/agents/workflows', requires: 'all' },
      { id: 'pipeline-runs', label: 'Pipeline runs', description: 'Each AI pipeline run, its report and its stages', to: '/agents', requires: 'all' },
      { id: 'agent-activity', label: 'Agent activity', description: 'What the agents did, and when', to: '/settings/agent-activity', requires: 'management' },
      { id: 'ai-eval', label: 'AI evaluation', description: 'How well the AI answers, measured', to: '/settings/ai-eval', requires: 'management' },
      { id: 'billing', label: 'LLM cost budget', description: 'Spend and quota per project', to: '/settings/billing', requires: 'management' },
    ],
  },
  {
    id: 'security',
    label: 'Security & access',
    items: [
      { id: 'users', label: 'Users', description: 'Accounts and their roles', to: '/users', requires: 'management' },
      { id: 'sso', label: 'SSO & identity', description: 'Single sign-on providers', to: '/settings/sso', requires: 'management' },
      { id: 'mfa-policy', label: 'MFA & lockout', description: 'Second factor and failed-login lockout', to: '/settings/mfa-policy', requires: 'management' },
      { id: 'audit', label: 'Audit dashboard', description: 'Who changed what', to: '/settings/audit', requires: 'management' },
      { id: 'activity', label: 'Activity log', description: 'Everything that happened, in order', to: '/activity', requires: 'management' },
    ],
  },
  {
    id: 'system',
    label: 'System',
    items: [
      { id: 'storage', label: 'Data & storage', description: 'Where results and artifacts are stored', to: '/settings/storage', requires: 'management' },
      { id: 'performance', label: 'Performance', description: 'Request timings and slow queries', to: '/settings/performance', requires: 'management' },
      { id: 'feature-flags', label: 'Feature flags', description: 'Turn features on and off', to: '/settings/feature-flags', requires: 'management' },
      { id: 'seed-data', label: 'Seed data', description: 'Load, reset or delete demo data', to: '/settings/seed-data', requires: 'management', devOnly: true },
    ],
  },
]

export interface SettingsViewer {
  canAccessManagement: boolean
  isDev: boolean
}

/** The groups this user sees, each with only the items they can open; empty groups dropped. */
export function settingsGroupsFor(viewer: SettingsViewer): SettingsGroup[] {
  return SETTINGS_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) => (item.requires === 'all' || viewer.canAccessManagement) && (!item.devOnly || viewer.isDev),
    ),
  })).filter((group) => group.items.length > 0)
}

export function settingsItemHref(item: SettingsItem): string {
  return item.tab ? `${item.to}?tab=${encodeURIComponent(item.tab)}` : item.to
}

function under(prefix: string, pathname: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

/**
 * The item the current page is: the longest `to` the path falls under; among
 * items of one page told apart by `?tab=`, the one whose tab is the current
 * tab, else the one with no tab (the page's own entry).
 */
export function activeSettingsItem(pathname: string, search: string, groups: readonly SettingsGroup[] = SETTINGS_GROUPS): SettingsItem | null {
  const tab = new URLSearchParams(search).get('tab')
  let best: SettingsItem | null = null
  let bestScore = -1
  for (const group of groups) {
    for (const item of group.items) {
      if (!under(item.to, pathname)) continue
      if (item.tab && item.tab !== tab) continue
      // Longer path first; a matching tab beats the page's own entry.
      const score = item.to.length * 2 + (item.tab ? 1 : 0)
      if (score > bestScore) {
        best = item
        bestScore = score
      }
    }
  }
  return best
}
