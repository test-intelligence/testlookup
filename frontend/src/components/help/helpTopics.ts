/**
 * Which documentation topic answers "help for this page" (UX redesign P1).
 *
 * A topic is a page id in `content/guide/manifest.ts`, with an optional anchor
 * (a heading slug in that page). `helpTopics.test.ts` holds every entry to a
 * real page and a real heading, and every sidebar route to a topic, so a
 * renamed doc or heading fails a test instead of opening an empty drawer.
 */

export interface HelpTopic {
  topic: string
  anchor?: string
}

/** Route prefix → topic. The longest matching prefix wins. */
export const HELP_TOPICS: Readonly<Record<string, HelpTopic>> = {
  '/overview': { topic: 'dashboards', anchor: 'the-views' },
  '/my-failures': { topic: 'inbox' },
  // Pinned by MyFailuresPage.inbox.test.tsx (the Approvals tab's help).
  '/reviews': { topic: 'reports', anchor: 'the-decision-report-is-verified-before-publication' },
  // Pinned by RunsPage.test.tsx; a run's own page (/runs/<id>) has the same prefix.
  '/runs': { topic: 'ingestion' },
  '/runs/compare': { topic: 'runs', anchor: 'comparing-two-runs' },
  '/live': { topic: 'live' },
  '/intelligence': { topic: 'runs' },
  '/failures': { topic: 'failure-analysis', anchor: 'the-failures-page' },
  '/defects': { topic: 'failure-analysis', anchor: 'promoting-to-a-defect' },
  '/deep-investigate': { topic: 'failure-analysis', anchor: 'root-cause-ai-deep-investigation' },
  '/flaky': { topic: 'flaky', anchor: 'the-flaky-tests-page' },
  '/flaky-coach': { topic: 'flaky', anchor: 'the-flaky-tests-page' },
  '/quarantine': { topic: 'flaky', anchor: 'quarantine-on-the-flaky-tests-page' },
  '/trends': { topic: 'dashboards' },
  '/coverage': { topic: 'dashboards', anchor: 'coverage-means-executed-not-code' },
  '/explore': { topic: 'dashboards', anchor: 'explorer' },
  // Pinned by SuiteCasesPage.test.tsx (topic only).
  '/suites': { topic: 'concepts', anchor: 'definitions' },
  '/canonical-test-cases': { topic: 'test-management', anchor: 'an-observed-tests-page' },
  '/test-management': { topic: 'test-management', anchor: 'test-cases' },
  '/reports': { topic: 'reports', anchor: 'the-summary-report' },
  '/value-metrics': { topic: 'reports', anchor: 'value-metrics' },
  '/release-gate': { topic: 'releases', anchor: 'the-release-gate' },
  '/releases': { topic: 'releases', anchor: 'releases' },
  // W11 REL-09: the rules page opens the guide's own section.
  '/releases/attribution-rules': { topic: 'releases', anchor: 'attribution-rules' },
  // Pinned by PolicyEditorPage.test.tsx.
  '/policies': { topic: 'releases', anchor: 'the-decision-rules-in-order' },
  '/search': { topic: 'search' },
  '/chat': { topic: 'ask-ai' },
  '/agents': { topic: 'ai-agents', anchor: 'pipeline-runs' },
  '/agents/workflows': { topic: 'ai-agents', anchor: 'the-workflow-editor' },
  '/settings': { topic: 'administration', anchor: 'the-settings-index' },
  '/settings/profile': { topic: 'administration', anchor: 'profile' },
  '/settings/sso': { topic: 'administration', anchor: 'sso-and-identity' },
  '/settings/mfa-policy': { topic: 'administration', anchor: 'mfa-and-lockout' },
  // The integrations topic is pinned by GitHubIntegrationPage/OutboundWebhooksPage tests (topic only).
  '/settings/integrations': { topic: 'integrations', anchor: 'webhooks-and-issue-trackers' },
  '/settings/github': { topic: 'integrations', anchor: 'webhooks-and-issue-trackers' },
  '/settings/gitlab': { topic: 'integrations', anchor: 'webhooks-and-issue-trackers' },
  '/settings/webhooks': { topic: 'integrations', anchor: 'webhooks-and-issue-trackers' },
  // Pinned by TeamChannelsPage.test.tsx.
  '/settings/team-channels': { topic: 'administration', anchor: 'notifications-and-webhooks' },
  // UX redesign P5: each settings page opens its own section, not the guide's top.
  '/settings/notifications': { topic: 'administration', anchor: 'email-smtp-and-channels' },
  '/settings/my-notifications': { topic: 'administration', anchor: 'my-notifications' },
  '/settings/digests': { topic: 'administration', anchor: 'digests-and-views' },
  '/settings/integration-health': { topic: 'administration', anchor: 'integration-health' },
  '/settings/api-keys': { topic: 'administration', anchor: 'api-keys' },
  '/settings/my-api-keys': { topic: 'administration', anchor: 'api-keys' },
  '/settings/retention': { topic: 'administration', anchor: 'retention-and-purge' },
  '/settings/expected-runs': { topic: 'administration', anchor: 'expected-runs' },
  '/settings/project-data': { topic: 'administration', anchor: 'project-data' },
  '/settings/storage': { topic: 'administration', anchor: 'data-and-storage' },
  '/settings/performance': { topic: 'administration', anchor: 'performance' },
  '/settings/feature-flags': { topic: 'administration', anchor: 'feature-flags' },
  '/settings/audit': { topic: 'administration', anchor: 'audit' },
  '/settings/ai': { topic: 'administration', anchor: 'ai-configuration' },
  '/settings/ai-agents': { topic: 'administration', anchor: 'ai-agents-and-agent-activity' },
  '/settings/agent-activity': { topic: 'administration', anchor: 'ai-agents-and-agent-activity' },
  '/settings/ai-eval': { topic: 'administration', anchor: 'ai-evaluation' },
  '/settings/billing': { topic: 'administration', anchor: 'llm-cost-budget' },
  '/projects': { topic: 'administration', anchor: 'projects' },
  '/users': { topic: 'administration', anchor: 'users-and-roles' },
  '/ownership': { topic: 'administration', anchor: 'ownership' },
  '/activity': { topic: 'administration', anchor: 'activity-log' },
  '/getting-started': { topic: 'getting-started', anchor: 'the-first-run-checklist' },
}

/** Where help starts when no route matches. */
export const DEFAULT_HELP_TOPIC: HelpTopic = { topic: 'introduction' }

/** `PageHeader`'s `helpTopic` for `pathname`: `topic`, or `topic#anchor` when the route has a section. */
export function helpTopicParam(pathname: string): string {
  const { topic, anchor } = helpTopicFor(pathname)
  return anchor ? `${topic}#${anchor}` : topic
}

/** The help topic for `pathname`. */
export function helpTopicFor(pathname: string): HelpTopic {
  let best: HelpTopic = DEFAULT_HELP_TOPIC
  let bestLength = 0
  for (const [prefix, topic] of Object.entries(HELP_TOPICS)) {
    const matches = pathname === prefix || pathname.startsWith(`${prefix}/`)
    if (matches && prefix.length > bestLength) {
      best = topic
      bestLength = prefix.length
    }
  }
  return best
}
