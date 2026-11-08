/**
 * NTF-01: where a bell entry links, if anywhere.
 *
 * The server sets `link` only from its own allow-list of three path shapes
 * (`gate_notifications.APP_PATH_RE`): a release (`/releases/<uuid>`), a run's
 * release decision (`/release-gate/<uuid>`) and (W21d ACT-02, a comment
 * mention) a test case (`/test-management?case=<uuid>`). The SPA re-checks the
 * shape (a link is never built from text, and nothing else is followed) and the
 * role: `/releases` is a management route (QA lead and admin), so for anyone
 * else that entry stays text; `/release-gate/<run>` and `/test-management` are
 * open to every role.
 *
 * W38 (W31 F-1, D-6): two EXACT strings join the list — `/users?tab=erasure-requests`
 * (the administrators' "An erasure request is waiting for review"; /users is a
 * management route, so it links only for QA lead / admin) and `/settings/profile`
 * (the person's "Your erasure request was declined"; every role). Exact: no other
 * tab, query, fragment, trailing slash or case variant is followed.
 *
 * Pure. Never throws.
 */
import type { NotificationLog } from '@/types/notifications'

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const RELEASE_GATE_PATH = new RegExp(`^/release-gate/${UUID}$`)
const RELEASE_PATH = new RegExp(`^/releases/${UUID}$`)
const TEST_CASE_PATH = new RegExp(`^/test-management[?]case=${UUID}$`)
/** W38: compared as whole strings (=== ), never as patterns. */
const ERASURE_REQUESTS_PATH = '/users?tab=erasure-requests'
const PROFILE_PATH = '/settings/profile'

export function notificationHref(
  log: Pick<NotificationLog, 'link'> | null | undefined,
  opts: { canAccessManagement: boolean },
): string | null {
  const link = log?.link
  if (typeof link !== 'string') return null
  if (RELEASE_GATE_PATH.test(link)) return link
  if (TEST_CASE_PATH.test(link)) return link
  if (RELEASE_PATH.test(link)) return opts.canAccessManagement ? link : null
  if (link === PROFILE_PATH) return link
  if (link === ERASURE_REQUESTS_PATH) return opts.canAccessManagement ? link : null
  return null
}

/** The bell's channel line: `in_app` (a mention) reads `IN APP`; every other channel as before, upper-cased. */
export function notificationChannelLabel(channel: string | null | undefined): string {
  if (typeof channel !== 'string') return ''
  return channel === 'in_app' ? 'IN APP' : channel.toUpperCase()
}
