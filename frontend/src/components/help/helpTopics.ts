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
  '/my-failures': { topic: 'failure-analysis' },
  '/reviews': { topic: 'reports', anchor: 'the-decision-report-is-verified-before-publication' },
  '/runs': { topic: 'ingestion' },
  '/live': { topic: 'ingestion', anchor: 'confirming-it-worked' },
  '/intelligence': { topic: 'ai-agents' },
  '/failures': { topic: 'failure-analysis' },
  '/defects': { topic: 'failure-analysis', anchor: 'promoting-to-a-defect' },
  '/deep-investigate': { topic: 'failure-analysis', anchor: 'the-path-a-failure-takes' },
  '/flaky-coach': { topic: 'flaky' },
  '/quarantine': { topic: 'flaky', anchor: 'quarantine-is-a-recommendation-not-an-action' },
  '/trends': { topic: 'dashboards' },
  '/coverage': { topic: 'dashboards', anchor: 'coverage-means-executed-not-code' },
  '/explore': { topic: 'dashboards', anchor: 'explorer' },
  '/suites': { topic: 'concepts', anchor: 'definitions' },
  '/canonical-test-cases': { topic: 'concepts', anchor: 'definitions' },
  '/test-management': { topic: 'test-management' },
  '/reports': { topic: 'reports' },
  '/value-metrics': { topic: 'reports' },
  '/release-gate': { topic: 'releases', anchor: 'the-recommendation' },
  '/releases': { topic: 'releases' },
  '/policies': { topic: 'releases', anchor: 'the-decision-rules-in-order' },
  '/search': { topic: 'search' },
  '/chat': { topic: 'ai-agents' },
  '/agents': { topic: 'ai-agents' },
  '/settings': { topic: 'administration' },
  '/settings/sso': { topic: 'security' },
  '/settings/mfa-policy': { topic: 'security' },
  '/settings/integrations': { topic: 'integrations' },
  '/settings/github': { topic: 'integrations' },
  '/settings/gitlab': { topic: 'integrations' },
  '/settings/webhooks': { topic: 'integrations' },
  '/projects': { topic: 'administration' },
  '/users': { topic: 'administration' },
  '/ownership': { topic: 'administration' },
  '/activity': { topic: 'administration' },
  '/getting-started': { topic: 'getting-started' },
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
